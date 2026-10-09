from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne
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

    # New financial writes carry exact paise alongside the legacy float field.
    # Existing records remain backward-compatible and are normalized lazily by
    # read models; no destructive migration is performed at application startup.
    await d.transactions.create_index([("tenant_id", 1), ("amount_minor", 1)])
    await d.transactions.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string","$ne":""}}, name="tenant_transaction_idempotency_unique")
    await d.loans.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string","$ne":""}}, name="tenant_loan_idempotency_unique")
    await d.loan_requests.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string","$ne":""}}, name="tenant_loan_request_idempotency_unique")
    await d.expenses.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string","$ne":""}}, name="tenant_expense_idempotency_unique")
    await d.operation_locks.create_index("operation_key", unique=True, name="operation_lock_unique")
    await d.operation_locks.create_index("expires_at", expireAfterSeconds=0, name="operation_lock_ttl")
    await d.transactions.create_index([("tenant_id", 1), ("transaction_ref", 1)], unique=True, sparse=True)
    # Materialized financial feed used by mobile infinite-scroll/accounting screens.
    # It keeps UI pagination at MongoDB level instead of slicing a 10k-row Python list.
    await d.financial_feed.create_index([("tenant_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("account", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("type", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("member_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("source_key", 1)], unique=True)
    await d.expenses.create_index([("tenant_id", 1), ("amount_minor", 1)])

    # Ledger / accounting query patterns.
    await d.transactions.create_index([("tenant_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.transactions.create_index([("tenant_id", 1), ("member_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
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


async def backfill_financial_feed(tenant_id: str | None = None):
    """Idempotently materialize legacy transactions/expenses into financial_feed.

    This is a read-model backfill only: source financial documents are never modified.
    New writes populate the feed synchronously in group API helpers.
    """
    d=get_db()
    ops=[]
    tx_query={"tenant_id":tenant_id} if tenant_id else {}
    exp_query={"tenant_id":tenant_id} if tenant_id else {}
    async for x in d.transactions.find(tx_query, {"_id":1,"tenant_id":1,"member_id":1,"type":1,"amount":1,"amount_minor":1,"account":1,"date":1,"created_at":1,"note":1,"payment_category":1,"loan_interest_collected":1,"loan_penalty_collected":1,"bc_regular_kist_penalty":1,"interest":1,"principal":1,"share_no":1,"share_id":1,"expense_id":1}):
        amount=float(x.get("amount",0) or 0)
        doc={"source_key":f"tx:{x['_id']}","source_type":"transaction","source_id":str(x['_id']),"tenant_id":x.get("tenant_id"),"member_id":str(x.get("member_id")) if x.get("member_id") else None,"type":x.get("type","transaction"),"amount":amount,"amount_minor":int(x.get("amount_minor",round(amount*100)) or 0),"account":x.get("account","cash"),"date":x.get("date") or x.get("created_at"),"created_at":x.get("created_at") or x.get("date"),"note":x.get("note","") or "","payment_category":x.get("payment_category"),"loan_interest_collected":float(x.get("loan_interest_collected",x.get("interest",0)) or 0),"loan_penalty_collected":float(x.get("loan_penalty_collected",0) or 0),"bc_regular_kist_penalty":float(x.get("bc_regular_kist_penalty",0) or 0),"interest":float(x.get("interest",0) or 0),"principal":float(x.get("principal",0) or 0),"share_no":x.get("share_no"),"share_id":str(x.get("share_id")) if x.get("share_id") else None,"expense_id":str(x.get("expense_id")) if x.get("expense_id") else None}
        ops.append(UpdateOne({"source_key":doc["source_key"]},{"$set":doc,"$setOnInsert":{"created_at":doc["created_at"]}},upsert=True))
        if len(ops)>=500:
            await d.financial_feed.bulk_write(ops,ordered=False); ops=[]
    async for x in d.expenses.find(exp_query, {"_id":1,"tenant_id":1,"amount":1,"amount_minor":1,"account":1,"date":1,"created_at":1,"category":1,"note":1}):
        amount=float(x.get("amount",0) or 0)
        doc={"source_key":f"expense:{x['_id']}","source_type":"expense","source_id":str(x['_id']),"tenant_id":x.get("tenant_id"),"member_id":None,"type":"expense","amount":-abs(amount),"amount_minor":-abs(int(x.get("amount_minor",round(amount*100)) or 0)),"account":x.get("account","cash"),"date":x.get("date") or x.get("created_at"),"created_at":x.get("created_at") or x.get("date"),"note":x.get("note","") or x.get("category","") or "Expense","category":x.get("category","Expense") or "Expense"}
        ops.append(UpdateOne({"source_key":doc["source_key"]},{"$set":doc,"$setOnInsert":{"created_at":doc["created_at"]}},upsert=True))
        if len(ops)>=500:
            await d.financial_feed.bulk_write(ops,ordered=False); ops=[]
    if ops: await d.financial_feed.bulk_write(ops,ordered=False)
