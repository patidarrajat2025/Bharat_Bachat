from datetime import datetime, timezone
from .db import get_db

async def ensure_member_shares(member):
    db = get_db()
    tenant_id = str(member["tenant_id"])
    member_id = str(member["_id"])
    count = int(member.get("shares", 1))
    now = datetime.now(timezone.utc)
    for no in range(1, count + 1):
        await db.shares.update_one(
            {"tenant_id": tenant_id, "member_id": member_id, "share_no": no},
            {"$setOnInsert": {
                "tenant_id": tenant_id,
                "member_id": member_id,
                "share_no": no,
                "share_code": f"SHR-{member_id[-8:].upper()}-{no:02d}",
                "status": "active",
                "created_at": now,
            }},
            upsert=True,
        )
    return await db.shares.find({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}).sort("share_no", 1).to_list(200)


async def ensure_group_admin_member(user):
    """Ensure every Group Admin has a real member record + active shares.

    Older builds created the member with tenant_id as an ObjectId while the
    rest of the application stores tenant_id as a string. Normalize that
    record here so the admin can use every member/passbook feature reliably.
    """
    if user.get("role") != "group_admin" or not user.get("tenant_id"):
        return user.get("member_id")
    db = get_db()
    tenant_id = str(user["tenant_id"])
    user_id = str(user["_id"])
    existing = await db.members.find_one({"user_id": user_id})
    if existing:
        if str(existing.get("tenant_id")) != tenant_id or existing.get("tenant_id") != tenant_id:
            await db.members.update_one({"_id": existing["_id"]}, {"$set": {"tenant_id": tenant_id, "is_group_admin_member": True}})
            existing = await db.members.find_one({"_id": existing["_id"]})
    else:
        parts = str(user.get("name") or "Group Admin").strip().split(" ", 1)
        now = datetime.now(timezone.utc)
        doc = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "first_name": parts[0],
            "last_name": parts[1] if len(parts) > 1 else "",
            "phone": user.get("phone", ""),
            "secondary_phone": "",
            "email": user.get("email"),
            "address": "",
            "shares": 1,
            "active": True,
            "profile_image_url": None,
            "profile_picture_url": None,
            "profile_image_public_id": None,
            "created_at": now,
            "is_group_admin_member": True,
        }
        result = await db.members.insert_one(doc)
        existing = await db.members.find_one({"_id": result.inserted_id})
    member_id = str(existing["_id"])
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"member_id": member_id}})
    user["member_id"] = member_id
    await ensure_member_shares(existing)
    return member_id
