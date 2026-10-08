from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .core.config import settings

client = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=15000,
        maxPoolSize=20,
        minPoolSize=2,
        appname="bharat-bachat-api",
    )
    await client.admin.command("ping")
    db = client[settings.mongodb_db]
    await create_indexes()


async def close_db():
    global client
    if client:
        client.close()


def get_db() -> AsyncIOMotorDatabase:
    if db is None:
        raise RuntimeError("Database is not initialized")
    return db


async def create_indexes():
    d = get_db()
    # Lightweight one-time-compatible field backfill. MongoDB is schemaless, so
    # legacy transactions are normalized into the explicit accounting buckets
    # used by the new dashboard/loan engine without changing their amount.
    await d.transactions.update_many({"type":"loan_repayment","principal_repaid":{"$exists":False}}, [{"$set":{"principal_repaid":{"$ifNull":["$principal",0]},"loan_interest_collected":{"$ifNull":["$interest",0]},"loan_penalty_collected":{"$ifNull":["$loan_penalty_collected",0]}}}])
    await d.transactions.update_many({"type":"penalty","$or":[{"payment_category":"bc"},{"penalty_category":"bc"}],"bc_regular_kist_penalty":{"$exists":False}}, [{"$set":{"bc_regular_kist_penalty":{"$ifNull":["$amount",0]}}}])
    await d.transactions.update_many({"type":"penalty","$or":[{"payment_category":"loan"},{"penalty_category":"loan"}],"loan_penalty_collected":{"$exists":False}}, [{"$set":{"loan_penalty_collected":{"$ifNull":["$amount",0]}}}])
    await d.transactions.update_many({"type":"interest","payment_category":"other_interest","other_interest":{"$exists":False}}, [{"$set":{"other_interest":{"$ifNull":["$amount",0]}}}])
    # Authentication / tenant isolation
    await d.users.create_index("phone", unique=True)
    await d.users.create_index([("tenant_id", 1), ("role", 1)])
    await d.users.create_index([("tenant_id", 1), ("member_id", 1)])
    await d.tenants.create_index("name", unique=True)
    await d.tenants.create_index("code", unique=True)

    # Member / share lookups. These are used on almost every operational screen.
    await d.members.create_index([("tenant_id", 1), ("phone", 1)], unique=True)
    await d.members.create_index([("tenant_id", 1), ("active", 1), ("first_name", 1)])
    await d.shares.create_index([("tenant_id", 1), ("member_id", 1), ("share_no", 1)], unique=True)
    await d.shares.create_index([("tenant_id", 1), ("member_id", 1), ("status", 1), ("share_no", 1)])
    await d.shares.create_index([("tenant_id", 1), ("status", 1)])
    await d.shares.create_index([("tenant_id", 1), ("share_code", 1)], unique=True)

    # Ledger / accounting query patterns.
    await d.transactions.create_index([("tenant_id", 1), ("date", -1)])
    await d.transactions.create_index([("tenant_id", 1), ("member_id", 1), ("date", -1)])
    await d.transactions.create_index([("tenant_id", 1), ("share_id", 1), ("date", -1)])
    await d.transactions.create_index([("tenant_id", 1), ("type", 1), ("date", -1)])
    await d.transactions.create_index([("tenant_id", 1), ("expense_id", 1), ("type", 1)])
    await d.transactions.create_index([("tenant_id", 1), ("bc_penalty_key", 1)], unique=True, partialFilterExpression={"bc_penalty_key":{"$type":"string"}})
    await d.transactions.create_index([("tenant_id", 1), ("loan_interest_key", 1)])
    await d.transactions.create_index([("tenant_id", 1), ("loan_penalty_key", 1)])

    await d.loans.create_index([("tenant_id", 1), ("member_id", 1), ("status", 1), ("created_at", -1)])
    await d.loans.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("member_id", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("member_id", 1), ("status", 1)])

    await d.expenses.create_index([("tenant_id", 1), ("date", -1)])
    await d.expense_categories.create_index([("tenant_id", 1), ("name", 1)], unique=True)
    await d.audit_logs.create_index([("tenant_id", 1), ("created_at", -1)])

    # Notification list / source de-duplication.
    await d.notifications.create_index([("tenant_id", 1), ("recipient_role", 1), ("recipient_member_id", 1), ("dismissed", 1), ("created_at", -1)])
    await d.notifications.create_index([("tenant_id", 1), ("source_key", 1)], unique=True, sparse=True)
