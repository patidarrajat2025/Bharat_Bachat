from datetime import datetime, timezone
import asyncio
from bson import ObjectId
from .db import get_db


def oid(value: str):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _month_starts(months: int, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    out = []
    for i in range(months - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            y -= 1
            m += 12
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        end = datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(y, m + 1, 1, tzinfo=timezone.utc)
        out.append((start, end))
    return out


async def tenant_summary(tenant_id: str, member_id: str | None = None):
    """Return the accounting summary without pulling the tenant ledger into Python.

    The previous implementation downloaded up to 20k transactions + 10k expenses +
    10k loans for every summary request. MongoDB can calculate these totals much
    faster in-place, and the API only needs the small aggregate result.
    """
    db = get_db()
    tenant, tx_stats_rows, exp_stats_rows, member_stats_rows, counts = await asyncio.gather(
        db.tenants.find_one({"_id": oid(tenant_id)}),
        db.transactions.aggregate([
            {"$match": {"tenant_id": tenant_id}},
            {"$project": {
                "type": 1,
                "account": 1,
                "payment_category": 1,
                "penalty_category": 1,
                "amount": {"$convert": {"input": {"$ifNull": ["$amount", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "interest_value": {"$convert": {"input": {"$ifNull": ["$interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
            "loan_interest_value": {"$convert": {"input": {"$ifNull": ["$loan_interest_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
            "loan_penalty_value": {"$convert": {"input": {"$ifNull": ["$loan_penalty_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
            "bc_penalty_value": {"$convert": {"input": {"$ifNull": ["$bc_regular_kist_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_interest_value": {"$convert": {"input": {"$ifNull": ["$loan_interest_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_penalty_value": {"$convert": {"input": {"$ifNull": ["$loan_penalty_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "bc_penalty_value": {"$convert": {"input": {"$ifNull": ["$bc_regular_kist_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_interest_value": {"$convert": {"input": {"$ifNull": ["$other_interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_penalty_value": {"$convert": {"input": {"$ifNull": ["$other_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "is_real": {"$and": [
                    {"$not": [{"$in": ["$type", ["expense_allocation", "expense"]]}]},
                    {"$eq": [{"$type": "$expense_id"}, "missing"]},
                ]},
            }},
            {"$group": {
                "_id": None,
                "contributions": {"$sum": {"$cond": [{"$eq": ["$type", "contribution"]}, "$amount", 0]}},
                "regular_contributions": {"$sum": {"$cond": [{"$and": [{"$eq": ["$type", "contribution"]}, {"$or": [{"$eq": ["$payment_category", "monthly_kist"]}, {"$eq": ["$payment_category", None]}, {"$eq": [{"$type": "$payment_category"}, "missing"]}]}]}, "$amount", 0]}},
                "interest": {"$sum": {"$cond": [{"$eq": ["$type", "interest"]}, "$amount", 0]}},
                "penalties": {"$sum": {"$add": ["$bc_penalty_value", "$loan_penalty_value", "$other_penalty_value"]}},
                "bc_penalties": {"$sum": {"$add": ["$bc_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$ne": ["$payment_category", "loan"]}, {"$eq": ["$bc_penalty_value", 0]}]}, "$amount", 0]}]}},
                "loan_penalties": {"$sum": {"$add": ["$loan_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "loan"]}, {"$eq": ["$penalty_category", "loan"]}]}, {"$eq": ["$loan_penalty_value", 0]}]}, "$amount", 0]}]}},
                "repayments": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$amount", 0]}},
                "loan_disbursed": {"$sum": {"$cond": [{"$eq": ["$type", "loan_disbursement"]}, {"$abs": "$amount"}, 0]}},
                "cash_asset_outflow": {"$sum": {"$cond": [{"$and": [{"$in": ["$type", ["loan_disbursement", "investment_disbursement", "asset_purchase"]]}, {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}]}, {"$abs": "$amount"}, 0]}},
                "bank_asset_outflow": {"$sum": {"$cond": [{"$and": [{"$in": ["$type", ["loan_disbursement", "investment_disbursement", "asset_purchase"]]}, {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}]}, {"$abs": "$amount"}, 0]}},
                "loan_interest_income": {"$sum": {"$cond": [{"$gt": ["$loan_interest_value", 0]}, "$loan_interest_value", {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$interest_value", 0]}]}},
                "other_interest_income": {"$sum": {"$add": ["$other_interest_value", {"$cond": [{"$and": [{"$eq": ["$type", "interest"]}, {"$eq": ["$payment_category", "other_interest"]}]}, "$amount", 0]}]}},
                "other_penalty_income": {"$sum": {"$add": ["$other_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$eq": ["$payment_category", "other"]}]}, "$amount", 0]}]}},
                "tx_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "tx_outflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "cash_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "bank_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "cash_outflow_tx": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "bank_outflow_tx": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "other_income": {"$sum": {"$cond": [
                    {"$and": ["$is_real", {"$not": [{"$in": ["$type", ["contribution", "loan_repayment", "loan_disbursement", "interest", "penalty"]]}]}, {"$gt": ["$amount", 0]}]},
                    "$amount", 0,
                ]}},
                "real_tx_count": {"$sum": {"$cond": ["$is_real", 1, 0]}},
            }},
        ]).to_list(1),
        db.expenses.aggregate([
            {"$match": {"tenant_id": tenant_id}},
            {"$project": {"amount": {"$convert": {"input": {"$ifNull": ["$amount", 0]}, "to": "double", "onError": 0, "onNull": 0}}, "account": {"$ifNull": ["$account", "cash"]}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": "$amount"},
                "cash": {"$sum": {"$cond": [{"$eq": ["$account", "cash"]}, "$amount", 0]}},
                "bank": {"$sum": {"$cond": [{"$eq": ["$account", "bank"]}, "$amount", 0]}},
            }},
        ]).to_list(1),
        db.transactions.aggregate([
            {"$match": {"tenant_id": tenant_id, "member_id": member_id}} if member_id else {"$match": {"tenant_id": tenant_id, "_id": {"$exists": False}}},
            {"$project": {"type": 1, "amount": {"$convert": {"input": {"$ifNull": ["$amount", 0]}, "to": "double", "onError": 0, "onNull": 0}}}},
            {"$group": {
                "_id": None,
                "regular_contributions": {"$sum": {"$cond": [{"$eq": ["$type", "contribution"]}, "$amount", 0]}},
                "net_balance": {"$sum": "$amount"},
                "expenses": {"$sum": {"$cond": [{"$eq": ["$type", "expense_allocation"]}, {"$abs": "$amount"}, 0]}},
            }},
        ]).to_list(1),
        asyncio.gather(
            db.members.count_documents({"tenant_id": tenant_id, "active": True}),
            db.members.count_documents({"tenant_id": tenant_id}),
            db.shares.count_documents({"tenant_id": tenant_id}),
            db.shares.count_documents({"tenant_id": tenant_id, "status": "active"}),
            db.shares.count_documents({"tenant_id": tenant_id, "status": {"$ne": "active"}}),
            db.loans.count_documents({"tenant_id": tenant_id, "status": "active"}),
            db.shares.count_documents({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}) if member_id else asyncio.sleep(0, result=0),
        ),
    )

    if not tenant:
        raise ValueError("Group not found")
    tx = tx_stats_rows[0] if tx_stats_rows else {}
    exp = exp_stats_rows[0] if exp_stats_rows else {}
    member_stats = member_stats_rows[0] if member_stats_rows else {}
    member_count, total_member_count, total_share_count, active_share_count, inactive_share_count, active_loan_count, member_share_count = counts

    opening_cash = float(tenant.get("opening_cash", 0) or 0)
    opening_bank = float(tenant.get("opening_bank", 0) or 0)
    interest = float(tx.get("interest", 0) or 0)
    penalties = float(tx.get("penalties", 0) or 0)
    bc_penalties = float(tx.get("bc_penalties", 0) or 0)
    loan_penalties = float(tx.get("loan_penalties", 0) or 0)
    loan_interest_income = float(tx.get("loan_interest_income", 0) or 0)
    other_interest_income = float(tx.get("other_interest_income", 0) or 0)
    other_income = float(tx.get("other_income", 0) or 0)
    # Banking-style accounting: profit is earned income only. Expenses and
    # loan principal movements never reduce Group Profit.
    profit_income = loan_interest_income + bc_penalties + loan_penalties
    bc_fund = float(tx.get("regular_contributions", 0) or 0) + interest + penalties + loan_interest_income
    exp_total = float(exp.get("total", 0) or 0)

    cash = opening_cash + float(tx.get("cash_inflow", 0) or 0) - float(tx.get("cash_outflow_tx", 0) or 0) - float(exp.get("cash", 0) or 0)
    bank = opening_bank + float(tx.get("bank_inflow", 0) or 0) - float(tx.get("bank_outflow_tx", 0) or 0) - float(exp.get("bank", 0) or 0)
    net_group_vault = round(cash + bank, 2)
    group_profit = round(profit_income, 2)
    cash_inflow = round(float(tx.get("tx_inflow", 0) or 0), 2)
    cash_outflow = round(float(tx.get("tx_outflow", 0) or 0) + exp_total, 2)
    cash_inflow_account = round(float(tx.get("cash_inflow", 0) or 0), 2)
    bank_inflow = round(float(tx.get("bank_inflow", 0) or 0), 2)
    cash_outflow_account = round(float(tx.get("cash_outflow_tx", 0) or 0) + float(exp.get("cash", 0) or 0), 2)
    bank_outflow = round(float(tx.get("bank_outflow_tx", 0) or 0) + float(exp.get("bank", 0) or 0), 2)

    member_profit = None
    if member_id:
        member_profit = round((profit_income / max(1, active_share_count)) * member_share_count, 2)

    return {
        "vault_balance": net_group_vault,
        "net_group_vault": net_group_vault,
        "total_bc_fund": round(bc_fund, 2),
        "active_account_balance": net_group_vault,
        "cash_balance": round(cash, 2),
        "bank_balance": round(bank, 2),
        "total_contributions": round(float(tx.get("contributions", 0) or 0), 2),
        "regular_bc_contributions": round(float(tx.get("regular_contributions", 0) or 0), 2),
        "member_principal_savings": round(float(member_stats.get("regular_contributions", 0) or 0), 2),
        "member_regular_contributions": round(float(member_stats.get("regular_contributions", 0) or 0), 2),
        "member_net_balance": round(float(member_stats.get("net_balance", 0) or 0), 2),
        "member_expenses": round(float(member_stats.get("expenses", 0) or 0), 2),
        "interest_collected": round(interest + loan_interest_income, 2),
        "other_interest_collected": round(other_interest_income, 2),
        "bank_interest_collected": round(interest, 2),
        "loan_interest_collected": round(loan_interest_income, 2),
        "penalties": round(penalties, 2),
        "bc_penalties": round(bc_penalties, 2),
        "loan_penalties": round(loan_penalties, 2),
        "other_penalties": round(float(tx.get("other_penalty_income", 0) or 0), 2),
        "loan_disbursed": round(float(tx.get("loan_disbursed", 0) or 0), 2),
        "loan_repayments": round(float(tx.get("repayments", 0) or 0), 2),
        "expenses": round(exp_total, 2),
        "profit": group_profit,
        "group_total_profit": group_profit,
        "group_profit": group_profit,
        "cash_inflow": cash_inflow,
        "bank_inflow": bank_inflow,
        "cash_inflow_account": cash_inflow_account,
        "cash_outflow": cash_outflow,
        "cash_outflow_account": cash_outflow_account,
        "bank_outflow": bank_outflow,
        "cash_asset_outflow": round(float(tx.get("cash_asset_outflow", 0) or 0), 2),
        "bank_asset_outflow": round(float(tx.get("bank_asset_outflow", 0) or 0), 2),
        "members": member_count,
        "total_members": total_member_count,
        "total_shares": total_share_count,
        "active_shares": active_share_count,
        "inactive_shares": inactive_share_count,
        "member_active_shares": member_share_count,
        "active_loans": active_loan_count,
        "member_profit": member_profit,
        "transactions_count": int(tx.get("real_tx_count", 0) or 0),
    }


async def analytics(tenant_id: str, months: int = 12, share_no: int | None = None, member_id: str | None = None):
    """One transaction aggregation + one expense aggregation for the full period."""
    db = get_db()
    periods = _month_starts(months)
    start, end = periods[0][0], periods[-1][1]
    member_share_count_task = db.shares.count_documents({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}) if member_id else asyncio.sleep(0, result=0)
    active_shares_task = db.shares.count_documents({"tenant_id": tenant_id, "status": "active"})

    tx_match = {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}
    if share_no is not None:
        tx_match["share_no"] = share_no

    tx_task = db.transactions.aggregate([
        {"$match": tx_match},
        {"$project": {
            "date": 1,
            "type": 1,
            "payment_category": 1,
            "penalty_category": 1,
            "amount": {"$convert": {"input": {"$ifNull": ["$amount", 0]}, "to": "double", "onError": 0, "onNull": 0}},
            "interest_value": {"$convert": {"input": {"$ifNull": ["$interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
        }},
        {"$group": {
            "_id": {"month": {"$dateToString": {"format": "%Y-%m", "date": "$date", "timezone": "UTC"}}},
            "contributions": {"$sum": {"$cond": [{"$eq": ["$type", "contribution"]}, "$amount", 0]}},
            "interest": {"$sum": {"$cond": [{"$eq": ["$type", "interest"]}, "$amount", 0]}},
            "bc_penalties": {"$sum": {"$add": ["$bc_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$ne": ["$payment_category", "loan"]}, {"$ne": ["$payment_category", "other"]}, {"$eq": ["$bc_penalty_value", 0]}]}, "$amount", 0]}]}},
            "loan_penalties": {"$sum": {"$add": ["$loan_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "loan"]}, {"$eq": ["$penalty_category", "loan"]}]}, {"$eq": ["$loan_penalty_value", 0]}]}, "$amount", 0]}]}},
            "loan_interest": {"$sum": {"$cond": [{"$gt": ["$loan_interest_value", 0]}, "$loan_interest_value", {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$interest_value", 0]}]}},
            "repayments": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$amount", 0]}},
            "other_income": {"$sum": {"$cond": [{"$and": [
                {"$not": [{"$in": ["$type", ["contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty"]]}]},
                {"$gt": ["$amount", 0]},
            ]}, "$amount", 0]}},
        }},
    ]).to_list(None)
    exp_task = db.expenses.aggregate([
        {"$match": {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}},
        {"$project": {"date": 1, "amount": {"$convert": {"input": {"$ifNull": ["$amount", 0]}, "to": "double", "onError": 0, "onNull": 0}}}},
        {"$group": {"_id": {"month": {"$dateToString": {"format": "%Y-%m", "date": "$date", "timezone": "UTC"}}}, "expenses": {"$sum": "$amount"}}},
    ]).to_list(None)

    tx_rows, exp_rows, member_share_count, active_share_count = await asyncio.gather(tx_task, exp_task, member_share_count_task, active_shares_task)
    tx_map = {str(x["_id"]["month"]): x for x in tx_rows}
    exp_map = {str(x["_id"]["month"]): float(x.get("expenses", 0) or 0) for x in exp_rows}
    active_shares = max(1, active_share_count)

    rows = []
    for start_month, _ in periods:
        key = start_month.strftime("%Y-%m")
        x = tx_map.get(key, {})
        expense_total = exp_map.get(key, 0.0)
        interest_income = float(x.get("interest", 0) or 0)
        loan_interest_income = float(x.get("loan_interest", 0) or 0)
        bc_penalties = float(x.get("bc_penalties", 0) or 0)
        loan_penalties = float(x.get("loan_penalties", 0) or 0)
        other_income = float(x.get("other_income", 0) or 0)
        # Group Profit follows the dashboard rule: loan interest + BC penalties + loan penalties.
        profit_income = loan_interest_income + bc_penalties + loan_penalties
        profit = round(profit_income, 2)
        per_share = round(profit / active_shares, 2)
        member_expenses = round((expense_total / active_shares) * member_share_count, 2) if member_id else None
        member_profit = round((profit_income / active_shares) * member_share_count, 2) if member_id else None
        rows.append({
            "month": start_month.strftime("%b %y"),
            "year": start_month.year,
            "month_key": key,
            "contributions": round(float(x.get("contributions", 0) or 0), 2),
            "interest": round(interest_income + loan_interest_income + other_income, 2),
            "repayments": round(float(x.get("repayments", 0) or 0), 2),
            "expenses": round(expense_total, 2),
            "profit": profit,
            "profit_per_share": per_share,
            "member_profit": member_profit,
            "member_expenses": member_expenses,
            "member_share_count": member_share_count,
        })
    return rows


async def backfill_legacy_expense_allocations():
    """Best-effort one-time migration for expenses created by older builds.

    This intentionally runs outside request/response paths. New expenses already
    create their allocations synchronously with a bulk write.
    """
    db = get_db()
    try:
        tenant_cursor = db.tenants.find({"expense_allocation_backfill_at": {"$exists": False}}, {"_id": 1})
        from pymongo import UpdateOne
        async for tenant in tenant_cursor:
            tenant_id = str(tenant["_id"])
            shares = await db.shares.find({"tenant_id": tenant_id, "status": "active"}).sort("share_no", 1).to_list(10000)
            if not shares:
                await db.tenants.update_one({"_id": tenant["_id"]}, {"$set": {"expense_allocation_backfill_at": datetime.now(timezone.utc)}})
                continue
            existing = set(await db.transactions.distinct("expense_id", {"tenant_id": tenant_id, "type": "expense_allocation"}))
            ops=[]
            expense_count=0
            async for expense in db.expenses.find({"tenant_id": tenant_id}).sort([("date",1),("_id",1)]):
                expense_count += 1
                expense_id=str(expense["_id"])
                if expense_id in existing: continue
                total=float(expense.get("amount",0) or 0); per=round(total/len(shares),2); allocated=0.0
                for i,share in enumerate(shares):
                    amount=round(total-allocated,2) if i==len(shares)-1 else per
                    allocated=round(allocated+amount,2)
                    ops.append(UpdateOne(
                        {"tenant_id":tenant_id,"expense_id":expense_id,"share_id":str(share["_id"]),"type":"expense_allocation"},
                        {"$setOnInsert":{
                            "tenant_id":tenant_id,"member_id":str(share["member_id"]),"share_id":str(share["_id"]),"share_no":int(share["share_no"]),
                            "expense_id":expense_id,"type":"expense_allocation","amount":-amount,"account":expense.get("account","cash"),
                            "date":expense.get("date"),"created_at":expense.get("created_at",datetime.now(timezone.utc)),
                            "payment_category":"group_expense_allocation","note":expense.get("category","Group expense")
                        }},upsert=True))
                    if len(ops)>=1000:
                        await db.transactions.bulk_write(ops,ordered=False); ops=[]
            if ops: await db.transactions.bulk_write(ops,ordered=False)
            await db.tenants.update_one({"_id": tenant["_id"]}, {"$set": {"expense_allocation_backfill_at": datetime.now(timezone.utc), "expense_allocation_backfill_count": expense_count}})
    except Exception as exc:
        print(f"[expense-backfill] skipped: {exc}", flush=True)
