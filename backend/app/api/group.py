from datetime import datetime, timezone, date, timedelta
import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..db import get_db, backfill_financial_feed
from ..deps import current_user, tenant_guard, require_roles, parse_oid
from ..models import *
from ..services import tenant_summary, analytics
from ..accounting_engine import monthly_overdue_penalty_minor, to_minor, from_minor, simple_interest_minor, allocate_kist_payment, profit_components, net_profit_from_buckets_minor, assert_idempotent_match, allocate_minor
from ..journal import build_journal, build_expense_journal, reverse_journal
from ..audit import audit
from ..core.config import settings
from ..core.cloudinary import upload_bytes, delete_asset
from ..core.security import hash_password, normalize_phone
from ..share_service import ensure_member_shares
from ..tenant_relations import scoped_entity_query, relation_matches

router=APIRouter(prefix="/group",tags=["group"])

# Financial-feed repair is lazy and only runs when the materialized ledger is
# behind the source collections. This keeps startup fast while guaranteeing that
# ledger drill-downs never show empty/zero data just because the background
# backfill has not finished yet.
_feed_repair_lock=asyncio.Lock()


def _doc_amount_minor(row: dict, minor_field: str = "amount_minor", amount_field: str = "amount") -> int:
    """Read money from exact paise fields, with a rounded legacy fallback."""
    value = row.get(minor_field)
    return int(value) if value is not None else to_minor(row.get(amount_field, 0) or 0)


def _mongo_amount_minor_expr(minor_field: str = "amount_minor", amount_field: str = "amount") -> dict:
    """Mongo expression to aggregate paise without summing binary doubles."""
    legacy_minor = {"$round": [{"$multiply": [{"$convert": {"input": {"$ifNull": [f"${amount_field}", 0]}, "to": "double", "onError": 0, "onNull": 0}}, 100]}, 0]}
    return {"$convert": {"input": {"$ifNull": [f"${minor_field}", legacy_minor]}, "to": "long", "onError": 0, "onNull": 0}}


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
        amount_expr={"$divide": [_mongo_amount_minor_expr(), 100]}
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
    await db.notifications.update_one({"_id":row["_id"],"tenant_id":tenant_id},{"$set":{"read":True}}); return {"ok":True}

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
    await db.notifications.update_one({"_id":row["_id"],"tenant_id":tenant_id},{"$set":{"dismissed":True,"dismissed_at":datetime.now(timezone.utc)}}); return {"ok":True}

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
        await db.members.delete_one({"_id":result.inserted_id,"tenant_id":tenant_id}); raise
    await ensure_member_shares(await db.members.find_one({"_id":result.inserted_id}))
    await audit(tenant_id,user,"MEMBER_CREATED","member",str(result.inserted_id),{"shares":body.shares}); return {"id":str(result.inserted_id)}

@router.patch("/{tenant_id}/members/{member_id}")
async def update_member(tenant_id:str,member_id:str,body:MemberUpdate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); m=await get_member(db,tenant_id,member_id)
    await ensure_member_shares(m)
    active_count=await db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"})
    if body.shares < active_count:
        raise HTTPException(400,"Share count cannot be reduced below the member's active shares. Close shares explicitly before reducing the count.")
    await db.members.update_one({"_id":m["_id"],"tenant_id":tenant_id},{"$set":body.model_dump()})
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
    await get_db().members.update_one({"_id":parse_oid(member_id),"tenant_id":tenant_id},{"$set":{"active":body.active}})
    await audit(tenant_id,user,"MEMBER_STATUS_CHANGED","member",member_id,{"active":body.active}); return {"ok":True}

@router.post("/{tenant_id}/members/{member_id}/reset-password")
async def reset_member_password(tenant_id:str,member_id:str,body:PasswordReset,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); await get_member(get_db(),tenant_id,member_id)
    target=await get_db().users.find_one({"member_id":member_id,"tenant_id":parse_oid(tenant_id),"role":"member"})
    if not target: raise HTTPException(404,"Member login account not found")
    now=datetime.now(timezone.utc)
    await get_db().users.update_one({"_id":target["_id"],"tenant_id":parse_oid(tenant_id)},{"$set":{"password_hash":hash_password(body.password),"must_change_password":True,"password_changed_at":now,"password_reset_at":now,"password_reset_by":str(user["_id"])}})
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
    await get_db().members.update_one({"_id":m["_id"],"tenant_id":tenant_id},{"$set":{"profile_image_url":r.get("secure_url"),"profile_picture_url":r.get("secure_url"),"profile_image_public_id":r.get("public_id")}})
    if old_public_id and old_public_id!=r.get("public_id"):
        try: delete_asset(old_public_id)
        except Exception: pass
    await audit(tenant_id,user,"MEMBER_PROFILE_IMAGE_UPDATED","member",member_id); return {"ok":True,"profile_image_url":r.get("secure_url")}

@router.delete("/{tenant_id}/members/{member_id}/profile-image")
async def delete_member_image(tenant_id:str,member_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,member_id)
    if m.get("profile_image_public_id"): delete_asset(m["profile_image_public_id"])
    await get_db().members.update_one({"_id":m["_id"],"tenant_id":tenant_id},{"$set":{"profile_image_url":None,"profile_picture_url":None,"profile_image_public_id":None}})
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
    y,m=map(int,period.split("-")); overdue=_due_overdue_days(payment_dt,due_day,y,m)
    amount_minor=overdue*to_minor(per_day); amount=float(from_minor(amount_minor))
    if amount_minor<=0: return None
    key=f"bc:{tenant_id}:{member_id}:{period}"
    existing=await db.transactions.find_one({"tenant_id":tenant_id,"bc_penalty_key":key})
    if existing:
        await _finalize_tx_side_effects(existing,user)
        return str(existing["_id"])
    try:
        return await insert_tx(tenant_id,member_id,"penalty",amount,account,user,date=payment_dt,note=note or f"BC Kist late penalty: {overdue} overdue day(s)",payment_category="bc",penalty_category="bc",bc_regular_kist_penalty=amount,overdue_days=overdue,per_day_penalty=per_day,period=period,bc_penalty_key=key)
    except Exception:
        # A concurrent collector may have inserted the unique period penalty
        # between the lookup and insert. Reuse it and finish any pending effects.
        existing=await db.transactions.find_one({"tenant_id":tenant_id,"bc_penalty_key":key})
        if not existing: raise
        await _finalize_tx_side_effects(existing,user)
        return str(existing["_id"])

async def _feed_upsert(doc, *, source_type="transaction"):
    db=get_db()
    raw_amount=float(doc.get("amount",0) or 0)
    source_minor=int(doc["amount_minor"]) if doc.get("amount_minor") is not None else to_minor(raw_amount)
    amount_minor=-abs(source_minor) if source_type=="expense" else source_minor
    amount=float(from_minor(amount_minor))
    feed={"source_key":f"{source_type}:{doc.get('_id')}","source_type":source_type,"source_id":str(doc.get("_id")),"transaction_ref":doc.get("transaction_ref"),"tenant_id":doc.get("tenant_id"),"member_id":str(doc.get("member_id")) if doc.get("member_id") else None,"type":doc.get("type","transaction"),"amount":amount,"amount_minor":amount_minor,"account":doc.get("account","cash"),"date":doc.get("date") or doc.get("created_at"),"created_at":doc.get("created_at") or doc.get("date"),"note":doc.get("note","") or "","payment_category":doc.get("payment_category"),"penalty_category":doc.get("penalty_category"),"original_type":doc.get("original_type"),"reversal_of":doc.get("reversal_of"),"period":doc.get("period"),"status":doc.get("status"),"loan_id":str(doc.get("loan_id")) if doc.get("loan_id") else None,"loan_interest_collected":float(doc.get("loan_interest_collected",doc.get("interest",0)) or 0),"loan_interest_minor":doc.get("loan_interest_minor"),"loan_penalty_collected":float(doc.get("loan_penalty_collected",0) or 0),"loan_penalty_minor":doc.get("loan_penalty_minor"),"bc_regular_kist_penalty":float(doc.get("bc_regular_kist_penalty",0) or 0),"bc_penalty_minor":doc.get("bc_penalty_minor"),"other_interest_value":float(doc.get("other_interest",doc.get("other_interest_value",0)) or 0),"other_interest_minor":doc.get("other_interest_minor"),"other_penalty_value":float(doc.get("other_penalty",doc.get("other_penalty_value",0)) or 0),"other_penalty_minor":doc.get("other_penalty_minor"),"interest":float(doc.get("interest",0) or 0),"interest_minor":doc.get("interest_minor"),"principal":float(doc.get("principal",0) or 0),"principal_minor":doc.get("principal_minor"),"share_no":doc.get("share_no"),"share_id":str(doc.get("share_id")) if doc.get("share_id") else None,"expense_id":str(doc.get("expense_id")) if doc.get("expense_id") else None,"category":doc.get("category") if source_type=="expense" else None}
    # MongoDB rejects the same path in $set and $setOnInsert. created_at is
    # immutable after insertion, so keep it out of $set exactly as in backfill.
    created_at=feed.pop("created_at",None)
    update={"$set":feed}
    if created_at is not None: update["$setOnInsert"]={"created_at":created_at}
    await db.financial_feed.update_one({"tenant_id":feed.get("tenant_id"),"source_key":feed["source_key"]},update,upsert=True)

async def _post_transaction_journal(doc: dict):
    """Persist and verify one transaction's balanced journal idempotently."""
    db=get_db(); journal=build_journal(doc)
    if not journal:
        return None
    source_key=f"tx:{doc['_id']}"
    journal_doc={"tenant_id":doc.get("tenant_id"),"source_type":"transaction","source_id":str(doc["_id"]),
                 "source_key":source_key,"status":"pending",**journal,
                 "created_at":doc.get("created_at") or datetime.now(timezone.utc)}
    await db.journal_entries.update_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key},
                                        {"$setOnInsert":journal_doc},upsert=True)
    stored=await db.journal_entries.find_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key})
    if not stored or not stored.get("balanced") or int(stored.get("debit_minor",-1)) != int(stored.get("credit_minor",-2)):
        raise RuntimeError(f"Journal for transaction {doc['_id']} is not balanced")
    await db.journal_entries.update_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key},
                                        {"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
    await db.transactions.update_one({"_id":doc["_id"],"tenant_id":doc.get("tenant_id")},
                                     {"$set":{"journal_status":"posted"}})
    stored["status"]="posted"
    return stored


async def _ensure_tx_journal(doc: dict):
    """Idempotently post/recover a journal, including the source of a reversal."""
    db=get_db()
    if not doc.get("journal_status") and doc.get("type") != "reversal":
        return
    journal=None
    if doc.get("type") == "reversal" and doc.get("reversal_of"):
        original_id=str(doc.get("reversal_of"))
        original=await db.transactions.find_one({"_id":parse_oid(original_id),"tenant_id":doc.get("tenant_id")})
        original_journal=await db.journal_entries.find_one({"source_key":f"tx:{original_id}","tenant_id":doc.get("tenant_id"),"balanced":True})
        if original and (not original_journal or original_journal.get("status") != "posted"):
            # Legacy/pending source transactions must be posted before their inverse;
            # otherwise the reversal could precede its original ledger leg.
            original_journal=await _post_transaction_journal(original)
        journal=reverse_journal(original_journal) if original_journal else None
    else:
        journal=build_journal(doc)
    if not journal:
        if doc.get("journal_status")=="pending":
            await db.transactions.update_one({"_id":doc["_id"],"tenant_id":doc.get("tenant_id")},
                                             {"$set":{"journal_status":"not_applicable"}})
        return
    source_key=f"tx:{doc['_id']}"
    journal_doc={"tenant_id":doc.get("tenant_id"),"source_type":"transaction","source_id":str(doc["_id"]),
                 "source_key":source_key,"status":"pending",**journal,
                 "created_at":doc.get("created_at") or datetime.now(timezone.utc)}
    await db.journal_entries.update_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key},
                                        {"$setOnInsert":journal_doc},upsert=True)
    stored=await db.journal_entries.find_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key})
    if not stored or not stored.get("balanced") or int(stored.get("debit_minor",-1)) != int(stored.get("credit_minor",-2)):
        raise RuntimeError(f"Journal for transaction {doc['_id']} is not balanced")
    await db.journal_entries.update_one({"tenant_id":doc.get("tenant_id"),"source_key":source_key},
                                        {"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
    await db.transactions.update_one({"_id":doc["_id"],"tenant_id":doc.get("tenant_id")},
                                     {"$set":{"journal_status":"posted"}})


async def _finalize_tx_side_effects(doc: dict, user: dict):
    """Retry-safe completion after the source transaction is durable.

    This outbox-like sequence is recoverable when a process dies between writes:
    client retries hit the same idempotency key and resume the missing effects.
    """
    db = get_db()
    await _ensure_tx_journal(doc)
    await _feed_upsert(doc)
    txid = str(doc["_id"])
    typ = str(doc.get("type", "transaction"))
    await audit(doc["tenant_id"], user, f"{typ.upper()}_POSTED", "transaction", txid,
                {"amount": doc.get("amount", 0)}, event_key=f"tx-posted:{txid}")
    member_id = doc.get("member_id")
    created_at = doc.get("date") or doc.get("created_at") or datetime.now(timezone.utc)
    if member_id and typ != "expense_allocation":
        await _create_notification(doc["tenant_id"], role="member", member_id=str(member_id), source_key=f"tx:{txid}:{member_id}", title="Account activity", body=f"{typ.replace('_',' ').title()} ₹{float(doc.get('amount',0) or 0):,.2f}.", created_at=created_at)
    if user.get("role") in ("member", "group_admin") and typ != "expense_allocation":
        await _create_notification(doc["tenant_id"], role="admin", source_key=f"admin-tx:{txid}", title="Group activity", body=f"{typ.replace('_',' ').title()} ₹{float(doc.get('amount',0) or 0):,.2f}.", created_at=created_at)
    if typ == "reversal" and doc.get("original_type") == "loan_repayment":
        await _apply_loan_repayment_reversal_once(doc["tenant_id"], doc)
    await db.transactions.update_one({"_id":doc["_id"],"tenant_id":doc["tenant_id"]},{"$set":{"side_effects_status":"complete","side_effects_completed_at":datetime.now(timezone.utc)}})


async def _validate_tx_relations(tenant_id: str, member_id, extra: dict):
    """Reject cross-tenant or mismatched member/share/loan links before posting money."""
    db = get_db()
    member = None
    if member_id:
        member = await db.members.find_one(scoped_entity_query(tenant_id, parse_oid(str(member_id))))
        if not relation_matches(member, tenant_id):
            raise HTTPException(404, "Member not found in this group")
    share_id = extra.get("share_id")
    if share_id:
        query = scoped_entity_query(tenant_id, parse_oid(str(share_id)), member_id=member_id if member_id else None)
        share = await db.shares.find_one(query)
        if not relation_matches(share, tenant_id, member_id=member_id if member_id else None):
            raise HTTPException(404, "Share not found for this member and group")
    loan_id = extra.get("loan_id")
    if loan_id:
        loan = await db.loans.find_one(scoped_entity_query(tenant_id, parse_oid(str(loan_id))))
        if not relation_matches(loan, tenant_id):
            raise HTTPException(404, "Loan not found in this group")
        if member_id and str(loan.get("member_id")) != str(member_id):
            raise HTTPException(400, "Loan does not belong to the selected member")
    expense_id = extra.get("expense_id")
    if expense_id:
        expense = await db.expenses.find_one(scoped_entity_query(tenant_id, parse_oid(str(expense_id))))
        if not relation_matches(expense, tenant_id):
            raise HTTPException(404, "Expense not found in this group")


def _assert_idempotent_tx_matches(existing: dict, tenant_id: str, member_id, typ: str, amount_minor: int, account, extra: dict):
    try:
        assert_idempotent_match(existing, tenant_id=tenant_id, member_id=member_id, typ=typ,
                                amount_minor=amount_minor, account=account, extra=extra)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


async def insert_tx(tenant_id,member_id,typ,amount,account,user,**extra):
    db=get_db(); now=datetime.now(timezone.utc); idem=extra.pop("idempotency_key",None)
    amount_minor = to_minor(amount or 0)
    idempotency_minor = int(extra.get("idempotency_amount_minor", amount_minor))
    await _validate_tx_relations(tenant_id, member_id, extra)
    if idem:
        existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":idem})
        if existing:
            _assert_idempotent_tx_matches(existing, tenant_id, member_id, typ, idempotency_minor, account, extra)
            await _finalize_tx_side_effects(existing, user)
            return str(existing["_id"])
    normalized = float(from_minor(amount_minor))
    doc={"tenant_id":tenant_id,"member_id":member_id,"type":typ,"amount":normalized,"amount_minor":amount_minor,"account":account,"transaction_ref":f"BB-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:10].upper()}","created_at":now,"idempotency_key":idem,"created_by":str(user.get("_id","system")),"created_by_role":user.get("role","system"),"created_by_phone":user.get("phone"),"side_effects_status":"pending","loan_apply_status":"pending" if typ=="loan_repayment" else "not_applicable",**extra}
    journal=build_journal(doc)
    if typ in {"transfer", "cash_bank_transfer"} and not journal:
        raise HTTPException(400,"A cash/bank transfer requires distinct from_account and to_account values")
    if typ == "reversal" and extra.get("reversal_of"):
        original_id = str(extra.get("reversal_of"))
        original = await db.transactions.find_one({"_id": parse_oid(original_id), "tenant_id": tenant_id})
        if not original: raise HTTPException(404, "Original transaction not found in this group")
        if original.get("type") == "reversal" or original.get("reversal_of"):
            raise HTTPException(400, "A reversal cannot be reversed")
        original_journal = await db.journal_entries.find_one({"source_key": f"tx:{original_id}", "tenant_id": tenant_id, "balanced": True})
        if not original_journal or original_journal.get("status") != "posted":
            original_journal = await _post_transaction_journal(original)
        if (not original_journal or not original_journal.get("balanced")
                or int(original_journal.get("debit_minor", -1)) != int(original_journal.get("credit_minor", -2))
                or original_journal.get("status") != "posted"):
            raise HTTPException(409, "Original transaction has no posted balanced journal; reconcile it before reversing")
        journal = reverse_journal(original_journal)
        if not journal:
            raise HTTPException(409, "Original transaction journal cannot be reversed safely")
        for field in ("loan_interest_collected", "bc_regular_kist_penalty", "other_interest", "other_penalty", "loan_penalty_collected", "interest", "principal_repaid", "principal", "interest_accrued_delta"):
            value = original.get(field)
            if value is not None: doc[field] = -abs(float(value or 0))
        doc["payment_category"] = original.get("payment_category")
        doc["penalty_category"] = original.get("penalty_category")
        doc["original_type"] = original.get("type")
        for identity_field in ("share_id", "share_no", "period", "expected_amount", "loan_interest_key", "loan_penalty_key", "payment_month"):
            if original.get(identity_field) is not None:
                doc[identity_field] = original.get(identity_field)
        if original.get("type") == "contribution":
            doc["status"] = "reversed"
            if not doc.get("period") and original.get("payment_category") in (None, "monthly_kist"):
                original_date=original.get("date") or original.get("created_at")
                if isinstance(original_date,datetime): doc["period"]=original_date.strftime("%Y-%m")
    # Persist exact integer-paise copies of every monetary component. Legacy rupee
    # fields remain for API compatibility and existing clients.
    component_minor_fields = {
        "principal": "principal_minor", "principal_repaid": "principal_repaid_minor",
        "interest": "interest_minor", "loan_interest_collected": "loan_interest_minor",
        "loan_penalty_collected": "loan_penalty_minor", "bc_regular_kist_penalty": "bc_penalty_minor",
        "other_interest": "other_interest_minor", "other_penalty": "other_penalty_minor",
        "interest_accrued_delta": "interest_accrued_delta_minor",
    }
    for rupee_field, minor_field in component_minor_fields.items():
        if doc.get(rupee_field) is not None:
            doc[minor_field] = to_minor(doc[rupee_field])
    if journal: doc["journal_status"]="pending"
    try:
        r=await db.transactions.insert_one(doc)
        doc["_id"]=r.inserted_id
    except Exception as exc:
        # Handles concurrent duplicate idempotency submissions without posting twice.
        if idem:
            existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":idem})
            if existing:
                _assert_idempotent_tx_matches(existing, tenant_id, member_id, typ, idempotency_minor, account, extra)
                await _finalize_tx_side_effects(existing, user)
                return str(existing["_id"])
        raise exc
    await _finalize_tx_side_effects(doc, user)
    return str(doc["_id"])

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
    expected_per_share_minor=to_minor((tenant or {}).get("kist_per_share",500))
    expected_per_share=float(from_minor(expected_per_share_minor))
    member_query={"tenant_id":tenant_id,"active":True}
    if user.get("role")=="member":
        member_id=str(user.get("member_id") or "")
        if not member_id: raise HTTPException(403,"Member profile is required")
        member_query["_id"]=parse_oid(member_id)
    members=await db.members.find(member_query).sort("first_name",1).to_list(5000)
    # A member sees only their own Kist status; administrators receive the full
    # group roll-up so one member cannot inspect another member's payment history.
    member_ids=[str(m["_id"]) for m in members]
    shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort("share_no",1).to_list(20000) if member_ids else []
    shares_by_member={mid:[] for mid in member_ids}
    for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
    for member in members:
        mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
        if len(shares_by_member.get(mid,[]))<expected_count:
            shares_by_member[mid]=await ensure_member_shares(member)
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":period},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}]}},
        {"$group":{"_id":{"share_id":"$share_id","member_id":"$member_id","share_no":"$share_no"},"paid_minor":{"$sum":_mongo_amount_minor_expr()}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_legacy={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid_minor=int(x.get("paid_minor",0) or 0); sid=key.get("share_id")
        if sid: paid_by_share[str(sid)]=paid_minor
        elif key.get("member_id") is not None and key.get("share_no") is not None: paid_by_legacy[(str(key.get("member_id")),int(key.get("share_no")))]=paid_minor
    exp_row=await db.expenses.aggregate([
        {"$match":{"tenant_id":tenant_id,"date":{"$gte":start,"$lt":end}}},
        {"$group":{"_id":None,"total_minor":{"$sum":_mongo_amount_minor_expr()}}}
    ]).to_list(1)
    group_expenses=float(from_minor(int((exp_row[0] if exp_row else {}).get("total_minor",0) or 0)))
    member_expenses=None
    if user["role"]=="member":
        allocation_row=await db.transactions.aggregate([
            {"$match":{"tenant_id":tenant_id,"member_id":str(user.get("member_id") or ""),"type":"expense_allocation","date":{"$gte":start,"$lt":end}}},
            {"$group":{"_id":None,"total_minor":{"$sum":{"$abs":_mongo_amount_minor_expr()}}}}
        ]).to_list(1)
        member_expenses=float(from_minor(int((allocation_row[0] if allocation_row else {}).get("total_minor",0) or 0)))
    expected_minor=paid_minor=0; paid_shares=partial_shares=pending_shares=0
    for member in members:
        for share in shares_by_member.get(str(member["_id"]),[]):
            sid=str(share["_id"]); p_minor=max(0,int(paid_by_share.get(sid,0))+int(paid_by_legacy.get((str(member["_id"]),int(share["share_no"])),0)))
            expected_minor+=expected_per_share_minor; paid_minor+=min(p_minor,expected_per_share_minor); remaining_minor=max(0,expected_per_share_minor-p_minor)
            if remaining_minor<=0: paid_shares+=1
            elif p_minor>0: partial_shares+=1
            else: pending_shares+=1
    result={"period":period,"expected_total":float(from_minor(expected_minor)),"paid_total":float(from_minor(paid_minor)),"pending_total":float(from_minor(max(0,expected_minor-paid_minor))),"paid_shares":int(paid_shares),"partial_shares":int(partial_shares),"pending_shares":int(pending_shares),"active_members":len(members)}
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
    shares=await ensure_member_shares(member); expected_minor=to_minor(tenant.get("kist_per_share",500)); expected=float(from_minor(expected_minor))
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"member_id":member_id,"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":period},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}]}},
        {"$group":{"_id":{"share_id":"$share_id","share_no":"$share_no"},"paid_minor":{"$sum":_mongo_amount_minor_expr()}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_no={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid_minor=int(x.get("paid_minor",0) or 0)
        if key.get("share_id"): paid_by_share[str(key["share_id"])]=paid_minor
        elif key.get("share_no") is not None: paid_by_no[int(key["share_no"])]=paid_minor
    out=[]
    for share in shares:
        paid_minor=max(0,int(paid_by_share.get(str(share["_id"]),0))+int(paid_by_no.get(int(share["share_no"]),0) if str(share["_id"]) not in paid_by_share else 0))
        remaining_minor=max(0,expected_minor-paid_minor); paid=float(from_minor(paid_minor)); remaining=float(from_minor(remaining_minor))
        out.append({"share_id":str(share["_id"]),"share_no":int(share["share_no"]),"expected_amount":expected,"paid_amount":paid,"remaining_amount":remaining,"status":"paid" if remaining_minor<=0 else ("partial" if paid_minor>0 else "pending")})
    expected_total_minor=expected_minor*len(shares); paid_total_minor=sum(to_minor(x["paid_amount"]) for x in out); remaining_total_minor=sum(to_minor(x["remaining_amount"]) for x in out)
    return {"member_id":member_id,"period":period,"kist_per_share":expected,"shares":out,"expected_total":float(from_minor(expected_total_minor)),"paid_total":float(from_minor(paid_total_minor)),"remaining_total":float(from_minor(remaining_total_minor))}

@router.get("/{tenant_id}/monthly-kist-bulk-status")
async def monthly_kist_bulk_status(tenant_id: str, period: str, user=Depends(admin_user)):
    await tenant_guard(user, tenant_id); db=get_db()
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", period): raise HTTPException(400,"Period must be YYYY-MM")
    try: y,m=map(int,period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc)
    except Exception: raise HTTPException(400,"Invalid period")
    end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
    expected_minor=to_minor(tenant.get("kist_per_share",500)); expected=float(from_minor(expected_minor))
    members=await db.members.find({"tenant_id":tenant_id,"active":True}).sort("first_name",1).to_list(5000)
    member_ids=[str(m["_id"]) for m in members]
    shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort("share_no",1).to_list(20000) if member_ids else []
    shares_by_member={mid:[] for mid in member_ids}
    for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
    for member in members:
        mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
        if len(shares_by_member.get(mid,[]))<expected_count: shares_by_member[mid]=await ensure_member_shares(member)
    tx_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":period},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}]}},
        {"$group":{"_id":{"member_id":"$member_id","share_id":"$share_id","share_no":"$share_no"},"paid_minor":{"$sum":_mongo_amount_minor_expr()}}}
    ]).to_list(None)
    paid_by_share={}; paid_by_legacy={}
    for x in tx_rows:
        key=x.get("_id") or {}; paid_minor=int(x.get("paid_minor",0) or 0); sid=key.get("share_id")
        if sid: paid_by_share[str(sid)]=paid_minor
        elif key.get("member_id") is not None and key.get("share_no") is not None: paid_by_legacy[(str(key["member_id"]),int(key["share_no"]))]=paid_minor
    out=[]
    for member in members:
        rows=[]
        for sh in shares_by_member.get(str(member["_id"]),[]):
            paid_minor=max(0,int(paid_by_share.get(str(sh["_id"]),0))+int(paid_by_legacy.get((str(member["_id"]),int(sh["share_no"])),0)))
            remaining_minor=max(0,expected_minor-paid_minor); paid=float(from_minor(paid_minor)); remaining=float(from_minor(remaining_minor))
            rows.append({"share_id":str(sh["_id"]),"share_no":int(sh["share_no"]),"expected_amount":expected,"paid_amount":paid,"remaining_amount":remaining,"status":"paid" if remaining_minor<=0 else ("partial" if paid_minor>0 else "pending")})
        expected_total=float(from_minor(expected_minor*len(rows)))
        paid_total=float(from_minor(sum(to_minor(x["paid_amount"]) for x in rows)))
        remaining_total=float(from_minor(sum(to_minor(x["remaining_amount"]) for x in rows)))
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
    expected_minor=to_minor(tenant.get("kist_per_share",500)); expected=float(from_minor(expected_minor)); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    total_minor=0; results=[]; seen=set(); paid_members=set()
    for entry in body.entries:
        if not entry.allocations: continue
        member=await get_member(db,tenant_id,entry.member_id); shares=await ensure_member_shares(member); share_map={str(x["_id"]):x for x in shares}
        for allocation in entry.allocations:
            lock_key=f"kist-payment:{tenant_id}:{body.period}:{allocation.share_id}"
            await acquire_operation_lock(lock_key,90)
            try:
                if allocation.share_id in seen: raise HTTPException(400,"Duplicate share selected in bulk collection")
                seen.add(allocation.share_id)
                share=share_map.get(allocation.share_id)
                if not share: raise HTTPException(400,"One or more selected shares are not active for this member")
                share_idem=f"{body.idempotency_key}:{allocation.share_id}" if body.idempotency_key else None
                if share_idem:
                    existing_tx=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":share_idem})
                    if existing_tx:
                        _assert_idempotent_tx_matches(existing_tx,tenant_id,entry.member_id,"contribution",to_minor(allocation.amount),body.account,{"share_id":allocation.share_id,"payment_category":"monthly_kist","period":body.period})
                        await _finalize_tx_side_effects(existing_tx,user)
                        paid_amount_minor=_doc_amount_minor(existing_tx); paid_amount=float(from_minor(paid_amount_minor)); total_minor+=paid_amount_minor; paid_members.add(entry.member_id)
                        results.append({"id":str(existing_tx["_id"]),"member_id":entry.member_id,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"amount":paid_amount,"status":existing_tx.get("status","posted")})
                        continue
                y,m=map(int,body.period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
                paid_rows=await db.transactions.find({"tenant_id":tenant_id,"member_id":entry.member_id,"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":body.period},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}] }).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(500)
                already_minor=sum(_doc_amount_minor(x) for x in paid_rows)
                # The status screen can be stale if another admin posts concurrently.
                # Never over-collect: cap each bulk allocation to the live balance.
                try: allocation_result=allocate_kist_payment(expected_minor,already_minor,to_minor(allocation.amount),cap_to_remaining=True)
                except ValueError: continue
                post_minor=allocation_result["posted_minor"]; after_minor=allocation_result["cumulative_minor"]; status=allocation_result["status"]
                post_amount=float(from_minor(post_minor)); after=float(from_minor(after_minor))
                txid=await insert_tx(tenant_id,entry.member_id,"contribution",post_amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note,idempotency_key=share_idem,idempotency_amount_minor=to_minor(allocation.amount))
                total_minor+=post_minor; paid_members.add(entry.member_id); results.append({"id":txid,"member_id":entry.member_id,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"amount":post_amount,"status":status})
            finally:
                await release_operation_lock(lock_key)
    penalties=[]
    for mid in paid_members:
        pid=await _post_bc_penalty_once(tenant_id,mid,body.period,dt,body.account,user,body.note)
        if pid: penalties.append(pid)
    if results:
        await audit(tenant_id,user,"MONTHLY_KIST_BULK_POSTED","tenant",tenant_id,{"period":body.period,"entries":len(results),"amount":float(from_minor(total_minor))},event_key=f"monthly-kist-bulk:{tenant_id}:{body.idempotency_key}" if body.idempotency_key else None)
    return {"period":body.period,"entries":len(results),"total":float(from_minor(total_minor)),"results":results,"penalty_entries":len(penalties)}

@router.post("/{tenant_id}/monthly-kist")
async def monthly_kist(tenant_id:str,body:MonthlyKistCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    member=await get_member(db,tenant_id,body.member_id)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    shares=await ensure_member_shares(member); share_map={str(x["_id"]):x for x in shares}
    if len({a.share_id for a in body.allocations})!=len(body.allocations): raise HTTPException(400,"Duplicate share selected")
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    results=[]; total_minor=0
    for allocation in body.allocations:
        lock_key=f"kist-payment:{tenant_id}:{body.period}:{allocation.share_id}"
        await acquire_operation_lock(lock_key,90)
        try:
            share=share_map.get(allocation.share_id)
            if not share: raise HTTPException(400,"One or more selected shares are not active for this member")
            expected_minor=to_minor(tenant.get("kist_per_share",500)); expected=float(from_minor(expected_minor))
            share_idem=f"{body.idempotency_key}:{allocation.share_id}" if body.idempotency_key else None
            if share_idem:
                existing_tx=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":share_idem})
                if existing_tx:
                    _assert_idempotent_tx_matches(existing_tx,tenant_id,body.member_id,"contribution",to_minor(allocation.amount),body.account,{"share_id":allocation.share_id,"payment_category":"monthly_kist","period":body.period})
                    await _finalize_tx_side_effects(existing_tx,user)
                    existing_paid_minor=_doc_amount_minor(existing_tx); total_minor+=existing_paid_minor
                    results.append({"id":str(existing_tx["_id"]),"share_id":allocation.share_id,"share_no":int(share["share_no"]),"paid_amount":float(from_minor(existing_paid_minor)),"cumulative_paid":float(from_minor(to_minor(existing_tx.get("paid_amount",0) or 0))),"expected_amount":expected,"status":existing_tx.get("status","posted")})
                    continue
            y,m=map(int,body.period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
            paid_cursor=await db.transactions.find({"tenant_id":tenant_id,"member_id":body.member_id,"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":body.period},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}] }).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(500)
            already_paid_minor=sum(_doc_amount_minor(x) for x in paid_cursor)
            allocation_minor=to_minor(allocation.amount)
            try: allocation_result=allocate_kist_payment(expected_minor,already_paid_minor,allocation_minor)
            except ValueError as exc:
                remaining_minor=max(0,expected_minor-max(0,already_paid_minor))
                if remaining_minor<=0: raise HTTPException(409,f"Share {share['share_no']} is already fully paid for {body.period}") from exc
                raise HTTPException(400,f"Share {share['share_no']} can accept at most {float(from_minor(remaining_minor)):.2f} for {body.period}") from exc
            after_minor=allocation_result["cumulative_minor"]; status=allocation_result["status"]
            posted_amount=float(from_minor(allocation_result["posted_minor"])); after=float(from_minor(after_minor))
            txid=await insert_tx(tenant_id,body.member_id,"contribution",posted_amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note,idempotency_key=share_idem)
            total_minor+=allocation_minor
            results.append({"id":txid,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"paid_amount":posted_amount,"cumulative_paid":after,"expected_amount":expected,"status":status})
        finally:
            await release_operation_lock(lock_key)
    penalty_id=await _post_bc_penalty_once(tenant_id,body.member_id,body.period,dt,body.account,user,body.note)
    penalty_amount=0.0
    if penalty_id:
        prow=await db.transactions.find_one({"_id":parse_oid(penalty_id),"tenant_id":tenant_id},{"amount":1})
        penalty_amount=float((prow or {}).get("amount",0) or 0)
    await audit(tenant_id,user,"MONTHLY_KIST_BATCH_POSTED","member",body.member_id,{"period":body.period,"shares":len(results),"amount":float(from_minor(total_minor)),"bc_regular_kist_penalty_posted":bool(penalty_id)},event_key=f"monthly-kist:{tenant_id}:{body.idempotency_key}" if body.idempotency_key else None)
    return {"period":body.period,"member_id":body.member_id,"results":results,"total":float(from_minor(total_minor)),"bc_regular_kist_penalty":round(penalty_amount,2)}

@router.post("/{tenant_id}/money-in")
async def money_in(tenant_id:str,body:MoneyInCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    if body.member_id: await get_member(get_db(),tenant_id,body.member_id)
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    if not body.note.strip(): raise HTTPException(400,"Note / Reason is required for manual interest or penalty entries")
    is_penalty=body.type=="penalty"
    payment_category="other" if is_penalty else "other_interest"
    penalty_category = body.penalty_category if is_penalty else None
    penalty_fields = ({
        "bc_regular_kist_penalty": body.amount if penalty_category == "bc" else 0,
        "loan_penalty_collected": body.amount if penalty_category == "loan" else 0,
        "other_penalty": 0,
    } if is_penalty else {})
    return {"id":await insert_tx(tenant_id,body.member_id,body.type,body.amount,body.account,user,date=dt,note=body.note,payment_category=payment_category,penalty_category=penalty_category,other_interest=body.amount if body.type=="interest" else 0,**penalty_fields,idempotency_key=body.idempotency_key)}

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
    requested_minor=to_minor(amount) if amount else 0
    requested=float(from_minor(requested_minor)); rate=float(tenant.get("loan_interest_rate_per_month",2) or 0); months_count=max(1,int(months or 1))
    total_interest_minor=simple_interest_minor(requested_minor,rate,months_count) if requested_minor else 0
    total_interest=float(from_minor(total_interest_minor)); total_due_minor=requested_minor+total_interest_minor
    liquidity_ok=available>=reserve-0.01
    min_advance_apply_months=max(0,int(tenant.get("min_advance_apply_months",2) or 2))
    return {"member_id":member_id,"shares":shares,"share_value":share_value,"max_loan_multiplier":multiplier,"min_loan_amount":minimum,"min_advance_apply_months":min_advance_apply_months,"credit_limit":credit_limit,"active_account_balance":available,"min_group_reserve_balance":reserve,"funds_available_for_disbursement":round(max_by_funds,2),"liquidity_ok":liquidity_ok,"eligible":(liquidity_ok and requested<=credit_limit and requested>=minimum and requested<=max_by_funds) if requested else liquidity_ok,"interest_rate_per_month":rate,"total_interest":total_interest,"total_due":float(from_minor(total_due_minor)),"estimated_emi":float(from_minor(allocate_minor(total_due_minor,1,months_count))) if requested_minor else 0}

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

@router.get("/{tenant_id}/accounting-reconciliation")
async def accounting_reconciliation(tenant_id: str, user=Depends(admin_user)):
    """Read-only production diagnostic for financial posting completeness.

    This endpoint is read-only. Startup migration journals supported legacy
    records, routes unknown transaction types through suspense, and blocks
    transfers whose paired accounts cannot be inferred. Any blocked or pending
    rows remain visible here instead of being reported as clean.
    """
    await tenant_guard(user, tenant_id)
    db = get_db()
    journalable_types = ["contribution", "loan_disbursement", "loan_repayment", "interest",
                         "penalty", "expense", "transfer", "cash_bank_transfer", "other_income",
                         "misc_income", "investment_income", "dividend_income", "donation_income",
                         "reversal", "asset_purchase", "asset_sale", "investment_disbursement"]
    async def count_posted_sources_missing_journals(collection, source_type, prefix, *, transaction_source=False):
        source_match = {"tenant_id": tenant_id, "journal_status": "posted"}
        if transaction_source:
            source_match["type"] = {"$ne": "expense_allocation"}
        pipeline = [
            {"$match": source_match},
            {"$lookup": {
                "from": "journal_entries",
                "let": {"source_key": {"$concat": [prefix, {"$toString": "$_id"}]}, "tenant": "$tenant_id", "source_type": source_type},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [
                        {"$eq": ["$source_key", "$$source_key"]},
                        {"$eq": ["$tenant_id", "$$tenant"]},
                        {"$eq": ["$source_type", "$$source_type"]},
                        {"$eq": ["$status", "posted"]},
                    ]}}},
                    {"$limit": 1},
                ],
                "as": "matched_journal",
            }},
            {"$match": {"matched_journal": {"$size": 0}}},
            {"$count": "count"},
        ]
        result = await collection.aggregate(pipeline).to_list(1)
        return int((result[0] if result else {}).get("count", 0) or 0)

    tx_count, expense_count, journal_count, posted_journal_count, pending_journal_count, unbalanced_journal_count, pending_tx_side_effects, pending_expense_side_effects, pending_loan_applications, pending_reversal_applications, unjournaled_legacy_count, unjournaled_expenses_count, missing_tx_journals, missing_expense_journals, blocked_tx_journals, blocked_expense_journals = await asyncio.gather(
        db.transactions.count_documents({"tenant_id": tenant_id}),
        db.expenses.count_documents({"tenant_id": tenant_id}),
        db.journal_entries.count_documents({"tenant_id": tenant_id}),
        db.journal_entries.count_documents({"tenant_id": tenant_id, "status": "posted"}),
        db.journal_entries.count_documents({"tenant_id": tenant_id, "$or": [{"status": {"$in": ["pending", "failed"]}}, {"status": {"$exists": False}}]}),
        db.journal_entries.count_documents({"tenant_id": tenant_id, "$or": [{"balanced": {"$ne": True}}, {"$expr": {"$ne": ["$debit_minor", "$credit_minor"]}}]}),
        db.transactions.count_documents({"tenant_id": tenant_id, "side_effects_status": "pending"}),
        db.expenses.count_documents({"tenant_id": tenant_id, "side_effects_status": "pending"}),
        db.transactions.count_documents({"tenant_id": tenant_id, "type": "loan_repayment", "loan_apply_status": "pending"}),
        db.transactions.count_documents({"tenant_id": tenant_id, "type": "reversal", "reversal_apply_status": "pending"}),
        db.transactions.count_documents({"tenant_id": tenant_id, "type": {"$ne": "expense_allocation"}, "$or": [{"journal_status": {"$exists": False}}, {"journal_status": None}]}),
        db.expenses.count_documents({"tenant_id": tenant_id, "$or": [{"journal_status": {"$exists": False}}, {"journal_status": None}]}),
        count_posted_sources_missing_journals(db.transactions, "transaction", "tx:", transaction_source=True),
        count_posted_sources_missing_journals(db.expenses, "expense", "expense:"),
        db.transactions.count_documents({"tenant_id": tenant_id, "journal_status": {"$in": ["blocked_missing_transfer_legs", "blocked_missing_original_journal", "blocked_error"]}}),
        db.expenses.count_documents({"tenant_id": tenant_id, "journal_status": {"$in": ["blocked_invalid_expense", "blocked_error"]}}),
    )
    issues = {
        "pending_journals": pending_journal_count,
        "unbalanced_journals": unbalanced_journal_count,
        "posted_transactions_missing_journal": missing_tx_journals,
        "posted_expenses_missing_journal": missing_expense_journals,
        "blocked_transaction_journals": blocked_tx_journals,
        "blocked_expense_journals": blocked_expense_journals,
        "pending_transaction_side_effects": pending_tx_side_effects,
        "pending_expense_side_effects": pending_expense_side_effects,
        "pending_loan_applications": pending_loan_applications,
        "pending_reversal_applications": pending_reversal_applications,
        "legacy_transactions_without_journal_marker": unjournaled_legacy_count,
        "legacy_expenses_without_journal_marker": unjournaled_expenses_count,
        "blocked_transaction_journals": blocked_tx_journals,
        "blocked_expense_journals": blocked_expense_journals,
    }
    return {
        "tenant_id": tenant_id,
        "status": "attention_required" if any(issues.values()) else "clean_for_checked_invariants",
        "source_counts": {"transactions": tx_count, "expenses": expense_count},
        "journal_counts": {"total": journal_count, "posted": posted_journal_count},
        "issues": issues,
        "note": "Read-only diagnostics. Blocked rows require explicit account/source mapping; this endpoint does not certify cash/bank balances against external bank statements.",
    }


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
    if user.get("role")=="member" and view in {"expenses","outflows","closing","register","active-loans"}:
        raise HTTPException(403,"This group-wide accounting view is available to group administrators only")
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
            # Drill-down totals must be global for the selected filter, not just
            # the current 10-row page; otherwise cards appear to repeat values or
            # change when the user scrolls to the next page.
            if view=="register" and direction in ("inflows","outflows"):
                filtered_total_rows=await db.financial_feed.aggregate([
                    {"$match":q},
                    {"$group":{"_id":None,"total":{"$sum":{"$abs":{"$ifNull":["$amount",0]}}}}},
                ]).to_list(1)
                filtered_total=round(float((filtered_total_rows[0] if filtered_total_rows else {}).get("total",0) or 0),2)
                display_credits=filtered_total if direction=="inflows" else 0.0
                display_debits=filtered_total if direction=="outflows" else 0.0
            else:
                display_credits=0.0; display_debits=0.0
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
            from ..services import profit_report
            # Members are entitled to their allocated profit only; never expose
            # group-wide income buckets or other members' income entries.
            if user.get("role") == "member":
                member_id = str(user.get("member_id") or "")
                report = await profit_report(tenant_id)
                member_shares, all_shares = await asyncio.gather(
                    db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"}),
                    db.shares.count_documents({"tenant_id":tenant_id,"status":"active"}),
                )
                allocated = allocate_minor(int(report["net_profit_minor"]), int(member_shares or 0), int(all_shares or 0))
                return {"view":view,"entries":[],"has_more":False,"total_entries":0,
                        "grand_total":round(allocated/100,2),"total_interest_and_penalties":0,
                        "total_expenses":0,"sources":{},"personal_view":True}
            report = await profit_report(tenant_id, entry_offset=offset, entry_limit=page_size)
            entries=[]; mids=[]
            labels={"bank_interest":"Bank Interest","loan_interest":"Loan Interest","other_interest":"Other Interest",
                    "loan_penalties":"Loan Penalty","bc_penalties":"BC Kist Penalty","other_penalties":"Other Penalty","other_income":"Other Income"}
            for row in report["entries"]:
                parts=row.get("profit_components_minor",{})
                active=[(key, int(parts.get(key,0) or 0)) for key in labels if int(parts.get(key,0) or 0)]
                source=labels[active[0][0]] if active else "Other Income"
                member_id=row.get("member_id")
                if member_id:
                    try: mids.append(parse_oid(str(member_id)))
                    except Exception: pass
                entries.append({"id":str(row.get("_id")),"date":str(row.get("date") or row.get("created_at")),
                                "member_id":str(member_id) if member_id else None,"member_name":"",
                                "type":row.get("type","profit"),"amount":round(int(row.get("profit_amount_minor",0) or 0)/100,2),
                                "account":row.get("account","cash"),"source":source,"reason":row.get("note","") or source})
            member_docs=await db.members.find({"tenant_id":tenant_id,"_id":{"$in":mids}}).to_list(len(mids) or 1) if mids else []
            names={str(m["_id"]):f'{m.get("first_name","")} {m.get("last_name","")}'.strip() for m in member_docs}
            for entry in entries:
                entry["member_name"]=names.get(str(entry.get("member_id")),"")
            component_totals={key:round(int(value)/100,2) for key,value in report["components_minor"].items()}
            expenses_total=round(int(report["expense_minor"])/100,2)
            earned=round(int(report["income_minor"])/100,2)
            return {"view":view,"entries":entries,"has_more":offset+len(entries)<int(report["income_entries_count"]),
                    "total_entries":int(report["income_entries_count"]),"grand_total":round(int(report["net_profit_minor"])/100,2),
                    "total_interest_and_penalties":round(earned-component_totals.get("other_income",0),2),
                    "total_expenses":expenses_total,
                    "sources":{"bank_interest":component_totals.get("bank_interest",0),"loan_interest":component_totals.get("loan_interest",0),
                               "other_interest":component_totals.get("other_interest",0),"bc_penalties":component_totals.get("bc_penalties",0),
                               "loan_penalties":component_totals.get("loan_penalties",0),"other_penalties":component_totals.get("other_penalties",0),
                               "other_income":component_totals.get("other_income",0)}}

        if view=="interest":
            # The profit source cards pass a filter key. Honor it in both the
            # entry query and total expression so each card opens its own ledger.
            supported_filters = {"bank_interest", "other_interest", "loan_interest",
                                 "bc_penalties", "loan_penalties", "other_penalties"}
            category = filter if filter in supported_filters else None
            base = {"tenant_id": tenant_id, "source_type": "transaction"}
            if category == "bank_interest":
                base["$or"] = [
                    {"type": "interest", "account": "bank", "amount": {"$ne": 0}},
                    {"type": "reversal", "original_type": "interest", "account": "bank"},
                ]
                source_label = "Bank Interest"
                amount_expr = {"$ifNull": ["$amount", 0]}
            elif category == "other_interest":
                base["$or"] = [
                    {"type": "interest", "account": {"$ne": "bank"}, "amount": {"$ne": 0}},
                    {"type": "reversal", "original_type": "interest", "account": {"$ne": "bank"}},
                ]
                source_label = "Other Interest"
                amount_expr = {"$ifNull": ["$amount", 0]}
            elif category == "loan_interest":
                base["$or"] = [
                    {"type": "loan_repayment", "loan_interest_collected": {"$ne": 0}},
                    {"type": "reversal", "original_type": "loan_repayment", "loan_interest_collected": {"$ne": 0}},
                ]
                source_label = "Member Loan Interest"
                amount_expr = {"$ifNull": ["$loan_interest_collected", 0]}
            elif category == "bc_penalties":
                base["$or"] = [
                    {"type": "penalty", "$or": [{"payment_category": "bc"}, {"penalty_category": "bc"}, {"bc_regular_kist_penalty": {"$ne": 0}}]},
                    {"type": "reversal", "original_type": "penalty", "$or": [{"payment_category": "bc"}, {"penalty_category": "bc"}, {"bc_regular_kist_penalty": {"$ne": 0}}]},
                ]
                source_label = "BC Kist Penalty"
                amount_expr = {"$ifNull": ["$amount", 0]}
            elif category == "loan_penalties":
                base["$or"] = [
                    {"type": "loan_repayment", "loan_penalty_collected": {"$ne": 0}},
                    {"type": "reversal", "original_type": "loan_repayment", "loan_penalty_collected": {"$ne": 0}},
                    {"type": "penalty", "$or": [{"payment_category": "loan"}, {"penalty_category": "loan"}, {"loan_penalty_collected": {"$ne": 0}}]},
                    {"type": "reversal", "original_type": "penalty", "$or": [{"payment_category": "loan"}, {"penalty_category": "loan"}, {"loan_penalty_collected": {"$ne": 0}}]},
                ]
                source_label = "Loan Penalty"
                amount_expr = {"$cond": [{"$or": [{"$eq": ["$type", "loan_repayment"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "loan_repayment"]}]}]}, {"$ifNull": ["$loan_penalty_collected", 0]}, {"$ifNull": ["$amount", 0]}]}
            elif category == "other_penalties":
                base["$or"] = [
                    {"type": "penalty", "$or": [{"payment_category": "other"}, {"penalty_category": "other"}, {"other_penalty_value": {"$ne": 0}}]},
                    {"type": "reversal", "original_type": "penalty", "$or": [{"payment_category": "other"}, {"penalty_category": "other"}, {"other_penalty_value": {"$ne": 0}}]},
                ]
                source_label = "Other Penalty"
                amount_expr = {"$ifNull": ["$amount", 0]}
            else:
                base["$or"] = [
                    {"type": "interest", "amount": {"$ne": 0}},
                    {"type": "loan_repayment", "loan_interest_collected": {"$ne": 0}},
                    {"type": "reversal", "original_type": "interest"},
                    {"type": "reversal", "original_type": "loan_repayment", "loan_interest_collected": {"$ne": 0}},
                ]
                source_label = "Interest"
                amount_expr = {"$cond": [{"$or": [{"$eq": ["$type", "loan_repayment"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "loan_repayment"]}]}]}, {"$ifNull": ["$loan_interest_collected", 0]}, {"$ifNull": ["$amount", 0]}]}
            if user.get("role") == "member":
                base["member_id"] = str(user.get("member_id") or "")
            rows = await db.financial_feed.find(base).sort([("date", -1), ("created_at", -1), ("_id", -1)]).skip(offset).limit(page_size).to_list(page_size)
            totals = await db.financial_feed.aggregate([{"$match": base}, {"$group": {"_id": None, "total": {"$sum": amount_expr}, "count": {"$sum": 1}}}]).to_list(1)
            summary = totals[0] if totals else {}
            mids=[]
            for row in rows:
                if row.get("member_id"):
                    try: mids.append(parse_oid(str(row["member_id"])) )
                    except Exception: pass
            member_docs = await db.members.find({"tenant_id": tenant_id, "_id": {"$in": mids}}).to_list(len(mids) or 1) if mids else []
            names = {str(m["_id"]): f'{m.get("first_name", "")} {m.get("last_name", "")}'.strip() for m in member_docs}
            entries=[]
            for row in rows:
                typ = row.get("type", "interest")
                is_loan_repayment = typ == "loan_repayment" or (typ == "reversal" and row.get("original_type") == "loan_repayment")
                if category == "loan_interest" or (category is None and is_loan_repayment):
                    amount = float(row.get("loan_interest_collected", 0) or 0)
                    item_source = "Member Loan Interest"
                elif category == "loan_penalties":
                    amount = float(row.get("loan_penalty_collected", 0) or 0) if is_loan_repayment else float(row.get("amount", 0) or 0)
                    item_source = source_label
                else:
                    amount = float(row.get("amount", 0) or 0)
                    if category:
                        item_source = source_label
                    elif is_loan_repayment:
                        item_source = "Member Loan Interest"
                    else:
                        item_source = "Bank Interest" if row.get("account") == "bank" else "Other Interest"
                entries.append({"id": str(row.get("source_id") or row.get("_id")), "date": str(row.get("date") or row.get("created_at")), "member_id": row.get("member_id"), "member_name": names.get(str(row.get("member_id")), ""), "type": typ, "amount": round(amount, 2), "account": row.get("account", "cash"), "source": item_source})
            total = int(summary.get("count", 0) or 0)
            return {"view": view, "filter": category, "entries": entries, "has_more": offset + len(entries) < total, "total_entries": total, "grand_total": round(float(summary.get("total", 0) or 0), 2)}

    if view=="active-loans":
        loan_query={"tenant_id":tenant_id,"status":"active"}
        if user.get("role")=="member": loan_query["member_id"]=str(user.get("member_id") or "")
        rows=await db.loans.find(loan_query).sort("created_at",-1).to_list(5000)
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
        if user.get("role")=="member": member_query["_id"]=parse_oid(str(user.get("member_id") or ""))
        members=await db.members.find(member_query).sort([("first_name",1),("last_name",1),("_id",1)]).to_list(5000)
        member_ids=[str(x["_id"]) for x in members]
        shares=await db.shares.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"status":"active"}).sort([("member_id",1),("share_no",1),("_id",1)]).to_list(20000) if member_ids else []
        shares_by_member={mid:[] for mid in member_ids}
        for sh in shares: shares_by_member.setdefault(str(sh["member_id"]),[]).append(sh)
        for member in members:
            mid=str(member["_id"]); expected_count=max(1,int(member.get("shares",1) or 1))
            if len(shares_by_member.get(mid,[]))<expected_count: shares_by_member[mid]=await ensure_member_shares(member)
        tx=await db.transactions.find({"tenant_id":tenant_id,"member_id":{"$in":member_ids},"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":p},{"$and":[{"period":None},{"date":{"$gte":start,"$lt":end}}]}]}]}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(20000) if member_ids else []
        paid={}
        for x in tx:
            key=(str(x.get("member_id")),str(x.get("share_id") or f'legacy:{x.get("share_no")}'))
            paid[key]=paid.get(key,0)+float(x.get("amount",0) or 0)
        rows=[]
        for mbr in members:
            mid=str(mbr["_id"]); mshares=shares_by_member.get(mid,[]); share_rows=[]
            for sh in mshares:
                key=(mid,str(sh["_id"])); amount=max(0.0,round(paid.get(key,0),2)); remaining=round(max(0,expected-amount),2)
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
        source_labels={"bank_interest":"Bank Interest","loan_interest":"Loan Interest","other_interest":"Other Interest","loan_penalties":"Loan Penalty","bc_penalties":"BC Penalty","other_penalties":"Other Penalty","other_income":"Other Income"}
        source_notes={"bank_interest":"Bank interest income","loan_interest":"Loan EMI interest","other_interest":"Other interest income","loan_penalties":"Loan EMI penalty","bc_penalties":"BC installment penalty","other_penalties":"Other penalty income","other_income":"Other income"}
        for x in tx_rows:
            if not tx_real(x): continue
            components=profit_components(x)
            for component,label in source_labels.items():
                amount_minor=components.get(component,0)
                if amount_minor != 0:
                    entries.append({**base_tx(x),"source":label,"reason":x.get("note") or source_notes[component],"amount":round(amount_minor/100,2)})
        component_totals={key:round(sum(x["amount"] for x in entries if x["source"]==label),2) for key,label in source_labels.items()}
        gross_profit=round(sum(component_totals.values()),2)
        expenses_total=round(sum(abs(float(x.get("amount",0) or 0)) for x in expense_rows),2)
        # Use the same canonical paise arithmetic as dashboard and Analytics.
        profit_minor=net_profit_from_buckets_minor(
            bank_interest=component_totals.get("bank_interest",0),
            loan_interest=component_totals.get("loan_interest",0),
            other_interest=component_totals.get("other_interest",0),
            loan_penalties=component_totals.get("loan_penalties",0),
            bc_penalties=component_totals.get("bc_penalties",0),
            other_penalties=component_totals.get("other_penalties",0),
            other_income=component_totals.get("other_income",0), expenses=expenses_total,
        )
        interest_and_penalties=round(gross_profit-component_totals.get("other_income",0),2)
        return {"view":view,"entries":entries[offset:offset+page_size],"has_more":offset+page_size<len(entries),"total_entries":len(entries),"grand_total":round(profit_minor/100,2),"total_interest_and_penalties":interest_and_penalties,"total_expenses":expenses_total,"sources":{"bank_interest":component_totals.get("bank_interest",0),"loan_interest":component_totals.get("loan_interest",0),"other_interest":component_totals.get("other_interest",0),"bc_penalties":component_totals.get("bc_penalties",0),"loan_penalties":component_totals.get("loan_penalties",0),"other_penalties":component_totals.get("other_penalties",0),"other_income":component_totals.get("other_income",0)}}

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
        {"$match":{"tenant_id":tenant_id,"member_id":{"$in":member_ids},"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]}},
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
    current_period=now.strftime("%Y-%m")
    kist_rows=await db.transactions.aggregate([
        {"$match":{"tenant_id":tenant_id,"member_id":{"$in":member_ids},"$and":[{"$or":[{"type":"contribution"},{"type":"reversal","original_type":"contribution"}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":None},{"payment_category":{"$exists":False}}]},{"$or":[{"period":current_period},{"$and":[{"period":None},{"date":{"$gte":month_start,"$lt":month_end}}]}]}]}},
        {"$group":{"_id":"$member_id","paid":{"$sum":{"$convert":{"input":{"$ifNull":["$amount",0]},"to":"double","onError":0,"onNull":0}}}}}
    ]).to_list(None) if member_ids else []
    kist_by_member={str(x["_id"]):max(0.0,float(x.get("paid",0) or 0)) for x in kist_rows}
    group_settings_row=await group_settings(tenant_id)
    kist_per_share=float(group_settings_row.get("kist_per_share",500) or 500)
    member_ledgers=[]
    for m in member_rows:
        mid=str(m["_id"]); c=contrib_by_member.get(mid,{}) ; l=loan_by_member.get(mid,{})
        active_share_count=max(1,int(m.get("active_shares_count",m.get("shares",1)) or 1))
        expected_kist=round(kist_per_share*active_share_count,2)
        member_ledgers.append({"member_id":mid,"savings":round(float(c.get("savings",0) or 0),2),"transaction_count":int(c.get("transactions",0) or 0),"principal":round(float(l.get("principal",0) or 0),2),"interest":round(float(l.get("interest",0) or 0),2),"active_loans":int(l.get("loans",0) or 0),"kist_paid":kist_by_member.get(mid,0)>=expected_kist-0.009})
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
    await tenant_guard(user,tenant_id); db=get_db(); lock_key=f"transaction-reversal:{tenant_id}:{transaction_id}"; await acquire_operation_lock(lock_key,90)
    loan_lock_key=None
    try:
        source=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
        if not source: raise HTTPException(404,"Transaction not found")
        if source.get("type")=="reversal" or source.get("reversal_of"): raise HTTPException(400,"A reversal cannot be reversed")
        if source.get("type")=="contribution" and source.get("payment_category") in (None,"monthly_kist") and not source.get("share_id") and not source.get("share_no") and source.get("member_id"):
            active_shares=await db.shares.find({"tenant_id":tenant_id,"member_id":str(source.get("member_id")),"status":"active"}).sort([("share_no",1)]).to_list(3)
            if len(active_shares)==1:
                source["share_id"]=str(active_shares[0]["_id"]); source["share_no"]=int(active_shares[0].get("share_no",1) or 1)
            elif len(active_shares)>1:
                raise HTTPException(409,"This legacy Kist payment has no share allocation metadata. Reconcile its share allocation before reversing it.")
        if source.get("type") == "loan_disbursement":
            raise HTTPException(409, "Loan disbursements must be cancelled through a dedicated loan cancellation workflow; generic reversal is not safe")
        if source.get("type") == "loan_repayment":
            loan_id = str(source.get("loan_id") or "")
            candidate_loan_lock=f"loan-payment:{tenant_id}:{loan_id}"
            await acquire_operation_lock(candidate_loan_lock,90)
            loan_lock_key=candidate_loan_lock
            # Refresh after obtaining the shared loan lock; an earlier read can
            # become stale while a repayment request is completing.
            source=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
            if not source: raise HTTPException(404,"Transaction not found")
            if source.get("loan_apply_status")=="pending":
                await _apply_loan_repayment_once(tenant_id,source)
                source=await db.transactions.find_one({"_id":parse_oid(transaction_id),"tenant_id":tenant_id})
            loan = await db.loans.find_one({"_id":parse_oid(loan_id),"tenant_id":tenant_id}) if loan_id else None
            if not loan: raise HTTPException(409, "Loan not found for this repayment")
            applied = [str(x) for x in loan.get("applied_repayment_ids", [])]
            source_id = str(source.get("_id"))
            if source_id not in applied and source.get("loan_apply_status") not in ("pending", "not_applied"):
                raise HTTPException(409, "This legacy repayment has no safe reversal marker. Reconcile the loan ledger before reversing it.")
            if source_id in applied and (not applied or applied[-1] != source_id):
                raise HTTPException(409, "Only the latest loan repayment can be reversed. Reverse later repayments first.")
        already=await db.transactions.find_one({"tenant_id":tenant_id,"reversal_of":transaction_id})
        if already: raise HTTPException(409,"Transaction is already reversed")
        original_journal=await db.journal_entries.find_one({"tenant_id":tenant_id,"source_key":f"tx:{transaction_id}","balanced":True})
        if not original_journal or original_journal.get("status") != "posted":
            original_journal=await _post_transaction_journal(source)
        if not original_journal or not original_journal.get("balanced") or original_journal.get("debit_minor") != original_journal.get("credit_minor"):
            raise HTTPException(400,"This transaction has no valid balanced journal and cannot be reversed safely")
        amount=-float(source.get("amount",0) or 0)
        now=datetime.now(timezone.utc); key=f"reversal:{transaction_id}"
        txid=await insert_tx(tenant_id,source.get("member_id"),"reversal",amount,source.get("account","cash"),user,reversal_of=transaction_id,original_type=source.get("type"),loan_id=source.get("loan_id"),share_id=source.get("share_id"),share_no=source.get("share_no"),period=source.get("period"),date=now,note=f"Reversal of {source.get('transaction_ref') or transaction_id}",idempotency_key=key,reversal_apply_status="pending" if source.get("type") == "loan_repayment" else "not_applicable")
        await audit(tenant_id,user,"TRANSACTION_REVERSED","transaction",transaction_id,{"reversal_id":txid})
        return {"ok":True,"id":txid,"reversal_of":transaction_id}
    finally:
        if loan_lock_key:
            await release_operation_lock(loan_lock_key)
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
            if (str(existing.get("member_id")) != str(body.member_id)
                    or to_minor(existing.get("principal", 0) or 0) != to_minor(body.principal)
                    or int(existing.get("months", 0) or 0) != int(body.months)
                    or to_minor(existing.get("interest_rate", 0) or 0) != to_minor(body.interest_rate if body.interest_rate is not None else existing.get("interest_rate", 0))):
                raise HTTPException(409, "Idempotency key was already used for a different loan request")
            await insert_tx(tenant_id,existing.get("member_id"),"loan_disbursement",-float(existing.get("principal",0) or 0),existing.get("account",body.account),user,loan_id=str(existing["_id"]),date=existing.get("start_date") or existing.get("created_at"),note=existing.get("purpose",body.purpose),principal=-float(existing.get("principal",0) or 0),idempotency_key=f"loan-disbursement:{existing['_id']}")
            await db.loans.update_one({"_id":existing["_id"],"tenant_id":tenant_id},{"$set":{"disbursement_status":"complete","disbursement_completed_at":datetime.now(timezone.utc)}})
            await audit(tenant_id,user,"LOAN_CREATED","loan",str(existing["_id"]),{"principal":existing.get("principal",0)},event_key=f"loan-created:{existing['_id']}")
            interest=float(existing.get("expected_interest",0) or 0)
            return {"id":str(existing["_id"]),"credit_limit":0,"total_interest":interest,"total_due":round(float(existing.get("principal",0) or 0)+interest,2)}
    lock_key=f"loan-disbursement:{tenant_id}"
    await acquire_operation_lock(lock_key,45)
    try:
        m=await get_member(db,tenant_id,body.member_id); tenant=await group_settings(tenant_id); now=datetime.now(timezone.utc)
        shares=int(m.get("active_shares_count",m.get("shares",1)) or 1); share_value=float(tenant.get("kist_per_share",500) or 500); multiplier=float(tenant.get("max_loan_multiplier",20) or 20); minimum=float(tenant.get("min_loan_amount",10000) or 10000); limit=round(max(minimum,shares*share_value*multiplier),2)
        summary=await tenant_summary(tenant_id,None); reserve=float(tenant.get("min_group_reserve_balance",0) or 0); available=float(summary.get("active_account_balance",0) or 0)
        principal_minor=to_minor(body.principal); principal_amount=float(from_minor(principal_minor))
        if principal_amount<minimum: raise HTTPException(400,f"Minimum loan amount is ₹{minimum:,.2f}")
        if principal_amount>limit+0.01: raise HTTPException(400,f"Loan exceeds member credit limit of ₹{limit:,.2f}")
        if available-principal_amount<reserve-0.01: raise HTTPException(400,"Insufficient Group Funds")
        rate=body.interest_rate if body.interest_rate is not None else float(tenant.get("loan_interest_rate_per_month",2) or 0)
        expected_interest_minor=simple_interest_minor(principal_minor,rate,body.months); interest=float(from_minor(expected_interest_minor))
        start_dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
        configured_penalty=tenant.get("loan_per_day_penalty")
        configured_penalty=float(1.0 if configured_penalty is None else configured_penalty)
        if configured_penalty < 0: raise HTTPException(400,"Loan per-day penalty cannot be negative")
        loan={"tenant_id":tenant_id,"member_id":body.member_id,"principal":principal_amount,"interest_rate":rate,"months":body.months,"expected_interest":interest,"interest_accrued":0.0,"principal_paid":0.0,"interest_paid":0.0,"loan_interest_collected":0.0,"loan_penalty_collected":0.0,"principal_repaid":0.0,"status":"active","purpose":body.purpose,"account":body.account,"created_at":now,"start_date":start_dt,"idempotency_key":body.idempotency_key,"disbursement_status":"pending","last_interest_accrual_month":start_dt.strftime("%Y-%m"),"loan_due_date":int(tenant.get("loan_due_date",10) or 10),"loan_per_day_penalty":configured_penalty,"loan_penalty_policy_version":2,"loan_penalty_compounding":False}
        loan.update({"principal_minor":principal_minor,"expected_interest_minor":expected_interest_minor,
                     "principal_paid_minor":0,"interest_paid_minor":0,"interest_accrued_minor":0,
                     "loan_interest_collected_minor":0,"loan_penalty_collected_minor":0,"principal_repaid_minor":0})
        r=await db.loans.insert_one(loan)
        await insert_tx(tenant_id,body.member_id,"loan_disbursement",-principal_amount,body.account,user,loan_id=str(r.inserted_id),date=start_dt,note=body.purpose,principal=-principal_amount,idempotency_key=f"loan-disbursement:{r.inserted_id}")
        await db.loans.update_one({"_id":r.inserted_id,"tenant_id":tenant_id},{"$set":{"disbursement_status":"complete","disbursement_completed_at":datetime.now(timezone.utc)}})
        await audit(tenant_id,user,"LOAN_CREATED","loan",str(r.inserted_id),{"principal":body.principal,"credit_limit":limit},event_key=f"loan-created:{r.inserted_id}")
        return {"id":str(r.inserted_id),"credit_limit":limit,"total_interest":interest,"total_due":float(from_minor(principal_minor+expected_interest_minor))}
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

async def _ensure_loan_minor_balances(tenant_id: str, loan: dict):
    """Lazily backfill exact paise counters for a legacy loan without resetting existing counters."""
    db = get_db()
    pairs = {
        "principal_minor": ("principal", 0),
        "expected_interest_minor": ("expected_interest", 0),
        "principal_paid_minor": ("principal_paid", 0),
        "interest_paid_minor": ("interest_paid", 0),
        "interest_accrued_minor": ("interest_accrued", loan.get("expected_interest", 0)),
        "loan_interest_collected_minor": ("loan_interest_collected", 0),
        "loan_penalty_collected_minor": ("loan_penalty_collected", 0),
        "principal_repaid_minor": ("principal_repaid", 0),
    }
    updates = {}
    for minor_field, (rupee_field, fallback) in pairs.items():
        minor_value = loan.get(minor_field)
        rupee_value = loan.get(rupee_field)
        if minor_value is None:
            value = rupee_value if rupee_value is not None else fallback
            updates[minor_field] = to_minor(value)
            # Older loan-payment code treated missing accrued-interest as the
            # scheduled expected interest. Materialize that exact legacy default
            # in both representations so the next $inc cannot split rupees/paise.
            if rupee_field == "interest_accrued" and rupee_value is None:
                updates[rupee_field] = float(from_minor(to_minor(fallback)))
        elif rupee_value is None:
            updates[rupee_field] = float(from_minor(int(minor_value)))
    if updates:
        # Only fill missing/null keys; never overwrite a counter another request
        # has already initialized or advanced.
        for field, value in updates.items():
            await db.loans.update_one(
                {"_id": loan["_id"], "tenant_id": tenant_id, "$or": [{field: {"$exists": False}}, {field: None}]},
                {"$set": {field: value}},
            )
        loan = await db.loans.find_one({"_id": loan["_id"], "tenant_id": tenant_id}) or loan
    return loan


async def _apply_loan_repayment_once(tenant_id: str, tx: dict):
    """Apply a persisted repayment to its loan at most once, even after retries."""
    db=get_db(); loan_id=str(tx.get("loan_id") or ""); txid=str(tx.get("_id") or "")
    if not loan_id or not txid: raise HTTPException(409,"Repayment is missing its loan reference")
    loan=await db.loans.find_one({"_id":parse_oid(loan_id),"tenant_id":tenant_id})
    if not loan: raise HTTPException(404,"Loan not found for this repayment")
    loan=await _ensure_loan_minor_balances(tenant_id,loan)
    principal_minor=int(tx["principal_repaid_minor"]) if tx.get("principal_repaid_minor") is not None else to_minor(tx.get("principal_repaid",tx.get("principal",0)) or 0)
    interest_minor=int(tx["loan_interest_minor"]) if tx.get("loan_interest_minor") is not None else to_minor(tx.get("loan_interest_collected",tx.get("interest",0)) or 0)
    penalty_minor=int(tx["loan_penalty_minor"]) if tx.get("loan_penalty_minor") is not None else to_minor(tx.get("loan_penalty_collected",0) or 0)
    accrued_minor=int(tx["interest_accrued_delta_minor"]) if tx.get("interest_accrued_delta_minor") is not None else to_minor(tx.get("interest_accrued_delta",0) or 0)
    principal=float(from_minor(principal_minor)); interest=float(from_minor(interest_minor))
    penalty=float(from_minor(penalty_minor)); accrued=float(from_minor(accrued_minor))
    update={"$inc":{
        "principal_paid":round(principal,2),"interest_paid":round(interest,2),
        "interest_accrued":round(accrued,2),"loan_interest_collected":round(interest,2),
        "loan_penalty_collected":round(penalty,2),"principal_repaid":round(principal,2),
        "principal_paid_minor":principal_minor,"interest_paid_minor":interest_minor,
        "interest_accrued_minor":accrued_minor,"loan_interest_collected_minor":interest_minor,
        "loan_penalty_collected_minor":penalty_minor,"principal_repaid_minor":principal_minor,
    },"$addToSet":{"applied_repayment_ids":txid}}
    if tx.get("date") is not None: update["$set"]={"last_payment_date":tx.get("date"),"last_interest_accrual_month":tx.get("payment_month") or str(tx.get("date"))[:7]}
    result=await db.loans.update_one({"_id":loan["_id"],"tenant_id":tenant_id,"applied_repayment_ids":{"$ne":txid}},update)
    updated=await db.loans.find_one({"_id":loan["_id"],"tenant_id":tenant_id})
    if not updated: raise HTTPException(404,"Loan not found")
    outstanding_minor=max(0,(int(updated["principal_minor"]) if updated.get("principal_minor") is not None else to_minor(updated.get("principal",0) or 0))-(int(updated["principal_paid_minor"]) if updated.get("principal_paid_minor") is not None else to_minor(updated.get("principal_paid",0) or 0)))
    accrued_interest_minor=int(updated["interest_accrued_minor"]) if updated.get("interest_accrued_minor") is not None else to_minor(updated.get("interest_accrued",updated.get("expected_interest",0)) or 0)
    interest_paid_minor=int(updated["interest_paid_minor"]) if updated.get("interest_paid_minor") is not None else to_minor(updated.get("interest_paid",0) or 0)
    interest_remaining_minor=max(0,accrued_interest_minor-interest_paid_minor)
    if outstanding_minor<=0 and interest_remaining_minor<=0:
        await db.loans.update_one({"_id":loan["_id"],"tenant_id":tenant_id},{"$set":{"status":"closed","closed_at":updated.get("closed_at") or datetime.now(timezone.utc)}})
    await db.transactions.update_one({"_id":tx["_id"],"tenant_id":tenant_id},{"$set":{"loan_apply_status":"applied","loan_applied_at":datetime.now(timezone.utc)}})
    return await db.loans.find_one({"_id":loan["_id"],"tenant_id":tenant_id})


async def _apply_loan_repayment_reversal_once(tenant_id: str, reversal: dict):
    """Undo a repayment only when it is the latest applied payment for that loan.

    Reversing an older repayment would require replaying every later accrual and
    payment allocation. Refuse that unsafe operation rather than corrupt balances.
    """
    db = get_db()
    original_id = str(reversal.get("reversal_of") or "")
    original = await db.transactions.find_one({"_id": parse_oid(original_id), "tenant_id": tenant_id, "type": "loan_repayment"})
    if not original:
        raise HTTPException(409, "Original loan repayment is missing; reversal cannot be applied safely")
    if reversal.get("reversal_apply_status") == "applied":
        return
    loan_id = str(original.get("loan_id") or "")
    loan = await db.loans.find_one({"_id": parse_oid(loan_id), "tenant_id": tenant_id}) if loan_id else None
    if not loan:
        raise HTTPException(409, "Loan is missing; repayment reversal cannot be applied safely")
    loan = await _ensure_loan_minor_balances(tenant_id, loan)
    applied = [str(x) for x in loan.get("applied_repayment_ids", [])]
    original_txid = str(original["_id"])
    if original_txid not in applied:
        await db.transactions.update_one({"_id": reversal["_id"], "tenant_id": tenant_id}, {"$set": {"reversal_apply_status": "applied", "reversal_apply_note": "Original repayment was not applied to loan balance"}})
        return
    if not applied or applied[-1] != original_txid:
        raise HTTPException(409, "Only the latest loan repayment can be reversed. Reverse later repayments first.")
    principal = int(original["principal_repaid_minor"]) if original.get("principal_repaid_minor") is not None else to_minor(original.get("principal_repaid", original.get("principal", 0)) or 0)
    interest = int(original["loan_interest_minor"]) if original.get("loan_interest_minor") is not None else to_minor(original.get("loan_interest_collected", original.get("interest", 0)) or 0)
    penalty = int(original["loan_penalty_minor"]) if original.get("loan_penalty_minor") is not None else to_minor(original.get("loan_penalty_collected", 0) or 0)
    accrued = int(original["interest_accrued_delta_minor"]) if original.get("interest_accrued_delta_minor") is not None else to_minor(original.get("interest_accrued_delta", 0) or 0)
    previous_id = applied[-2] if len(applied) > 1 else None
    previous_payment = await db.transactions.find_one({"_id": parse_oid(previous_id), "tenant_id": tenant_id}) if previous_id else None
    start_value = loan.get("start_date") or loan.get("created_at") or datetime.now(timezone.utc)
    start_month = start_value.strftime("%Y-%m") if hasattr(start_value, "strftime") else str(start_value)[:7]
    previous_date = (previous_payment or {}).get("date")
    previous_month = (previous_payment or {}).get("payment_month")
    if not previous_month and previous_date is not None:
        previous_month = previous_date.strftime("%Y-%m") if hasattr(previous_date, "strftime") else str(previous_date)[:7]
    restored_month = str(previous_month or start_month)
    restored_payment_date = previous_date
    result = await db.loans.update_one(
        {"_id": loan["_id"], "tenant_id": tenant_id, "applied_repayment_ids": original_txid},
        {"$inc": {"principal_paid": -principal / 100, "interest_paid": -interest / 100,
                  "interest_accrued": -accrued / 100, "loan_interest_collected": -interest / 100,
                  "loan_penalty_collected": -penalty / 100, "principal_repaid": -principal / 100,
                  "principal_paid_minor": -principal, "interest_paid_minor": -interest,
                  "interest_accrued_minor": -accrued, "loan_interest_collected_minor": -interest,
                  "loan_penalty_collected_minor": -penalty, "principal_repaid_minor": -principal},
         "$pull": {"applied_repayment_ids": original_txid},
         "$set": {"status": "active", "closed_at": None,
                   "last_interest_accrual_month": restored_month, "last_payment_date": restored_payment_date}})
    if result.modified_count != 1:
        raise HTTPException(409, "Loan changed during reversal; retry after refreshing the loan")
    await db.transactions.update_one({"_id": reversal["_id"], "tenant_id": tenant_id}, {"$set": {"reversal_apply_status": "applied", "reversal_applied_at": datetime.now(timezone.utc)}})


@router.post("/{tenant_id}/loan-payments")
async def loan_payment(tenant_id:str,body:LoanPayment,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    if body.idempotency_key:
        existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing:
            idem_lock_key=f"loan-payment:{tenant_id}:{body.loan_id}"
            await acquire_operation_lock(idem_lock_key,90)
            try:
                existing=await db.transactions.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
                if existing:
                    if (str(existing.get("loan_id")) != str(body.loan_id) or existing.get("type") != "loan_repayment"
                            or (body.amount is not None and (int(existing["amount_minor"]) if existing.get("amount_minor") is not None else to_minor(existing.get("amount",0) or 0)) != to_minor(body.amount))
                            or (existing.get("account") or "cash") != (body.account or "cash")):
                        raise HTTPException(409,"Idempotency key was already used for a different operation")
                    await _finalize_tx_side_effects(existing,user)
                    loan_existing=await _apply_loan_repayment_once(tenant_id,existing)
                    outstanding_minor=max(0,(int(loan_existing["principal_minor"]) if loan_existing.get("principal_minor") is not None else to_minor(loan_existing.get("principal",0) or 0))-(int(loan_existing["principal_paid_minor"]) if loan_existing.get("principal_paid_minor") is not None else to_minor(loan_existing.get("principal_paid",0) or 0)))
                    accrued_interest_minor=int(loan_existing["interest_accrued_minor"]) if loan_existing.get("interest_accrued_minor") is not None else to_minor(loan_existing.get("interest_accrued",loan_existing.get("expected_interest",0)) or 0)
                    interest_paid_minor=int(loan_existing["interest_paid_minor"]) if loan_existing.get("interest_paid_minor") is not None else to_minor(loan_existing.get("interest_paid",0) or 0)
                    interest_remaining_minor=max(0,accrued_interest_minor-interest_paid_minor)
                    return {"id":str(existing["_id"]),"amount":float(from_minor(int(existing["amount_minor"]) if existing.get("amount_minor") is not None else to_minor(existing.get("amount",0) or 0))),"principal":float(from_minor(int(existing["principal_repaid_minor"]) if existing.get("principal_repaid_minor") is not None else to_minor(existing.get("principal_repaid",0) or 0))),"interest":float(from_minor(int(existing["loan_interest_minor"]) if existing.get("loan_interest_minor") is not None else to_minor(existing.get("loan_interest_collected",0) or 0))),"penalty":float(from_minor(int(existing["loan_penalty_minor"]) if existing.get("loan_penalty_minor") is not None else to_minor(existing.get("loan_penalty_collected",0) or 0))),"principal_remaining":float(from_minor(outstanding_minor)),"interest_remaining":float(from_minor(interest_remaining_minor))}
            finally:
                await release_operation_lock(idem_lock_key)
    lock_key=f"loan-payment:{tenant_id}:{body.loan_id}"; await acquire_operation_lock(lock_key,90)
    try:
        loan=await db.loans.find_one({"_id":parse_oid(body.loan_id),"tenant_id":tenant_id})
        if not loan: raise HTTPException(404,"Loan not found")
        if loan.get("status")!="active": raise HTTPException(400,"Loan is already closed")
        tenant=await group_settings(tenant_id); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
        principal_minor=int(loan["principal_minor"]) if loan.get("principal_minor") is not None else to_minor(loan.get("principal",0) or 0)
        principal_paid_minor=int(loan["principal_paid_minor"]) if loan.get("principal_paid_minor") is not None else to_minor(loan.get("principal_paid",0) or 0)
        outstanding_principal_minor=max(0,principal_minor-principal_paid_minor)
        # Use the rate agreed on this loan; group settings are only a legacy fallback.
        rate=loan.get("interest_rate",tenant.get("loan_interest_rate_per_month",2))
        due_day=int(loan.get("loan_due_date",tenant.get("loan_due_date",10)) or 10)
        month_key=dt.strftime("%Y-%m"); interest_key=f"loan-interest:{body.loan_id}:{month_key}"; penalty_key=f"loan-penalty:{body.loan_id}:{month_key}"
        month_start=datetime(dt.year,dt.month,1,tzinfo=timezone.utc)
        month_end=datetime(dt.year+1,1,1,tzinfo=timezone.utc) if dt.month==12 else datetime(dt.year,dt.month+1,1,tzinfo=timezone.utc)
        interest_rows=await db.transactions.find({"tenant_id":tenant_id,"loan_id":body.loan_id,"type":"loan_repayment","$or":[{"loan_interest_key":interest_key},{"payment_month":month_key},{"date":{"$gte":month_start,"$lt":month_end}}]},{"_id":1,"loan_interest_collected":1,"loan_interest_minor":1}).sort([("date",-1),("created_at",-1),("_id",-1)]).to_list(1000)
        interest_already_minor=sum(int(x["loan_interest_minor"]) if x.get("loan_interest_minor") is not None else to_minor(x.get("loan_interest_collected",0) or 0) for x in interest_rows)
        # A reversal must release the interest that its original payment collected.
        original_interest_ids=[str(x["_id"]) for x in interest_rows]
        if original_interest_ids:
            reversed_interest_rows=await db.transactions.find({"tenant_id":tenant_id,"type":"reversal","original_type":"loan_repayment","reversal_of":{"$in":original_interest_ids}},{"loan_interest_minor":1,"loan_interest_collected":1}).to_list(1000)
            interest_already_minor += sum(int(x["loan_interest_minor"]) if x.get("loan_interest_minor") is not None else to_minor(x.get("loan_interest_collected",0) or 0) for x in reversed_interest_rows)
        last_month=str(loan.get("last_interest_accrual_month") or str(loan.get("start_date",dt))[:7]); current_month=month_key
        try:
            ly,lm=map(int,last_month.split("-")); cy,cm=map(int,current_month.split("-")); elapsed_months=max(0,(cy-ly)*12+(cm-lm))
        except Exception: elapsed_months=1
        accrued_minor=int(loan["interest_accrued_minor"]) if loan.get("interest_accrued_minor") is not None else to_minor(loan.get("interest_accrued",loan.get("expected_interest",0)) or 0)
        interest_paid_total_minor=int(loan["interest_paid_minor"]) if loan.get("interest_paid_minor") is not None else to_minor(loan.get("interest_paid",0) or 0)
        if (elapsed_months==0 and not interest_rows and not loan.get("last_payment_date")
                and accrued_minor<=0 and interest_paid_total_minor<=0):
            # Preserve the first-payment accrual rule, but do not accrue the same
            # month twice after a penalty-only or principal-only payment.
            elapsed_months=1
        new_interest_minor=max(0,simple_interest_minor(outstanding_principal_minor,rate,elapsed_months)-interest_already_minor)
        # Carry forward accrued-but-unpaid interest when the payment is partial.
        accrued_before_minor=max(0,accrued_minor-interest_paid_total_minor)
        interest_due_minor=accrued_before_minor+new_interest_minor
        overdue=_due_overdue_days(dt,due_day,dt.year,dt.month)
        # Fixed penalty per overdue day per missed monthly cycle; use the loan's
        # stored terms and subtract all net penalties already collected.
        per_day_penalty=float(loan.get("loan_per_day_penalty",tenant.get("loan_per_day_penalty",0)) or 0)
        start_value=loan.get("start_date") or loan.get("created_at") or dt
        if isinstance(start_value,datetime): start_date=start_value.date()
        elif isinstance(start_value,date): start_date=start_value
        elif isinstance(start_value,str):
            try: start_date=date.fromisoformat(start_value[:10])
            except Exception: start_date=dt.date()
        else: start_date=dt.date()
        accrued_penalty_minor=monthly_overdue_penalty_minor(start_date,dt.date(),due_day,to_minor(per_day_penalty))
        all_penalty_rows=await db.transactions.find({"tenant_id":tenant_id,"loan_id":body.loan_id,"$or":[{"type":"loan_repayment"},{"type":"reversal","original_type":"loan_repayment"}]},{"loan_penalty_collected":1,"loan_penalty_minor":1}).to_list(10000)
        penalty_already_minor=sum(int(x["loan_penalty_minor"]) if x.get("loan_penalty_minor") is not None else to_minor(x.get("loan_penalty_collected",0) or 0) for x in all_penalty_rows)
        penalty_due_minor=max(0,accrued_penalty_minor-penalty_already_minor)
        payment_minor=to_minor(body.amount) if body.amount is not None else to_minor(body.principal)+to_minor(body.interest)
        if payment_minor<=0: raise HTTPException(400,"Payment must be greater than zero")
        penalty_paid_minor=min(payment_minor,penalty_due_minor); remaining_minor=payment_minor-penalty_paid_minor
        interest_paid_minor=min(remaining_minor,interest_due_minor); remaining_minor-=interest_paid_minor
        principal_paid_minor=min(remaining_minor,outstanding_principal_minor); remaining_minor-=principal_paid_minor
        if remaining_minor>0:
            raise HTTPException(400,"Payment exceeds current principal, interest and penalty due")
        payment=float(from_minor(payment_minor)); penalty_paid=float(from_minor(penalty_paid_minor))
        interest_paid=float(from_minor(interest_paid_minor)); principal_paid=float(from_minor(principal_paid_minor))
        new_interest=float(from_minor(new_interest_minor))
        txid=await insert_tx(tenant_id,loan["member_id"],"loan_repayment",payment,body.account,user,loan_id=body.loan_id,principal=principal_paid,interest=interest_paid,loan_interest_collected=interest_paid,loan_penalty_collected=penalty_paid,principal_repaid=principal_paid,loan_penalty_key=penalty_key if penalty_paid else None,loan_interest_key=interest_key if interest_paid else None,overdue_days=overdue,per_day_penalty=per_day_penalty,interest_accrued_delta=new_interest,payment_month=month_key,payment_category="loan_emi",date=dt,note=body.note,idempotency_key=body.idempotency_key)
        tx_doc=await db.transactions.find_one({"_id":parse_oid(txid),"tenant_id":tenant_id})
        updated=await _apply_loan_repayment_once(tenant_id,tx_doc)
        remaining_principal_minor=max(0,(int(updated["principal_minor"]) if updated.get("principal_minor") is not None else to_minor(updated.get("principal",0) or 0))-(int(updated["principal_paid_minor"]) if updated.get("principal_paid_minor") is not None else to_minor(updated.get("principal_paid",0) or 0)))
        accrued_interest_minor=int(updated["interest_accrued_minor"]) if updated.get("interest_accrued_minor") is not None else to_minor(updated.get("interest_accrued",updated.get("expected_interest",0)) or 0)
        interest_paid_total_minor=int(updated["interest_paid_minor"]) if updated.get("interest_paid_minor") is not None else to_minor(updated.get("interest_paid",0) or 0)
        remaining_interest_minor=max(0,accrued_interest_minor-interest_paid_total_minor)
        return {"id":txid,"amount":payment,"principal":principal_paid,"interest":interest_paid,"penalty":penalty_paid,"loan_interest_part":interest_paid,"loan_principal_part":principal_paid,"loan_penalty_part":penalty_paid,"principal_remaining":float(from_minor(remaining_principal_minor)),"interest_remaining":float(from_minor(remaining_interest_minor))}
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
        if existing:
            if (str(existing.get("member_id")) != member_id
                    or to_minor(existing.get("amount", 0) or 0) != to_minor(body.amount)
                    or int(existing.get("months", 0) or 0) != int(body.months)
                    or str(existing.get("purpose", "") or "") != str(body.purpose or "")):
                raise HTTPException(409, "Idempotency key was already used for a different loan request")
            return {"id":str(existing["_id"]),"credit_limit":elig["credit_limit"],"loan_apply_date":str(existing.get("loan_apply_date",now.date()))[:10],"requested_start_date":str(existing.get("requested_start_date",now.date()))[:10]}
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
            await db.loan_requests.update_one({"_id":req["_id"],"tenant_id":tenant_id},{"$set":{"status":"rejected","decision_note":body.note or "Rejected by administrator","decided_at":datetime.now(timezone.utc),"rejected_by":str(user["_id"])}})
            await audit(tenant_id,user,"LOAN_REQUEST_REJECTED","loan_request",request_id,{"reason":body.note or "Rejected by administrator"}); return {"ok":True,"status":"rejected"}
        tenant=await group_settings(tenant_id); required=int(tenant.get("required_admin_approvals",1) or 1); uid=str(user["_id"]); approvals=[str(x) for x in req.get("approval_records",req.get("loan_approval_records",[]))]
        if uid not in approvals: approvals.append(uid)
        if len(approvals)<required:
            await db.loan_requests.update_one({"_id":req["_id"],"tenant_id":tenant_id},{"$set":{"status":"pending","approval_records":approvals,"loan_approval_records":approvals,"last_approved_at":datetime.now(timezone.utc)}})
            await audit(tenant_id,user,"LOAN_REQUEST_APPROVAL_RECORDED","loan_request",request_id,{"approval_count":len(approvals),"required":required})
            return {"ok":True,"status":"pending","approval_count":len(approvals),"required_admin_approvals":required}
        principal=body.principal or req["amount"]; rate=body.interest_rate if body.interest_rate is not None else float(tenant.get("loan_interest_rate_per_month",2) or 0); months=body.months or 2
        apply_value=req.get("loan_apply_date") or req.get("created_at") or datetime.now(timezone.utc); apply_date=apply_value.date() if hasattr(apply_value,"date") else datetime.now(timezone.utc).date()
        start_value=req.get("requested_start_date") or req.get("loan_apply_date") or datetime.now(timezone.utc); requested_start=start_value.date() if hasattr(start_value,"date") else datetime.now(timezone.utc).date()
        min_months=max(0,int(tenant.get("min_advance_apply_months",2) or 2))
        if not req.get("requested_start_date") and not req.get("loan_apply_date"): requested_start=_add_months(apply_date,min_months)
        months_ahead=(requested_start.year-apply_date.year)*12+(requested_start.month-apply_date.month)
        if months_ahead<min_months:
            await db.loan_requests.update_one({"_id":req["_id"],"tenant_id":tenant_id},{"$set":{"status":"rejected","decision_note":f"Loans must be requested at least {min_months} months in advance as per group policy.","rejected_by":"system","decided_at":datetime.now(timezone.utc)}})
            return {"ok":True,"status":"rejected","reason":"advance_rule"}
        elig=await loan_eligibility(tenant_id,req["member_id"],principal,months,user=user)
        if not elig["eligible"]:
            reason="Insufficient Group Funds" if elig["funds_available_for_disbursement"]<principal else "Loan exceeds member credit eligibility"
            await db.loan_requests.update_one({"_id":req["_id"],"tenant_id":tenant_id},{"$set":{"status":"rejected","decision_note":reason,"rejected_by":"system","decided_at":datetime.now(timezone.utc),"approval_records":approvals,"loan_approval_records":approvals}})
            await _create_notification(tenant_id,role="member",member_id=req["member_id"],source_key=f"loan-request-reject:{req['_id']}",title="Loan request rejected",body=reason,created_at=datetime.now(timezone.utc))
            return {"ok":True,"status":"rejected","reason":reason}
        idem=f"loan-request-approval:{request_id}"
        created=await create_loan(tenant_id,LoanCreate(member_id=req["member_id"],principal=principal,interest_rate=rate,months=months,account=body.account,purpose=req.get("purpose", ""),date=requested_start,idempotency_key=idem),user)
        await db.loan_requests.update_one({"_id":req["_id"],"tenant_id":tenant_id},{"$set":{"status":"approved","decision_note":body.note,"decided_at":datetime.now(timezone.utc),"approval_records":approvals,"loan_approval_records":approvals,"loan_id":parse_oid(created["id"])}})
        return {"ok":True,"status":"approved","approval_count":len(approvals),"required_admin_approvals":required,"loan":created}
    finally:
        await release_operation_lock(lock_key)

async def ensure_expense_allocations(tenant_id:str,expense_doc):
    """Create expense allocations once against an immutable share snapshot.

    The allocation plan is persisted before child rows are written, so a restart
    or share-status change cannot redistribute an already-recorded expense.
    Amounts are split in integer paise and always sum exactly to the expense.
    """
    db=get_db(); eid=str(expense_doc["_id"]); lock_key=f"expense-allocations:{tenant_id}:{eid}"
    await acquire_operation_lock(lock_key,300)
    try:
        current=await db.expenses.find_one({"_id":expense_doc["_id"],"tenant_id":tenant_id}) or expense_doc
        plan=current.get("allocation_plan")
        if not isinstance(plan,list):
            # Recover old partial allocations first. Their share IDs are evidence
            # of the original distribution; merge them with active shares so a
            # crash cannot silently drop already-created allocation rows.
            existing_allocations=await db.transactions.find({"tenant_id":tenant_id,"expense_id":eid,"type":"expense_allocation"}).sort([("created_at",1),("_id",1)]).to_list(10000)
            shares=await db.shares.find({"tenant_id":tenant_id,"status":"active"}).sort([("share_no",1),("_id",1)]).to_list(10000)
            plan_by_id={str(sh["_id"]):{"share_id":str(sh["_id"]),"member_id":str(sh["member_id"]),"share_no":int(sh.get("share_no",0) or 0)} for sh in shares}
            # These rows are derived allocations, not cash movements. Remove
            # duplicate child rows for the same share before normalizing them.
            seen_allocations=set()
            for allocation in existing_allocations:
                allocation_key=str(allocation.get("share_id") or f"{allocation.get('member_id')}:{allocation.get('share_no')}")
                if allocation_key in seen_allocations:
                    await db.transactions.delete_one({"_id":allocation["_id"],"tenant_id":tenant_id,"type":"expense_allocation"})
                    continue
                seen_allocations.add(allocation_key)
            for allocation in existing_allocations:
                sid=str(allocation.get("share_id") or "")
                if sid and sid not in plan_by_id and allocation.get("member_id"):
                    plan_by_id[sid]={"share_id":sid,"member_id":str(allocation["member_id"]),"share_no":int(allocation.get("share_no",0) or 0)}
            plan=sorted(plan_by_id.values(),key=lambda item:(item.get("share_no",0),item["share_id"]))
            await db.expenses.update_one({"_id":expense_doc["_id"],"tenant_id":tenant_id,"allocation_plan":{"$exists":False}}, {"$set":{"allocation_plan":plan,"allocation_plan_created_at":datetime.now(timezone.utc)}})
            current=await db.expenses.find_one({"_id":expense_doc["_id"],"tenant_id":tenant_id}) or current
            plan=current.get("allocation_plan",plan)
        if not plan:
            return
        total_minor=int(current.get("amount_minor") if current.get("amount_minor") is not None else to_minor(current.get("amount",0) or 0))
        count=len(plan); base,remainder=divmod(abs(total_minor),count)
        from pymongo import UpdateOne
        ops=[]
        for i,item in enumerate(plan):
            part=base+(1 if i < remainder else 0)
            signed_minor=-part
            ops.append(UpdateOne(
                {"tenant_id":tenant_id,"expense_id":eid,"share_id":str(item["share_id"]),"type":"expense_allocation"},
                {"$set":{
                    "tenant_id":tenant_id,"member_id":str(item["member_id"]),"share_id":str(item["share_id"]),
                    "share_no":int(item.get("share_no",0) or 0),"expense_id":eid,"type":"expense_allocation",
                    "amount":float(from_minor(signed_minor)),"amount_minor":signed_minor,"account":current.get("account","cash"),
                    "date":current.get("date"),"created_at":current.get("created_at",datetime.now(timezone.utc)),
                    "payment_category":"group_expense_allocation","note":current.get("category", "Group expense")
                }},upsert=True))
        if ops: await db.transactions.bulk_write(ops,ordered=False)
    finally:
        await release_operation_lock(lock_key)

async def _finalize_expense_side_effects(expense_doc: dict, user: dict):
    db=get_db(); eid=str(expense_doc["_id"]); tenant_id=expense_doc["tenant_id"]
    journal=build_expense_journal(expense_doc)
    if journal:
        source_key=f"expense:{eid}"
        journal_doc={"tenant_id":tenant_id,"source_type":"expense","source_id":eid,"source_key":source_key,"status":"pending",**journal,"created_at":expense_doc.get("created_at") or datetime.now(timezone.utc)}
        await db.journal_entries.update_one({"tenant_id":tenant_id,"source_key":source_key},{"$setOnInsert":journal_doc},upsert=True)
        stored=await db.journal_entries.find_one({"tenant_id":tenant_id,"source_key":source_key})
        if not stored or not stored.get("balanced") or stored.get("debit_minor") != stored.get("credit_minor"):
            raise RuntimeError(f"Journal for expense {eid} is not balanced")
        await db.journal_entries.update_one({"tenant_id":tenant_id,"source_key":source_key},{"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
        await db.expenses.update_one({"_id":expense_doc["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":"posted"}})
    await _feed_upsert(expense_doc,source_type="expense")
    current=await db.expenses.find_one({"_id":expense_doc["_id"],"tenant_id":tenant_id}) or expense_doc
    await ensure_expense_allocations(tenant_id,current)
    await audit(tenant_id,user,"EXPENSE_CREATED","expense",eid,{"amount":expense_doc.get("amount",0)},event_key=f"expense-created:{eid}")
    await _create_notification(tenant_id,role="admin",source_key=f"expense:{eid}",title="Group expense recorded",body=f"{current.get('category','Expense')}: ₹{float(current.get('amount',0) or 0):,.2f}.",created_at=current.get("created_at") or datetime.now(timezone.utc))
    await db.expenses.update_one({"_id":expense_doc["_id"],"tenant_id":tenant_id},{"$set":{"side_effects_status":"complete","side_effects_completed_at":datetime.now(timezone.utc)}})


@router.post("/{tenant_id}/expenses")
async def expense(tenant_id:str,body:ExpenseCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); now=datetime.now(timezone.utc); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    if body.idempotency_key:
        existing=await db.expenses.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing:
            existing_amount_minor=int(existing["amount_minor"]) if existing.get("amount_minor") is not None else to_minor(existing.get("amount",0) or 0)
            if (existing_amount_minor != to_minor(body.amount)
                    or str(existing.get("category", "") or "") != str(body.category or "")
                    or (existing.get("account") or "cash") != (body.account or "cash")):
                raise HTTPException(409, "Idempotency key was already used for a different expense")
            await _finalize_expense_side_effects(existing,user)
            return {"id":str(existing["_id"])}
    if not await db.expense_categories.find_one({"tenant_id":tenant_id,"name":body.category}):
        await db.expense_categories.insert_one({"tenant_id":tenant_id,"name":body.category,"created_at":now})
    expense_amount_minor=to_minor(body.amount)
    expense_doc={**body.model_dump(),"amount":float(from_minor(expense_amount_minor)),"tenant_id":tenant_id,"date":dt,"created_at":now,"proof_url":None,"proof_public_id":None,"idempotency_key":body.idempotency_key,"amount_minor":expense_amount_minor,"created_by":str(user.get("_id","system")),"created_by_role":user.get("role","system"),"created_by_phone":user.get("phone"),"side_effects_status":"pending"}
    if body.idempotency_key:
        existing=await db.expenses.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
        if existing:
            existing_amount_minor=int(existing["amount_minor"]) if existing.get("amount_minor") is not None else to_minor(existing.get("amount",0) or 0)
            if (existing_amount_minor != to_minor(body.amount)
                    or str(existing.get("category", "") or "") != str(body.category or "")
                    or (existing.get("account") or "cash") != (body.account or "cash")):
                raise HTTPException(409, "Idempotency key was already used for a different expense")
            await _finalize_expense_side_effects(existing,user)
            return {"id":str(existing["_id"])}
    expense_doc["journal_status"]="pending"
    try:
        r=await db.expenses.insert_one(expense_doc)
        expense_doc["_id"]=r.inserted_id
    except Exception:
        if body.idempotency_key:
            existing=await db.expenses.find_one({"tenant_id":tenant_id,"idempotency_key":body.idempotency_key})
            if existing:
                if ((int(existing["amount_minor"]) if existing.get("amount_minor") is not None else to_minor(existing.get("amount",0) or 0)) != to_minor(body.amount)
                        or str(existing.get("category","") or "") != str(body.category or "")
                        or (existing.get("account") or "cash") != (body.account or "cash")):
                    raise HTTPException(409,"Idempotency key was already used for a different expense")
                await _finalize_expense_side_effects(existing,user)
                return {"id":str(existing["_id"])}
        raise
    await _finalize_expense_side_effects(expense_doc,user)
    return {"id":str(expense_doc["_id"])}

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
    await db.expenses.update_one({"_id":exp["_id"],"tenant_id":tenant_id},{"$set":{"proof_url":r.get("secure_url"),"proof_public_id":r.get("public_id"),"proof_resource_type":rt,"proof_original_filename":file.filename,"proof_content_type":file.content_type}})
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
    await db.expenses.update_one({"_id":exp["_id"],"tenant_id":tenant_id},{"$set":{"proof_url":None,"proof_public_id":None}}); await audit(tenant_id,user,"EXPENSE_PROOF_DELETED","expense",expense_id); return {"ok":True}

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
