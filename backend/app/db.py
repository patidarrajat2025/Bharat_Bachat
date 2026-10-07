from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .core.config import settings
client=None; db=None
async def connect_db():
    global client, db
    client=AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=10000)
    await client.admin.command("ping"); db=client[settings.mongodb_db]; await create_indexes()
async def close_db():
    global client
    if client: client.close()
def get_db()->AsyncIOMotorDatabase:
    if db is None: raise RuntimeError("Database is not initialized")
    return db
async def create_indexes():
    d=get_db()
    await d.users.create_index("phone",unique=True)
    await d.tenants.create_index("name",unique=True)
    await d.tenants.create_index("code",unique=True)
    await d.members.create_index([("tenant_id",1),("phone",1)],unique=True)
    await d.users.create_index([("tenant_id",1),("role",1)])
    await d.transactions.create_index([("tenant_id",1),("date",-1)])
    await d.transactions.create_index([("tenant_id",1),("member_id",1),("date",-1)])
    await d.transactions.create_index([("tenant_id",1),("type",1),("date",-1)])
    await d.transactions.create_index([("tenant_id",1),("member_id",1),("type",1),("date",-1)])
    await d.transactions.create_index([("tenant_id",1),("share_id",1),("type",1),("date",-1)])
    await d.transactions.create_index([("tenant_id",1),("expense_id",1),("type",1)])
    await d.transactions.create_index([("tenant_id",1),("loan_id",1),("date",-1)])
    await d.shares.create_index([("tenant_id",1),("member_id",1),("share_no",1)], unique=True)
    await d.shares.create_index([("tenant_id",1),("share_code",1)], unique=True)
    await d.shares.create_index([("tenant_id",1),("status",1),("member_id",1)])
    await d.transactions.create_index([("tenant_id",1),("share_id",1),("date",-1)])
    await d.loans.create_index([("tenant_id",1),("member_id",1),("status",1)])
    await d.loans.create_index([("tenant_id",1),("status",1),("created_at",-1)])
    await d.loan_requests.create_index([("tenant_id",1),("member_id",1),("created_at",-1)])
    await d.loan_requests.create_index([("tenant_id",1),("status",1),("created_at",-1)])
    await d.expenses.create_index([("tenant_id",1),("date",-1)])
    await d.expenses.create_index([("tenant_id",1),("account",1),("date",-1)])
    await d.expense_categories.create_index([("tenant_id",1),("name",1)],unique=True)
    await d.audit_logs.create_index([("tenant_id",1),("created_at",-1)])
    await d.notifications.create_index([("tenant_id",1),("recipient_role",1),("recipient_member_id",1),("created_at",-1)])
    await d.notifications.create_index([("tenant_id",1),("source_key",1)], unique=True, sparse=True)
