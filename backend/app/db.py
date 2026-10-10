from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from bson import ObjectId
from pymongo import UpdateOne
from .core.config import settings
from .accounting_engine import to_minor, from_minor

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
    # MongoDB partial indexes support $type here, but not $ne. Remove legacy
    # empty-string placeholders before building the unique indexes; missing/null
    # keys remain outside the partial index, while every real string key is unique.
    for collection in (d.transactions, d.loans, d.loan_requests, d.expenses):
        await collection.update_many({"idempotency_key": ""}, {"$unset": {"idempotency_key": ""}})
    await d.transactions.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string"}}, name="tenant_transaction_idempotency_unique")
    await d.loans.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string"}}, name="tenant_loan_idempotency_unique")
    await d.loan_requests.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string"}}, name="tenant_loan_request_idempotency_unique")
    await d.expenses.create_index([("tenant_id", 1), ("idempotency_key", 1)], unique=True, partialFilterExpression={"idempotency_key":{"$type":"string"}}, name="tenant_expense_idempotency_unique")
    await d.operation_locks.create_index("operation_key", unique=True, name="operation_lock_unique")
    await d.operation_locks.create_index("expires_at", expireAfterSeconds=0, name="operation_lock_ttl")
    # A sparse unique index still indexes explicit null values, which caused
    # E11000 on existing production data. Keep uniqueness only for real refs.
    ref_index = await d.transactions.index_information()
    old_ref_index = ref_index.get("tenant_id_1_transaction_ref_1")
    if old_ref_index and old_ref_index.get("unique") and not old_ref_index.get("partialFilterExpression"):
        await d.transactions.drop_index("tenant_id_1_transaction_ref_1")
    await d.transactions.create_index(
        [("tenant_id", 1), ("transaction_ref", 1)],
        unique=True,
        partialFilterExpression={"transaction_ref": {"$type": "string"}},
        name="tenant_transaction_ref_string_unique",
    )
    # Materialized financial feed used by mobile infinite-scroll/accounting screens.
    # It keeps UI pagination at MongoDB level instead of slicing a 10k-row Python list.
    await d.financial_feed.create_index([("tenant_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("account", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("type", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("member_id", 1), ("date", -1), ("created_at", -1), ("_id", -1)])
    await d.financial_feed.create_index([("tenant_id", 1), ("source_key", 1)], unique=True)
    await d.expenses.create_index([("tenant_id", 1), ("amount_minor", 1)])

    # Balanced journal source is idempotent; pending records can be reconciled after a crash.
    await d.journal_entries.create_index([("tenant_id", 1), ("source_key", 1)], unique=True, name="tenant_journal_source_unique")
    await d.journal_entries.create_index([("tenant_id", 1), ("status", 1), ("created_at", 1)])

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
    await d.transactions.create_index([("tenant_id", 1), ("reversal_of", 1)], unique=True, partialFilterExpression={"reversal_of": {"$type": "string"}}, name="tenant_transaction_reversal_unique")

    await d.loans.create_index([("tenant_id", 1), ("member_id", 1), ("status", 1), ("created_at", -1)])
    await d.loans.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("member_id", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await d.loan_requests.create_index([("tenant_id", 1), ("member_id", 1), ("status", 1)])

    await d.expenses.create_index([("tenant_id", 1), ("date", -1)])
    await d.expense_categories.create_index([("tenant_id", 1), ("name", 1)], unique=True)
    await d.audit_logs.create_index([("tenant_id", 1), ("created_at", -1)])
    await d.audit_logs.create_index([("tenant_id", 1), ("event_key", 1)], unique=True, partialFilterExpression={"event_key": {"$type": "string"}}, name="tenant_audit_event_unique")

    # Notification list / source de-duplication.
    await d.notifications.create_index([("tenant_id", 1), ("recipient_role", 1), ("recipient_member_id", 1), ("dismissed", 1), ("created_at", -1)])
    await d.notifications.create_index([("tenant_id", 1), ("source_key", 1)], unique=True, sparse=True)

    # Recover pending journals and safely migrate legacy source rows that have no
    # journal marker. Unknown positive/negative transaction types go to suspense,
    # never straight into profit. Transfers without explicit paired accounts are
    # blocked and surfaced by the reconciliation endpoint rather than guessed.
    from .journal import build_journal, build_expense_journal, reverse_journal
    tx_recovery_query={"$or":[
        {"journal_status":"pending"},
        {"journal_status":{"$exists":False}},
        {"journal_status":None},
        # Revisit recoverable blocks: an original journal or transfer metadata
        # may have been repaired after the previous startup attempt.
        {"journal_status":{"$in":["blocked_missing_original_journal","blocked_missing_transfer_legs","blocked_error"]}},
        {"type":{"$in":["transfer","cash_bank_transfer"]},"journal_status":"not_applicable"},
    ]}
    async for source in d.transactions.find(tx_recovery_query):
        tenant_id=source.get("tenant_id")
        try:
            typ=source.get("type")
            if typ == "expense_allocation":
                await d.transactions.update_one({"_id":source["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":"not_applicable"}})
                continue
            if typ == "expense" and source.get("expense_id"):
                # Older versions could materialize an expense source document and
                # a linked transaction shadow. Journal the canonical expense row
                # only; posting both would double-count the same cash movement.
                linked_id=str(source.get("expense_id"))
                linked_expense=await d.expenses.find_one({"_id":ObjectId(linked_id),"tenant_id":tenant_id}) if ObjectId.is_valid(linked_id) else None
                if linked_expense:
                    await d.transactions.update_one({"_id":source["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":"not_applicable"}})
                    continue
            journal=None
            if typ == "reversal" and source.get("reversal_of"):
                original_id=str(source.get("reversal_of"))
                original=await d.transactions.find_one({"_id":ObjectId(original_id),"tenant_id":tenant_id}) if ObjectId.is_valid(original_id) else None
                original_source_key=f"tx:{original_id}"
                original_journal=await d.journal_entries.find_one({"source_key":original_source_key,"tenant_id":tenant_id,"balanced":True})
                if not original_journal and original:
                    original_journal=build_journal(original)
                    if original_journal:
                        original_doc={"tenant_id":tenant_id,"source_type":"transaction","source_id":original_id,"source_key":original_source_key,
                                      "status":"pending",**original_journal,"created_at":original.get("created_at") or datetime.now(timezone.utc)}
                        await d.journal_entries.update_one({"tenant_id":tenant_id,"source_key":original_source_key},{"$setOnInsert":original_doc},upsert=True)
                        original_journal=await d.journal_entries.find_one({"tenant_id":tenant_id,"source_key":original_source_key,"balanced":True})
                if original_journal and int(original_journal.get("debit_minor",-1)) == int(original_journal.get("credit_minor",-2)):
                    await d.journal_entries.update_one({"tenant_id":tenant_id,"source_key":original_source_key},{"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
                    if original:
                        await d.transactions.update_one({"_id":original["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":"posted"}})
                    original_journal["status"]="posted"
                    journal=reverse_journal(original_journal)
            else:
                journal=build_journal(source)
            if not journal:
                marker="blocked_missing_transfer_legs" if typ in ("transfer","cash_bank_transfer") else ("blocked_missing_original_journal" if typ=="reversal" else "not_applicable")
                await d.transactions.update_one({"_id":source["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":marker,"journal_blocked_at":datetime.now(timezone.utc)}})
                continue
            source_key=f"tx:{source['_id']}"
            journal_doc={"tenant_id":tenant_id,"source_type":"transaction","source_id":str(source["_id"]),"source_key":source_key,
                         "status":"pending",**journal,"created_at":source.get("created_at") or datetime.now(timezone.utc)}
            await d.journal_entries.update_one({"tenant_id":tenant_id,"source_key":source_key},{"$setOnInsert":journal_doc},upsert=True)
            stored=await d.journal_entries.find_one({"tenant_id":tenant_id,"source_key":source_key})
            if not stored or not stored.get("balanced") or int(stored.get("debit_minor",-1)) != int(stored.get("credit_minor",-2)):
                continue
            await d.journal_entries.update_one({"tenant_id":tenant_id,"source_key":source_key},{"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
            await d.transactions.update_one({"_id":source["_id"],"tenant_id":tenant_id},{"$set":{"journal_status":"posted"}})
        except Exception:
            # Leave the source pending/unmarked so a subsequent startup retries;
            # never pretend an interrupted journal is complete.
            continue

    expense_recovery_query={"$or":[{"journal_status":"pending"},{"journal_status":{"$exists":False}},{"journal_status":None}]}
    async for source in d.expenses.find(expense_recovery_query):
        try:
            journal=build_expense_journal(source)
            if not journal:
                await d.expenses.update_one({"_id":source["_id"],"tenant_id":source.get("tenant_id")},{"$set":{"journal_status":"blocked_invalid_expense","journal_blocked_at":datetime.now(timezone.utc)}})
                continue
            source_key=f"expense:{source['_id']}"
            journal_doc={"tenant_id":source.get("tenant_id"),"source_type":"expense","source_id":str(source["_id"]),"source_key":source_key,
                         "status":"pending",**journal,"created_at":source.get("created_at") or datetime.now(timezone.utc)}
            await d.journal_entries.update_one({"tenant_id":source.get("tenant_id"),"source_key":source_key},{"$setOnInsert":journal_doc},upsert=True)
            stored=await d.journal_entries.find_one({"tenant_id":source.get("tenant_id"),"source_key":source_key})
            if not stored or not stored.get("balanced") or int(stored.get("debit_minor",-1)) != int(stored.get("credit_minor",-2)):
                continue
            await d.journal_entries.update_one({"tenant_id":source.get("tenant_id"),"source_key":source_key},{"$set":{"status":"posted","posted_at":datetime.now(timezone.utc)}})
            await d.expenses.update_one({"_id":source["_id"],"tenant_id":source.get("tenant_id")},{"$set":{"journal_status":"posted"}})
        except Exception:
            continue


async def backfill_financial_feed(tenant_id: str | None = None):
    """Idempotently materialize legacy transactions/expenses into financial_feed.

    This is a read-model backfill only: source financial documents are never modified.
    New writes populate the feed synchronously in group API helpers.
    """
    d=get_db()
    ops=[]
    tx_query={"tenant_id":tenant_id} if tenant_id else {}
    exp_query={"tenant_id":tenant_id} if tenant_id else {}
    async for x in d.transactions.find(tx_query, {"_id":1,"tenant_id":1,"member_id":1,"type":1,"amount":1,"amount_minor":1,"account":1,"date":1,"created_at":1,"note":1,"payment_category":1,"penalty_category":1,"original_type":1,"reversal_of":1,"period":1,"status":1,"loan_id":1,"loan_interest_collected":1,"loan_interest_minor":1,"loan_penalty_collected":1,"loan_penalty_minor":1,"bc_regular_kist_penalty":1,"bc_penalty_minor":1,"other_interest":1,"other_interest_value":1,"other_interest_minor":1,"other_penalty":1,"other_penalty_value":1,"other_penalty_minor":1,"interest":1,"interest_minor":1,"principal":1,"principal_minor":1,"share_no":1,"share_id":1,"expense_id":1}):
        raw_amount=float(x.get("amount",0) or 0)
        amount_minor=int(x["amount_minor"]) if x.get("amount_minor") is not None else to_minor(raw_amount)
        amount=float(from_minor(amount_minor))
        doc={"source_key":f"tx:{x['_id']}","source_type":"transaction","source_id":str(x['_id']),"tenant_id":x.get("tenant_id"),"member_id":str(x.get("member_id")) if x.get("member_id") else None,"type":x.get("type","transaction"),"amount":amount,"amount_minor":amount_minor,"account":x.get("account") or "cash","date":x.get("date") or x.get("created_at"),"created_at":x.get("created_at") or x.get("date"),"note":x.get("note","") or "","payment_category":x.get("payment_category"),"penalty_category":x.get("penalty_category"),"original_type":x.get("original_type"),"reversal_of":x.get("reversal_of"),"period":x.get("period"),"status":x.get("status"),"loan_id":str(x.get("loan_id")) if x.get("loan_id") else None,"loan_interest_collected":float(x.get("loan_interest_collected",x.get("interest",0)) or 0),"loan_interest_minor":x.get("loan_interest_minor"),"loan_penalty_collected":float(x.get("loan_penalty_collected",0) or 0),"loan_penalty_minor":x.get("loan_penalty_minor"),"bc_regular_kist_penalty":float(x.get("bc_regular_kist_penalty",0) or 0),"bc_penalty_minor":x.get("bc_penalty_minor"),"other_interest_value":float(x.get("other_interest",x.get("other_interest_value",0)) or 0),"other_interest_minor":x.get("other_interest_minor"),"other_penalty_value":float(x.get("other_penalty",x.get("other_penalty_value",0)) or 0),"other_penalty_minor":x.get("other_penalty_minor"),"interest":float(x.get("interest",0) or 0),"interest_minor":x.get("interest_minor"),"principal":float(x.get("principal",0) or 0),"principal_minor":x.get("principal_minor"),"share_no":x.get("share_no"),"share_id":str(x.get("share_id")) if x.get("share_id") else None,"expense_id":str(x.get("expense_id")) if x.get("expense_id") else None}
        # created_at is immutable for an existing feed row. Keep it out of $set
        # and initialize it only for new rows to avoid MongoDB path conflicts.
        created_at = doc.pop("created_at", None)
        update = {"$set": doc}
        if created_at is not None:
            update["$setOnInsert"] = {"created_at": created_at}
        ops.append(UpdateOne({"tenant_id":doc.get("tenant_id"),"source_key":doc["source_key"]}, update, upsert=True))
        if len(ops)>=500:
            await d.financial_feed.bulk_write(ops,ordered=False); ops=[]
    async for x in d.expenses.find(exp_query, {"_id":1,"tenant_id":1,"amount":1,"amount_minor":1,"account":1,"date":1,"created_at":1,"category":1,"note":1}):
        raw_amount=float(x.get("amount",0) or 0)
        source_minor=int(x["amount_minor"]) if x.get("amount_minor") is not None else to_minor(raw_amount)
        amount_minor=-abs(source_minor); amount=float(from_minor(amount_minor))
        doc={"source_key":f"expense:{x['_id']}","source_type":"expense","source_id":str(x['_id']),"tenant_id":x.get("tenant_id"),"member_id":None,"type":"expense","amount":amount,"amount_minor":amount_minor,"account":x.get("account") or "cash","date":x.get("date") or x.get("created_at"),"created_at":x.get("created_at") or x.get("date"),"note":x.get("note","") or x.get("category","") or "Expense","category":x.get("category","Expense") or "Expense"}
        # created_at is immutable for an existing feed row. Keep it out of $set
        # and initialize it only for new rows to avoid MongoDB path conflicts.
        created_at = doc.pop("created_at", None)
        update = {"$set": doc}
        if created_at is not None:
            update["$setOnInsert"] = {"created_at": created_at}
        ops.append(UpdateOne({"tenant_id":doc.get("tenant_id"),"source_key":doc["source_key"]}, update, upsert=True))
        if len(ops)>=500:
            await d.financial_feed.bulk_write(ops,ordered=False); ops=[]
    if ops: await d.financial_feed.bulk_write(ops,ordered=False)
