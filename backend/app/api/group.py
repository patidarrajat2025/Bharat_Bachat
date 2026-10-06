from datetime import datetime, timezone, date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..db import get_db
from ..deps import current_user, tenant_guard, require_roles, parse_oid
from ..models import *
from ..services import tenant_summary, analytics
from ..audit import audit
from ..core.config import settings
from ..core.cloudinary import upload_bytes, delete_asset
from ..core.security import hash_password, normalize_phone
from ..share_service import ensure_member_shares

router=APIRouter(prefix="/group",tags=["group"])

def as_id(v): return str(v)
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

@router.get("/{tenant_id}/summary")
async def summary(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); return await tenant_summary(tenant_id)

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
    await tenant_guard(user,tenant_id)
    db=get_db(); now=datetime.now(timezone.utc)
    try:
        if user["role"] in ("group_admin","super_admin"):
            pending=await db.loan_requests.find({"tenant_id":tenant_id,"status":"pending"}).sort("created_at",-1).to_list(20)
            for r in pending:
                key=f"loan-request:{r['_id']}"
                await db.notifications.update_one({"tenant_id":tenant_id,"source_key":key},{"$setOnInsert":{"tenant_id":tenant_id,"recipient_role":"admin","type":"loan_request","title":"New loan request","body":f"A member has requested ₹{float(r.get('amount',0)):,.2f}.","read":False,"created_at":r.get("created_at",now),"source_key":key}},upsert=True)
            logs=await db.audit_logs.find({"tenant_id":tenant_id}).sort("created_at",-1).limit(10).to_list(10)
            for l in logs:
                key=f"audit:{l['_id']}"
                await db.notifications.update_one({"tenant_id":tenant_id,"source_key":key},{"$setOnInsert":{"tenant_id":tenant_id,"recipient_role":"admin","type":"activity","title":str(l.get("action","Activity")).replace("_"," ").title(),"body":"New group activity recorded.","read":False,"created_at":l.get("created_at",now),"source_key":key}},upsert=True)
            q={"tenant_id":tenant_id,"recipient_role":"admin","dismissed":{"$ne":True}}
        else:
            mid=str(user.get("member_id") or "")
            if not mid: return []
            try: member_oid=parse_oid(mid)
            except HTTPException: return []
            recent=await db.transactions.find({"tenant_id":tenant_id,"member_id":member_oid}).sort("date",-1).limit(10).to_list(10)
            for t in recent:
                key=f"tx:{t['_id']}:{mid}"
                await db.notifications.update_one({"tenant_id":tenant_id,"source_key":key},{"$setOnInsert":{"tenant_id":tenant_id,"source_key":key,"recipient_role":"member","recipient_member_id":mid,"type":"transaction","title":"Account activity","body":f"{str(t.get('type','Transaction')).replace('_',' ').title()} ₹{float(t.get('amount',0)):,.2f}.","read":False,"created_at":t.get("date",now)}},upsert=True)
            q={"tenant_id":tenant_id,"recipient_role":"member","recipient_member_id":mid,"dismissed":{"$ne":True}}
        rows=await db.notifications.find(q).sort("created_at",-1).limit(20).to_list(20)
        return [serialize(x) for x in rows]
    except Exception:
        # Notification delivery is non-critical. A malformed legacy record must never break the dashboard.
        return []

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
async def members(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); q={"tenant_id":tenant_id}
    if user["role"]=="member": q["_id"]=parse_oid(user.get("member_id"))
    rows=await get_db().members.find(q).sort("first_name",1).to_list(2000)
    out=[]
    for row in rows:
        active_shares=await ensure_member_shares(row)
        x=serialize(row); x["profile_picture_url"]=x.get("profile_picture_url") or x.get("profile_image_url"); x["active_shares_count"]=len(active_shares); x["share_ids"]=[str(s["_id"]) for s in active_shares]; out.append(x)
    return out

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

async def insert_tx(tenant_id,member_id,typ,amount,account,user,**extra):
    db=get_db(); now=datetime.now(timezone.utc); doc={"tenant_id":tenant_id,"member_id":member_id,"type":typ,"amount":amount,"account":account,"created_at":now,**extra}
    r=await db.transactions.insert_one(doc); await audit(tenant_id,user,f"{typ.upper()}_POSTED","transaction",str(r.inserted_id),{"amount":amount}); return str(r.inserted_id)

@router.post("/{tenant_id}/contributions")
async def contribution(tenant_id:str,body:ContributionCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,body.member_id); share=await ensure_share(m,body.share_no)
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    return {"id":await insert_tx(tenant_id,body.member_id,"contribution",body.amount,body.account,user,share_id=str(share["_id"]),share_no=body.share_no,payment_category="manual_contribution",date=dt,note=body.note)}

@router.get("/{tenant_id}/monthly-kist-summary")
async def monthly_kist_summary(tenant_id:str,period:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    import re
    if not re.fullmatch(r"\d{4}-\d{2}",period): raise HTTPException(400,"Period must be YYYY-MM")
    y,m=map(int,period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    expected_per_share=float((tenant or {}).get("kist_per_share",500))
    members=await db.members.find({"tenant_id":tenant_id,"active":True}).to_list(5000)
    if user["role"]=="member": members=[m for m in members if str(m["_id"])==str(user.get("member_id"))]
    tx_rows=await db.transactions.find({"tenant_id":tenant_id,"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}).to_list(20000)
    # Monthly collection status is group-level even when a member is logged in.
    # Never use per-share/member expense allocations here; those are personal passbook rows.
    expense_rows=await db.expenses.find({"tenant_id":tenant_id,"date":{"$gte":start,"$lt":end}}).to_list(20000)
    group_expenses=round(sum(float(x.get("amount",0) or 0) for x in expense_rows),2)
    paid_by_share={}; paid_by_legacy={}
    for x in tx_rows:
        amount=float(x.get("amount",0)); sid=x.get("share_id")
        if sid: paid_by_share[sid]=paid_by_share.get(sid,0)+amount
        elif x.get("member_id") is not None and x.get("share_no") is not None:
            key=(str(x.get("member_id")),int(x.get("share_no"))); paid_by_legacy[key]=paid_by_legacy.get(key,0)+amount
    expected=paid=paid_shares=partial_shares=pending_shares=0
    for member in members:
        shares=await ensure_member_shares(member)
        for share in shares:
            sid=str(share["_id"]); p=float(paid_by_share.get(sid,0) + paid_by_legacy.get((str(member["_id"]),int(share["share_no"])),0)); p=round(p,2); expected+=expected_per_share; paid+=min(p,expected_per_share); remaining=max(0,round(expected_per_share-p,2))
            if remaining<=0.009: paid_shares+=1
            elif p>0: partial_shares+=1
            else: pending_shares+=1
    return {"period":period,"expected_total":round(expected,2),"paid_total":round(paid,2),"pending_total":round(max(0,expected-paid),2),"paid_shares":paid_shares,"partial_shares":partial_shares,"pending_shares":pending_shares,"active_members":len(members),"group_expenses":group_expenses}

@router.get("/{tenant_id}/monthly-kist/{member_id}")
async def monthly_kist_status(tenant_id:str,member_id:str,period:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); member=await get_member(db,tenant_id,member_id)
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    import re
    if not re.fullmatch(r"\d{4}-\d{2}",period): raise HTTPException(400,"Period must be YYYY-MM")
    shares=await ensure_member_shares(member); expected=float(tenant.get("kist_per_share",500))
    out=[]
    for share in shares:
        y,m=map(int,period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
        paid_rows=await db.transactions.find({"tenant_id":tenant_id,"member_id":member_id,"type":"contribution","$and":[{"$or":[{"share_id":str(share["_id"])},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}]}],"date":{"$gte":start,"$lt":end}}).to_list(500)
        paid=round(sum(float(x.get("amount",0)) for x in paid_rows),2); remaining=round(max(0,expected-paid),2)
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
    out=[]
    for member in members:
        shares=await ensure_member_shares(member)
        txs=await db.transactions.find({"tenant_id":tenant_id,"member_id":str(member["_id"]),"type":"contribution","$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}],"date":{"$gte":start,"$lt":end}}).to_list(1000)
        rows=[]
        for sh in shares:
            paid=round(sum(float(x.get("amount",0)) for x in txs if str(x.get("share_id") or "") == str(sh["_id"]) or (not x.get("share_id") and int(x.get("share_no",0))==int(sh["share_no"]))),2)
            remaining=round(max(0,expected-paid),2)
            rows.append({"share_id":str(sh["_id"]),"share_no":int(sh["share_no"]),"expected_amount":expected,"paid_amount":paid,"remaining_amount":remaining,"status":"paid" if remaining<=0.009 else ("partial" if paid>0 else "pending")})
        out.append({"member_id":str(member["_id"]),"name":f"{member.get('first_name','')} {member.get('last_name','')}".strip(),"phone":member.get("phone",""),"shares":rows,"expected_total":round(expected*len(rows),2),"paid_total":round(sum(x["paid_amount"] for x in rows),2),"remaining_total":round(sum(x["remaining_amount"] for x in rows),2)})
    return {"period":period,"kist_per_share":expected,"members":out}

@router.post("/{tenant_id}/monthly-kist-bulk")
async def monthly_kist_bulk(tenant_id: str, body: BulkMonthlyKistCreate, user=Depends(admin_user)):
    await tenant_guard(user, tenant_id); db=get_db()
    tenant=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not tenant: raise HTTPException(404,"Group not found")
    expected=float(tenant.get("kist_per_share",500)); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    total=0.0; results=[]; seen=set()
    for entry in body.entries:
        if not entry.allocations: continue
        member=await get_member(db,tenant_id,entry.member_id); shares=await ensure_member_shares(member); share_map={str(x["_id"]):x for x in shares}
        for allocation in entry.allocations:
            if allocation.share_id in seen: raise HTTPException(400,"Duplicate share selected in bulk collection")
            seen.add(allocation.share_id)
            share=share_map.get(allocation.share_id)
            if not share: raise HTTPException(400,"One or more selected shares are not active for this member")
            y,m=map(int,body.period.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc); end=datetime(y+1,1,1,tzinfo=timezone.utc) if m==12 else datetime(y,m+1,1,tzinfo=timezone.utc)
            paid_rows=await db.transactions.find({"tenant_id":tenant_id,"member_id":entry.member_id,"type":"contribution","$and":[{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}]}],"date":{"$gte":start,"$lt":end}}).to_list(500)
            already=round(sum(float(x.get("amount",0)) for x in paid_rows),2); remaining=round(expected-already,2)
            if remaining<=0.009: continue
            if allocation.amount>remaining+0.009: raise HTTPException(400,f"Share {share['share_no']} can accept at most {remaining:.2f} for {body.period}")
            after=round(already+allocation.amount,2); status="paid" if after>=expected-0.009 else "partial"
            txid=await insert_tx(tenant_id,entry.member_id,"contribution",allocation.amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note)
            total+=allocation.amount; results.append({"id":txid,"member_id":entry.member_id,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"amount":allocation.amount,"status":status})
    if results:
        await audit(tenant_id,user,"MONTHLY_KIST_BULK_POSTED","tenant",tenant_id,{"period":body.period,"entries":len(results),"amount":round(total,2)})
    return {"period":body.period,"entries":len(results),"total":round(total,2),"results":results}

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
        paid_cursor=await db.transactions.find({"tenant_id":tenant_id,"member_id":body.member_id,"type":"contribution","$and":[{"$or":[{"share_id":allocation.share_id},{"share_no":int(share["share_no"]),"share_id":{"$exists":False}}]},{"$or":[{"payment_category":"monthly_kist"},{"payment_category":{"$exists":False}}]}],"date":{"$gte":start,"$lt":end}}).to_list(500)
        already_paid=round(sum(float(x.get("amount",0)) for x in paid_cursor),2)
        remaining=round(expected-already_paid,2)
        if remaining<=0.009: raise HTTPException(409,f"Share {share['share_no']} is already fully paid for {body.period}")
        if allocation.amount>remaining+0.009: raise HTTPException(400,f"Share {share['share_no']} can accept at most {remaining:.2f} for {body.period}")
        after=round(already_paid+allocation.amount,2)
        status="paid" if after>=expected-0.009 else "partial"
        txid=await insert_tx(tenant_id,body.member_id,"contribution",allocation.amount,body.account,user,share_id=allocation.share_id,share_no=int(share["share_no"]),payment_category="monthly_kist",period=body.period,expected_amount=expected,paid_amount=after,status=status,date=dt,note=body.note)
        results.append({"id":txid,"share_id":allocation.share_id,"share_no":int(share["share_no"]),"paid_amount":allocation.amount,"cumulative_paid":after,"expected_amount":expected,"status":status})
    await audit(tenant_id,user,"MONTHLY_KIST_BATCH_POSTED","member",body.member_id,{"period":body.period,"shares":len(results),"amount":round(sum(x["paid_amount"] for x in results),2)})
    return {"period":body.period,"member_id":body.member_id,"results":results,"total":round(sum(x["paid_amount"] for x in results),2)}

@router.post("/{tenant_id}/money-in")
async def money_in(tenant_id:str,body:MoneyInCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id)
    if body.member_id: await get_member(get_db(),tenant_id,body.member_id)
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    return {"id":await insert_tx(tenant_id,body.member_id,body.type,body.amount,body.account,user,date=dt,note=body.note)}

@router.get("/{tenant_id}/activity")
async def group_activity(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db()
    tx=await db.transactions.find({"tenant_id":tenant_id,"type":{"$ne":"expense_allocation"}}).sort("date",-1).limit(50).to_list(50)
    ex=await db.expenses.find({"tenant_id":tenant_id}).sort("date",-1).limit(50).to_list(50)
    rows=[]
    member_ids={str(x.get("member_id")) for x in tx if x.get("member_id")}
    members={str(m["_id"]):m for m in await db.members.find({"tenant_id":tenant_id,"_id":{"$in":[parse_oid(v) for v in member_ids if parse_oid(v)]}}).to_list(5000)}
    for x in tx:
        mid=str(x.get("member_id")) if x.get("member_id") else ""
        m=members.get(mid)
        member_name=f'{m.get("first_name","")} {m.get("last_name","")}'.strip() if m else ""
        rows.append({"kind":"transaction","type":str(x.get("type","Transaction")).replace("_"," ").title(),"amount":float(x.get("amount",0) or 0),"account":x.get("account","cash"),"date":str(x.get("date",x.get("created_at",""))),"created_at":str(x.get("created_at",x.get("date",""))),"note":x.get("note",""),"member_name":member_name,"member_id":mid,"principal":float(x.get("principal",0) or 0),"interest":float(x.get("interest",0) or 0)})
    for x in ex:
        rows.append({"kind":"expense","type":"Expense","amount":-float(x.get("amount",0) or 0),"account":x.get("account","cash"),"date":str(x.get("date",x.get("created_at",""))),"created_at":str(x.get("created_at",x.get("date",""))),"note":x.get("category","")})
    return sorted(rows,key=lambda x:(x.get("created_at") or x.get("date") or ""),reverse=True)[:50]

@router.get("/{tenant_id}/transactions")
async def transactions(tenant_id:str,from_date:date|None=None,to_date:date|None=None,typ:str|None=None,user=Depends(current_user)):
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
    rows=await get_db().transactions.find(q).sort("date",-1).to_list(5000); return [serialize(x) for x in rows]

@router.post("/{tenant_id}/loans")
async def create_loan(tenant_id:str,body:LoanCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); m=await get_member(get_db(),tenant_id,body.member_id); now=datetime.now(timezone.utc)
    interest=round(body.principal*body.interest_rate/100*body.months,2)
    loan={"tenant_id":tenant_id,"member_id":body.member_id,"principal":body.principal,"interest_rate":body.interest_rate,"months":body.months,"expected_interest":interest,"principal_paid":0.0,"interest_paid":0.0,"status":"active","purpose":body.purpose,"account":body.account,"created_at":now,"start_date":datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)}
    r=await get_db().loans.insert_one(loan)
    await insert_tx(tenant_id,body.member_id,"loan_disbursement",-body.principal,body.account,user,loan_id=str(r.inserted_id),date=loan["start_date"],note=body.purpose)
    await audit(tenant_id,user,"LOAN_CREATED","loan",str(r.inserted_id),{"principal":body.principal}); return {"id":str(r.inserted_id)}

@router.get("/{tenant_id}/loans")
async def loans(tenant_id:str,member_id:str|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); q={"tenant_id":tenant_id}
    if user["role"]=="member": q["member_id"]=str(user.get("member_id"))
    elif member_id: q["member_id"]=member_id
    rows=await get_db().loans.find(q).sort("created_at",-1).to_list(5000); return [serialize(x) for x in rows]

@router.get("/{tenant_id}/group-loans")
async def group_loans(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); db=get_db(); rows=await db.loans.find({"tenant_id":tenant_id,"status":"active"}).sort("created_at",-1).to_list(5000); out=[]
    for r in rows:
        m=await db.members.find_one({"_id":parse_oid(r["member_id"]),"tenant_id":tenant_id})
        x=serialize(r); x["member_name"]=f'{m.get("first_name","")} {m.get("last_name","")}'.strip() if m else "Member"; out.append(x)
    return out

@router.post("/{tenant_id}/loan-payments")
async def loan_payment(tenant_id:str,body:LoanPayment,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); loan=await db.loans.find_one({"_id":parse_oid(body.loan_id),"tenant_id":tenant_id})
    if not loan: raise HTTPException(404,"Loan not found")
    op=float(loan.get("principal_paid",0)); oi=float(loan.get("interest_paid",0)); ep=max(0.0,float(loan["principal"])-op); ei=max(0.0,float(loan.get("expected_interest",0))-oi)
    if body.amount is not None:
        total=round(float(body.amount),2)
        if total>ep+ei+0.01: raise HTTPException(400,"Payment exceeds outstanding principal + interest")
        # Standard EMI allocation: clear outstanding interest first, then principal.
        interest=round(min(total,ei),2)
        principal=round(min(max(0.0,total-interest),ep),2)
    else:
        principal=round(float(body.principal),2); interest=round(float(body.interest),2)
        if principal>ep+0.01 or interest>ei+0.01: raise HTTPException(400,"Payment exceeds outstanding amount")
    if principal+interest<=0: raise HTTPException(400,"Payment must be greater than zero")
    dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    await db.loans.update_one({"_id":loan["_id"]},{"$inc":{"principal_paid":principal,"interest_paid":interest}})
    txid=await insert_tx(tenant_id,loan["member_id"],"loan_repayment",principal+interest,body.account,user,loan_id=body.loan_id,principal=principal,interest=interest,payment_category="loan_emi",date=dt,note=body.note)
    updated=await db.loans.find_one({"_id":loan["_id"]})
    if updated["principal_paid"]>=updated["principal"]-0.01 and updated.get("interest_paid",0)>=updated.get("expected_interest",0)-0.01:
        await db.loans.update_one({"_id":loan["_id"]},{"$set":{"status":"closed","closed_at":datetime.now(timezone.utc)}})
    return {"id":txid,"amount":round(principal+interest,2),"principal":principal,"interest":interest}

@router.post("/{tenant_id}/loan-requests")
async def loan_request(tenant_id:str,body:LoanRequestCreate,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"] not in ("member","group_admin"): raise HTTPException(403,"Only group members can request an advance loan")
    doc={"tenant_id":tenant_id,"member_id":str(user.get("member_id")),"amount":body.amount,"purpose":body.purpose,"status":"pending","created_at":datetime.now(timezone.utc)}
    r=await get_db().loan_requests.insert_one(doc); await audit(tenant_id,user,"LOAN_REQUESTED","loan_request",str(r.inserted_id),{"amount":body.amount}); return {"id":str(r.inserted_id)}

@router.get("/{tenant_id}/loan-requests")
async def loan_requests(tenant_id:str,user=Depends(current_user)):
    await tenant_guard(user,tenant_id); q={"tenant_id":tenant_id}
    if user["role"] in ("member","group_admin") and user.get("member_id"):q["member_id"]=str(user.get("member_id"))
    rows=await get_db().loan_requests.find(q).sort("created_at",-1).to_list(2000); return [serialize(x) for x in rows]

@router.patch("/{tenant_id}/loan-requests/{request_id}")
async def decide_loan_request(tenant_id:str,request_id:str,body:LoanRequestDecision,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); req=await db.loan_requests.find_one({"_id":parse_oid(request_id),"tenant_id":tenant_id})
    if not req: raise HTTPException(404,"Loan request not found")
    if req["status"]!="pending": raise HTTPException(400,"Request already decided")
    if body.decision=="rejected":
        await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"rejected","decision_note":body.note,"decided_at":datetime.now(timezone.utc)}})
        await audit(tenant_id,user,"LOAN_REQUEST_REJECTED","loan_request",request_id); return {"ok":True}
    principal=body.principal or req["amount"]; rate=body.interest_rate if body.interest_rate is not None else 2.0; months=body.months or 2
    await db.loan_requests.update_one({"_id":req["_id"]},{"$set":{"status":"approved","decision_note":body.note,"decided_at":datetime.now(timezone.utc)}})
    await create_loan(tenant_id,LoanCreate(member_id=req["member_id"],principal=principal,interest_rate=rate,months=months,account=body.account,purpose=req.get("purpose", "")),user)
    return {"ok":True}

async def ensure_expense_allocations(tenant_id:str,expense_doc):
    db=get_db()
    if await db.transactions.find_one({"tenant_id":tenant_id,"expense_id":str(expense_doc["_id"]),"type":"expense_allocation"}):
        return
    shares=await db.shares.find({"tenant_id":tenant_id,"status":"active"}).sort("share_no",1).to_list(10000)
    if not shares: return
    total=float(expense_doc.get("amount",0) or 0); per=round(total/len(shares),2); allocated=0.0
    for i,share in enumerate(shares):
        amount=round(total-allocated,2) if i==len(shares)-1 else per
        allocated=round(allocated+amount,2)
        await db.transactions.update_one(
            {"tenant_id":tenant_id,"expense_id":str(expense_doc["_id"]),"share_id":str(share["_id"]),"type":"expense_allocation"},
            {"$setOnInsert":{
                "tenant_id":tenant_id,"member_id":str(share["member_id"]),"share_id":str(share["_id"]),
                "share_no":int(share["share_no"]),"expense_id":str(expense_doc["_id"]),"type":"expense_allocation",
                "amount":-amount,"account":expense_doc.get("account","cash"),"date":expense_doc.get("date"),
                "created_at":expense_doc.get("created_at",datetime.now(timezone.utc)),
                "payment_category":"group_expense_allocation","note":expense_doc.get("category", "Group expense")
            }},upsert=True)

@router.post("/{tenant_id}/expenses")
async def expense(tenant_id:str,body:ExpenseCreate,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); now=datetime.now(timezone.utc); dt=datetime.combine(body.date or date.today(),datetime.min.time(),tzinfo=timezone.utc)
    if not await db.expense_categories.find_one({"tenant_id":tenant_id,"name":body.category}):
        await db.expense_categories.insert_one({"tenant_id":tenant_id,"name":body.category,"created_at":now})
    r=await db.expenses.insert_one({**body.model_dump(),"tenant_id":tenant_id,"date":dt,"created_at":now,"proof_url":None,"proof_public_id":None})
    created=await db.expenses.find_one({"_id":r.inserted_id})
    await ensure_expense_allocations(tenant_id,created)
    await audit(tenant_id,user,"EXPENSE_CREATED","expense",str(r.inserted_id),{"amount":body.amount}); return {"id":str(r.inserted_id)}

@router.get("/{tenant_id}/expenses")
async def expenses(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); rows=await get_db().expenses.find({"tenant_id":tenant_id}).sort("date",-1).to_list(5000); return [serialize(x) for x in rows]

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
    if exp.get("proof_public_id"): delete_asset(exp["proof_public_id"],exp.get("proof_resource_type","image"))
    rt="raw" if file.content_type=="application/pdf" else "image"
    r=upload_bytes(data,public_id=f"proof-{expense_id}",folder=f"bharat-bachat/tenants/{tenant_id}/expenses/{expense_id}",resource_type=rt)
    await db.expenses.update_one({"_id":exp["_id"]},{"$set":{"proof_url":r.get("secure_url"),"proof_public_id":r.get("public_id"),"proof_resource_type":rt,"proof_original_filename":file.filename,"proof_content_type":file.content_type}})
    await audit(tenant_id,user,"EXPENSE_PROOF_UPLOADED","expense",expense_id); return {"ok":True,"proof_url":r.get("secure_url")}

@router.delete("/{tenant_id}/expenses/{expense_id}/proof")
async def delete_expense_proof(tenant_id:str,expense_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); db=get_db(); exp=await db.expenses.find_one({"_id":parse_oid(expense_id),"tenant_id":tenant_id})
    if not exp: raise HTTPException(404,"Expense not found")
    if exp.get("proof_public_id"): delete_asset(exp["proof_public_id"],exp.get("proof_resource_type","image"))
    await db.expenses.update_one({"_id":exp["_id"]},{"$set":{"proof_url":None,"proof_public_id":None}}); await audit(tenant_id,user,"EXPENSE_PROOF_DELETED","expense",expense_id); return {"ok":True}

@router.get("/{tenant_id}/passbook/{member_id}")
async def passbook(tenant_id:str,member_id:str,from_date:date|None=None,to_date:date|None=None,share_no:int|None=None,user=Depends(current_user)):
    await tenant_guard(user,tenant_id)
    if user["role"]=="group_admin" and str(user.get("member_id"))==member_id:
        pass
    if user["role"]=="member" and str(user.get("member_id"))!=member_id: raise HTTPException(403,"Member access denied")
    await get_member(get_db(),tenant_id,member_id); q={"tenant_id":tenant_id,"member_id":member_id}
    if from_date or to_date:
        q["date"]={}
        if from_date:q["date"]["$gte"]=datetime.combine(from_date,datetime.min.time(),tzinfo=timezone.utc)
        if to_date:q["date"]["$lte"]=datetime.combine(to_date,datetime.max.time(),tzinfo=timezone.utc)
    if share_no:q["share_no"]=share_no
    # Backfill member-visible expense allocations for older expense records so
    # historical expenses also appear in the member passbook.
    expenses=await get_db().expenses.find({"tenant_id":tenant_id}).to_list(10000)
    for exp in expenses:
        await ensure_expense_allocations(tenant_id,exp)
    rows=await get_db().transactions.find(q).sort("date",1).to_list(10000); balance=0; out=[]
    for r in rows:
        balance+=float(r.get("amount",0)); x=serialize(r); x["running_balance"]=round(balance,2); out.append(x)
    return out

@router.get("/{tenant_id}/audit")
async def audit_logs(tenant_id:str,user=Depends(admin_user)):
    await tenant_guard(user,tenant_id); rows=await get_db().audit_logs.find({"tenant_id":tenant_id}).sort("created_at",-1).to_list(5000); return [serialize(x) for x in rows]
