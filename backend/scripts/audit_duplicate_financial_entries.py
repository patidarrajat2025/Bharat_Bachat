"""Read-only audit for possible duplicate financial source rows.

This script NEVER edits or deletes financial records. Same-value payments can be
legitimate, so every candidate must be reconciled against receipts/audit logs
before any correction is posted.
Run from backend/: python scripts/audit_duplicate_financial_entries.py [TENANT_ID]
"""
import asyncio
import json
import sys
from datetime import datetime, date
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings


def json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


async def main():
    client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=15000,
        appname="bharat-bachat-duplicate-audit-readonly",
    )
    try:
        await client.admin.command("ping")
        db = client[settings.mongodb_db]
        match = {"type": {"$in": ["loan_repayment", "interest", "penalty", "contribution", "loan_disbursement"]}}
        if len(sys.argv) > 1:
            match["tenant_id"] = sys.argv[1]
        pipeline = [
            {"$match": match},
            {"$addFields": {
                "_audit_date": {"$dateToString": {"format": "%Y-%m-%d", "date": {"$ifNull": ["$date", "$created_at"]}, "onNull": "unknown"}},
                "_audit_amount_minor": {"$ifNull": ["$amount_minor", {"$round": [{"$multiply": [{"$ifNull": ["$amount", 0]}, 100]}, 0]}]},
            }},
            {"$group": {
                "_id": {"tenant_id": "$tenant_id", "member_id": "$member_id", "loan_id": "$loan_id", "type": "$type", "amount_minor": "$_audit_amount_minor", "account": "$account", "date": "$_audit_date", "note": "$note"},
                "count": {"$sum": 1},
                "rows": {"$push": {"id": {"$toString": "$_id"}, "transaction_ref": "$transaction_ref", "idempotency_key": "$idempotency_key", "created_at": "$created_at", "amount": "$amount", "amount_minor": "$amount_minor", "created_by": "$created_by"}},
            }},
            {"$match": {"count": {"$gt": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 500},
        ]
        groups = await db.transactions.aggregate(pipeline, allowDiskUse=True).to_list(500)
        print(json.dumps({
            "mode": "READ_ONLY_NO_RECORDS_CHANGED",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "tenant_filter": sys.argv[1] if len(sys.argv) > 1 else None,
            "candidate_groups": len(groups),
            "warning": "Candidates can be legitimate equal-value payments. Verify receipts and audit logs; do not delete based on amount/date alone.",
            "groups": json_safe(groups),
        }, indent=2, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
