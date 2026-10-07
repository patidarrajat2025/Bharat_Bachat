from datetime import datetime, timezone
import asyncio
from bson import ObjectId
from .db import get_db

def oid(value: str):
    try: return ObjectId(value)
    except Exception: return None

def _date_match(doc, from_date=None, to_date=None):
    d = doc.get("date") or doc.get("created_at")
    if hasattr(d, "date"): d = d.date()
    if from_date and d < from_date: return False
    if to_date and d > to_date: return False
    return True

async def tenant_summary(tenant_id: str, member_id: str | None = None):
    db = get_db()
    tenant, tx, expenses, loans = await asyncio.gather(
        db.tenants.find_one({"_id": oid(tenant_id)}),
        db.transactions.find({"tenant_id": tenant_id}).to_list(20000),
        db.expenses.find({"tenant_id": tenant_id}).to_list(10000),
        db.loans.find({"tenant_id": tenant_id}).to_list(10000),
    )
    opening_cash = float((tenant or {}).get("opening_cash", 0)); opening_bank = float((tenant or {}).get("opening_bank", 0))
    cash = opening_cash; bank = opening_bank
    contributions = interest = penalties = repayments = disbursed = 0.0
    for x in tx:
        amount = float(x.get("amount", 0)); account = x.get("account", "cash")
        typ = x.get("type")
        # Expense-allocation entries exist only for individual member passbooks.
        # They must never reduce the group vault a second time because the source
        # expense is already stored in db.expenses.
        if typ != "expense_allocation":
            if account == "bank": bank += amount
            else: cash += amount
        if typ == "contribution": contributions += amount
        elif typ == "interest": interest += amount
        elif typ == "penalty": penalties += amount
        elif typ == "loan_repayment": repayments += amount
        elif typ == "loan_disbursement": disbursed += abs(amount)
    exp = sum(float(x.get("amount", 0)) for x in expenses)
    loan_interest_income = sum(float(x.get("interest", 0) or 0) for x in tx if x.get("type") == "loan_repayment")
    excluded = {"contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty"}
    other_income = sum(max(float(x.get("amount", 0) or 0), 0.0) for x in tx if x.get("type") not in excluded)
    profit_income = interest + penalties + loan_interest_income + other_income
    group_profit = round(profit_income - exp, 2)
    # Group expenses are stored in db.expenses and also have per-share
    # expense_allocation transactions for member passbooks. Legacy builds may
    # also contain a transaction linked to expense_id. Never count those group
    # allocations/source rows as a second group outflow.
    real_tx = [x for x in tx if x.get("type") not in {"expense_allocation", "expense"} and not x.get("expense_id")]
    tx_inflow = sum(max(float(x.get("amount", 0)), 0.0) for x in real_tx)
    tx_outflow = sum(max(-float(x.get("amount", 0)), 0.0) for x in real_tx)
    cash_inflow = round(tx_inflow, 2)
    cash_outflow = round(tx_outflow + exp, 2)
    cash_inflow_total = round(sum(max(float(x.get("amount", 0)), 0.0) for x in real_tx if x.get("account", "cash") == "cash"), 2)
    bank_inflow_total = round(sum(max(float(x.get("amount", 0)), 0.0) for x in real_tx if x.get("account", "cash") == "bank"), 2)
    cash_outflow_total = round(sum(max(-float(x.get("amount", 0)), 0.0) for x in real_tx if x.get("account", "cash") == "cash") + sum(float(x.get("amount", 0) or 0) for x in expenses if x.get("account", "cash") == "cash"), 2)
    bank_outflow_total = round(sum(max(-float(x.get("amount", 0)), 0.0) for x in real_tx if x.get("account", "cash") == "bank") + sum(float(x.get("amount", 0) or 0) for x in expenses if x.get("account", "cash") == "bank"), 2)
    for x in expenses:
        if x.get("account") == "bank": bank -= float(x.get("amount",0))
        else: cash -= float(x.get("amount",0))
    net_group_vault = round(cash + bank, 2)
    member_count, total_member_count, total_share_count, active_share_count, inactive_share_count, active_loan_count = await asyncio.gather(
        db.members.count_documents({"tenant_id":tenant_id,"active":True}),
        db.members.count_documents({"tenant_id":tenant_id}),
        db.shares.count_documents({"tenant_id":tenant_id}),
        db.shares.count_documents({"tenant_id":tenant_id,"status":"active"}),
        db.shares.count_documents({"tenant_id":tenant_id,"status":{"$ne":"active"}}),
        db.loans.count_documents({"tenant_id":tenant_id,"status":"active"}),
    )
    member_profit = None
    if member_id:
        member_share_count = await db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"})
        member_profit = round((profit_income / max(1, active_share_count)) * member_share_count - (exp / max(1, active_share_count)) * member_share_count, 2)
    return {"vault_balance": net_group_vault, "net_group_vault": net_group_vault, "cash_balance": round(cash,2), "bank_balance": round(bank,2),
            "total_contributions": round(contributions,2), "member_principal_savings": round(contributions,2), "interest_collected": round(interest,2),
            "penalties": round(penalties,2), "loan_disbursed": round(disbursed,2), "loan_repayments": round(repayments,2),
            "expenses": round(exp,2), "profit": group_profit, "group_total_profit": group_profit, "cash_inflow": cash_inflow, "bank_inflow": bank_inflow_total, "cash_inflow_account": cash_inflow_total, "cash_outflow": cash_outflow, "cash_outflow_account": cash_outflow_total, "bank_outflow": bank_outflow_total, "members": member_count,
            "total_members": total_member_count,
            "total_shares": total_share_count,
            "active_shares": active_share_count,
            "inactive_shares": inactive_share_count,
            "active_loans": active_loan_count,
            "member_profit": member_profit,
            "transactions_count": len(real_tx)}

async def analytics(tenant_id: str, months: int = 12, share_no: int | None = None, member_id: str | None = None):
    db=get_db(); now=datetime.now(timezone.utc); rows=[]
    member_share_count=0
    if member_id:
        member_share_count=await db.shares.count_documents({"tenant_id":tenant_id,"member_id":member_id,"status":"active"})
    for i in range(months-1,-1,-1):
        y=now.year; m=now.month-i
        while m<=0: y-=1; m+=12
        start=datetime(y,m,1,tzinfo=timezone.utc)
        if m==12: end=datetime(y+1,1,1,tzinfo=timezone.utc)
        else: end=datetime(y,m+1,1,tzinfo=timezone.utc)
        tx_q={"tenant_id":tenant_id,"date":{"$gte":start,"$lt":end}}
        if share_no is not None: tx_q["share_no"]=share_no
        tx=await db.transactions.find(tx_q).to_list(5000)
        exp=await db.expenses.find({"tenant_id":tenant_id,"date":{"$gte":start,"$lt":end}}).to_list(5000)
        interest_income=sum(float(x.get("amount",0) or 0) for x in tx if x.get("type") in ("interest","penalty"))
        loan_interest_income=sum(float(x.get("interest",0) or 0) for x in tx if x.get("type")=="loan_repayment")
        # Profit is not generated by regular BC contributions. It comes from
        # interest/penalty and other genuine income sources, then group
        # expenses are allocated per active share. Loan principal is excluded;
        # only the interest component of a repayment is income.
        excluded={"contribution","loan_repayment","loan_disbursement","expense_allocation","expense","interest","penalty"}
        other_income=sum(max(float(x.get("amount",0) or 0),0.0) for x in tx if x.get("type") not in excluded)
        profit_income=interest_income+loan_interest_income+other_income
        expense_total=sum(float(x.get("amount",0) or 0) for x in exp)
        profit=round(profit_income-expense_total,2)
        active_shares=max(1,await db.shares.count_documents({"tenant_id":tenant_id,"status":"active"}))
        per_share=round(profit/active_shares,2)
        member_expenses=round((expense_total/active_shares)*member_share_count,2) if member_id else None
        member_profit=round(profit_income/active_shares*member_share_count-member_expenses,2) if member_id else None
        rows.append({"month":start.strftime("%b %y"),"year":y,"month_key":start.strftime("%Y-%m"),
                     "contributions":round(sum(float(x.get("amount",0) or 0) for x in tx if x.get("type")=="contribution"),2),
                     "interest":round(interest_income+loan_interest_income+other_income,2),
                     "repayments":round(sum(float(x.get("amount",0) or 0) for x in tx if x.get("type")=="loan_repayment"),2),
                     "expenses":round(expense_total,2),"profit":profit,"profit_per_share":per_share,
                     "member_profit":member_profit,"member_expenses":member_expenses,"member_share_count":member_share_count})
    return rows
