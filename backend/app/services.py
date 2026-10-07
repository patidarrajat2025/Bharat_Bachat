from datetime import datetime, timezone
import asyncio
from bson import ObjectId
from .db import get_db

def oid(value: str):
    try: return ObjectId(value)
    except Exception: return None

async def tenant_summary(tenant_id: str, member_id: str | None = None):
    """Return banking-style group and member metrics without loading the full ledger.

    Group cash/bank balances are calculated from real ledger transactions plus
    source expenses. Expense-allocation rows are intentionally excluded because
    they are member-passbook projections, not additional group outflows.
    """
    db = get_db()
    real_match = {
        "tenant_id": tenant_id,
        "type": {"$nin": ["expense_allocation", "expense"]},
        "expense_id": {"$exists": False},
    }
    tx_pipeline = [
        {"$match": real_match},
        {"$group": {
            "_id": None,
            "cash_net": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, "$amount", 0]}},
            "bank_net": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, "$amount", 0]}},
            "cash_inflow": {"$sum": {"$cond": [{"$and": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
            "bank_inflow": {"$sum": {"$cond": [{"$and": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
            "cash_outflow": {"$sum": {"$cond": [{"$and": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
            "bank_outflow": {"$sum": {"$cond": [{"$and": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
            "tx_inflow": {"$sum": {"$cond": [{"$gt": ["$amount", 0]}, "$amount", 0]}},
            "tx_outflow": {"$sum": {"$cond": [{"$lt": ["$amount", 0]}, {"$abs": "$amount"}, 0]}},
            "contributions": {"$sum": {"$cond": [{"$eq": ["$type", "contribution"]}, "$amount", 0]}},
            "interest": {"$sum": {"$cond": [{"$eq": ["$type", "interest"]}, "$amount", 0]}},
            "penalties": {"$sum": {"$cond": [{"$eq": ["$type", "penalty"]}, "$amount", 0]}},
            "loan_repayments": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$amount", 0]}},
            "loan_disbursed": {"$sum": {"$cond": [{"$eq": ["$type", "loan_disbursement"]}, {"$abs": "$amount"}, 0]}},
            "loan_interest_income": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, {"$ifNull": ["$interest", 0]}, 0]}},
            "other_income": {"$sum": {"$cond": [{"$and": [{"$not": [{"$in": ["$type", ["contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty"]]}]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
            "transactions_count": {"$sum": 1},
        }}
    ]
    exp_pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$group": {
            "_id": None,
            "total": {"$sum": {"$ifNull": ["$amount", 0]}},
            "cash": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$ifNull": ["$amount", 0]}, 0]}},
            "bank": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$ifNull": ["$amount", 0]}, 0]}},
        }}
    ]
    tenant, tx_rows, exp_rows, counts = await asyncio.gather(
        db.tenants.find_one({"_id": oid(tenant_id)}, {"opening_cash": 1, "opening_bank": 1}),
        db.transactions.aggregate(tx_pipeline).to_list(1),
        db.expenses.aggregate(exp_pipeline).to_list(1),
        asyncio.gather(
            db.members.count_documents({"tenant_id": tenant_id, "active": True}),
            db.members.count_documents({"tenant_id": tenant_id}),
            db.shares.count_documents({"tenant_id": tenant_id}),
            db.shares.count_documents({"tenant_id": tenant_id, "status": "active"}),
            db.shares.count_documents({"tenant_id": tenant_id, "status": {"$ne": "active"}}),
            db.loans.count_documents({"tenant_id": tenant_id, "status": "active"}),
        )
    )
    tx = tx_rows[0] if tx_rows else {}
    exp = exp_rows[0] if exp_rows else {}
    opening_cash = float((tenant or {}).get("opening_cash", 0) or 0)
    opening_bank = float((tenant or {}).get("opening_bank", 0) or 0)
    expense_total = float(exp.get("total", 0) or 0)
    expense_cash = float(exp.get("cash", 0) or 0)
    expense_bank = float(exp.get("bank", 0) or 0)
    cash = opening_cash + float(tx.get("cash_net", 0) or 0) - expense_cash
    bank = opening_bank + float(tx.get("bank_net", 0) or 0) - expense_bank
    interest = float(tx.get("interest", 0) or 0)
    penalties = float(tx.get("penalties", 0) or 0)
    loan_interest_income = float(tx.get("loan_interest_income", 0) or 0)
    other_income = float(tx.get("other_income", 0) or 0)
    profit_income = interest + penalties + loan_interest_income + other_income
    group_profit = round(profit_income - expense_total, 2)
    active_members, total_members, total_shares, active_shares, inactive_shares, active_loans = counts
    member_profit = None
    if member_id:
        member_share_count = await db.shares.count_documents({"tenant_id": tenant_id, "member_id": member_id, "status": "active"})
        member_profit = round((group_profit / max(1, active_shares)) * member_share_count, 2)
    return {
        "vault_balance": round(cash + bank, 2), "net_group_vault": round(cash + bank, 2),
        "cash_balance": round(cash, 2), "bank_balance": round(bank, 2),
        "total_contributions": round(float(tx.get("contributions", 0) or 0), 2),
        "member_principal_savings": round(float(tx.get("contributions", 0) or 0), 2),
        "interest_collected": round(interest, 2), "penalties": round(penalties, 2),
        "loan_disbursed": round(float(tx.get("loan_disbursed", 0) or 0), 2),
        "loan_repayments": round(float(tx.get("loan_repayments", 0) or 0), 2),
        "expenses": round(expense_total, 2), "profit": group_profit, "group_total_profit": group_profit,
        "cash_inflow": round(float(tx.get("tx_inflow", 0) or 0), 2),
        "bank_inflow": round(float(tx.get("bank_inflow", 0) or 0), 2),
        "cash_inflow_account": round(float(tx.get("cash_inflow", 0) or 0), 2),
        "cash_outflow": round(float(tx.get("tx_outflow", 0) or 0) + expense_total, 2),
        "cash_outflow_account": round(float(tx.get("cash_outflow", 0) or 0) + expense_cash, 2),
        "bank_outflow": round(float(tx.get("bank_outflow", 0) or 0) + expense_bank, 2),
        "members": active_members, "total_members": total_members, "total_shares": total_shares,
        "active_shares": active_shares, "inactive_shares": inactive_shares, "active_loans": active_loans,
        "member_profit": member_profit, "transactions_count": int(tx.get("transactions_count", 0) or 0),
    }

async def analytics(tenant_id: str, months: int = 12, share_no: int | None = None, member_id: str | None = None):
    """Monthly analytics in two indexed aggregation queries instead of 2N queries."""
    db = get_db(); months = max(3, min(months, 24)); now = datetime.now(timezone.utc)
    start_month = now.month - months + 1; start_year = now.year
    while start_month <= 0:
        start_month += 12; start_year -= 1
    start = datetime(start_year, start_month, 1, tzinfo=timezone.utc)
    end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc) if now.month < 12 else datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    tx_match = {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}
    if share_no is not None: tx_match["share_no"] = share_no
    excluded = ["contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty"]
    tx_pipeline = [{"$match": tx_match}, {"$group": {
        "_id": {"y": {"$year": "$date"}, "m": {"$month": "$date"}},
        "contributions": {"$sum": {"$cond": [{"$eq": ["$type", "contribution"]}, {"$ifNull": ["$amount", 0]}, 0]}},
        "interest": {"$sum": {"$cond": [{"$in": ["$type", ["interest", "penalty"]]}, {"$ifNull": ["$amount", 0]}, 0]}},
        "loan_interest": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, {"$ifNull": ["$interest", 0]}, 0]}},
        "repayments": {"$sum": {"$cond": [{"$eq": ["$type", "loan_repayment"]}, {"$ifNull": ["$amount", 0]}, 0]}},
        "other_income": {"$sum": {"$cond": [{"$and": [{"$not": [{"$in": ["$type", excluded]}]}, {"$gt": ["$amount", 0]}]}, {"$ifNull": ["$amount", 0]}, 0]}},
    }}]
    exp_pipeline = [{"$match": {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}}, {"$group": {"_id": {"y": {"$year": "$date"}, "m": {"$month": "$date"}}, "expenses": {"$sum": {"$ifNull": ["$amount", 0]}}}}]
    member_share_count_task = db.shares.count_documents({"tenant_id": tenant_id, "member_id": member_id, "status": "active"}) if member_id else None
    tx_rows, exp_rows, active_shares, member_share_count = await asyncio.gather(
        db.transactions.aggregate(tx_pipeline).to_list(months),
        db.expenses.aggregate(exp_pipeline).to_list(months),
        db.shares.count_documents({"tenant_id": tenant_id, "status": "active"}),
        member_share_count_task if member_share_count_task is not None else asyncio.sleep(0, result=0),
    )
    tx_by={(r["_id"]["y"],r["_id"]["m"]):r for r in tx_rows}
    exp_by={(r["_id"]["y"],r["_id"]["m"]):r for r in exp_rows}
    rows=[]
    for offset in range(months-1,-1,-1):
        y=now.year; m=now.month-offset
        while m<=0: y-=1; m+=12
        key=(y,m); t=tx_by.get(key,{}); expense=float(exp_by.get(key,{}).get("expenses",0) or 0)
        interest=float(t.get("interest",0) or 0)+float(t.get("loan_interest",0) or 0)
        profit_income=interest+float(t.get("other_income",0) or 0)
        profit=round(profit_income-expense,2)
        per_share=round(profit/max(1,active_shares),2)
        member_expenses=round(expense/max(1,active_shares)*member_share_count,2) if member_id else None
        member_profit=round(profit/max(1,active_shares)*member_share_count,2) if member_id else None
        rows.append({"month":datetime(y,m,1).strftime("%b %y"),"year":y,"month_key":f"{y:04d}-{m:02d}",
                     "contributions":round(float(t.get("contributions",0) or 0),2),"interest":round(interest,2),
                     "repayments":round(float(t.get("repayments",0) or 0),2),"expenses":round(expense,2),"profit":profit,
                     "profit_per_share":per_share,"member_profit":member_profit,"member_expenses":member_expenses,
                     "member_share_count":member_share_count})
    return rows
