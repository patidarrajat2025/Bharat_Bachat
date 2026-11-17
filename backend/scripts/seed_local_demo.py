"""Create an idempotent synthetic group/admin/member for LOCAL MongoDB only."""
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from app.core.config import settings
from app.db import connect_db, close_db, get_db
from app.core.security import hash_password, normalize_phone
from app.share_service import ensure_member_shares

async def main():
    if settings.database_mode.lower() != "local":
        raise SystemExit("Refusing to seed: DATABASE_MODE must be local")
    await connect_db(); db=get_db(); now=datetime.now(timezone.utc)
    tenant=await db.tenants.find_one({"code":"BBLOCAL"})
    if not tenant:
        tid=ObjectId(); tenant_doc={"_id":tid,"name":"Bharat Bachat Local Demo","code":"BBLOCAL","opening_cash":100000,"opening_bank":200000,"kist_per_share":500,"bc_due_date":10,"bc_per_day_penalty":0,"loan_interest_rate_per_month":2,"loan_per_day_penalty":0,"loan_due_date":10,"required_admin_approvals":1,"max_loan_multiplier":20,"min_group_reserve_balance":0,"min_loan_amount":1000,"min_advance_apply_months":0,"active":True,"created_at":now}
        await db.tenants.insert_one(tenant_doc); tenant=tenant_doc
    tid=str(tenant["_id"])
    admin_phone=normalize_phone("9999999001")
    admin=await db.users.find_one({"phone":admin_phone})
    if not admin:
        uid=ObjectId(); mid=ObjectId()
        await db.members.insert_one({"_id":mid,"tenant_id":tid,"user_id":str(uid),"first_name":"Local","last_name":"Admin","phone":admin_phone,"secondary_phone":"","address":"Local test data","shares":2,"active":True,"created_at":now,"is_group_admin_member":True})
        await db.users.insert_one({"_id":uid,"tenant_id":tenant["_id"],"member_id":str(mid),"phone":admin_phone,"password_hash":hash_password("LocalAdmin@123"),"name":"Local Admin","role":"group_admin","active":True,"must_change_password":False,"password_changed_at":now,"created_at":now})
        await ensure_member_shares(await db.members.find_one({"_id":mid}))
    member_phone=normalize_phone("9999999002")
    member=await db.users.find_one({"phone":member_phone})
    if not member:
        mid=ObjectId(); uid=ObjectId()
        await db.members.insert_one({"_id":mid,"tenant_id":tid,"user_id":str(uid),"first_name":"Demo","last_name":"Member","phone":member_phone,"secondary_phone":"","address":"Local test data","shares":1,"active":True,"created_at":now})
        await db.users.insert_one({"_id":uid,"tenant_id":tenant["_id"],"member_id":str(mid),"phone":member_phone,"password_hash":hash_password("LocalMember@123"),"name":"Demo Member","role":"member","active":True,"must_change_password":False,"password_changed_at":now,"created_at":now})
        await ensure_member_shares(await db.members.find_one({"_id":mid}))
    print(f"Local demo seeded. Group={tid}; admin={admin_phone} / LocalAdmin@123; member={member_phone} / LocalMember@123")
    await close_db()

if __name__ == "__main__": asyncio.run(main())
