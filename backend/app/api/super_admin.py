from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..db import get_db
from ..deps import require_roles, parse_oid
from ..models import TenantCreate, TenantSettingsUpdate, AdminCreate, UserStatus, PasswordReset
from ..core.security import hash_password, normalize_phone
from ..core.cloudinary import upload_bytes, delete_asset
from ..core.config import settings
from ..share_service import ensure_member_shares
from ..audit import audit
router=APIRouter(prefix="/super-admin",tags=["super-admin"])

def serialize(d):
    x=dict(d); x["_id"]=str(x["_id"])
    if x.get("tenant_id"): x["tenant_id"]=str(x["tenant_id"])
    x.pop("password_hash",None); return x

@router.get("/tenants")
async def tenants(user=Depends(require_roles("super_admin"))):
    return [serialize(x) for x in await get_db().tenants.find().sort("created_at",-1).to_list(1000)]

@router.post("/tenants")
async def create_tenant(body:TenantCreate,user=Depends(require_roles("super_admin"))):
    db=get_db(); code=body.code.strip().upper()
    if await db.tenants.find_one({"$or":[{"name":body.name.strip()},{"code":code}]}): raise HTTPException(409,"Group name or code already exists")
    now=datetime.now(timezone.utc); doc={"name":body.name.strip(),"code":code,"opening_cash":body.opening_cash,"opening_bank":body.opening_bank,"kist_per_share":body.kist_per_share,"bc_due_date":10,"bc_per_day_penalty":0,"loan_interest_rate_per_month":2,"loan_per_day_penalty":0,"loan_due_date":10,"required_admin_approvals":1,"max_loan_multiplier":20,"min_group_reserve_balance":0,"min_loan_amount":10000,"min_advance_apply_months":2,"logo_url":body.logo_url,"logo_public_id":None,"active":True,"created_at":now}
    r=await db.tenants.insert_one(doc); await audit(str(r.inserted_id),user,"GROUP_CREATED","tenant",str(r.inserted_id),{"name":body.name,"code":code}); return {"id":str(r.inserted_id)}

@router.patch("/tenants/{tenant_id}/settings")
async def tenant_settings(tenant_id:str,body:TenantSettingsUpdate,user=Depends(require_roles("super_admin"))):
    db=get_db(); values=body.model_dump(exclude_none=True); r=await db.tenants.update_one({"_id":parse_oid(tenant_id)},{"$set":values})
    if not r.matched_count: raise HTTPException(404,"Group not found")
    await audit(tenant_id,user,"GROUP_SETTINGS_UPDATED","tenant",tenant_id,values); return {"ok":True,**values}

@router.post("/tenants/{tenant_id}/logo")
async def tenant_logo(tenant_id:str,file:UploadFile=File(...),user=Depends(require_roles("super_admin"))):
    db=get_db(); t=await db.tenants.find_one({"_id":parse_oid(tenant_id)})
    if not t: raise HTTPException(404,"Group not found")
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Only JPG, PNG or WebP images are allowed")
    data=await file.read()
    if len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    if t.get("logo_public_id"): delete_asset(t["logo_public_id"])
    r=upload_bytes(data,public_id="logo",folder=f"bharat-bachat/tenants/{tenant_id}",resource_type="image")
    await db.tenants.update_one({"_id":t["_id"]},{"$set":{"logo_url":r.get("secure_url"),"logo_public_id":r.get("public_id")}}); await audit(tenant_id,user,"GROUP_LOGO_UPDATED","tenant",tenant_id); return {"logo_url":r.get("secure_url")}

@router.patch("/tenants/{tenant_id}/status")
async def tenant_status(tenant_id:str,body:UserStatus,user=Depends(require_roles("super_admin"))):
    r=await get_db().tenants.update_one({"_id":parse_oid(tenant_id)},{"$set":{"active":body.active}})
    if not r.matched_count: raise HTTPException(404,"Group not found")
    await get_db().users.update_many({"tenant_id":parse_oid(tenant_id)},{"$set":{"active":body.active}}); await audit(tenant_id,user,"GROUP_STATUS_CHANGED","tenant",tenant_id,{"active":body.active}); return {"ok":True}

@router.post("/admins")
async def create_admin(body:AdminCreate,user=Depends(require_roles("super_admin"))):
    db=get_db(); tid=parse_oid(body.tenant_id)
    if not await db.tenants.find_one({"_id":tid}): raise HTTPException(404,"Group not found")
    phone = normalize_phone(body.phone)
    if await db.users.find_one({"phone":phone}): raise HTTPException(409,"Phone already registered")
    now=datetime.now(timezone.utc)
    admin_name=f"{body.first_name} {body.last_name}".strip()
    r=await db.users.insert_one({"tenant_id":tid,"phone":phone,"password_hash":hash_password(body.password),"name":admin_name,"email":body.email,"role":"group_admin","active":True,"must_change_password":True,"password_changed_at":now,"created_at":now})
    member={"tenant_id":body.tenant_id,"user_id":str(r.inserted_id),"first_name":body.first_name,"last_name":body.last_name,"phone":phone,"secondary_phone":"","email":body.email,"address":"","shares":body.shares,"active":True,"profile_image_url":None,"profile_picture_url":None,"profile_image_public_id":None,"created_at":now,"is_group_admin_member":True}
    mr=await db.members.insert_one(member)
    await db.users.update_one({"_id":r.inserted_id},{"$set":{"member_id":str(mr.inserted_id)}})
    member["_id"]=mr.inserted_id
    await ensure_member_shares(member)
    await audit(body.tenant_id,user,"ADMIN_CREATED","user",str(r.inserted_id),{"member_id":str(mr.inserted_id),"shares":body.shares}); return {"id":str(r.inserted_id),"member_id":str(mr.inserted_id),"shares":body.shares}

@router.get("/admins")
async def admins(user=Depends(require_roles("super_admin"))):
    return [serialize(x) for x in await get_db().users.find({"role":"group_admin"}).sort("created_at",-1).to_list(2000)]

@router.patch("/users/{user_id}/status")
async def status(user_id:str,body:UserStatus,user=Depends(require_roles("super_admin"))):
    target=await get_db().users.find_one({"_id":parse_oid(user_id)})
    if not target: raise HTTPException(404,"User not found")
    if target.get("role")=="super_admin": raise HTTPException(403,"Super Admin status cannot be changed here")
    await get_db().users.update_one({"_id":target["_id"]},{"$set":{"active":body.active}}); await audit(str(target.get("tenant_id")),user,"USER_STATUS_CHANGED","user",user_id,{"active":body.active}); return {"ok":True}

@router.post("/users/{user_id}/reset-password")
async def reset(user_id:str,body:PasswordReset,user=Depends(require_roles("super_admin"))):
    db=get_db(); target=await db.users.find_one({"_id":parse_oid(user_id)})
    if not target: raise HTTPException(404,"User not found")
    if target.get("role")=="super_admin": raise HTTPException(403,"Super Admin password is managed directly in the database")
    now=datetime.now(timezone.utc); await db.users.update_one({"_id":target["_id"]},{"$set":{"password_hash":hash_password(body.password),"must_change_password":True,"password_changed_at":now,"password_reset_at":now,"password_reset_by":str(user["_id"])}})
    await audit(str(target.get("tenant_id")),user,"PASSWORD_RESET","user",user_id); return {"ok":True}
