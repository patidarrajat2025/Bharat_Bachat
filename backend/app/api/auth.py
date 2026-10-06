from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from ..db import get_db
from ..models import LoginRequest, ChangePassword
from ..core.security import verify_password, create_access_token, hash_password, normalize_phone, phone_lookup_candidates, phone_legacy_regex
from ..deps import current_user, current_user_raw
from ..core.cloudinary import upload_bytes, delete_asset
from ..core.config import settings
from ..share_service import ensure_member_shares, ensure_group_admin_member

router = APIRouter(prefix="/auth", tags=["auth"])

async def _user_payload(user):
    db=get_db()
    if user.get("role")=="group_admin":
        await ensure_group_admin_member(user)
    profile_image_url=user.get("profile_image_url") or user.get("profile_picture_url")
    if user.get("member_id"):
        try:
            from bson import ObjectId
            m=await db.members.find_one({"_id": ObjectId(str(user["member_id"]))})
            member_image=(m or {}).get("profile_image_url") or (m or {}).get("profile_picture_url")
            if member_image:
                profile_image_url=member_image
        except Exception:
            pass
    return {"id": str(user["_id"]), "phone": user["phone"], "name": user.get("name", ""),
            "role": user["role"], "tenant_id": str(user.get("tenant_id")) if user.get("tenant_id") else None,
            "must_change_password": bool(user.get("must_change_password", False)), "member_id": user.get("member_id"),
            "profile_image_url": profile_image_url, "profile_picture_url": profile_image_url}


@router.post("/login")
async def login(body: LoginRequest):
    db = get_db()
    canonical_phone = normalize_phone(body.phone)
    if len(canonical_phone) < 6:
        raise HTTPException(status_code=401, detail="Invalid phone or password")

    candidates = phone_lookup_candidates(body.phone)
    user = await db.users.find_one({"phone": {"$in": candidates}})
    if not user:
        legacy_regex = phone_legacy_regex(body.phone)
        if legacy_regex:
            user = await db.users.find_one({"phone": {"$regex": legacy_regex, "$options": "i"}})

    valid_password = False
    # Existing projects have used both `password_hash` and `passwordHash`.
    # Prefer a bcrypt hash and keep a one-time compatibility path for older
    # plaintext `password` records. Successful legacy records are migrated.
    password_hash = None
    if user:
        password_hash = user.get("password_hash") or user.get("passwordHash") or user.get("hashed_password")
        if isinstance(password_hash, str) and password_hash:
            try:
                valid_password = verify_password(body.password, password_hash)
            except Exception:
                valid_password = False

    if user and not valid_password and isinstance(user.get("password"), str):
        legacy_password = user["password"]
        # Prefer bcrypt if the legacy field already contains a bcrypt hash;
        # otherwise support the old plaintext development records once.
        if legacy_password.startswith(("$2a$", "$2b$", "$2y$")):
            try:
                valid_password = verify_password(body.password, legacy_password)
            except Exception:
                valid_password = False
        else:
            valid_password = body.password == legacy_password

        if valid_password:
            password_hash = hash_password(body.password)
            await db.users.update_one(
                {"_id": user["_id"]},
                {"$set": {"password_hash": password_hash, "phone": canonical_phone},
                 "$unset": {"password": "", "passwordHash": "", "hashed_password": ""}},
            )
            user["password_hash"] = password_hash
            user["phone"] = canonical_phone

    if not user or not valid_password:
        raise HTTPException(status_code=401, detail="Invalid phone or password")
    if not user.get("active", True):
        raise HTTPException(status_code=403, detail="Account inactive")

    # Normalize legacy phone formatting after a successful login so subsequent
    # authentication uses the indexed canonical value.
    if user.get("phone") != canonical_phone:
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"phone": canonical_phone}})
        user["phone"] = canonical_phone

    token = create_access_token(str(user["_id"]), user["role"], str(user.get("tenant_id")) if user.get("tenant_id") else None, user.get("password_changed_at"))
    return {"access_token": token, "token_type": "bearer", "user": await _user_payload(user)}

@router.get("/me")
async def me(user=Depends(current_user)):
    return await _user_payload(user)


@router.post("/change-password")
async def change_password(body: ChangePassword, user=Depends(current_user_raw)):
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await get_db().users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "password_hash": hash_password(body.new_password),
            "must_change_password": False,
            "password_changed_at": now,
        }}
    )
    token = create_access_token(str(user["_id"]), user["role"], str(user.get("tenant_id")) if user.get("tenant_id") else None, now)
    return {"access_token": token, "token_type": "bearer", "must_change_password": False}


@router.post("/profile-image")
async def upload_profile_image(file: UploadFile = File(...), user=Depends(current_user)):
    if not user.get("member_id"):
        raise HTTPException(400, "Profile photo is available for member accounts.")
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Only JPG, PNG or WebP images are allowed")
    data=await file.read()
    if len(data)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    from bson import ObjectId
    m=await get_db().members.find_one({"_id":ObjectId(str(user["member_id"]))})
    if not m: raise HTTPException(404,"Member profile not found")
    old_public_id=m.get("profile_image_public_id")
    import uuid
    r=upload_bytes(data,public_id=f"member-{user['member_id']}-{uuid.uuid4().hex[:10]}",folder=f"bharat-bachat/tenants/{user.get('tenant_id')}/members/{user['member_id']}",resource_type="image")
    await get_db().members.update_one({"_id":m["_id"]},{"$set":{"profile_image_url":r.get("secure_url"),"profile_picture_url":r.get("secure_url"),"profile_image_public_id":r.get("public_id")}})
    if old_public_id and old_public_id!=r.get("public_id"):
        try: delete_asset(old_public_id)
        except Exception: pass
    return {"ok":True,"profile_image_url":r.get("secure_url")}

@router.delete("/profile-image")
async def delete_profile_image(user=Depends(current_user)):
    if not user.get("member_id"): raise HTTPException(400,"Profile photo is available for member accounts.")
    from bson import ObjectId
    m=await get_db().members.find_one({"_id":ObjectId(str(user["member_id"]))})
    if not m: raise HTTPException(404,"Member profile not found")
    if m.get("profile_image_public_id"): delete_asset(m["profile_image_public_id"])
    await get_db().members.update_one({"_id":m["_id"]},{"$set":{"profile_image_url":None,"profile_picture_url":None,"profile_image_public_id":None}})
    return {"ok":True}
