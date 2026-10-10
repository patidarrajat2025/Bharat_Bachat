from datetime import datetime, timezone, date, timedelta
import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..db import get_db, backfill_financial_feed
from ..deps import current_user, tenant_guard, require_roles, parse_oid
from ..models import *
from ..services import tenant_summary, analytics
from ..audit import audit
from ..core.config import settings
from ..core.cloudinary import upload_bytes, delete_asset
from ..core.security import hash_password, normalize_phone
from ..share_service import ensure_member_shares

router=APIRouter(prefix="/group",tags=["group"])

# Financial-feed repair is lazy and only runs when the materialized ledger is
# behind the source collections. This keeps startup fast while guaranteeing that
# ledger drill-downs never show empty/zero data just because the background
# backfill has not finished yet.
_feed_repair_lock=asyncio.Lock()

async def ensure_financial_feed_ready(tenant_id: str):
    """Repair the accounting read model when source counts OR stored totals drift.

    A total-count-only check can miss a stale feed whose row count matches the
    source collections (for example, old rows with zero amounts). Compare each
    source bucket independently and its signed total before trusting the feed.
    """
    db=get_db()
    # Normalize already-materialized legacy rows whose account was missing,
    # null or blank. Otherwise an account=cash query misses records even when
    # account-wise totals appear to match during reconciliation.
    await db.financial_feed.update_many(
        {"tenant_id":tenant_id,"$or":[{"account":None},{"account":""}]},
        {"$set":{"account":"cash"}},
    )

    async def needs_repair():
        amount_expr={"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}
        account_expr={"$cond":[{"$in":[{"$ifNull":["$account",""]},[""]] },"cash","$account"]}

        def account_pipeline(match):
            return [
                {"$match":match},
                {"$project":{"amount":amount_expr,"account":account_expr}},
                {"$group":{"_id":"$account","count":{"$sum":1},"total":{"$sum":"$amount"}}},
            ]

        tx_count, expense_count, feed_tx_count, feed_expense_count, tx_sum_rows, expense_sum_rows, feed_tx_sum_rows, feed_expense_sum_rows, tx_accounts, expense_accounts, feed_tx_accounts, feed_expense_accounts = await asyncio.gather(
            db.transactions.count_documents({"tenant_id":tenant_id}),
            db.expenses.count_documents({"tenant_id":tenant_id}),
            db.financial_feed.count_documents({"tenant_id":tenant_id,"source_type":"transaction"}),
            db.financial_feed.count_documents({"tenant_id":tenant_id,"source_type":"expense"}),
            db.transactions.aggregate([{"$match":{"tenant_id":tenant_id}},{"$group":{"_id":None,"total":{"$sum":amount_expr}}}]).to_list(1),
            db.expenses.aggregate([{"$match":{"tenant_id":tenant_id}},{"$group":{"_id":None,"total":{"$sum":amount_expr}}}]).to_list(1),
            db.financial_feed.aggregate([{"$match":{"tenant_id":tenant_id,"source_type":"transaction"}},{"$group":{"_id":None,"total":{"$sum":{"$ifNull":["$amount",0]}}}}]).to_list(1),
            db.financial_feed.aggregate([{"$match":{"tenant_id":tenant_id,"source_type":"expense"}},{"$group":{"_id":None,"total":{"$sum":{"$ifNull":["$amount",0]}}}}]).to_list(1),
            db.transactions.aggregate(account_pipeline({"tenant_id":tenant_id})).to_list(None),
            db.expenses.aggregate(account_pipeline({"tenant_id":tenant_id})).to_list(None),
            db.financial_feed.aggregate(account_pipeline({"tenant_id":tenant_id,"source_type":"transaction"})).to_list(None),
            db.financial_feed.aggregate(account_pipeline({"tenant_id":tenant_id,"source_type":"expense"})).to_list(None),
        )
        source_tx_total=float((tx_sum_rows[0] if tx_sum_rows else {}).get("total",0) or 0)
        source_expense_total=float((expense_sum_rows[0] if expense_sum_rows else {}).get("total",0) or 0)
        feed_tx_total=float((feed_tx_sum_rows[0] if feed_tx_sum_rows else {}).get("total",0) or 0)
        feed_expense_total=float((feed_expense_sum_rows[0] if feed_expense_sum_rows else {}).get("total",0) or 0)

        def account_buckets(rows, expense_source=False):
            # Counts and signed totals per account catch Cash/Bank misclassification
            # even when the global financial-feed sum still matches the source data.
            buckets={}
            for row in rows:
                account=str(row.get("_id") or "cash")
                total=float(row.get("total",0) or 0)
                if expense_source:
                    total=-abs(total)
                buckets[account]=(int(row.get("count",0) or 0),round(total,2))
            return buckets

        return (
            tx_count != feed_tx_count
            or expense_count != feed_expense_count
            or abs(source_tx_total-feed_tx_total) > 0.01
            or abs(-source_expense_total-feed_expense_total) > 0.01
            or account_buckets(tx_accounts) != account_buckets(feed_tx_accounts)
            or account_buckets(expense_accounts, expense_source=True) != account_buckets(feed_expense_accounts)
        )

    # Legacy source rows without an account are treated as cash throughout the
    # dashboard; normalize the materialized rows to the same default so the
    # cash statement and group summary agree.
    await db.financial_feed.update_many({"tenant_id":tenant_id,"$or":[{"account":{"$exists":False}},{"account":None},{"account":""}]},{"$set":{"account":"cash"}})
    # Normalize legacy positive expense feed rows once. Expense entries are
    # debits in the universal register and therefore must have a negative sign.
    await db.financial_feed.update_many({"tenant_id":tenant_id,"source_type":"expense","amount":{"$gt":0}},[{"$set":{"amount":{"$multiply":["$amount",-1]},"amount_minor":{"$multiply":["$amount_minor",-1]}}}])
    if not await needs_repair():
        return
    async with _feed_repair_lock:
        if await needs_repair():
            await backfill_financial_feed(tenant_id)
            # The idempotent backfill refreshes source values. Re-normalize any
            # legacy expense rows it has just upserted before they are queried.
            await db.financial_feed.update_many({"tenant_id":tenant_id,"source_type":"expense","amount":{"$gt":0}},[{"$set":{"amount":{"$multiply":["$amount",-1]},"amount_minor":{"$multiply":["$amount_minor",-1]}}}])

def as_id(v): return str(v)

async def acquire_operation_lock(operation_key: str, ttl_seconds: int = 30):
    """Short-lived Mongo lock for money-changing operations.
    It prevents two admin requests from calculating the same balance/outstanding
    concurrently without requiring a multi-document transaction on every write.
    """
    db=get_db(); now=datetime.now(timezone.utc); expires=now+timedelta(seconds=ttl_seconds)
    await db.operation_locks.delete_many({"operation_key":operation_key,"expires_at":{"$lte":now}})
    try:
        await db.operation_locks.insert_one({"operation_key":operation_key,"created_at":now,"expires_at":expires})
    except Exception:
        raise HTTPException(409,"Another financial operation is already being processed. Please try again.")
    return operation_key

async def release_operation_lock(operation_key: str):
    try: await get_db().operation_locks.delete_one({"operation_key":operation_key})
    except Exception: pass

async def existing_tx_by_idempotency(tenant_id: str, key: str | None):
    if not key: return None
    return await get_db().transactions.find_one({"tenant_id":tenant_id,"idempotency_key":key})

async def admin_user(user=Depends(current_user)):
    if user["role"] not in ("group_admin","super_admin"): raise HTTPException(403,"Admin access required")
    return user
async def get_member(db,tenant_id,member_id):
    m=await db.members.find_one({"_id":parse_oid(member_id),"tenant_id":tenant_id})
    if not m: raise HTTPException(404,"Member not found")
    return m

def serialize(doc):
    if not doc:return doc
    d=dict(doc); d["_id"]=str(d["_id"])
    for k in ("tenant_id","member_id","loan_id","share_id","created_by","password_reset_by"):
        if k in d and hasattr(d[k],"__str__"): d[k]=str(d[k])
    return d

@router.get("/{tenant_id}")
async def tenant(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); row=await get_db().tenants.find_one({"_id":parse_oid(tenant_id)})
    if not row: raise HTTPException(404,"Group not found")
    return serialize(row)

@router.post("/{tenant_id}/logo")
async def upload_group_logo(tenant_id:str,file:UploadFile=File(...),user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Only JPG, PNG or WebP images are allowed")
    data=await file.read()
    if len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    import uuid
    r=upload_bytes(data,public_id="logo",folder=f"bharat-bachat/tenants/{tenant_id}",resource_type="image")
    old=tenant.get("logo_public_id")
    await db.tenants.update_one({"_id":tenant["_id"]},{"$set":{"logo_url":r.get("secure_url"),"logo_public_id":r.get("public_id")}})
    if old and old!=r.get("public_id"):
        try: delete_asset(old)
        except Exception: pass
    await audit(tenant_id,user,"GROUP_LOGO_UPDATED","tenant",tenant_id)
    return {"ok":True,"logo_url":r.get("secure_url")}

@router.get("/{tenant_id}/dashboard")
async def dashboard(tenant_id:str,user=Depends(current_user)):
    """Single read model for the main dashboard.

    The browser used to open 5-7 independent endpoints on every dashboard mount.
    We keep those endpoints for compatibility, but the dashboard can now be
    hydrated with one HTTP request while MongoDB work runs concurrently.
    """
    await tenant_guard(user,tenant_id)
    db=get_db()
    from ..share_service import ensure_group_admin_member
    member_id=str(user.get("member_id") or "")
    if user.get("role")=="group_admin" and not member_id:
        member_id=str(await ensure_group_admin_member(user) or "")

    summary_task=tenant_summary(tenant_id,member_id or None)
    tenant_task=db.tenants.find_one({"_id":parse_oid(tenant_id)})
    members_task=asyncio.sleep(0,result=[])
    activity_task=group_activity(tenant_id,user=user)
    # Monthly status is intentionally kept compatible with the existing UI.
    month=datetime.now(timezone.utc).strftime("%Y-%m")
    kist_task=monthly_kist_summary(tenant_id,month,user)
    if member_id:
        passbook_task=passbook(tenant_id,member_id,user=user)
        loans_task=loans(tenant_id,member_id,user=user)
        shares_task=member_shares(tenant_id,member_id,user=user)
    else:
        passbook_task=asyncio.sleep(0,result=[])
        loans_task=asyncio.sleep(0,result=[])
        shares_task=asyncio.sleep(0,result=[])
    summary_row, tenant_row, member_rows, activity_rows, kist_row, personal_rows, personal_loans, shares_rows = await asyncio.gather(
        summary_task, tenant_task, members_task, activity_task, kist_task, passbook_task, loans_task, shares_task
    )
    if not tenant_row: raise HTTPException(404,"Group not found")
    return {"tenant":serialize(tenant_row),"summary":summary_row,"members":member_rows,"activity":activity_rows.get("items",[]) if isinstance(activity_rows,dict) else activity_rows,"activity_has_more":bool(activity_rows.get("has_more")) if isinstance(activity_rows,dict) else len(activity_rows)==10,"kist":kist_row,"personal":personal_rows,"personal_loans":personal_loans,"shares":shares_rows}

@router.get("/{tenant_id}/summary")
async def summary(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    member_id = str(user.get("member_id") or "") if user.get("role") in ("member", "group_admin") else ""
    if user.get("role") == "group_admin" and not member_id:
        from ..share_service import ensure_group_admin_member
        member_id = str(await ensure_group_admin_member(user) or "")
    return await tenant_summary(tenant_id, member_id or None)

@router.get("/{tenant_id}/analytics")
async def get_analytics(tenant_id:str,months:int=12,share_no:int|None=None,member_id:str|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member":
        member_id=str(user.get("member_id") or "")
    elif user["role"]=="group_admin":
        # A group admin is also represented by a member/share record. Resolve it
        # server-side so My Share profit works even with an older login session
        # whose JWT/user payload did not yet contain member_id.
        from ..share_service import ensure_group_admin_member
        member_id=str(await ensure_group_admin_member(user) or "")
    return await analytics(tenant_id,max(3,min(months,24)),share_no,member_id)


@router.get("/{tenant_id}/notifications")
async def notifications(tenant_id:str,user=Depends(current_user)):
    """Read-only notification feed.

    Notification creation is event-driven now. The previous GET endpoint created
    notifications from recent loans/audit/transactions on every page mount, which
    made a harmless bell refresh perform dozens of MongoDB writes.
    """
    await tenant_guard(user,tenant_id)
    db=get_db()
    if user["role"] in ("group_admin","super_admin"):
        q={"tenant_id":tenant_id,"recipient_role":"admin","dismissed":{"$ne":True}}
    else:
        mid=str(user.get("member_id") or "")
        if not mid: return []
        q={"tenant_id":tenant_id,"recipient_role":"member","recipient_member_id":mid,"dismissed":{"$ne":True}}
    try:
        rows=await db.notifications.find(q).sort("created_at",-1).limit(20).to_list(20)
        # Compatibility bootstrap only: if this tenant has never had any
        # notification records, seed a small initial feed once. Subsequent GETs
        # are read-only and never recreate dismissed notifications.
        any_existing=await db.notifications.find_one({"tenant_id":tenant_id,"source_key":{"$exists":True}}, {"_id":1})
        if not rows and not any_existing:
            if user["role"] in ("group_admin","super_admin"):
                seeds=await db.audit_logs.find({"tenant_id":tenant_id}).sort("created_at",-1).limit(10).to_list(10)
                for item in seeds:
                    key=f"audit:{item['_id']}"
                    await _create_notification(tenant_id,role="admin",source_key=key,title=str(item.get("action","Activity")).replace("_"," ").title(),body="New group activity recorded.",created_at=item.get("created_at"))
            else:
                mid=str(user.get("member_id") or "")
                seeds=await db.transactions.find({"tenant_id":tenant_id,"member_id":mid}).sort("date",-1).limit(10).to_list(10)
                for item in seeds:
                    key=f"tx:{item['_id']}:{mid}"
                    await _create_notification(tenant_id,role="member",member_id=mid,source_key=key,title="Account activity",body=f"{str(item.get('type','Transaction')).replace('_',' ').title()} ₹{float(item.get('amount',0)):,.2f}.",created_at=item.get("date"))
            rows=await db.notifications.find(q).sort("created_at",-1).limit(20).to_list(20)
        return [serialize(x) for x in rows]
    except Exception:
        return []

async def _create_notification(tenant_id:str, *, role:str, title:str, body:str, source_key:str, member_id:str|None=None, created_at=None):
    db=get_db()
    doc={"tenant_id":tenant_id,"recipient_role":role,"title":title,"body":body,"read":False,"created_at":created_at or datetime.now(timezone.utc),"source_key":source_key}
    if member_id: doc["recipient_member_id"]=member_id
    await db.notifications.update_one({"tenant_id":tenant_id,"source_key":source_key},{"$setOnInsert":doc},upsert=True)

@router.post("/{tenant_id}/notifications/clear")
async def clear_notifications(tenant_id:str,body:dict,user=Depends(current_user)):
    """Clear the currently displayed notifications in one POST request.

    We intentionally soft-delete by setting dismissed=True rather than physically
    deleting source-backed rows. The notification GET endpoint uses the same source_key
    with upsert, so keeping the row prevents an already-cleared notification from being
    recreated on the next refresh.
    """
    await tenant_guard(user,tenant_id)
    db=get_db(); ids=[str(x) for x in (body.get("ids") or []) if str(x)]
    valid=[]
    from bson import ObjectId
    for x in ids:
        try: valid.append(ObjectId(x))
        except Exception: pass
    if valid:
        q={"_id":{"$in":valid},"tenant_id":tenant_id}
        if user["role"]=="member":
            q["recipient_member_id"]=str(user.get("member_id") or "")
        result=await db.notifications.update_many(q,{"$set":{"dismissed":True,"dismissed_at":datetime.now(timezone.utc)}})
        return {"ok":True,"count":result.modified_count}
    return {"ok":True,"count":0}

@router.delete("/{tenant_id}/notifications/batch")
async def delete_notifications_batch(tenant_id:str,body:dict,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); ids=[str(x) for x in (body.get("ids") or []) if str(x)]
    if ids:
        from bson import ObjectId
        valid=[]
        for x in ids:
            try: valid.append(ObjectId(x))
            except Exception: pass
        if valid:
            await get_db().notifications.delete_many({"_id":{"$in":valid},"tenant_id":tenant_id})
    return {"ok":True,"count":len(ids)}

@router.patch("/{tenant_id}/notifications/{notification_id}/read")
async def mark_notification_read(tenant_id:str,notification_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    try:
        oid=parse_oid(notification_id)
    except HTTPException:
        raise HTTPException(400,"Invalid notification identifier")
    row=await db.notifications.find_one({"_id":oid,"tenant_id":tenant_id})
    if not row: raise HTTPException(404,"Notification not found")
    if user["role"]=="member" and row.get("recipient_member_id")!=str(user.get("member_id")): raise HTTPException(403,"Notification access denied")
    await db.notifications.update_one({"_id":row["_id"]},{"$set":{"read":True}}); return {"ok":True}

@router.delete("/{tenant_id}/notifications/{notification_id}")
async def delete_notification(tenant_id:str,notification_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    try:
        oid=parse_oid(notification_id)
    except HTTPException:
        raise HTTPException(400,"Invalid notification identifier")
    row=await db.notifications.find_one({"_id":oid,"tenant_id":tenant_id})
    if not row: raise HTTPException(404,"Notification not found")
    if user["role"]=="member" and row.get("recipient_member_id")!=str(user.get("member_id")): raise HTTPException(403,"Notification access denied")
    await db.notifications.update_one({"_id":row["_id"]},{"$set":{"dismissed":True,"dismissed_at":datetime.now(timezone.utc)}}); return {"ok":True}

@router.get("/{tenant_id}/members")
async def members(tenant_id:str,user=Depends(current_user),page:int|None=None,page_size:int=10,search:str|None=None,status:str|None=None):
    await tenant_guard(user,tenant_id); db=get_db(); q={"tenant_id":tenant_id}
    if user["role"]=="member": q["_id"]=parse_oid(user.get("member_id"))
    if search:
        import re
        safe=re.escape(search.strip())
        q["$or"]= [{"first_name":{"$regex":safe,"$options":"i"}},{"last_name":{"$regex":safe,"$options":"i"}},{"phone":{"$regex":safe,"$options":"i"}}]
    if status=="active": q["active"]={"$ne":False}
    elif status in ("inactive","pending"): q["active"]=False
    if page is None:
        rows=await db.members.find(q).sort("first_name",1).to_list(2000)
    else:
        page=max(1,page); page_size=max(1,min(page_size,50)); rows=await db.members.find(q).sort([("first_name",1),("_id",1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    ids=[str(row["_id"]) for row in rows]
    share_rows=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":ids},"status":"active"}).sort("share_no",1).to_list(20000) if ids else []
    share_map={mid:[] for mid in ids}
    for share in share_rows: share_map.setdefault(str(share["member_id"]),[]).append(share)
    # Reconcile only legacy/incomplete member share records. Normal reads remain read-only.
    for row in rows:
        mid=str(row["_id"]); expected=max(1,int(row.get("shares",1) or 1))
        if len(share_map.get(mid,[]))<expected:
            share_map[mid]=await ensure_member_shares(row)
    out=[]
    for row in rows:
        active_shares=share_map.get(str(row["_id"]),[])
        x=serialize(row); x["profile_picture_url"]=x.get("profile_picture_url") or x.get("profile_image_url")
        x["active_shares_count"]=len(active_shares); x["share_ids"]= [str(s["_id"]) for s in active_shares]
        x["share_numbers"]=[int(s.get("share_no",0)) for s in active_shares]
        out.append(x)
    if page is None: return out
    # Header metrics are global to the current tenant; search/status only filters the list.
    metric_q={"tenant_id":tenant_id}
    if user["role"]=="member": metric_q["_id"]=parse_oid(user.get("member_id"))
    total=await db.members.count_documents(metric_q)
    active=await db.members.count_documents({**metric_q,"active":{"$ne":False}})
    inactive=await db.members.count_documents({**metric_q,"active":False})
    share_match={"tenant_id":tenant_id,"status":"active"}
    if user["role"]=="member": share_match["member_id"]=str(user.get("member_id"))
    share_count=await db.shares.count_documents(share_match)
    return {"items":out,"page":page,"page_size":page_size,"total":total,"active_count":active,"inactive_count":inactive,"total_shares":share_count,"has_more":page*page_size<total}

@router.post("/{tenant_id}/members")
async def create_member(tenant_id:str,body:MemberCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    phone = normalize_phone(body.phone)
    if await db.users.find_one({"phone":phone}): raise HTTPException(409,"Phone already registered")
    if body.shares<1: raise HTTPException(400,"At least one share is required")
    now=datetime.now(timezone.utc)
    doc=body.model_dump(exclude={"password"})|{"tenant_id":tenant_id,"profile_image_url":None,"profile_image_public_id":None,"created_at":now,"active":True}
    result=await db.members.insert_one(doc)
    try:
        await db.users.insert_one({"tenant_id":parse_oid(tenant_id),"member_id":str(result.inserted_id),"phone":phone,"password_hash":hash_password(body.password),"name":f"{body.first_name} {body.last_name}".strip(),"email":body.email,"role":"member","active":True,"must_change_password":True,"password_changed_at":now,"created_at":now})
    except Exception:
        await db.members.delete_one({"_id":result.inserted_id}); raise
    await ensure_member_shares(await db.members.find_one({"_id":result.inserted_id}))
    await audit(tenant_id,user,"MEMBER_CREATED","member",str(result.inserted_id),{"shares":body.shares}); return {"id":str(result.inserted_id)}

@router.patch("/{tenant_id}/members/{member_id}")
async def update_member(tenant_id:str,member_id:str,body:MemberUpdate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); m=await get_member(db,tenant_id,member_id)
    await ensure_member_shares(m)
    active_count=await db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"})
    if body.shares < active_count:
        raise HTTPException(400,"Share count cannot be reduced below the member's active shares. Close shares explicitly before reducing the count.")
    await db.members.update_one({"_id":m["_id"]},{"$set":body.model_dump()})
    updated=await db.members.find_one({"_id":m["_id"]})
    await ensure_member_shares(updated)
    await db.users.update_one({"member_id":member_id,"tenant_id":parse_oid(tenant_id)},{"$set":{"name":f"{body.first_name} {body.last_name}".strip(),"email":body.email,"phone":m["phone"]}})
    await audit(tenant_id,user,"MEMBER_UPDATED","member",member_id,{"shares":body.shares}); return {"ok":True}

@router.get("/{tenant_id}/members/{member_id}/details")
async def member_details(tenant_id:str,member_id:str,user=Depends(current_user)):
    """Focused member read model; never downloads the whole group's loan ledger."""
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id:
        raise HTTPException(403,"Member access denied")
    db=get_db()
    member=await get_member(db,tenant_id,member_id)
    member_row, shares_row, passbook_row, loans_row, summary_row = await asyncio.gather(
        asyncio.sleep(0,result=serialize(member)),
        ensure_member_shares(member),
        passbook(tenant_id,member_id,page=1,page_size=10,user=user),
        loans(tenant_id,member_id,user=user),
        tenant_summary(tenant_id,member_id),
    )
    return {"member":member_row,"shares":[serialize(x) for x in shares_row],"passbook":passbook_row,"passbook_has_more":len(passbook_row)==10,"loans":loans_row,"summary":summary_row}

@router.get("/{tenant_id}/members/{member_id}/activity")
async def member_activity(tenant_id:str,member_id:str,page:int=1,page_size:int=10,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Member access denied")
    page=max(1,page); page_size=max(1,min(page_size,50))
    rows=await passbook(tenant_id,member_id,page=page,page_size=page_size,user=user)
    return {"items":rows,"has_more":len(rows)==page_size,"page":page}

@router.get("/{tenant_id}/members/{member_id}/shares")
async def member_shares(tenant_id:str,member_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id:
        raise HTTPException(403,"Member access denied")
    m=await get_member(get_db(),tenant_id,member_id)
    rows=await ensure_member_shares(m)
    return [serialize(x) for x in rows]

@router.patch("/{tenant_id}/members/{member_id}/status")
async def member_status(tenant_id:str,member_id:str,body:UserStatus,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); await get_member(get_db(),tenant_id,member_id)
    await get_db().users.update_one({"member_id":member_id,"tenant_id":parse_oid(tenant_id)},{"$set":{"active":body.active}})
    await get_db().members.update_one({"_id":parse_oid(member_id)},{"$set":{"active":body.active}})
    await audit(tenant_id,user,"MEMBER_STATUS_CHANGED","member",member_id,{"active":body.active}); return {"ok":True}

@router.post("/{tenant_id}/members/{member_id}/reset-password")
async def reset_member_password(tenant_id:str,member_id:str,body:PasswordReset,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); await get_member(get_db(),tenant_id,member_id)
    target=await get_db().users.find_one({"member_id":member_id,"tenant_id":parse_oid(tenant_id),"role":"member"})
    if not target: raise HTTPException(404,"Member login account not found")
    now=datetime.now(timezone.utc)
    await get_db().users.update_one({"_id":target["_id"]},{"$set":{"password_hash":hash_password(body.password),"must_change_password":True,"password_changed_at":now,"password_reset_at":now,"password_reset_by":str(user["_id"])}})
    await audit(tenant_id,user,"MEMBER_PASSWORD_RESET","user",str(target["_id"]),{"member_id":member_id}); return {"ok":True}

@router.post("/{tenant_id}/members/{member_id}/profile-image")
async def upload_member_image(tenant_id:str,member_id:str,file:UploadFile=File(...),user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,member_id)
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Only JPG, PNG or WebP images are allowed")
    data=await file.read()
    if len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    old_public_id=m.get("profile_image_public_id")
    import uuid
    r=upload_bytes(data,public_id=f"member-{member_id}-{uuid.uuid4().hex[:10]}",folder=f"bharat-bachat/tenants/{tenant_id}/members/{member_id}",resource_type="image")
    await get_db().members.update_one({"_id":m["_id"]},{"$set":{"profile_image_url":r.get("secure_url"),"profile_picture_url":r.get("secure_url"),"profile_image_public_id":r.get("public_id")}})
    if old_public_id and old_public_id!=r.get("public_id"):
        try: delete_asset(old_public_id)
        except Exception: pass
    await audit(tenant_id,user,"MEMBER_PROFILE_IMAGE_UPDATED","member",member_id); return {"ok":True,"profile_image_url":r.get("secure_url")}

@router.delete("/{tenant_id}/members/{member_id}/profile-image")
async def delete_member_image(tenant_id:str,member_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,member_id)
    if m.get("profile_image_public_id"): delete_asset(m["profile_image_public_id"])
    await get_db().members.update_one({"_id":m["_id"]},{"$set":{"profile_image_url":None,"profile_picture_url":None,"profile_image_public_id":None}})
    await audit(tenant_id,user,"MEMBER_PROFILE_IMAGE_DELETED","member",member_id); return {"ok":True}

async def get_share_for_member(tenant_id:str,member_id:str,share_id:str):
    row=await get_db().shares.find_one({"_id":parse_oid(share_id),"tenant_id":tenant_id,"member_id":member_id,"status":"active"})
    if not row: raise HTTPException(404,"Active share not found for this member")
    return row

async def ensure_share(member,share_no):
    if share_no>int(member.get("shares",1)): raise HTTPException(400,f"Member has only {member.get('shares',1)} share(s)")
    rows=await ensure_member_shares(member)
    return next((x for x in rows if int(x.get("share_no"))==share_no),None)


async def group_settings(tenant_id:str):
    tenant=await get_db().tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    return tenant

def _add_months(d:date, months:int):
    import calendar
    months=max(0,int(months or 0)); y=d.year+(d.month-1+months)//12; m=(d.month-1+months)%12+1; day=min(d.day,calendar.monthrange(y,m)[1])
    return date(y,m,day)

def _due_overdue_days(payment_dt:datetime, due_day:int, year:int|None=None, month:int|None=None):
    y=year or payment_dt.year; m=month or payment_dt.month
    import calendar
    due_day=min(max(1,int(due_day or 10)),calendar.monthrange(y,m)[1])
    due=datetime(y,m,due_day,tzinfo=timezone.utc)
    return max(0,(payment_dt.date()-due.date()).days)

async def _post_bc_penalty_once(tenant_id:str,member_id:str,period:str,payment_dt:datetime,account:str,user,note:str=""):
    db=get_db(); tenant=await group_settings(tenant_id)
    due_day=int(tenant.get("bc_due_date",10) or 10); per_day=float(tenant.get("bc_per_day_penalty",0) or 0)
    y,m=map(int,period.split("-")); overdue=_due_overdue_days(payment_dt,due_day,y,m); amount=round(overdue*per_day,2)
    if amount<=0: return None
    key=f"bc:{tenant_id}:{member_id}:{period}"
    if await db.transactions.find_one({"tenant_id":tenant_id,"bc_penalty_key":key}): return None
    return await insert_tx(tenant_id,member_id,"penalty",amount,account,user,date=payment_dt,note=note or f"BC Kist late penalty: {overdue} overdue day(s)",payment_category="bc",penalty_category="bc",bc_regular_kist_penalty=amount,overdue_days=overdue,per_day_penalty=per_day,period=period,bc_penalty_key=key)

async def _feed_upsert(doc, *, source_type="transaction"):
    db=get_db(); amount=float(doc.get("amount",0) or 0)
    if source_type=="expense": amount=-abs(amount)
    feed={"source_key":f"{source_type}:{doc.get('_id')}","source_type":source_type,"source_id":str(doc.get("_id")),"transaction_ref":doc.get("transaction_ref"),"tenant_id":doc.get("tenant_id"),"member_id":str(doc.get("member_id")) if doc.get("member_id") else None,"type":doc.get("type","transaction"),"amount":amount,"amount_minor":(-abs(int(doc.get("amount_minor",round(abs(amount)*100)) or 0)) if source_type=="expense" else int(doc.get("amount_minor",round(amount*100)) or 0)),"account":doc.get("account","cash"),"date":doc.get("date") or doc.get("created_at"),"created_at":doc.get("created_at") or doc.get("date"),"note":doc.get("note","") or "","payment_category":doc.get("payment_category"),"loan_interest_collected":float(doc.get("loan_interest_collected",doc.get("interest",0)) or 0),"loan_penalty_collected":float(doc.get("loan_penalty_collected",0) or 0),"bc_regular_kist_penalty":float(doc.get("bc_regular_kist_penalty",0) or 0),"interest":float(doc.get("interest",0) or 0),"principal":float(doc.get("principal",0) or 0),"share_no":doc.get("share_no"),"share_id":str(doc.get("share_id")) if doc.get("share_id") else None,"expense_id":str(doc.get("expense_id")) if doc.get("expense_id") else None,"category":doc.get("category") if source_type=="expense" else None}
    await db.financial_feed.update_one({"source_key":feed["source_key"]},{"$set":feed,"$setOnInsert":{"created_at":feed["created_at"]}},upsert=True)

async def insert_tx(tenant_id,member_id,typ,amount,account,user,**extra):
    db=get_db(); now=datetime.now(timezone.utc); idem=extra.pop("idempotency_key",None)
    if idem:
        existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":idem})
        if existing: return str(existing["_id"])
    normalized=round(float(amount or 0),2); doc={"tenant_id":tenant_id,"member_id":member_id,"type":typ,"amount":normalized,"amount_minor":int(round(normalized*100)),"account":account,"transaction_ref":f"BB-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:10].upper()}","created_at":now,"idempotency_key":idem,**extra}
    r=await db.transactions.insert_one(doc)
    doc["_id"]=r.inserted_id
    await _feed_upsert(doc)
    await audit(tenant_id,user,f"{typ.upper()}_POSTED","transaction",str(r.inserted_id),{"amount":amount})
    if member_id and typ != "expense_allocation":
        await _create_notification(tenant_id,role="member",member_id=str(member_id),source_key=f"tx:{r.inserted_id}:{member_id}",title="Account activity",body=f"{str(typ).replace('_',' ').title()} ₹{float(amount):,.2f}.",created_at=extra.get("date",now))
    if user.get("role") in ("member","group_admin") and typ != "expense_allocation":
        await _create_notification(tenant_id,role="admin",source_key=f"admin-tx:{r.inserted_id}",title="Group activity",body=f"{str(typ).replace('_',' ').title()} ₹{float(amount):,.2f}.",created_at=extra.get("date",now))
    return str(r.inserted_id)

@router.post("/{tenant_id}/contributions")
async def contribution(tenant_id:str,body:ContributionCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,body.member_id); share=await ensure_share(m,body.share_no)
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    return {"id":await insert_tx(tenant_id,body.member_id,"contribution",body.amount,body.account,user,share_id=str(share["_id"]),share_no=body.share_no,payment_category="manual_contribution",date=dt,note=body.note,idempotency_key=body.idempotency_key)}

@router.get("/{tenant_id}/monthly-kist-summary")
async def monthly_kist_summary(tenant_id:str,period:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    import re
    if not re.fullmatch(r"\d{4}-\d{2}",period): raise HTTPException(400,"Period must be YYYY-MM")
    y,m=map(int,period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    expected_per_share=float((tenant or {}).get("kist_per_share",500))
    members=await db.members.find({"tenant_id":tenant_id,"active":True}).sort("first_name",1).to_list(5000)
    # Dashboard status cards are intentionally group-wide for every role so
    # member and admin dashboards share the same totals. Personal amounts remain
    # separately available through the member passbook / My Share view.
    member_ids=[str(m["_id"]) for m in members]
    shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort("share_no",1).to_list(20000) if member_ids else []
    shares_by_member={mid:[] for mid in member_ids}
    for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
    for member in members:
        mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
        if len(shares_by_member.get(mid,[]))<expected_count:
            shares_by_member[mid]=await ensure_member_shares(member)
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}},
        {"$group":{"_id":{"share_id":"$share_id","member_id":"$member_id","share_no":"$share_no"},"paid":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_legacy={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid=float(x.get("paid",0) or 0); sid=key.get("share_id")
        if sid: paid_by_share[str(sid)]=paid
        elif key.get("member_id") is not None and key.get("share_no") is not None: paid_by_legacy[(str(key.get("member_id")),int(key.get("share_no")))]=paid
    exp_row=await db.expenses.aggregate([
        {"$match":{"tenant_id":tenant_id,"date":{"$gte":start,"$lt":end}}},
        {"$group":{"_id":None,"total":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}
    ]).to_list(1)
    group_expenses=round(float((exp_row[0] if exp_row else {}).get("total",0) or 0),2)
    member_expenses=None
    if user["role"]=="member":
        allocation_row=await db.transactions.aggregate([
            {"$match":{"tenant_id":tenant_id,"member_id":str(user.get("member_id") or ""),"type":"expense_allocation","date":{"$gte":start,"$lt":end}}},
            {"$group":{"_id":None,"total":{"$sum":{"$abs":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}}
        ]).to_list(1)
        member_expenses=round(float((allocation_row[0] if allocation_row else {}).get("total",0) or 0),2)
    expected=paid=paid_shares=partial_shares=pending_shares=0.0
    for member in members:
        for share in shares_by_member.get(str(member["_id"]),[]):
            sid=str(share["_id"]); p=round(float(paid_by_share.get(sid,0))+float(paid_by_legacy.get((str(member["_id"]),int(share["share_no"])),0)),2)
            expected+=expected_per_share; paid+=min(p,expected_per_share); remaining=max(0,round(expected_per_share-p,2))
            if remaining<=0.009: paid_shares+=1
            elif p>0: partial_shares+=1
            else: pending_shares+=1
    result={"period":period,"expected_total":round(expected,2),"paid_total":round(paid,2),"pending_total":round(max(0,expected-paid),2),"paid_shares":int(paid_shares),"partial_shares":int(partial_shares),"pending_shares":int(pending_shares),"active_members":len(members)}
    if user["role"]=="member": result["member_expenses"]=member_expenses
    else: result["group_expenses"]=group_expenses
    return result

@router.get("/{tenant_id}/monthly-kist/{member_id}")
async def monthly_kist_status(tenant_id:str,member_id:str,period:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); member=await get_member(db,tenant_id,member_id)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    import re
    if not re.fullmatch(r"\d{4}-\d{2}",period): raise HTTPException(400,"Period must be YYYY-MM")
    y,m=map(int,period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
    shares=await ensure_member_shares(member); expected=float(tenant.get("kist_per_share",500))
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"member_id":member_id,"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}},
        {"$group":{"_id":{"share_id":"$share_id","share_no":"$share_no"},"paid":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_no={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid=float(x.get("paid",0) or 0)
        if key.get("share_id"): paid_by_share[str(key["share_id"])]=paid
        elif key.get("share_no") is not None: paid_by_no[int(key["share_no"])]=paid
    out=[]
    for share in shares:
        paid=round(float(paid_by_share.get(str(share["_id"]),0))+float(paid_by_no.get(int(share["share_no"]),0) if str(share["_id"]) not in paid_by_share else 0),2)
        remaining=round(max(0,expected-paid),2)
        out.append({"share_id":str(share["_id"]),"share_no":int(share["share_no"]),"expected_amount":expected,"paid_amount":paid,"remaining_amount":remaining,"status":"paid" if remaining<=0.009 else ("partial" if paid>0 else "pending")})
    return {"member_id":member_id,"period":period,"kist_per_share":expected,"shares":out,"expected_total":round(expected*len(shares),2),"paid_total":round(sum(x["paid_amount"] for x in out),2),"remaining_total":round(sum(x["remaining_amount"] for x in out),2)}

@router.get("/{tenant_id}/monthly-kist-bulk-status")
async def monthly_kist_bulk_status(tenant_id: str, period: str, user=Depends(admin_user)):
    await tenant_guard(user, tenant_id); db=get_db()
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    try: y,m=map(int,period.split("-"))
    except Exception: raise HTTPException(400,"Invalid period")
    start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
    expected=float(tenant.get("kist_per_share",500))
    members=await db.members.find({"tenant_id":tenant_id,"active":True}).sort("first_name",1).to_list(5000)
    member_ids=[str(m["_id"]) for m in members]
    shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort("share_no",1).to_list(20000) if member_ids else []
    shares_by_member={mid:[] for mid in member_ids}
    for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
    for member in members:
        mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
        if len(shares_by_member.get(mid,[]))<expected_count: shares_by_member[mid]=await ensure_member_shares(member)
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}},
        {"$group":{"_id":{"member_id":"$member_id","share_id":"$share_id","share_no":"$share_no"},"paid":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_legacy={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid=float(x.get("paid",0) or 0); sid=key.get("share_id")
        if sid: paid_by_share[str(sid)]=paid
        elif key.get("member_id") is not None and key.get("share_no") is not None: paid_by_legacy[(str(key["member_id"]),int(key["share_no"]))]=paid
    out=[]
    for member in members:
        rows=[]
        for sh in shares_by_member.get(str(member["_id"]),[]):
            paid=round(float(paid_by_share.get(str(sh["_id"]),0))+float(paid_by_legacy.get((str(member["_id"]),int(sh["share_no"])),0)),2)
            remaining=round(max(0,expected-paid),2)
            rows.append({"share_id":str(sh["_id"]),"share_no":int(sh["share_no"]),"expected_amount":expected,"paid_amount":paid,"remaining_amount":remaining,"status":"paid" if remaining<=0.009 else ("partial" if paid>0 else "pending")})
        expected_total=round(expected*len(rows),2)
        paid_total=round(sum(x["paid_amount"] for x in rows),2)
        remaining_total=round(sum(x["remaining_amount"] for x in rows),2)
        member_status="paid" if rows and all(x["status"]=="paid" for x in rows) else "pending" if rows and all(x["status"]=="pending" for x in rows) else "partial"
        out.append({"member_id":str(member["_id"]),"name":f'{member.get("first_name","")} {member.get("last_name","")}'.strip(),"phone":member.get("phone",""),"shares":rows,"shares_count":len(rows),"expected_total":expected_total,"paid_total":paid_total,"remaining_total":remaining_total,"status":member_status})
    collected=[x for x in out if x["status"]=="paid"]
    pending=[x for x in out if x["status"]=="pending"]
    partial=[x for x in out if x["status"]=="partial"]
    return {"period":period,"kist_per_share":expected,"members":out,"collected":collected,"pending":pending,"partial":partial,"total_collected":round(sum(x["paid_total"] for x in collected),2),"total_pending":round(sum(x["remaining_total"] for x in pending),2),"total_partial":round(sum(x["paid_total"] for x in partial),2)}

@router.post("/{tenant_id}/monthly-kist-bulk")
async def monthly_kist_bulk(tenant_id: str, body: BulkMonthlyKistCreate, user=Depends(admin_user)):
    await tenant_guard(user, tenant_id); db=get_db()
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    expected=float(tenant.get("kist_per_share",500)); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    total=0.0; results=[]; seen=set(); paid_members=set()
    for entry in body.entries:
        if not entry.allocations: continue
        member=await get_member(db,tenant_id,entry.member_id); shares=await ensure_member_shares(member); share_map={str(x["_id"]):x for x in shares}
        for allocation in entry.allocations:
            if allocation.share_id in seen: raise HTTPException(400,"Duplicate share selected in bulk collection")
            seen.add(allocation.share_id)
            share=share_map.get(allocation.share_id)
            if not share: raise HTTPException(400,"One or more selected shares are not active for this member")
            y,m=map(int,body.period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
            paid_rows=await db.transactions.find({"tenant_id":tenant_id,"member_id":entry.member_id,"type":"contribution","$and":[{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}]}],"date":{"$gte":start,"$lt":end}}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(500)
            already=round(sum(float(x.get("amount",0)) for x in paid_rows),2); remaining=round(expected-already,2)
            if remaining<=0.009: continue
            # The status screen can be stale if another admin posts concurrently.
            # Never over-collect: cap the requested amount to the live remaining
            # balance instead of aborting the entire Save All operation mid-batch.
            post_amount=round(min(float(allocation.amount),remaining),2)
            if post_amount<=0.009: continue
            after=round(already+post_amount,2); status="paid" if after>=expected-0.009 else "partial"
            txid=await insert_tx(tenant_id,entry.member_id,"contribution",post_amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note,idempotency_key=f"{body.idempotency_key}:{allocation.share_id}" if body.idempotency_key else None)
            total+=post_amount; paid_members.add(entry.member_id); results.append({"id":txid,"member_id":entry.member_id,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"amount":post_amount,"status":status})
    penalties=[]
    for mid in paid_members:
        pid=await _post_bc_penalty_once(tenant_id,mid,body.period,dt,body.account,user,body.note)
        if pid: penalties.append(pid)
    if results:
        await audit(tenant_id,user,"MONTHLY_KIST_BULK_POSTED","tenant",tenant_id,{"period":body.period,"entries":len(results),"amount":round(total,2)})
    return {"period":body.period,"entries":len(results),"total":round(total,2),"results":results,"penalty_entries":len(penalties)}

@router.post("/{tenant_id}/monthly-kist")
async def monthly_kist(tenant_id:str,body:MonthlyKistCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    member=await get_member(db,tenant_id,body.member_id)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    shares=await ensure_member_shares(member); share_map={str(x["_id"]):x for x in shares}
    if len({a.share_id for a in body.allocations})!=len(body.allocations): raise HTTPException(400,"Duplicate share selected")
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    results=[]
    for allocation in body.allocations:
        share=share_map.get(allocation.share_id)
        if not share: raise HTTPException(400,"One or more selected shares are not active for this member")
        expected=float(tenant.get("kist_per_share",500))
        y,m=map(int,body.period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
        paid_cursor=await db.transactions.find({"tenant_id":tenant_id,"member_id":body.member_id,"type":"contribution","$and":[{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}]}],"date":{"$gte":start,"$lt":end}}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(500)
        already_paid=round(sum(float(x.get("amount",0)) for x in paid_cursor),2)
        remaining=round(expected-already_paid,2)
        if remaining<=0.009: raise HTTPException(409,f"Share {share['share_no']} is already fully paid for {body.period}")
        if allocation.amount>remaining+0.009: raise HTTPException(400,f"Share {share['share_no']} can accept at most {remaining:.2f} for {body.period}")
        after=round(already_paid+allocation.amount,2)
        status="paid" if after>=expected-0.009 else "partial"
        txid=await insert_tx(tenant_id,body.member_id,"contribution",allocation.amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note,idempotency_key=f"{body.idempotency_key}:{allocation.share_id}" if body.idempotency_key else None)
        results.append({"id":txid,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"paid_amount":allocation.amount,"cumulative_paid":after,"expected_amount":expected,"status":status})
    penalty_id=await _post_bc_penalty_once(tenant_id,body.member_id,body.period,dt,body.account,user,body.note)
    penalty_amount=0.0
    if penalty_id:
        prow=await db.transactions.find_one({"_id":parse_oid(penalty_id)},{"amount":1})
        penalty_amount=float((prow or {}).get("amount",0) or 0)
    await audit(tenant_id,user,"MONTHLY_KIST_BATCH_POSTED","member",body.member_id,{"period":body.period,"shares":len(results),"amount":round(sum(x["paid_amount"] for x in results),2),"bc_regular_kist_penalty_posted":bool(penalty_id)})
    return {"period":body.period,"member_id":body.member_id,"results":results,"total":round(sum(x["paid_amount"] for x in results),2),"bc_regular_kist_penalty":round(penalty_amount,2)}

@router.post("/{tenant_id}/money-in")
async def money_in(tenant_id:str,body:MoneyInCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    if body.member_id: await get_member(get_db(),tenant_id,body.member_id)
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    if not body.note.strip(): raise HTTPException(400,"Note / Reason is required for manual interest or penalty entries")
    is_penalty=body.type=="penalty"
    payment_category="other" if is_penalty else "other_interest"
    return {"id":await insert_tx(tenant_id,body.member_id,body.type,body.amount,body.account,user,date=dt,note=body.note,payment_category=payment_category,penalty_category=body.penalty_category if is_penalty else None,other_interest=body.amount if body.type=="interest" else 0,other_penalty=body.amount if is_penalty else 0,idempotency_key=body.idempotency_key)}

@router.get("/{tenant_id}/settings")
async def get_group_settings(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    t=await group_settings(tenant_id)
    keys=("kist_per_share","bc_due_date","bc_per_day_penalty","loan_interest_rate_per_month","loan_per_day_penalty","loan_due_date","required_admin_approvals","max_loan_multiplier","min_group_reserve_balance","min_loan_amount","min_advance_apply_months")
    return {k:t.get(k) for k in keys}

@router.patch("/{tenant_id}/settings")
async def update_group_settings(tenant_id:str,body:TenantSettingsUpdate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    values=body.model_dump(exclude_none=True)
    allowed={"kist_per_share","bc_due_date","bc_per_day_penalty","loan_interest_rate_per_month","loan_per_day_penalty","loan_due_date","required_admin_approvals","max_loan_multiplier","min_group_reserve_balance","min_loan_amount","min_advance_apply_months"}
    values={k:v for k,v in values.items() if k in allowed}
    if not values: raise HTTPException(400,"No settings supplied")
    await get_db().tenants.update_one({"_id":parse_oid(tenant_id)},{"$set":values})
    await audit(tenant_id,user,"GROUP_SETTINGS_UPDATED","tenant",tenant_id,values)
    return {"ok":True,**values}

@router.get("/{tenant_id}/loan-eligibility/{member_id}")
async def loan_eligibility(tenant_id:str,member_id:str,amount:float|None=None,months:int=1,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db(); member=await get_member(db,tenant_id,member_id); tenant=await group_settings(tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Member access denied")
    shares=int(member.get("active_shares_count",member.get("shares",1)) or 1); share_value=float(tenant.get("kist_per_share",500) or 500); multiplier=float(tenant.get("max_loan_multiplier",20) or 20); minimum=float(tenant.get("min_loan_amount",10000) or 10000)
    credit_limit=round(max(minimum,shares*share_value*multiplier),2)
    summary=await tenant_summary(tenant_id,None); reserve=float(tenant.get("min_group_reserve_balance",0) or 0); available=float(summary.get("active_account_balance",0) or 0); max_by_funds=max(0,available-reserve)
    requested=float(amount or 0); rate=float(tenant.get("loan_interest_rate_per_month",2) or 0); total_interest=round((requested*rate/100*max(1,int(months or 1))),2) if requested else 0
    liquidity_ok=available>=reserve-0.01
    min_advance_apply_months=max(0,int(tenant.get("min_advance_apply_months",2) or 2))
    return {"member_id":member_id,"shares":shares,"share_value":share_value,"max_loan_multiplier":multiplier,"min_loan_amount":minimum,"min_advance_apply_months":min_advance_apply_months,"credit_limit":credit_limit,"active_account_balance":available,"min_group_reserve_balance":reserve,"funds_available_for_disbursement":round(max_by_funds,2),"liquidity_ok":liquidity_ok,"eligible":(liquidity_ok and requested<=credit_limit and requested>=minimum and requested<=max_by_funds) if requested else liquidity_ok,"interest_rate_per_month":rate,"total_interest":total_interest,"total_due":round(requested+total_interest,2),"estimated_emi":round((requested+total_interest)/max(1,int(months or 1)),2) if requested else 0}

@router.get("/{tenant_id}/activity")
async def group_activity(tenant_id:str,page:int=1,page_size:int=10,user=Depends(current_user)):
    """Dashboard activity feed: exactly one MongoDB page of the materialized feed."""
    await tenant_guard(user,tenant_id); db=get_db(); page=max(1,page); page_size=max(1,min(page_size,50)); q={"tenant_id":tenant_id}
    if user.get("role")=="member": q["member_id"]=str(user.get("member_id") or "")
    total=await db.financial_feed.count_documents(q)
    if total==0:
        # Safe compatibility fallback while the asynchronous legacy read-model backfill finishes.
        tx_q={"tenant_id":tenant_id,"type":{"$ne":"expense_allocation"}}; ex_q={"tenant_id":tenant_id}
        if user.get("role")=="member": tx_q["member_id"]=str(user.get("member_id") or ""); ex_q={"tenant_id":tenant_id,"_id":{"$exists":False}}
        tx=await db.transactions.find(tx_q).sort([("date",-1),("created_at",-1),("_id",-1)]).limit(page*page_size).to_list(page*page_size)
        ex=await db.expenses.find(ex_q).sort([("date",-1),("created_at",-1),("_id",-1)]).limit(page*page_size).to_list(page*page_size)
        rows=[]
        for x in tx:
            y=dict(x); y["source_type"]="transaction"; y["source_id"]=str(x["_id"]); rows.append(y)
        for x in ex:
            y=dict(x); y["source_type"]="expense"; y["source_id"]=str(x["_id"]); y["type"]="expense"; y["amount"]=-abs(float(x.get("amount",0) or 0)); rows.append(y)
        rows.sort(key=lambda x:(x.get("date") or x.get("created_at") or "",x.get("created_at") or "",str(x.get("_id"))),reverse=True)
        total=len(rows); rows=rows[(page-1)*page_size:page*page_size]
    else:
        rows=await db.financial_feed.find(q).sort([("date",-1),("created_at",-1),("_id",-1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    items=[]; mids=[]
    for x in rows:
        if x.get("member_id"):
            try: mids.append(parse_oid(str(x["member_id"])))
            except Exception: pass
    docs=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":mids}}).to_list(len(mids) or 1) if mids else []
    names={str(m["_id"]):f'{m.get("first_name","")} {m.get("last_name","")}'.strip() for m in docs}
    for x in rows:
        amount=float(x.get("amount",0) or 0)
        items.append({"kind":x.get("source_type","transaction"),"id":str(x.get("source_id") or x.get("_id")),"type":str(x.get("type","Transaction")).replace("_"," ").title(),"amount":amount,"account":x.get("account","cash"),"date":str(x.get("date") or x.get("created_at")),"created_at":str(x.get("created_at") or x.get("date")),"note":x.get("note","") or x.get("category","") or "","member_name":names.get(str(x.get("member_id")),""),"member_id":x.get("member_id"),"payment_category":x.get("payment_category")})
    return {"items":items,"page":page,"page_size":page_size,"has_more":page*page_size<total,"total":total}

@router.get("/{tenant_id}/accounting/{view}")
async def accounting_view(tenant_id:str, view:str, period:str|None=None, account:str|None=None, filter:str|None=None, page:int=1, page_size:int=10, user=Depends(current_user)):
    """Accounting drill-down read model.

    This endpoint deliberately separates earned income/profit, operating
    expenses, asset/loan outflows and liquid closing balance. It never treats
    expenses or loan principal payouts as profit, and it never subtracts them
    from the cumulative BC Fund metric.
    """
    await tenant_guard(user, tenant_id)
    allowed={"profit","expenses","outflows","closing","interest","active-loans","kist","register"}
    if user.get("role") not in ("member", "group_admin", "super_admin"):
        raise HTTPException(403,"Accounting access denied")
    if user.get("role")=="member" and view not in allowed:
        raise HTTPException(403,"Accounting access denied")
    if view not in allowed: raise HTTPException(404,"Accounting view not found")
    db=get_db()
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    # Never let a newly deployed/read-model-backed ledger race its background
    # backfill. If source documents exist without matching feed rows, repair
    # the read model before calculating any counts, balances or drill-down rows.
    await ensure_financial_feed_ready(tenant_id)
    page=max(1,page); page_size=max(1,min(page_size,50)); offset=(page-1)*page_size

    async def _attach_running_balances(rows, base_query, closing_balance):
        if not rows: return []
        first=rows[0]; fd=first.get("date"); fc=first.get("created_at"); fid=first.get("_id")
        newer_net=0.0
        newer_or=[]
        if fd is not None: newer_or.append({"date":{"$gt":fd}})
        if fd is not None and fc is not None: newer_or.append({"date":fd,"created_at":{"$gt":fc}})
        if fd is not None and fc is not None: newer_or.append({"date":fd,"created_at":fc,"_id":{"$gt":fid}})
        if newer_or:
            newer=await db.financial_feed.aggregate([{"$match":{**base_query,"$or":newer_or}},{"$group":{"_id":None,"net":{"$sum":{"$ifNull":["$amount",0]}}}}]).to_list(1)
            newer_net=float((newer[0] if newer else {}).get("net",0) or 0)
        balance=round(float(closing_balance)-newer_net,2); out=[]
        for row in rows:
            x=dict(row); x["running_balance"]=round(balance,2); balance=round(balance-float(row.get("amount",0) or 0),2); out.append(x)
        return out

    # Final mobile feed path: MongoDB paginates the materialized financial read model.
    # This prevents loading/slicing thousands of rows in Python while keeping the
    # existing response contract intact for the frontend.
    if view in {"register","profit","expenses","outflows","interest","closing"}:
        feed={"tenant_id":tenant_id}
        if view in {"register","closing"}:
            base_feed={"tenant_id":tenant_id,"$or":[{"source_type":"expense"},{"source_type":"transaction","type":{"$nin":["expense_allocation","expense"]},"expense_id":None}]}
            if account: base_feed["account"]=account
            direction=(filter or period or "all").lower() if view=="register" else "closing"
            # Summary cards must always use the complete bank book. The selected
            # inflow/outflow filter controls only the rows shown below them.
            summary_agg=await db.financial_feed.aggregate([{"$match":base_feed},{"$group":{"_id":None,"credits":{"$sum":{"$cond":[{"$gt":["$amount",0]},"$amount",0]}},"debits":{"$sum":{"$cond":[{"$lt":["$amount",0]},{"$abs":"$amount"},0]}},"count":{"$sum":1}}}]).to_list(1)
            a=summary_agg[0] if summary_agg else {}
            credits=round(float(a.get("credits",0) or 0),2); debits=round(float(a.get("debits",0) or 0),2)
            opening_cash=float(tenant.get("opening_cash",0) or 0); opening_bank=float(tenant.get("opening_bank",0) or 0)
            opening=(opening_cash+opening_bank) if not account else (opening_cash if account=="cash" else opening_bank)
            display_feed=dict(base_feed)
            opening_mode = view=="register" and direction=="opening"
            if view=="register":
                if direction=="inflows": display_feed["amount"]={"$gt":0}
                elif direction=="outflows": display_feed["amount"]={"$lt":0}
            q=display_feed
            # Opening balance is a distinct, explicit starting entry; it must not
            # be represented by an impossible query (_id does not exist).
            rows=[] if opening_mode else await db.financial_feed.find(q).sort([("date",-1),("created_at",-1),("_id",-1)]).skip(offset).limit(page_size).to_list(page_size)
            if view=="closing" or direction=="closing":
                rows=await _attach_running_balances(rows,q,opening+credits-debits)
            member_ids=[]
            for r in rows:
                if r.get("member_id"):
                    try: member_ids.append(parse_oid(str(r["member_id"])))
                    except Exception: pass
            member_docs=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":member_ids}}).to_list(len(member_ids) or 1) if member_ids else []
            names={str(m["_id"]):f'{m.get("first_name","")} {m.get("last_name","")}'.strip() for m in member_docs}
            entries=[]
            for r in rows:
                amount=float(r.get("amount",0) or 0); name=names.get(str(r.get("member_id")),"")
                entries.append({"id":str(r.get("source_id") or r.get("_id")),"date":str(r.get("date") or r.get("created_at")),"member_id":r.get("member_id"),"member_name":name,"type":r.get("type","transaction"),"amount":round(abs(amount),2),"account":r.get("account","cash"),"note":r.get("note","") or r.get("category","") or "","direction":"Credit" if amount>=0 else "Debit","entry_type":"Credit" if amount>=0 else "Debit","payment_category":r.get("payment_category"),"reason":r.get("note","") or r.get("category","") or r.get("type","Transaction"),"running_balance":r.get("running_balance")})
            total=1 if opening_mode else await db.financial_feed.count_documents(q)
            if opening_mode:
                entries=[{"id":f"opening-{tenant_id}-{account or 'group'}","date":str(tenant.get("created_at") or ""),"member_id":None,"member_name":"","type":"Opening Balance","amount":abs(round(opening,2)),"account":account or "cash","note":"Opening balance","direction":"Credit" if opening>=0 else "Debit","entry_type":"Credit" if opening>=0 else "Debit","payment_category":None,"reason":"Starting group balance","running_balance":round(opening,2)}]
            display_credits=round(sum(float(r.get("amount",0) or 0) for r in rows if float(r.get("amount",0) or 0)>0),2)
            display_debits=round(sum(abs(float(r.get("amount",0) or 0)) for r in rows if float(r.get("amount",0) or 0)<0),2)
            return {"view":view,"filter":direction,"account":account,"account_label":("Bank Deposit" if account=="bank" else "Cash Deposit" if account=="cash" else "Group Active Account Balance"),"entries":entries,"has_more":False if opening_mode else offset+len(entries)<total,"total_entries":total,"total_credits":credits,"total_debits":debits,"grand_total":round(opening,2) if direction=="opening" else round(opening+credits-debits,2) if direction=="closing" else display_credits if direction=="inflows" else display_debits if direction=="outflows" else round(credits-debits,2),"opening_balance":round(opening,2),"closing_balance":round(opening+credits-debits,2),"opening_cash":opening_cash,"opening_bank":opening_bank}

        if view=="expenses":
            feed={"tenant_id":tenant_id,"source_type":"expense"}
            if account: feed["account"]=account
            agg=await db.financial_feed.aggregate([{"$match":feed},{"$group":{"_id":None,"total":{"$sum":{"$abs":"$amount"}},"count":{"$sum":1}}}]).to_list(1); a=agg[0] if agg else {}
            rows=await db.financial_feed.find(feed).sort([("date",-1),("created_at",-1),("_id",-1)]).skip(offset).limit(page_size).to_list(page_size)
            entries=[{"id":str(r.get("source_id") or r.get("_id")),"date":str(r.get("date") or r.get("created_at")),"category":r.get("category",r.get("note","Expense")),"reason":r.get("note","") or r.get("category","Expense"),"account":r.get("account","cash"),"amount":abs(float(r.get("amount",0) or 0))} for r in rows]
            total=int(a.get("count",0) or 0); return {"view":view,"entries":entries,"has_more":offset+len(entries)<total,"total_entries":total,"grand_total":round(float(a.get("total",0) or 0),2)}

        if view=="outflows":
            feed={"tenant_id":tenant_id,"source_type":"transaction","type":{"$in":["loan_disbursement","asset_purchase","asset_outflow","investment_outflow"]}}
            if account: feed["account"]=account
            agg=await db.financial_feed.aggregate([{"$match":feed},{"$group":{"_id":None,"total":{"$sum":{"$abs":"$amount"}},"count":{"$sum":1}}}]).to_list(1); a=agg[0] if agg else {}
            rows=await db.financial_feed.find(feed).sort([("date",-1),("created_at",-1),("_id",-1)]).skip(offset).limit(page_size).to_list(page_size)
            entries=[{"id":str(r.get("source_id") or r.get("_id")),"date":str(r.get("date") or r.get("created_at")),"member_id":r.get("member_id"),"member_name":"","type":r.get("type",""),"amount":abs(float(r.get("amount",0) or 0)),"account":r.get("account","cash"),"note":r.get("note",""),"outflow_type":"Loan Disbursement" if r.get("type")=="loan_disbursement" else "Asset / Investment"} for r in rows]
            total=int(a.get("count",0) or 0); return {"view":view,"account":account,"entries":entries,"has_more":offset+len(entries)<total,"total_entries":total,"grand_total":round(float(a.get("total",0) or 0),2)}

        # Profit / interest are derived from the transaction read model, with
        # totals aggregated in MongoDB and only the visible 10 rows materialized.
        if view=="profit":
            feed={"tenant_id":tenant_id,"source_type":"transaction","$or":[{"loan_interest_collected":{"$gt":0}},{"loan_penalty_collected":{"$gt":0}},{"bc_regular_kist_penalty":{"$gt":0}},{"type":"penalty"},{"type":"interest"}]}
            rows=await db.financial_feed.find(feed).sort([("date",-1),("created_at",-1),("_id",-1)]).skip(offset).limit(page_size).to_list(page_size)
            profit_expr={"$add":[{"$ifNull":["$loan_interest_collected",0]},{"$ifNull":["$other_interest_value",0]},{"$ifNull":["$loan_penalty_collected",0]},{"$ifNull":["$bc_regular_kist_penalty",0]},{"$cond":[{"$and":[{"$eq":["$type","interest"]},{"$eq":[{"$ifNull":["$other_interest_value",0]},0]}]},{"$abs":{"$ifNull":["$amount",0]}},0]}]}
            # Legacy penalty rows may not have one of the explicit fields.
            legacy_penalty={"$cond":[{"$and":[{"$eq":["$type","penalty"]},{"$eq":[{"$ifNull":["$loan_penalty_collected",0]},0]},{"$eq":[{"$ifNull":["$bc_regular_kist_penalty",0]},0]}]}, {"$abs":{"$ifNull":["$amount",0]}}, 0]}
            profit_expr={"$add":[profit_expr,legacy_penalty]}
            totals=await db.financial_feed.aggregate([{"$match":feed},{"$group":{"_id":None,"grand":{"$sum":profit_expr},"loan_interest":{"$sum":{"$ifNull":["$loan_interest_collected",0]}},"other_interest":{"$sum":{"$add":[{"$ifNull":["$other_interest_value",0]},{"$cond":[{"$and":[{"$eq":["$type","interest"]},{"$eq":[{"$ifNull":["$other_interest_value",0]},0]}]},{"$abs":{"$ifNull":["$amount",0]}},0]}]}},"loan_penalties":{"$sum":{"$ifNull":["$loan_penalty_collected",0]}},"bc_penalties":{"$sum":{"$add":[{"$ifNull":["$bc_regular_kist_penalty",0]},legacy_penalty]}},"count":{"$sum":1}}}]).to_list(1)
            a=totals[0] if totals else {}
            entries=[]
            for r in rows:
                li=float(r.get("loan_interest_collected",0) or 0); oi=float(r.get("other_interest_value",0) or 0); lp=float(r.get("loan_penalty_collected",0) or 0); bp=float(r.get("bc_regular_kist_penalty",0) or 0)
                if r.get("type")=="interest" and not oi: oi=abs(float(r.get("amount",0) or 0))
                if r.get("type")=="penalty" and not (li or lp or bp): bp=abs(float(r.get("amount",0) or 0))
                amount=li+oi+lp+bp
                entries.append({"id":str(r.get("source_id") or r.get("_id")),"date":str(r.get("date") or r.get("created_at")),"member_name":"","type":r.get("type","profit"),"amount":round(amount,2),"account":r.get("account","cash"),"source":"Loan Interest" if li else "Loan Penalty" if lp else "BC Penalty","reason":r.get("note","")})
            total=int(a.get("count",0) or 0); src={"loan_interest":float(a.get("loan_interest",0) or 0),"other_interest":float(a.get("other_interest",0) or 0),"bc_penalties":float(a.get("bc_penalties",0) or 0),"loan_penalties":float(a.get("loan_penalties",0) or 0)}
            expense_agg=await db.financial_feed.aggregate([{"$match":{"tenant_id":tenant_id,"source_type":"expense"}},{"$group":{"_id":None,"total":{"$sum":{"$abs":{"$ifNull":["$amount",0]}}}}}]).to_list(1)
            total_expenses=float(expense_agg[0].get("total",0) or 0) if expense_agg else 0.0
            earned=float(a.get("grand",0) or 0)
            return {"view":view,"entries":entries,"has_more":offset+len(entries)<total,"total_entries":total,"grand_total":round(earned-total_expenses,2),"total_interest_and_penalties":round(earned,2),"total_expenses":round(total_expenses,2),"sources":{k:round(v,2) for k,v in src.items()}}

        if view=="interest":
            feed={"tenant_id":tenant_id,"source_type":"transaction","$or":[{"type":"interest","amount":{"$gt":0}},{"type":"loan_repayment","loan_interest_collected":{"$gt":0}}]}
            rows=await db.financial_feed.find(feed).sort([("date",-1),("created_at",-1),("_id",-1)]).skip(offset).limit(page_size).to_list(page_size)
            amount_expr={"$cond":[{"$eq":["$type","loan_repayment"]},{"$ifNull":["$loan_interest_collected",0]},{"$ifNull":["$amount",0]}]}
            totals=await db.financial_feed.aggregate([{"$match":feed},{"$group":{"_id":None,"total":{"$sum":amount_expr},"count":{"$sum":1}}}]).to_list(1); a=totals[0] if totals else {}
            entries=[]
            for r in rows:
                amount=float(r.get("loan_interest_collected",0) or 0) if r.get("type")=="loan_repayment" else float(r.get("amount",0) or 0)
                entries.append({"id":str(r.get("source_id") or r.get("_id")),"date":str(r.get("date") or r.get("created_at")),"member_name":"","type":r.get("type","interest"),"amount":round(amount,2),"account":r.get("account","cash"),"source":"Member Loan Interest" if r.get("type")=="loan_repayment" else "Bank / Other Interest"})
            total=int(a.get("count",0) or 0)
            return {"view":view,"entries":entries,"has_more":offset+len(entries)<total,"total_entries":total,"grand_total":round(float(a.get("total",0) or 0),2)}

    if view=="active-loans":
        rows=await db.loans.find({"tenant_id":tenant_id,"status":"active"}).sort("created_at",-1).to_list(5000)
        mids=[parse_oid(str(x.get("member_id"))) for x in rows if parse_oid(str(x.get("member_id")))]
        members={str(m["_id"]):m for m in await db.members.find({"tenant_id":tenant_id,"_id":{"$in":mids}}).to_list(len(mids) or 1)}
        out=[]
        for row in rows:
            x=serialize(row); m=members.get(str(row.get("member_id")))
            principal=float(row.get("principal",0) or 0); principal_paid=float(row.get("principal_paid",0) or 0)
            expected_interest=float(row.get("interest_accrued",row.get("expected_interest",0)) or 0); interest_paid=float(row.get("interest_paid",0) or 0)
            x.update({
                "member_name":f'{m.get("first_name","")} {m.get("last_name","")}'.strip() if m else "Member",
                "principal_remaining":round(max(0,principal-principal_paid),2),
                "interest_remaining":round(max(0,expected_interest-interest_paid),2),
                "total_outstanding":round(max(0,principal-principal_paid)+max(0,expected_interest-interest_paid),2),
                "principal_progress":round(min(100,principal_paid/max(1,principal)*100),1),
                "interest_progress":round(min(100,interest_paid/max(1,expected_interest)*100),1) if expected_interest else 100,
            })
            out.append(x)
        return {"view":view,"loans":out,"total_principal":round(sum(float(x.get("principal",0) or 0) for x in rows),2),"total_outstanding":round(sum(x["total_outstanding"] for x in out),2)}

    # Kist drill-down is intentionally member/share based and uses the exact
    # same current-month rules as the collection screen.
    if view=="kist":
        import re
        p=period or datetime.now(timezone.utc).strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-\d{2}",p): raise HTTPException(400,"Period must be YYYY-MM")
        y,m=map(int,p.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
        expected=float(tenant.get("kist_per_share",500) or 500)
        member_query={"tenant_id":tenant_id,"active":True}
        members=await db.members.find(member_query).sort([("first_name",1),("last_name",1),("_id",1)]).to_list(5000)
        member_ids=[str(x["_id"]) for x in members]
        shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort([("member_id",1),("share_no",1),("_id",1)]).to_list(20000) if member_ids else []
        shares_by_member={mid:[] for mid in member_ids}
        for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
        for member in members:
            mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
            if len(shares_by_member.get(mid,[]))<expected_count: shares_by_member[mid]=await ensure_member_shares(member)
        tx=await db.transactions.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(20000) if member_ids else []
        paid={}
        for x in tx:
            key=(str(x.get("member_id")),str(x.get("share_id") or f'legacy:{x.get("share_no")}'))
            paid[key]=paid.get(key,0)+float(x.get("amount",0) or 0)
        rows=[]
        for mbr in members:
            mid=str(mbr["_id"]); mshares=shares_by_member.get(mid,[]); share_rows=[]
            for sh in mshares:
                key=(mid,str(sh["_id"])); amount=round(paid.get(key,0),2); remaining=round(max(0,expected-amount),2)
                status="paid" if remaining<=0.01 else "partial" if amount>0 else "pending"
                share_rows.append({"share_id":str(sh["_id"]),"share_no":int(sh.get("share_no",0)),"expected_amount":expected,"paid_amount":amount,"remaining_amount":remaining,"status":status})
            expected_total=round(expected*len(share_rows),2); paid_total=round(sum(x["paid_amount"] for x in share_rows),2); remaining_total=round(sum(x["remaining_amount"] for x in share_rows),2)
            member_status="paid" if share_rows and all(x["status"]=="paid" for x in share_rows) else "pending" if share_rows and all(x["status"]=="pending" for x in share_rows) else "partial"
            rows.append({"member_id":mid,"member_name":f'{mbr.get("first_name","")} {mbr.get("last_name","")}'.strip(),"phone":mbr.get("phone",""),"shares":share_rows,"shares_count":len(share_rows),"expected_total":expected_total,"paid_total":paid_total,"remaining_total":remaining_total,"status":member_status})
        status_filter=filter or "all"
        selected=rows if status_filter=="all" else [x for x in rows if x["status"]==status_filter]
        return {"view":view,"period":p,"kist_per_share":expected,"members":rows,"collected":[x for x in rows if x["status"]=="paid"],"pending":[x for x in rows if x["status"]=="pending"],"partial":[x for x in rows if x["status"]=="partial"],"rows":selected,"total_collected":round(sum(x["paid_total"] for x in rows if x["status"]=="paid"),2),"total_pending":round(sum(x["remaining_total"] for x in rows if x["status"]=="pending"),2),"total_partial":round(sum(x["paid_total"] for x in rows if x["status"]=="partial"),2)}

    # Common raw financial data for the remaining views.
    tx_rows=await db.transactions.find({"tenant_id":tenant_id}).sort("date",-1).to_list(10000)
    expense_rows=await db.expenses.find({"tenant_id":tenant_id}).sort("date",-1).to_list(10000)
    member_ids={str(x.get("member_id")) for x in tx_rows if x.get("member_id")}
    member_docs=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":[parse_oid(x) for x in member_ids if parse_oid(x)]}}).to_list(len(member_ids) or 1)
    names={str(x["_id"]):f'{x.get("first_name","")} {x.get("last_name","")}'.strip() for x in member_docs}

    def tx_real(x): return x.get("type") not in ("expense_allocation","expense") and not x.get("expense_id")
    def dt(x): return str(x.get("date",x.get("created_at","")))
    def base_tx(x): return {"id":str(x["_id"]),"date":dt(x),"member_id":str(x.get("member_id")) if x.get("member_id") else None,"member_name":names.get(str(x.get("member_id")),""),"type":x.get("type",""),"amount":round(float(x.get("amount",0) or 0),2),"account":x.get("account","cash"),"note":x.get("note","")}

    if view=="register":
        direction=(filter or period or "all").lower()
        rows=[]
        for x in tx_rows:
            if not tx_real(x): continue
            if account and x.get("account","cash")!=account: continue
            amount=float(x.get("amount",0) or 0)
            if direction=="inflows" and amount<=0: continue
            if direction=="outflows" and amount>=0: continue
            rows.append({**base_tx(x),"direction":"Credit" if amount>0 else "Debit","amount":round(abs(amount),2),"reason":x.get("note") or x.get("payment_category") or x.get("type","Transaction")})
        if direction in ("outflows","all"):
            for x in expense_rows:
                if account and x.get("account","cash")!=account: continue
                amount=float(x.get("amount",0) or 0)
                if direction=="inflows": continue
                rows.append({"id":str(x["_id"]),"date":dt(x),"member_id":None,"member_name":"","type":"expense","amount":round(abs(amount),2),"account":x.get("account","cash"),"note":x.get("note") or x.get("category") or "Expense","direction":"Debit","reason":x.get("note") or x.get("category") or "Expense"})
        rows.sort(key=lambda x:x.get("date", ""),reverse=True)
        credits=round(sum(x["amount"] for x in rows if x["direction"]=="Credit"),2); debits=round(sum(x["amount"] for x in rows if x["direction"]=="Debit"),2)
        opening_cash=float(tenant.get("opening_cash",0) or 0); opening_bank=float(tenant.get("opening_bank",0) or 0); opening=(opening_cash+opening_bank) if not account else (opening_cash if account=="cash" else opening_bank)
        closing=round(opening+credits-debits,2)
        return {"view":view,"filter":direction,"account":account,"entries":rows[offset:offset+page_size],"has_more":offset+page_size<len(rows),"total_entries":len(rows),"total_credits":credits,"total_debits":debits,"grand_total":credits if direction=="inflows" else debits,"opening_balance":round(opening,2),"closing_balance":closing,"opening_cash":opening_cash,"opening_bank":opening_bank}

    if view=="profit":
        entries=[]
        for x in tx_rows:
            if not tx_real(x): continue
            typ=x.get("type")
            loan_interest=float(x.get("loan_interest_collected",0) or 0)
            loan_penalty=float(x.get("loan_penalty_collected",0) or 0)
            bc_penalty=float(x.get("bc_regular_kist_penalty",0) or 0)
            if loan_interest>0:
                entries.append({**base_tx(x),"source":"Loan Interest","reason":x.get("note") or "Loan EMI interest","amount":round(loan_interest,2)})
            if loan_penalty>0:
                entries.append({**base_tx(x),"source":"Loan Penalty","reason":x.get("note") or "Loan EMI penalty","amount":round(loan_penalty,2)})
            if bc_penalty>0:
                entries.append({**base_tx(x),"source":"BC Penalty","reason":x.get("note") or "BC installment penalty","amount":round(bc_penalty,2)})
            if typ=="penalty" and not (loan_penalty or bc_penalty) and x.get("payment_category") in (None,"bc") and x.get("penalty_category","bc")!="loan":
                entries.append({**base_tx(x),"source":"BC Penalty","reason":x.get("note") or "BC installment penalty","amount":round(float(x.get("amount",0) or 0),2)})
        return {"view":view,"entries":entries[offset:offset+page_size],"has_more":offset+page_size<len(entries),"total_entries":len(entries),"grand_total":round(sum(x["amount"] for x in entries),2),"sources":{"loan_interest":round(sum(x["amount"] for x in entries if x["source"]=="Loan Interest"),2),"bc_penalties":round(sum(x["amount"] for x in entries if x["source"]=="BC Penalty"),2),"loan_penalties":round(sum(x["amount"] for x in entries if x["source"]=="Loan Penalty"),2)}}

    if view=="expenses":
        rows=[]
        for x in expense_rows:
            rows.append({"id":str(x["_id"]),"date":dt(x),"category":x.get("category","Expense"),"reason":x.get("note","") or x.get("category","Expense"),"account":x.get("account","cash"),"amount":round(float(x.get("amount",0) or 0),2),"proof_url":x.get("proof_url")})
        return {"view":view,"entries":rows[offset:offset+page_size],"has_more":offset+page_size<len(rows),"total_entries":len(rows),"grand_total":round(sum(x["amount"] for x in rows),2)}

    if view=="outflows":
        rows=[]
        for x in tx_rows:
            if not tx_real(x): continue
            typ=str(x.get("type","")).lower()
            if typ=="loan_disbursement" or typ.startswith("investment") or typ.startswith("asset_"):
                rows.append({**base_tx(x),"amount":abs(round(float(x.get("amount",0) or 0),2)),"outflow_type":"Loan Disbursement" if typ=="loan_disbursement" else "Asset / Investment"})
        if account: rows=[x for x in rows if x["account"]==account]
        return {"view":view,"account":account,"entries":rows[offset:offset+page_size],"has_more":offset+page_size<len(rows),"total_entries":len(rows),"grand_total":round(sum(x["amount"] for x in rows),2)}

    if view=="interest":
        rows=[]
        for x in tx_rows:
            if not tx_real(x): continue
            if x.get("type")=="interest" and float(x.get("amount",0) or 0)>0:
                rows.append({**base_tx(x),"source":"Bank / Other Interest","amount":round(float(x.get("amount",0) or 0),2)})
            elif x.get("type")=="loan_repayment" and float(x.get("loan_interest_collected",x.get("interest",0)) or 0)>0:
                rows.append({**base_tx(x),"source":"Member Loan Interest","amount":round(float(x.get("loan_interest_collected",x.get("interest",0)) or 0),2)})
        return {"view":view,"entries":rows[offset:offset+page_size],"has_more":offset+page_size<len(rows),"total_entries":len(rows),"grand_total":round(sum(x["amount"] for x in rows),2)}

    # Closing statement: credits and debits are asset movements only. Expenses
    # are debits, loan principal payouts are debits; neither is profit.
    opening_cash=float(tenant.get("opening_cash",0) or 0); opening_bank=float(tenant.get("opening_bank",0) or 0)
    entries=[]
    for x in tx_rows:
        if not tx_real(x): continue
        amount=float(x.get("amount",0) or 0)
        if account and x.get("account","cash")!=account: continue
        if amount>0: entries.append({**base_tx(x),"entry_type":"Credit","credit":round(amount,2),"debit":0})
        elif amount<0: entries.append({**base_tx(x),"entry_type":"Debit","credit":0,"debit":round(abs(amount),2)})
    for x in expense_rows:
        if account and x.get("account","cash")!=account: continue
        amount=float(x.get("amount",0) or 0)
        entries.append({"id":str(x["_id"]),"date":dt(x),"member_id":None,"member_name":"","type":"expense","amount":-round(amount,2),"account":x.get("account","cash"),"note":x.get("category","") or x.get("note","") or "Expense","entry_type":"Debit","credit":0,"debit":round(amount,2)})
    credits=round(sum(x["credit"] for x in entries),2); debits=round(sum(x["debit"] for x in entries),2)
    opening=(opening_cash+opening_bank) if not account else (opening_cash if account=="cash" else opening_bank)
    closing=round(opening+credits-debits,2)
    return {"view":view,"account":account,"opening_balance":round(opening,2),"total_credits":credits,"total_debits":debits,"closing_balance":closing,"opening_cash":round(opening_cash,2),"opening_bank":round(opening_bank,2),"entries":entries[offset:offset+page_size],"has_more":offset+page_size<len(entries),"total_entries":len(entries)}

@router.get("/{tenant_id}/admin-overview")
async def admin_overview(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    tasks=[members(tenant_id,user=user), loans(tenant_id,user=user), expenses(tenant_id,user=user), expense_categories(tenant_id,user=user), loan_requests(tenant_id,user=user)]
    member_rows, loan_rows, expense_rows, category_rows, request_rows = await asyncio.gather(*tasks)
    audit_rows=[]
    if user.get("role")=="super_admin":
        audit_rows=await audit_logs(tenant_id,user=user)
    return {"members":member_rows,"loans":loan_rows,"expenses":expense_rows,"categories":category_rows,"requests":request_rows,"audit":audit_rows}

@router.get("/{tenant_id}/register-overview")
async def register_overview(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    summary_row, tx_rows, member_rows = await asyncio.gather(tenant_summary(tenant_id, str(user.get("member_id")) if user.get("role") in ("member","group_admin") else None), transactions(tenant_id,user=user), members(tenant_id,user=user))
    return {"summary":summary_row,"transactions":tx_rows,"members":member_rows}

@router.get("/{tenant_id}/ledger-overview")
async def ledger_overview(tenant_id:str,user=Depends(admin_user)):
    """Member ledger read model.

    The UI no longer downloads thousands of transactions just to calculate one
    card per member. MongoDB calculates the member-level aggregates and the
    transaction feed remains independently paginated.
    """
    await tenant_guard(user,tenant_id); db=get_db()
    member_rows=await members(tenant_id,user=user)
    member_ids=[str(m["_id"]) for m in member_rows]
    contribution_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"member_id":{"$in":member_ids},"type":"contribution"}},
        {"$group":{"_id":"$member_id","savings":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}},"transactions":{"$sum":1}}}
    ]).to_list(None) if member_ids else []
    loan_rows=await db.loans.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids}}).sort("created_at",-1).to_list(5000) if member_ids else []
    loan_by_member={}
    for loan in loan_rows:
        if loan.get("status")!="active": continue
        mid=str(loan.get("member_id")); bucket=loan_by_member.setdefault(mid,{"principal":0.0,"interest":0.0,"loans":0})
        bucket["principal"]+=max(0,float(loan.get("principal",0) or 0)-float(loan.get("principal_paid",0) or 0))
        bucket["interest"]+=max(0,float(loan.get("interest_accrued",loan.get("expected_interest",0)) or 0)-float(loan.get("interest_paid",0) or 0))
        bucket["loans"]+=1
    contrib_by_member={str(x["_id"]):x for x in contribution_rows}
    now=datetime.now(timezone.utc); month_start=datetime(now.year,now.month,1,tzinfo=timezone.utc); month_end=datetime(now.year+1,1,1,tzinfo=timezone.utc) if now.month==12 else datetime(now.year,now.month+1,1,tzinfo=timezone.utc)
    kist_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"member_id":{"$in":member_ids},"type":"contribution","date":{"$gte":month_start,"$lt":month_end}}},
        {"$group":{"_id":"$member_id","count":{"$sum":1}}}
    ]).to_list(None) if member_ids else []
    kist_by_member={str(x["_id"]):int(x.get("count",0) or 0) for x in kist_rows}
    member_ledgers=[]
    for m in member_rows:
        mid=str(m["_id"]); c=contrib_by_member.get(mid,{}) ; l=loan_by_member.get(mid,{})
        member_ledgers.append({"member_id":mid,"savings":round(float(c.get("savings",0) or 0),2),"transaction_count":int(c.get("transactions",0) or 0),"principal":round(float(l.get("principal",0) or 0),2),"interest":round(float(l.get("interest",0) or 0),2),"active_loans":int(l.get("loans",0) or 0),"kist_paid":kist_by_member.get(mid,0)>0})
    return {"members":member_rows,"member_ledgers":member_ledgers,"loans":[serialize(x) for x in loan_rows]}

@router.get("/{tenant_id}/loans-overview")
async def loans_overview(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    loan_rows, request_rows, member_rows = await asyncio.gather(group_loans(tenant_id,user=user), loan_requests(tenant_id,user=user), members(tenant_id,user=user))
    return {"loans":loan_rows,"requests":request_rows,"members":member_rows}

@router.get("/{tenant_id}/personal-loan-overview")
async def personal_loan_overview(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if not user.get("member_id"): return {"loans":[],"requests":[]}
    loan_rows, request_rows = await asyncio.gather(loans(tenant_id,str(user["member_id"]),user=user), _loan_request_rows(tenant_id,user,personal_only=True))
    return {"loans":loan_rows,"requests":request_rows}

@router.post("/{tenant_id}/transactions/{transaction_id}/reverse")
async def reverse_transaction(tenant_id:str,transaction_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); lock_key=f"transaction-reversal:{tenant_id}:{transaction_id}"; await acquire_operation_lock(lock_key,30)
    try:
        source=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
        if not source: raise HTTPException(404,"Transaction not found")
        if source.get("type")=="reversal" or source.get("reversal_of"): raise HTTPException(400,"A reversal cannot be reversed")
        already=await db.transactions.find_one({"tenant_id":tenant_id,"reversal_of":transaction_id})
        if already: raise HTTPException(409,"Transaction is already reversed")
        amount=-float(source.get("amount",0) or 0)
        now=datetime.now(timezone.utc); key=f"reversal:{transaction_id}"
        txid=await insert_tx(tenant_id,source.get("member_id"),"reversal",amount,source.get("account","cash"),user,reversal_of=transaction_id,original_type=source.get("type"),date=now,note=f"Reversal of {source.get('transaction_ref') or transaction_id}",idempotency_key=key)
        await audit(tenant_id,user,"TRANSACTION_REVERSED","transaction",transaction_id,{"reversal_id":txid})
        return {"ok":True,"id":txid,"reversal_of":transaction_id}
    finally:
        await release_operation_lock(lock_key)

@router.get("/{tenant_id}/transactions")
async def transactions(tenant_id:str,from_date:date|None=None,to_date:date|None=None,typ:str|None=None,page:int=1,page_size:int=10,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); q={"tenant_id":tenant_id}
    if user["role"]=="member":
        q["member_id"]=str(user.get("member_id"))
    else:
        # Group financial register must not include per-share expense allocations
        # (those are member passbook entries) or legacy source expense rows.
        q["type"]={"$nin":["expense_allocation","expense"]}
        q["expense_id"]={"$exists":False}
    if typ:q["type"]=typ
    if from_date or to_date:
        q["date"]={};
        if from_date:q["date"]["$gte"]=datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)
        if to_date:q["date"]["$lte"]=datetime.combine(to_date,datetime.max.time(),tzinfo=timezone.utc)
    page=max(1,page); page_size=max(1,min(page_size,5000)); rows=await get_db().transactions.find(q).sort([("date",-1),("created_at",-1),("_id",-1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size); return [serialize(x) for x in rows]

@router.post("/{tenant_id}/loans")
async def create_loan(tenant_id:str,body:LoanCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    if body.idempotency_key:
        existing=await db.loans.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing:
            interest=float(existing.get("expected_interest",0) or 0)
            return {"id":str(existing["_id"]),"credit_limit":0,"total_interest":interest,"total_due":round(float(existing.get("principal",0) or 0)+interest,2)}
    lock_key=f"loan-disbursement:{tenant_id}"
    await acquire_operation_lock(lock_key,45)
    try:
        m=await get_member(db,tenant_id,body.member_id); tenant=await group_settings(tenant_id); now=datetime.now(timezone.utc)
        shares=int(m.get("active_shares_count",m.get("shares",1)) or 1); share_value=float(tenant.get("kist_per_share",500) or 500); multiplier=float(tenant.get("max_loan_multiplier",20) or 20); minimum=float(tenant.get("min_loan_amount",10000) or 10000); limit=round(max(minimum,shares*share_value*multiplier),2)
        summary=await tenant_summary(tenant_id,None); reserve=float(tenant.get("min_group_reserve_balance",0) or 0); available=float(summary.get("active_account_balance",0) or 0)
        if body.principal<minimum: raise HTTPException(400,f"Minimum loan amount is ₹{minimum:,.2f}")
        if body.principal>limit+0.01: raise HTTPException(400,f"Loan exceeds member credit limit of ₹{limit:,.2f}")
        if available-body.principal<reserve-0.01: raise HTTPException(400,"Insufficient Group Funds")
        rate=body.interest_rate if body.interest_rate is not None else float(tenant.get("loan_interest_rate_per_month",2) or 0); interest=round(body.principal*rate/100*body.months,2)
        start_dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
        loan={"tenant_id":tenant_id,"member_id":body.member_id,"principal":body.principal,"interest_rate":rate,"months":body.months,"expected_interest":interest,"interest_accrued":0.0,"principal_paid":0.0,"interest_paid":0.0,"loan_interest_collected":0.0,"loan_penalty_collected":0.0,"principal_repaid":0.0,"status":"active","purpose":body.purpose,"account":body.account,"created_at":now,"start_date":start_dt,"idempotency_key":body.idempotency_key,"last_interest_accrual_month":start_dt.strftime("%Y-%m"),"loan_due_date":int(tenant.get("loan_due_date",10) or 10)}
        loan["principal_minor"]=int(round(float(body.principal)*100)); loan["expected_interest_minor"]=int(round(float(interest)*100))
        r=await db.loans.insert_one(loan)
        await insert_tx(tenant_id,body.member_id,"loan_disbursement",-body.principal,body.account,user,loan_id=str(r.inserted_id),date=start_dt,note=body.purpose,principal=-body.principal,idempotency_key=f"loan-disbursement:{body.idempotency_key}" if body.idempotency_key else None)
        await audit(tenant_id,user,"LOAN_CREATED","loan",str(r.inserted_id),{"principal":body.principal,"credit_limit":limit})
        return {"id":str(r.inserted_id),"credit_limit":limit,"total_interest":interest,"total_due":round(body.principal+interest,2)}
    finally:
        await release_operation_lock(lock_key)

@router.get("/{tenant_id}/loans")
async def loans(tenant_id:str,member_id:str|None=None,user=Depends(current_user),page:int|None=None,page_size:int=10):
    await tenant_guard(user,tenant_id); db=get_db(); q={"tenant_id":tenant_id}
    if user["role"]=="member": q["member_id"]=str(user.get("member_id"))
    elif member_id: q["member_id"]=member_id
    if page is None:
        rows=await db.loans.find(q).sort([("created_at",-1),("_id",-1)]).limit(200).to_list(200); return [serialize(x) for x in rows]
    page=max(1,page); page_size=max(1,min(page_size,50)); rows=await db.loans.find(q).sort([("created_at",-1),("_id",-1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size)
    mids=[]
    for x in rows:
        if x.get("member_id"):
            try: mids.append(parse_oid(str(x["member_id"])))
            except Exception: pass
    docs=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":mids}}).to_list(len(mids) or 1) if mids else []
    names={str(m["_id"]):f'{m.get("first_name","")} {m.get("last_name","")}'.strip() for m in docs}
    items=[]
    for x in rows:
        item=serialize(x); item["member_name"]=names.get(str(x.get("member_id")),"Member"); items.append(item)
    total=await db.loans.count_documents(q); active_q={**q,"status":"active"}; active_count=await db.loans.count_documents(active_q)
    totals=await db.loans.aggregate([{"$match":q},{"$group":{"_id":None,
        "principal":{"$sum":{"$convert":{"input":{"$ifNull":["$principal",0]},"to":"double","onError":0,"onNull":0}}},
        "outstanding":{"$sum":{"$subtract":[{"$convert":{"input":{"$ifNull":["$principal",0]},"to":"double","onError":0,"onNull":0}},{"$convert":{"input":{"$ifNull":["$principal_paid",0]},"to":"double","onError":0,"onNull":0}}]}},
        "interest":{"$sum":{"$convert":{"input":{"$ifNull":["$expected_interest","$interest_accrued"]},"to":"double","onError":0,"onNull":0}}}
    }}]).to_list(1)
    t=totals[0] if totals else {}
    return {"items":items,"page":page,"page_size":page_size,"total":total,"active_count":active_count,
            "total_principal":round(float(t.get("principal",0) or 0),2),
            "total_outstanding":round(float(t.get("outstanding",0) or 0),2),
            "total_interest":round(float(t.get("interest",0) or 0),2),
            "has_more":page*page_size<total}

@router.get("/{tenant_id}/group-loans")
async def group_loans(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    rows=await db.loans.find({"tenant_id":tenant_id,"status":"active"}).sort("created_at",-1).to_list(5000)
    member_ids=[]
    for r in rows:
        try: member_ids.append(parse_oid(str(r.get("member_id"))))
        except Exception: pass
    member_ids=[x for x in member_ids if x]
    member_rows=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":member_ids}}).to_list(len(member_ids) or 1)
    members_by_id={str(m["_id"]):m for m in member_rows}
    out=[]
    for r in rows:
        m=members_by_id.get(str(r.get("member_id")))
        x=serialize(r); x["member_name"]=f'{m.get("first_name","")} {m.get("last_name","")}'.strip() if m else "Member"; out.append(x)
    return out

@router.post("/{tenant_id}/loan-payments")
async def loan_payment(tenant_id:str,body:LoanPayment,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    if body.idempotency_key:
        existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing:
            loan_existing=await db.loans.find_one({"_id":parse_oid(body.loan_id),"tenant_id":tenant_id})
            return {"id":str(existing["_id"]),"amount":float(existing.get("amount",0) or 0),"principal":float(existing.get("principal_repaid",0) or 0),"interest":float(existing.get("loan_interest_collected",0) or 0),"penalty":float(existing.get("loan_penalty_collected",0) or 0),"principal_remaining":max(0,float(loan_existing.get("principal",0))-float(loan_existing.get("principal_paid",0))) if loan_existing else 0,"interest_remaining":0}
    lock_key=f"loan-payment:{tenant_id}:{body.loan_id}"; await acquire_operation_lock(lock_key,45)
    try:
        loan=await db.loans.find_one({"_id":parse_oid(body.loan_id),"tenant_id":tenant_id})
        if not loan: raise HTTPException(404,"Loan not found")
        if loan.get("status")!="active": raise HTTPException(400,"Loan is already closed")
        tenant=await group_settings(tenant_id); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
        op=float(loan.get("principal_paid",0) or 0); principal=round(float(loan.get("principal",0) or 0),2); outstanding_principal=max(0,principal-op)
        rate=float(tenant.get("loan_interest_rate_per_month",loan.get("interest_rate",2)) or 0); due_day=int(tenant.get("loan_due_date",loan.get("loan_due_date",10)) or 10)
        month_key=dt.strftime("%Y-%m"); interest_key=f"loan-interest:{body.loan_id}:{month_key}"; penalty_key=f"loan-penalty:{body.loan_id}:{month_key}"
        interest_rows=await db.transactions.find({"tenant_id":tenant_id,"loan_interest_key":interest_key}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(100)
        interest_already=round(sum(float(x.get("loan_interest_collected",0) or 0) for x in interest_rows),2)
        last_month=str(loan.get("last_interest_accrual_month") or str(loan.get("start_date",dt))[:7]); current_month=month_key
        try:
            ly,lm=map(int,last_month.split("-")); cy,cm=map(int,current_month.split("-")); elapsed_months=max(0,(cy-ly)*12+(cm-lm))
        except Exception: elapsed_months=1
        if elapsed_months==0 and not interest_rows: elapsed_months=1
        monthly_interest=max(0.0,round(outstanding_principal*rate/100*elapsed_months,2)-interest_already)
        overdue=_due_overdue_days(dt,due_day,dt.year,dt.month); per_day_penalty=float(tenant.get("loan_per_day_penalty",0) or 0); penalty_rows=await db.transactions.find({"tenant_id":tenant_id,"loan_penalty_key":penalty_key}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(100); penalty_already=round(sum(float(x.get("loan_penalty_collected",0) or 0) for x in penalty_rows),2); penalty=max(0.0,round(overdue*per_day_penalty,2)-penalty_already)
        payment=round(float(body.amount),2) if body.amount is not None else round(float(body.principal)+float(body.interest),2)
        if payment<=0: raise HTTPException(400,"Payment must be greater than zero")
        penalty_paid=min(payment,penalty); remaining=round(payment-penalty_paid,2)
        interest_paid=round(min(remaining,monthly_interest),2); remaining=round(remaining-interest_paid,2)
        principal_paid=round(min(remaining,outstanding_principal),2)
        if body.amount is not None and principal_paid+interest_paid+penalty_paid < payment-0.01: raise HTTPException(400,"Payment exceeds current principal, interest and penalty due")
        txid=await insert_tx(tenant_id,loan["member_id"],"loan_repayment",payment,body.account,user,loan_id=body.loan_id,principal=principal_paid,interest=interest_paid,loan_interest_collected=interest_paid,loan_penalty_collected=penalty_paid,principal_repaid=principal_paid,loan_penalty_key=penalty_key if penalty_paid else None,loan_interest_key=interest_key if interest_paid else None,overdue_days=overdue,per_day_penalty=per_day_penalty,payment_category="loan_emi",date=dt,note=body.note,idempotency_key=body.idempotency_key)
        await db.loans.update_one({"_id":loan["_id"]},{"$inc":{"principal_paid":principal_paid,"interest_paid":interest_paid,"interest_accrued":monthly_interest,"loan_interest_collected":interest_paid,"loan_penalty_collected":penalty_paid,"principal_repaid":principal_paid},"$set":{"last_payment_date":dt,"last_interest_accrual_month":month_key}})
        updated=await db.loans.find_one({"_id":loan["_id"]}); remaining_principal=max(0,float(updated.get("principal",0))-float(updated.get("principal_paid",0))); accrued_interest=float(updated.get("interest_accrued",updated.get("expected_interest",0)) or 0); remaining_interest=max(0,accrued_interest-float(updated.get("interest_paid",0) or 0))
        if remaining_principal<=0.01 and remaining_interest<=0.01: await db.loans.update_one({"_id":loan["_id"]},{"$set":{"status":"closed","closed_at":datetime.now(timezone.utc)}})
        return {"id":txid,"amount":payment,"principal":principal_paid,"interest":interest_paid,"penalty":penalty_paid,"loan_interest_part":interest_paid,"loan_principal_part":principal_paid,"loan_penalty_part":penalty_paid,"principal_remaining":round(remaining_principal,2),"interest_remaining":round(remaining_interest,2)}
    finally:
        await release_operation_lock(lock_key)

@router.post("/{tenant_id}/loan-requests")
async def loan_request(tenant_id:str,body:LoanRequestCreate,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"] not in ("member","group_admin"): raise HTTPException(403,"Only group members can request an advance loan")
    member_id=str(user.get("member_id") or "");
    if not member_id: raise HTTPException(400,"Member profile is required")
    elig=await loan_eligibility(tenant_id,member_id,body.amount,body.months,user=user)
    if not elig["liquidity_ok"]:
        raise HTTPException(400,f"Group account balance is currently low (₹{float(elig['active_account_balance']):,.2f}). Loan requests are paused.")
    if not elig["eligible"]: raise HTTPException(400,"Requested amount exceeds your current credit eligibility or available group funds")
    now=datetime.now(timezone.utc)
    if body.idempotency_key:
        existing=await get_db().loan_requests.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing: return {"id":str(existing["_id"]),"credit_limit":elig["credit_limit"],"loan_apply_date":str(existing.get("loan_apply_date",now.date()))[:10],"requested_start_date":str(existing.get("requested_start_date",now.date()))[:10]}
    apply_date=body.loan_apply_date or now.date()
    min_months=max(0,int((await group_settings(tenant_id)).get("min_advance_apply_months",2) or 2))
    start_date=body.requested_start_date or _add_months(apply_date,min_months)
    months_ahead=(start_date.year-apply_date.year)*12+(start_date.month-apply_date.month)
    if months_ahead<min_months:
        raise HTTPException(400,f"Loans must be requested at least {min_months} months in advance as per group policy.")
    doc={"tenant_id":tenant_id,"member_id":member_id,"amount":body.amount,"months":body.months,"purpose":body.purpose,"loan_apply_date":datetime.combine(apply_date,datetime.min.time(),tzinfo=timezone.utc),"requested_start_date":datetime.combine(start_date,datetime.min.time(),tzinfo=timezone.utc),"status":"pending","idempotency_key":body.idempotency_key,"approval_records":[],"loan_approval_records":[],"created_at":now}
    r=await get_db().loan_requests.insert_one(doc); await audit(tenant_id,user,"LOAN_REQUESTED","loan_request",str(r.inserted_id),{"amount":body.amount,"loan_apply_date":str(apply_date),"requested_start_date":str(start_date)}); await _create_notification(tenant_id,role="admin",source_key=f"loan-request:{r.inserted_id}",title="New loan request",body=f"A member has requested ₹{float(body.amount):,.2f}.",created_at=now); return {"id":str(r.inserted_id),"credit_limit":elig["credit_limit"],"loan_apply_date":str(apply_date),"requested_start_date":str(start_date)}

async def _loan_request_rows(tenant_id:str,user,personal_only:bool=False):
    await tenant_guard(user,tenant_id); q={"tenant_id":tenant_id}
    # Members see only their own requests. Group admins may also have a member
    # record, but their admin approval queue must include all requests in-group.
    if user["role"]=="member" or personal_only:
        q["member_id"]=str(user.get("member_id") or "")
    rows=await get_db().loan_requests.find(q).sort("created_at",-1).to_list(2000)
    tenant=await group_settings(tenant_id); required=int(tenant.get("required_admin_approvals",1) or 1)
    for x in rows:
        x["approval_records"]=x.get("approval_records",x.get("loan_approval_records",[])); x["approval_count"]=len(x["approval_records"]); x["required_admin_approvals"]=required
    return [serialize(x) for x in rows]

@router.get("/{tenant_id}/loan-requests")
async def loan_requests(tenant_id:str,user=Depends(current_user)):
    return await _loan_request_rows(tenant_id,user)

@router.patch("/{tenant_id}/loan-requests/{request_id}")
async def decide_loan_request(tenant_id:str,request_id:str,body:LoanRequestDecision,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); lock_key=f"loan-request:{tenant_id}:{request_id}"; await acquire_operation_lock(lock_key,45)
    try:
        req=await db.loan_requests.find_one({"_id":parse_oid(request_id),"tenant_id":tenant_id})
        if not req: raise HTTPException(404,"Loan request not found")
        if req.get("status") in ("approved","rejected"):
            if req.get("loan_id"): return {"ok":True,"status":req.get("status"),"loan":{"id":str(req["loan_id"])}}
            raise HTTPException(400,"Request already decided")
        if body.decision=="rejected":
            await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"rejected","decision_note":body.note or "Rejected by administrator","decided_at":datetime.now(timezone.utc),"rejected_by":str(user["_id"])}})
            await audit(tenant_id,user,"LOAN_REQUEST_REJECTED","loan_request",request_id,{"reason":body.note or "Rejected by administrator"}); return {"ok":True,"status":"rejected"}
        tenant=await group_settings(tenant_id); required=int(tenant.get("required_admin_approvals",1) or 1); uid=str(user["_id"]); approvals=[str(x) for x in req.get("approval_records",req.get("loan_approval_records",[]))]
        if uid not in approvals: approvals.append(uid)
        if len(approvals)<required:
            await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"pending","approval_records":approvals,"loan_approval_records":approvals,"last_approved_at":datetime.now(timezone.utc)}})
            await audit(tenant_id,user,"LOAN_REQUEST_APPROVAL_RECORDED","loan_request",request_id,{"approval_count":len(approvals),"required":required})
            return {"ok":True,"status":"pending","approval_count":len(approvals),"required_admin_approvals":required}
        principal=body.principal or req["amount"]; rate=body.interest_rate if body.interest_rate is not None else float(tenant.get("loan_interest_rate_per_month",2) or 0); months=body.months or 2
        apply_value=req.get("loan_apply_date") or req.get("created_at") or datetime.now(timezone.utc); apply_date=apply_value.date() if hasattr(apply_value,"date") else datetime.now(timezone.utc).date()
        start_value=req.get("requested_start_date") or req.get("loan_apply_date") or datetime.now(timezone.utc); requested_start=start_value.date() if hasattr(start_value,"date") else datetime.now(timezone.utc).date()
        min_months=max(0,int(tenant.get("min_advance_apply_months",2) or 2))
        if not req.get("requested_start_date") and not req.get("loan_apply_date"): requested_start=_add_months(apply_date,min_months)
        months_ahead=(requested_start.year-apply_date.year)*12+(requested_start.month-apply_date.month)
        if months_ahead<min_months:
            await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"rejected","decision_note":f"Loans must be requested at least {min_months} months in advance as per group policy.","rejected_by":"system","decided_at":datetime.now(timezone.utc)}})
            return {"ok":True,"status":"rejected","reason":"advance_rule"}
        elig=await loan_eligibility(tenant_id,req["member_id"],principal,months,user=user)
        if not elig["eligible"]:
            reason="Insufficient Group Funds" if elig["funds_available_for_disbursement"]<principal else "Loan exceeds member credit eligibility"
            await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"rejected","decision_note":reason,"rejected_by":"system","decided_at":datetime.now(timezone.utc),"approval_records":approvals,"loan_approval_records":approvals}})
            await _create_notification(tenant_id,role="member",member_id=req["member_id"],source_key=f"loan-request-reject:{req['_id']}",title="Loan request rejected",body=reason,created_at=datetime.now(timezone.utc))
            return {"ok":True,"status":"rejected","reason":reason}
        idem=f"loan-request-approval:{request_id}"
        created=await create_loan(tenant_id,LoanCreate(member_id=req["member_id"],principal=principal,interest_rate=rate,months=months,account=body.account,purpose=req.get("purpose", ""),date=requested_start,idempotency_key=idem),user)
        await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"approved","decision_note":body.note,"decided_at":datetime.now(timezone.utc),"approval_records":approvals,"loan_approval_records":approvals,"loan_id":parse_oid(created["id"])}})
        return {"ok":True,"status":"approved","approval_count":len(approvals),"required_admin_approvals":required,"loan":created}
    finally:
        await release_operation_lock(lock_key)

async def ensure_expense_allocations(tenant_id:str,expense_doc):
    db=get_db()
    shares=await db.shares.find({"tenant_id":tenant_id,"status":"active"}).sort("share_no",1).to_list(10000)
    if not shares: return
    existing_count=await db.transactions.count_documents({"tenant_id":tenant_id,"expense_id":str(expense_doc["_id"]),"type":"expense_allocation"})
    if existing_count>=len(shares): return
    total=float(expense_doc.get("amount",0) or 0); per=round(total/len(shares),2); allocated=0.0
    from pymongo import UpdateOne
    ops=[]
    for i,share in enumerate(shares):
        amount=round(total-allocated,2) if i==len(shares)-1 else per
        allocated=round(allocated+amount,2)
        ops.append(UpdateOne(
            {"tenant_id":tenant_id,"expense_id":str(expense_doc["_id"]),"share_id":str(share["_id"]),"type":"expense_allocation"},
            {"$setOnInsert":{
                "tenant_id":tenant_id,"member_id":str(share["member_id"]),"share_id":str(share["_id"]),
                "share_no":int(share["share_no"]),"expense_id":str(expense_doc["_id"]),"type":"expense_allocation",
                "amount":-amount,"amount_minor":-int(round(amount*100)),"account":expense_doc.get("account","cash"),"date":expense_doc.get("date"),
                "created_at":expense_doc.get("created_at",datetime.now(timezone.utc)),
                "payment_category":"group_expense_allocation","note":expense_doc.get("category", "Group expense")
            }},upsert=True))
    if ops: await db.transactions.bulk_write(ops,ordered=False)

@router.post("/{tenant_id}/expenses")
async def expense(tenant_id:str,body:ExpenseCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); now=datetime.now(timezone.utc); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    if body.idempotency_key:
        existing=await db.expenses.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing: return {"id":str(existing["_id"])}
    if not await db.expense_categories.find_one({"tenant_id":tenant_id,"name":body.category}):
        await db.expense_categories.insert_one({"tenant_id":tenant_id,"name":body.category,"created_at":now})
    expense_doc={**body.model_dump(),"tenant_id":tenant_id,"date":dt,"created_at":now,"proof_url":None,"proof_public_id":None,"idempotency_key":body.idempotency_key,"amount_minor":int(round(float(body.amount)*100))}
    r=await db.expenses.insert_one(expense_doc)
    expense_doc["_id"]=r.inserted_id
    await _feed_upsert(expense_doc,source_type="expense")
    created=await db.expenses.find_one({"_id":r.inserted_id})
    await ensure_expense_allocations(tenant_id,created)
    await audit(tenant_id,user,"EXPENSE_CREATED","expense",str(r.inserted_id),{"amount":body.amount})
    await _create_notification(tenant_id,role="admin",source_key=f"expense:{r.inserted_id}",title="Group expense recorded",body=f"{body.category}: ₹{float(body.amount):,.2f}.",created_at=now)
    return {"id":str(r.inserted_id)}

@router.get("/{tenant_id}/expenses")
async def expenses(tenant_id:str,user=Depends(admin_user),page:int|None=None,page_size:int=10):
    await tenant_guard(user,tenant_id); db=get_db(); q={"tenant_id":tenant_id}
    if page is None:
        rows=await db.expenses.find(q).sort([("date",-1),("created_at",-1),("_id",-1)]).limit(200).to_list(200); return [serialize(x) for x in rows]
    page=max(1,page); page_size=max(1,min(page_size,50)); rows=await db.expenses.find(q).sort([("date",-1),("created_at",-1),("_id",-1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size); total=await db.expenses.count_documents(q)
    agg=await db.expenses.aggregate([{"$match":q},{"$group":{"_id":None,"total":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}]).to_list(1); grand=float((agg[0] if agg else {}).get("total",0) or 0)
    return {"items":[serialize(x) for x in rows],"page":page,"page_size":page_size,"total":total,"grand_total":round(grand,2),"has_more":page*page_size<total}

@router.get("/{tenant_id}/expense-categories")
async def expense_categories(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); rows=await get_db().expense_categories.find({"tenant_id":tenant_id}).sort("name",1).to_list(500); return [serialize(x) for x in rows]

@router.post("/{tenant_id}/expense-categories")
async def create_expense_category(tenant_id:str,body:ExpenseCategoryCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); name=body.name.strip()
    if await db.expense_categories.find_one({"tenant_id":tenant_id,"name":name}): raise HTTPException(409,"Category already exists")
    r=await db.expense_categories.insert_one({"tenant_id":tenant_id,"name":name,"created_at":datetime.now(timezone.utc)}); await audit(tenant_id,user,"EXPENSE_CATEGORY_CREATED","expense_category",str(r.inserted_id)); return {"id":str(r.inserted_id),"name":name}

@router.post("/{tenant_id}/expenses/{expense_id}/proof")
async def expense_proof(tenant_id:str,expense_id:str,file:UploadFile=File(...),user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); exp=await db.expenses.find_one({"_id":parse_oid(expense_id),"tenant_id":tenant_id})
    if not exp: raise HTTPException(404,"Expense not found")
    if file.content_type not in {"image/jpeg","image/png","image/webp","application/pdf"}: raise HTTPException(415,"Only JPG, PNG, WebP or PDF receipts are allowed")
    data=await file.read()
    if len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    rt="raw" if file.content_type=="application/pdf" else "image"
    # Upload and persist the replacement before deleting the previous proof so a
    # Cloudinary failure cannot destroy the currently attached receipt.
    r=upload_bytes(data,public_id=f"proof-{expense_id}-{uuid.uuid4().hex[:8]}",folder=f"bharat-bachat/tenants/{tenant_id}/expenses/{expense_id}",resource_type=rt)
    await db.expenses.update_one({"_id":exp["_id"]},{"$set":{"proof_url":r.get("secure_url"),"proof_public_id":r.get("public_id"),"proof_resource_type":rt,"proof_original_filename":file.filename,"proof_content_type":file.content_type}})
    old_public_id=exp.get("proof_public_id")
    if old_public_id and old_public_id!=r.get("public_id"):
        try: delete_asset(old_public_id,exp.get("proof_resource_type","image"))
        except Exception: pass
    await audit(tenant_id,user,"EXPENSE_PROOF_UPLOADED","expense",expense_id); return {"ok":True,"proof_url":r.get("secure_url")}

@router.delete("/{tenant_id}/expenses/{expense_id}/proof")
async def delete_expense_proof(tenant_id:str,expense_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); exp=await db.expenses.find_one({"_id":parse_oid(expense_id),"tenant_id":tenant_id})
    if not exp: raise HTTPException(404,"Expense not found")
    if exp.get("proof_public_id"): delete_asset(exp["proof_public_id"],exp.get("proof_resource_type","image"))
    await db.expenses.update_one({"_id":exp["_id"]},{"$set":{"proof_url":None,"proof_public_id":None}}); await audit(tenant_id,user,"EXPENSE_PROOF_DELETED","expense",expense_id); return {"ok":True}

@router.get("/{tenant_id}/passbook/{member_id}")
async def passbook(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,account:str|None=None,book:str|None=None,page:int=1,page_size:int=10,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Member access denied")
    await get_member(get_db(),tenant_id,member_id); db=get_db(); q={"tenant_id":tenant_id,"member_id":member_id}
    if from_date or to_date:
        q["date"]={}
        if from_date:q["date"]["$gte"]=datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)
        if to_date:q["date"]["$lte"]=datetime.combine(to_date,datetime.max.time(),tzinfo=timezone.utc)
    if share_no:q["share_no"]=share_no
    if account:q["account"]=account
    if book=="expense":q["type"]="expense_allocation"
    elif book in ("bank","cash"):q["account"]=book
    page=max(1,page); page_size=max(1,min(page_size,50)); skip=(page-1)*page_size
    sort=[("date",-1),("created_at",-1),("_id",-1)]
    rows=await db.transactions.find(q).sort(sort).skip(skip).limit(page_size).to_list(page_size)
    if not rows: return []
    # Running balance is server-authoritative and page-independent.  Compute the
    # complete filtered sum plus the amount belonging to records newer than the
    # first row on this page; then walk this page from newest to oldest.
    total_rows=await db.transactions.aggregate([{"$match":q},{"$group":{"_id":None,"total":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}},"count":{"$sum":1}}}]).to_list(1)
    total=float((total_rows[0] if total_rows else {}).get("total",0) or 0)
    first=rows[0]; newer=0.0
    if skip>0:
        fd=first.get("date"); fc=first.get("created_at"); fid=first.get("_id")
        newer_q={"$or":[]}
        if fd is not None:newer_q["$or"].append({"date":{"$gt":fd}})
        if fd is not None and fc is not None:newer_q["$or"].append({"date":fd,"created_at":{"$gt":fc}})
        if fd is not None and fc is not None:newer_q["$or"].append({"date":fd,"created_at":fc,"_id":{"$gt":fid}})
        if newer_q["$or"]:
            newer_rows=await db.transactions.aggregate([{"$match":{**q,**newer_q}},{"$group":{"_id":None,"total":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}]).to_list(1)
            newer=float((newer_rows[0] if newer_rows else {}).get("total",0) or 0)
    balance=round(total-newer,2); out=[]
    for row in rows:
        x=serialize(row); amount=float(row.get("amount",0) or 0); balance=round(balance,2); x["running_balance"]=balance; out.append(x); balance=round(balance-amount,2)
    return out

@router.get("/{tenant_id}/passbook-summary/{member_id}")
async def passbook_summary(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,account:str|None=None,book:str|None=None,user=Depends(current_user)):
    """Summary is calculated independently of the paginated transaction feed."""
    await tenant_guard(user,tenant_id)
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Member access denied")
    await get_member(get_db(),tenant_id,member_id); db=get_db(); q={"tenant_id":tenant_id,"member_id":member_id}
    if from_date or to_date:
        q["date"]={}
        if from_date:q["date"]["$gte"]=datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)
        if to_date:q["date"]["$lte"]=datetime.combine(to_date,datetime.max.time(),tzinfo=timezone.utc)
    if share_no:q["share_no"]=share_no
    if account:q["account"]=account
    if book=="expense":q["type"]="expense_allocation"
    elif book in ("bank","cash"):q["account"]=book
    agg=await db.transactions.aggregate([{"$match":q},{"$group":{"_id":None,"credits":{"$sum":{"$cond":[{"$gt":[{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}},0]},{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}},0]}},"debits":{"$sum":{"$cond":[{"$lt":[{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}},0]},{"$abs":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}},0]}},"net":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}},"count":{"$sum":1}}}]).to_list(1)
    row=agg[0] if agg else {}
    opening=0.0
    if from_date:
        before=dict(q); before.pop("date",None); before["date"]={"$lt":datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)}
        before_row=await db.transactions.aggregate([{"$match":before},{"$group":{"_id":None,"net":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}]).to_list(1)
        opening=float((before_row[0] if before_row else {}).get("net",0) or 0)
    net=float(row.get("net",0) or 0)
    return {"opening_balance":round(opening,2),"total_credits":round(float(row.get("credits",0) or 0),2),"total_debits":round(float(row.get("debits",0) or 0),2),"closing_balance":round(opening+net,2),"transaction_count":int(row.get("count",0) or 0)}

@router.get("/{tenant_id}/audit")
async def audit_logs(tenant_id:str,user=Depends(require_roles("super_admin")),page:int|None=None,page_size:int=10):
    await tenant_guard(user,tenant_id); db=get_db(); q={"tenant_id":tenant_id}
    if page is None:
        rows=await db.audit_logs.find(q).sort([("created_at",-1),("_id",-1)]).limit(200).to_list(200); return [serialize(x) for x in rows]
    page=max(1,page); page_size=max(1,min(page_size,50)); rows=await db.audit_logs.find(q).sort([("created_at",-1),("_id",-1)]).skip((page-1)*page_size).limit(page_size).to_list(page_size); total=await db.audit_logs.count_documents(q)
    return {"items":[serialize(x) for x in rows],"page":page,"page_size":page_size,"total":total,"has_more":page*page_size<total}
