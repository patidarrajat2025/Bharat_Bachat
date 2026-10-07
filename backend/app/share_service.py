from datetime import datetime, timezone
from .db import get_db


async def ensure_member_shares(member):
    """Return active shares, creating only missing shares.

    Reads are by far the most common path. The old implementation performed an
    upsert for every share on every members/dashboard request. That turned a
    simple GET into multiple writes. We now read first and only reconcile when
    the stored share count is actually behind the member's configured count.
    """
    db = get_db()
    tenant_id = str(member["tenant_id"])
    member_id = str(member["_id"])
    count = max(1, int(member.get("shares", 1) or 1))
    rows = await db.shares.find({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}).sort("share_no", 1).to_list(200)
    existing_numbers = {int(x.get("share_no", 0)) for x in rows}
    missing = [no for no in range(1, count + 1) if no not in existing_numbers]
    if missing:
        now = datetime.now(timezone.utc)
        ops = []
        from pymongo import UpdateOne
        for no in missing:
            ops.append(UpdateOne(
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
            ))
        if ops:
            await db.shares.bulk_write(ops, ordered=False)
            rows = await db.shares.find({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}).sort("share_no", 1).to_list(200)
    return rows


async def ensure_group_admin_member(user):
    """Ensure every Group Admin has a real member record + active shares.

    This compatibility helper is intentionally idempotent and cheap. Normal
    authenticated requests skip it when member_id is already present.
    """
    if user.get("role") != "group_admin" or not user.get("tenant_id"):
        return user.get("member_id")
    db = get_db()
    tenant_id = str(user["tenant_id"])
    user_id = str(user["_id"])
    existing = await db.members.find_one({"user_id": user_id})
    if existing:
        if str(existing.get("tenant_id")) != tenant_id:
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
    if str(user.get("member_id") or "") != member_id:
        await db.users.update_one({"_id": user["_id"]}, {"$set": {"member_id": member_id}})
        user["member_id"] = member_id
    await ensure_member_shares(existing)
    return member_id
