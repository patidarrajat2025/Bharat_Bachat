from datetime import datetime, timezone
import asyncio
from bson import ObjectId
from .db import get_db
from .accounting_engine import profit_components, to_minor, from_minor, allocate_minor


def oid(value: str):
    try:
        return ObjectId(value)
    except Exception:
        return None


def _mongo_amount_rupees_expr(minor_field: str = "amount_minor", amount_field: str = "amount") -> dict:
    raw = {"$convert": {"input": {"$ifNull": [f"${amount_field}", 0]}, "to": "double", "onError": 0, "onNull": 0}}
    legacy_minor = {"$round": [{"$multiply": [raw, 100]}, 0]}
    minor = {"$convert": {"input": {"$ifNull": [f"${minor_field}", legacy_minor]}, "to": "double", "onError": 0, "onNull": 0}}
    return {"$divide": [minor, 100]}


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


async def profit_report(tenant_id: str, start: datetime | None = None, end: datetime | None = None,
                        entry_offset: int = 0, entry_limit: int = 0):
    """Canonical profit read model shared by dashboard, analytics and register.

    It classifies the immutable source ledger with the same integer-paise engine
    in every read path. MongoDB cursors stream rows to avoid loading the entire
    ledger into memory; only the requested income-entry page is retained.
    """
    db = get_db()
    tx_query = {"tenant_id": tenant_id}
    exp_query = {"tenant_id": tenant_id}
    if start is not None or end is not None:
        date_range = {}
        if start is not None: date_range["$gte"] = start
        if end is not None: date_range["$lt"] = end
        # Legacy rows may have only created_at. Keep them in period reports
        # without including rows whose actual date falls outside the period.
        tx_query = {"tenant_id": tenant_id, "$or": [
            {"date": dict(date_range)},
            {"date": {"$exists": False}, "created_at": dict(date_range)},
            {"date": None, "created_at": dict(date_range)},
        ]}
        exp_query = {"tenant_id": tenant_id, "$or": [
            {"date": dict(date_range)},
            {"date": {"$exists": False}, "created_at": dict(date_range)},
            {"date": None, "created_at": dict(date_range)},
        ]}
    projection = {"_id":1,"tenant_id":1,"member_id":1,"type":1,"original_type":1,"reversal_of":1,
                  "amount":1,"amount_minor":1,"account":1,"date":1,"created_at":1,"note":1,
                  "loan_interest_collected":1,"loan_interest_minor":1,"interest":1,"interest_minor":1,
                  "other_interest":1,"other_interest_value":1,"other_interest_minor":1,
                  "loan_penalty_collected":1,"loan_penalty_minor":1,"bc_regular_kist_penalty":1,
                  "bc_penalty_minor":1,"other_penalty":1,"other_penalty_value":1,"other_penalty_minor":1,
                  "payment_category":1,"penalty_category":1}
    component_keys = ("bank_interest", "loan_interest", "other_interest", "loan_penalties", "bc_penalties", "other_penalties", "other_income")
    components = {key: 0 for key in component_keys}
    income_minor = 0
    expense_minor = 0
    income_count = 0
    entries = []
    monthly = {}

    async for tx in db.transactions.find(tx_query, projection).sort([("date", -1), ("created_at", -1), ("_id", -1)]):
        parts = profit_components(tx)
        amount = int(parts["income_total"])
        for key in component_keys: components[key] += int(parts[key])
        income_minor += amount
        if amount:
            if entry_offset <= income_count < entry_offset + max(0, entry_limit):
                entries.append({**tx, "profit_components_minor": parts, "profit_amount_minor": amount})
            income_count += 1
        dt = tx.get("date") or tx.get("created_at")
        if isinstance(dt, datetime):
            month_key = dt.strftime("%Y-%m")
            bucket = monthly.setdefault(month_key, {"income_minor":0,"expenses_minor":0, **{key:0 for key in component_keys}})
            bucket["income_minor"] += amount
            for key in component_keys: bucket[key] += int(parts[key])

    async for expense in db.expenses.find(exp_query, {"amount":1,"amount_minor":1,"date":1,"created_at":1}):
        amount = expense.get("amount_minor")
        amount = abs(int(amount)) if amount is not None else abs(to_minor(expense.get("amount", 0) or 0))
        expense_minor += amount
        dt = expense.get("date") or expense.get("created_at")
        if isinstance(dt, datetime):
            month_key = dt.strftime("%Y-%m")
            bucket = monthly.setdefault(month_key, {"income_minor":0,"expenses_minor":0, **{key:0 for key in component_keys}})
            bucket["expenses_minor"] += amount

    for bucket in monthly.values():
        bucket["profit_minor"] = bucket["income_minor"] - bucket["expenses_minor"]
    return {"income_minor":income_minor,"expense_minor":expense_minor,"net_profit_minor":income_minor-expense_minor,
            "components_minor":components,"income_entries_count":income_count,"entries":entries,"monthly":monthly}


async def tenant_summary(tenant_id: str, member_id: str | None = None):
    """Return summary balances and canonical profit from the tenant's source ledger.

    Balance and count buckets remain MongoDB aggregates; the profit engine streams
    only projected accounting fields so dashboard, analytics and register agree.
    """
    db = get_db()
    canonical_profit_task = profit_report(tenant_id)
    tenant, tx_stats_rows, exp_stats_rows, member_stats_rows, counts, canonical_profit = await asyncio.gather(
        db.tenants.find_one({"_id": oid(tenant_id)}),
        db.transactions.aggregate([
            {"$match": {"tenant_id": tenant_id}},
            {"$project": {
                "type": 1, "original_type": 1,
                # Legacy blank/null accounts are cash throughout the ledger.
                "account": {"$cond": [{"$in": [{"$ifNull": ["$account", ""]}, [""]]}, "cash", "$account"]},
                "payment_category": 1,
                "penalty_category": 1,
                "amount": _mongo_amount_rupees_expr(),
                "interest_value": {"$convert": {"input": {"$ifNull": ["$interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_interest_value": {"$convert": {"input": {"$ifNull": ["$loan_interest_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_penalty_value": {"$convert": {"input": {"$ifNull": ["$loan_penalty_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "bc_penalty_value": {"$convert": {"input": {"$ifNull": ["$bc_regular_kist_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_interest_value": {"$convert": {"input": {"$ifNull": ["$other_interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_penalty_value": {"$convert": {"input": {"$ifNull": ["$other_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                # A normal transaction may carry expense_id=None because the
                # materialized feed schema writes that key for every row. Treat
                # missing AND explicit-null as an ordinary transaction; exclude
                # only rows linked to a real expense document.
                "is_real": {"$and": [
                    {"$not": [{"$in": ["$type", ["expense_allocation", "expense"]]}]},
                    {"$in": [{"$type": "$expense_id"}, ["missing", "null"]]},
                ]},
            }},
            {"$group": {
                "_id": None,
                "contributions": {"$sum": {"$cond": [{"$or": [{"$eq": ["$type", "contribution"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "contribution"]}]}]}, "$amount", 0]}},
                "regular_contributions": {"$sum": {"$cond": [{"$and": [{"$or": [{"$eq": ["$type", "contribution"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "contribution"]}]}]}, {"$or": [{"$eq": ["$payment_category", "monthly_kist"]}, {"$eq": ["$payment_category", None]}, {"$eq": [{"$type": "$payment_category"}, "missing"]}]}]}, "$amount", 0]}},
                # Interest is classified once by source. Bank-account interest is
                # bank interest; tagged/legacy non-bank interest is other interest.
                "interest": {"$sum": {"$cond": [{"$and": [{"$or": [{"$eq": ["$type", "interest"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "interest"]}]}]}, {"$eq": ["$account", "bank"]}]}, "$amount", 0]}},
                "penalties": {"$sum": {"$add": [
                    {"$cond": [{"$ne": ["$bc_penalty_value", 0]}, "$bc_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "bc"]}, {"$eq": ["$penalty_category", "bc"]}, {"$and": [{"$in": [{"$type": "$payment_category"}, ["missing", "null"]]}, {"$in": [{"$type": "$penalty_category"}, ["missing", "null"]]}]}] }]}, "$amount", 0]}]},
                    {"$cond": [{"$ne": ["$loan_penalty_value", 0]}, "$loan_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "loan"]}, {"$eq": ["$penalty_category", "loan"]}] }]}, "$amount", 0]}]},
                    {"$cond": [{"$ne": ["$other_penalty_value", 0]}, "$other_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$eq": ["$payment_category", "other"]}]}, "$amount", 0]}]}
                ]}},
                "bc_penalties": {"$sum": {"$cond": [{"$ne": ["$bc_penalty_value", 0]}, "$bc_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "bc"]}, {"$eq": ["$penalty_category", "bc"]}] }]}, "$amount", 0]}]}},
                "loan_penalties": {"$sum": {"$cond": [{"$ne": ["$loan_penalty_value", 0]}, "$loan_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "loan"]}, {"$eq": ["$penalty_category", "loan"]}] }]}, "$amount", 0]}]}},
                "repayments": {"$sum": {"$cond": [{"$or": [{"$eq": ["$type", "loan_repayment"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "loan_repayment"]}]}]}, "$amount", 0]}},
                "loan_disbursed": {"$sum": {"$cond": [{"$eq": ["$type", "loan_disbursement"]}, {"$abs": "$amount"}, 0]}},
                "cash_asset_outflow": {"$sum": {"$cond": [{"$and": [{"$in": ["$type", ["loan_disbursement", "investment_disbursement", "asset_purchase"]]}, {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}]}, {"$abs": "$amount"}, 0]}},
                "bank_asset_outflow": {"$sum": {"$cond": [{"$and": [{"$in": ["$type", ["loan_disbursement", "investment_disbursement", "asset_purchase"]]}, {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}]}, {"$abs": "$amount"}, 0]}},
                "loan_interest_income": {"$sum": {"$cond": [{"$ne": ["$loan_interest_value", 0]}, "$loan_interest_value", {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$interest_value", 0]}]}},
                "other_interest_income": {"$sum": {"$cond": [{"$and": [{"$ne": ["$other_interest_value", 0]}, {"$ne": ["$account", "bank"]}, {"$ne": ["$payment_category", "bank_interest"]}]}, "$other_interest_value", {"$cond": [{"$and": [{"$or": [{"$eq": ["$type", "interest"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "interest"]}]}]}, {"$ne": ["$account", "bank"]}, {"$ne": ["$payment_category", "bank_interest"]}]}, "$amount", 0]}]}},
                "other_penalty_income": {"$sum": {"$cond": [{"$ne": ["$other_penalty_value", 0]}, "$other_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$eq": ["$payment_category", "other"]}]}, "$amount", 0]}]}},
                "tx_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "tx_outflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "cash_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "bank_inflow": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$gt": ["$amount", 0]}]}, "$amount", 0]}},
                "cash_outflow_tx": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "cash"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "bank_outflow_tx": {"$sum": {"$cond": [{"$and": ["$is_real", {"$eq": [{"$ifNull": ["$account", "cash"]}, "bank"]}, {"$lt": ["$amount", 0]}]}, {"$abs": "$amount"}, 0]}},
                "other_income": {"$sum": {"$cond": [
                    {"$and": ["$is_real", {"$or": [
                        {"$and": [{"$ne": ["$type", "reversal"]}, {"$not": [{"$in": ["$type", ["contribution", "loan_repayment", "loan_disbursement", "interest", "penalty", "transfer", "cash_bank_transfer"]]}]}, {"$gt": ["$amount", 0]}]},
                        {"$and": [{"$eq": ["$type", "reversal"]}, {"$not": [{"$in": ["$original_type", ["contribution", "loan_repayment", "loan_disbursement", "interest", "penalty", "transfer", "cash_bank_transfer", "expense_allocation", "expense"]]}]}, {"$lt": ["$amount", 0]}]}
                    ]}]},
                    "$amount", 0,
                ]}},
                "real_tx_count": {"$sum": {"$cond": ["$is_real", 1, 0]}},
            }},
        ]).to_list(1),
        db.expenses.aggregate([
            {"$match": {"tenant_id": tenant_id}},
            {"$project": {
                "amount": _mongo_amount_rupees_expr(),
                "account": {"$cond": [{"$in": [{"$ifNull": ["$account", ""]}, [""]]}, "cash", "$account"]},
            }},
            {"$group": {
                "_id": None,
                "total": {"$sum": "$amount"},
                "cash": {"$sum": {"$cond": [{"$eq": ["$account", "cash"]}, "$amount", 0]}},
                "bank": {"$sum": {"$cond": [{"$eq": ["$account", "bank"]}, "$amount", 0]}},
            }},
        ]).to_list(1),
        db.transactions.aggregate([
            {"$match": {"tenant_id": tenant_id, "member_id": member_id}} if member_id else {"$match": {"tenant_id": tenant_id, "_id": {"$exists": False}}},
            {"$project": {"type": 1, "original_type": 1, "amount": _mongo_amount_rupees_expr()}},
            {"$group": {
                "_id": None,
                "regular_contributions": {"$sum": {"$cond": [{"$or": [{"$eq": ["$type", "contribution"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "contribution"]}]}]}, "$amount", 0]}},
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
        canonical_profit_task,
    )

    if not tenant:
        raise ValueError("Group not found")
    tx = tx_stats_rows[0] if tx_stats_rows else {}
    exp = exp_stats_rows[0] if exp_stats_rows else {}
    member_stats = member_stats_rows[0] if member_stats_rows else {}
    member_count, total_member_count, total_share_count, active_share_count, inactive_share_count, active_loan_count, member_share_count = counts

    opening_cash = float(tenant.get("opening_cash", 0) or 0)
    opening_bank = float(tenant.get("opening_bank", 0) or 0)
    # All profit-related dashboard buckets come from the same integer-paise
    # source-ledger classifier as Analytics and the Financial Register. Do not
    # expose the older Mongo aggregate's subtly different category arithmetic.
    canonical_parts = canonical_profit.get("components_minor", {})
    interest = int(canonical_parts.get("bank_interest", 0) or 0) / 100
    bc_penalties = int(canonical_parts.get("bc_penalties", 0) or 0) / 100
    loan_penalties = int(canonical_parts.get("loan_penalties", 0) or 0) / 100
    other_penalties = int(canonical_parts.get("other_penalties", 0) or 0) / 100
    penalties = bc_penalties + loan_penalties + other_penalties
    loan_interest_income = int(canonical_parts.get("loan_interest", 0) or 0) / 100
    other_interest_income = int(canonical_parts.get("other_interest", 0) or 0) / 100
    other_income = int(canonical_parts.get("other_income", 0) or 0) / 100
    # Define expenses before using it so an empty expense aggregate is safe.
    exp_total = int(canonical_profit.get("expense_minor", 0) or 0) / 100
    # Principal repayments and loan disbursements are balance-sheet movements,
    # not profit. Expenses are costs. Profit is canonical across all read models.
    profit_income_minor = int(canonical_profit["net_profit_minor"])
    profit_income = profit_income_minor / 100
    bc_fund = float(tx.get("regular_contributions", 0) or 0) + interest + penalties + loan_interest_income + other_interest_income

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
        member_profit = float(from_minor(allocate_minor(profit_income_minor, member_share_count, active_share_count)))

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
        "other_penalties": round(other_penalties, 2),
        "other_income": round(other_income, 2),
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
    """Return group analytics to admins and scoped personal analytics to members.

    Member analytics must not expose another member's transactions or group-wide
    totals. Group profit is still used server-side to calculate the caller's own
    share, but only that allocated share is returned in personal mode.
    """
    db = get_db()
    periods = _month_starts(months)
    start, end = periods[0][0], periods[-1][1]
    share_query = {"tenant_id": tenant_id, "status": "active"}
    if member_id:
        share_query["member_id"] = member_id
    if share_no is not None:
        share_query["share_no"] = share_no
    member_share_count_task = db.shares.count_documents(share_query) if member_id else asyncio.sleep(0, result=0)
    active_shares_task = db.shares.count_documents({"tenant_id": tenant_id, "status": "active"})

    base_match = {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}
    personal_match = {**base_match, "member_id": member_id} if member_id else None
    if share_no is not None:
        # In personal mode, share_no is scoped to the caller's own shares; the
        # group-wide profit denominator must still use the whole group's income.
        if member_id and personal_match is not None:
            personal_match["share_no"] = share_no
        else:
            base_match["share_no"] = share_no

    def transaction_pipeline(match):
        return [
            {"$match": match},
            {"$project": {
                "date": 1, "type": 1, "original_type": 1, "payment_category": 1, "penalty_category": 1,
                "amount": _mongo_amount_rupees_expr(),
                "interest_value": {"$convert": {"input": {"$ifNull": ["$interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "bc_penalty_value": {"$convert": {"input": {"$ifNull": ["$bc_regular_kist_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_penalty_value": {"$convert": {"input": {"$ifNull": ["$loan_penalty_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_penalty_value": {"$convert": {"input": {"$ifNull": ["$other_penalty", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "loan_interest_value": {"$convert": {"input": {"$ifNull": ["$loan_interest_collected", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "other_interest_value": {"$convert": {"input": {"$ifNull": ["$other_interest", 0]}, "to": "double", "onError": 0, "onNull": 0}},
                "account": {"$ifNull": ["$account", "cash"]},
            }},
            {"$group": {
                "_id": {"month": {"$dateToString": {"format": "%Y-%m", "date": "$date", "timezone": "UTC"}}},
                "contributions": {"$sum": {"$cond": [{"$or": [{"$eq": ["$type", "contribution"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "contribution"]}]}]}, "$amount", 0]}},
                "interest": {"$sum": {"$cond": [{"$and": [{"$or": [{"$eq": ["$type", "interest"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "interest"]}]}]}, {"$eq": ["$account", "bank"]}]}, "$amount", 0]}},
                "other_interest": {"$sum": {"$cond": [{"$and": [{"$or": [{"$eq": ["$type", "interest"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "interest"]}]}]}, {"$ne": ["$account", "bank"]}]}, "$amount", 0]}},
                "bc_penalties": {"$sum": {"$add": ["$bc_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$ne": ["$payment_category", "loan"]}, {"$ne": ["$payment_category", "other"]}, {"$eq": ["$bc_penalty_value", 0]}]}, "$amount", 0]}]}},
                "loan_penalties": {"$sum": {"$add": ["$loan_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$or": [{"$eq": ["$payment_category", "loan"]}, {"$eq": ["$penalty_category", "loan"]}]}, {"$eq": ["$loan_penalty_value", 0]}]}, "$amount", 0]}]}},
                "other_penalties": {"$sum": {"$add": ["$other_penalty_value", {"$cond": [{"$and": [{"$eq": ["$type", "penalty"]}, {"$eq": ["$payment_category", "other"]}, {"$eq": ["$other_penalty_value", 0]}]}, "$amount", 0]}]}},
                "loan_interest": {"$sum": {"$cond": [{"$ne": ["$loan_interest_value", 0]}, "$loan_interest_value", {"$cond": [{"$eq": ["$type", "loan_repayment"]}, "$interest_value", 0]}]}},
                "repayments": {"$sum": {"$cond": [{"$or": [{"$eq": ["$type", "loan_repayment"]}, {"$and": [{"$eq": ["$type", "reversal"]}, {"$eq": ["$original_type", "loan_repayment"]}]}]}, "$amount", 0]}},
                "other_income": {"$sum": {"$cond": [{"$or": [
                    {"$and": [
                        {"$ne": ["$type", "reversal"]},
                        {"$not": [{"$in": ["$type", ["contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty", "transfer", "cash_bank_transfer"]]}]},
                        {"$gt": ["$amount", 0]},
                    ]},
                    {"$and": [
                        {"$eq": ["$type", "reversal"]},
                        {"$not": [{"$in": ["$original_type", ["contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty", "transfer", "cash_bank_transfer"]]}]},
                        {"$lt": ["$amount", 0]},
                    ]},
                ]}, "$amount", 0]}},
            }},
        ]

    group_tx_task = db.transactions.aggregate(transaction_pipeline(base_match)).to_list(None)
    personal_tx_task = db.transactions.aggregate(transaction_pipeline(personal_match)).to_list(None) if personal_match else asyncio.sleep(0, result=[])
    exp_task = db.expenses.aggregate([
        {"$match": {"tenant_id": tenant_id, "date": {"$gte": start, "$lt": end}}},
        {"$project": {"date": 1, "amount": _mongo_amount_rupees_expr()}},
        {"$group": {"_id": {"month": {"$dateToString": {"format": "%Y-%m", "date": "$date", "timezone": "UTC"}}}, "expenses": {"$sum": "$amount"}}},
    ]).to_list(None)

    group_rows, personal_rows, exp_rows, member_share_count, active_share_count, canonical_profit = await asyncio.gather(
        group_tx_task, personal_tx_task, exp_task, member_share_count_task, active_shares_task,
        profit_report(tenant_id, start, end),
    )
    group_map = {str(x["_id"]["month"]): x for x in group_rows}
    personal_map = {str(x["_id"]["month"]): x for x in personal_rows}
    exp_map = {str(x["_id"]["month"]): float(x.get("expenses", 0) or 0) for x in exp_rows}
    active_shares = max(1, active_share_count)
    member_share_count = int(member_share_count or 0)

    rows = []
    for start_month, _ in periods:
        key = start_month.strftime("%Y-%m")
        group_x = group_map.get(key, {})
        x = personal_map.get(key, {}) if member_id else group_x
        # Profit and its source buckets are read from the shared paise engine.
        # The older aggregation remains for non-profit member activity metrics,
        # but cannot override the financial report's income classification.
        canonical_month = canonical_profit.get("monthly", {}).get(key, {})
        canonical_parts = canonical_month
        if not member_id:
            group_expense_total = int(canonical_month.get("expenses_minor", 0) or 0) / 100
            interest_income = int(canonical_parts.get("bank_interest", 0) or 0) / 100
            other_interest_income = int(canonical_parts.get("other_interest", 0) or 0) / 100
            loan_interest_income = int(canonical_parts.get("loan_interest", 0) or 0) / 100
            bc_penalties = int(canonical_parts.get("bc_penalties", 0) or 0) / 100
            loan_penalties = int(canonical_parts.get("loan_penalties", 0) or 0) / 100
            other_penalties = int(canonical_parts.get("other_penalties", 0) or 0) / 100
            other_income = int(canonical_parts.get("other_income", 0) or 0) / 100
        else:
            interest_income = float(x.get("interest", 0) or 0)
            other_interest_income = float(x.get("other_interest", 0) or 0)
            loan_interest_income = float(x.get("loan_interest", 0) or 0)
            bc_penalties = float(group_x.get("bc_penalties", 0) or 0)
            loan_penalties = float(group_x.get("loan_penalties", 0) or 0)
            other_penalties = float(group_x.get("other_penalties", 0) or 0)
            other_income = float(x.get("other_income", 0) or 0)
        # Profit allocation uses group income internally, but personal mode only
        # returns the caller's share rather than the full group profit.
        group_profit_income = int(canonical_month.get("profit_minor", 0) or 0) / 100
        member_profit = float(from_minor(allocate_minor(int(canonical_month.get("profit_minor", 0) or 0), member_share_count, active_shares))) if member_id else None
        member_expenses = round((group_expense_total / active_shares) * member_share_count, 2) if member_id else None
        visible_profit = member_profit if member_id else round(group_profit_income, 2)
        visible_expenses = member_expenses if member_id else round(group_expense_total, 2)
        visible_per_share = round((member_profit / max(1, member_share_count)), 2) if member_id else round(group_profit_income / active_shares, 2)
        rows.append({
            "month": start_month.strftime("%b %y"), "year": start_month.year, "month_key": key,
            "contributions": round(float(x.get("contributions", 0) or 0), 2),
            "interest": round(interest_income + other_interest_income + loan_interest_income, 2),
            "bank_interest": round(interest_income, 2),
            "other_interest": round(other_interest_income, 2),
            "loan_interest": round(loan_interest_income, 2),
            "bc_penalties": round(bc_penalties, 2),
            "loan_penalties": round(loan_penalties, 2),
            "other_penalties": round(other_penalties, 2),
            "other_income": round(other_income, 2),
            "repayments": round(float(x.get("repayments", 0) or 0), 2),
            "expenses": visible_expenses, "profit": visible_profit, "profit_per_share": visible_per_share,
            "member_profit": member_profit, "member_expenses": member_expenses,
            "member_share_count": member_share_count if member_id else None,
        })
    return rows


async def backfill_legacy_expense_allocations():
    """Repair complete and partial legacy expense allocations in paise.

    The source expense remains authoritative. The shared allocator stores an
    immutable share plan, upserts every planned allocation, and can resume after
    a crash even when only some child rows were previously written.
    """
    db = get_db()
    from .api.group import ensure_expense_allocations
    tenant_cursor = db.tenants.find({"expense_allocation_backfill_at": {"$exists": False}}, {"_id": 1})
    async for tenant in tenant_cursor:
        tenant_id = str(tenant["_id"])
        try:
            async for expense in db.expenses.find({"tenant_id": tenant_id}).sort([("date", 1), ("_id", 1)]):
                try:
                    await ensure_expense_allocations(tenant_id, expense)
                except Exception:
                    # Continue other expenses, but leave the tenant marker unset
                    # so the failed allocation is retried on next startup.
                    raise
            await db.tenants.update_one({"_id": tenant["_id"]}, {"$set": {"expense_allocation_backfill_at": datetime.now(timezone.utc)}})
        except Exception:
            # A tenant is retried as a whole; every individual expense is
            # idempotent, so successfully repaired expenses are safe to revisit.
            continue

