"""Balanced journal construction and recoverable journal posting records."""
from __future__ import annotations
from .accounting_engine import to_minor

def _minor(row, minor_key, *rupee_keys):
    if row.get(minor_key) is not None:
        return int(row[minor_key])
    for key in rupee_keys:
        if row.get(key) is not None:
            return to_minor(row[key])
    return 0


def reverse_journal(original: dict) -> dict | None:
    """Create a balanced exact inverse of a previously posted journal."""
    if not original or not original.get("balanced"):
        return None
    lines = [{
        "account_code": line["account_code"],
        "debit_minor": int(line.get("credit_minor", 0) or 0),
        "credit_minor": int(line.get("debit_minor", 0) or 0),
    } for line in original.get("lines", [])]
    debit_total = sum(x["debit_minor"] for x in lines)
    credit_total = sum(x["credit_minor"] for x in lines)
    if not lines or debit_total != credit_total:
        raise ValueError("Cannot reverse an invalid or unbalanced journal")
    return {"lines": lines, "debit_minor": debit_total, "credit_minor": credit_total,
            "currency": original.get("currency", "INR"), "balanced": True}


def build_journal(transaction: dict) -> dict | None:
    typ = str(transaction.get("type", ""))
    if typ in {"expense_allocation", "reversal"}:
        # Reversals must invert the original journal; expense allocations are not cash expenses.
        return None
    signed_amount = _minor(transaction, "amount_minor", "amount")
    amount = abs(signed_amount)
    if typ in {"transfer", "cash_bank_transfer"}:
        source_account = str(transaction.get("from_account") or "").strip().lower()
        destination_account = str(transaction.get("to_account") or "").strip().lower()
        allowed = {"cash", "bank"}
        # Transfer direction is represented by explicit from/to accounts; a
        # negative signed amount is ambiguous and must not silently keep the
        # original direction while flipping only the number's sign.
        if signed_amount <= 0 or source_account not in allowed or destination_account not in allowed or source_account == destination_account:
            return None
        amount = signed_amount
        lines = [
            {"account_code": destination_account, "debit_minor": amount, "credit_minor": 0},
            {"account_code": source_account, "debit_minor": 0, "credit_minor": amount},
        ]
        return {"lines": lines, "debit_minor": amount, "credit_minor": amount,
                "currency": "INR", "balanced": True}
    if amount == 0:
        return None
    account = "bank" if transaction.get("account") == "bank" else "cash"
    debit = credit = None
    if typ == "loan_disbursement":
        principal = abs(_minor(transaction, "principal_minor", "principal")) or amount
        debit = [("loan_receivable", principal)]
        credit = [(account, principal)]
    elif typ == "loan_repayment":
        principal = abs(_minor(transaction, "principal_repaid_minor", "principal_repaid", "principal"))
        interest = abs(_minor(transaction, "loan_interest_minor", "loan_interest_collected", "interest"))
        penalty = abs(_minor(transaction, "loan_penalty_minor", "loan_penalty_collected"))
        residual = amount - principal - interest - penalty
        if residual < 0: raise ValueError("Loan repayment components exceed payment amount")
        debit = [(account, amount)]
        credit = [("loan_receivable", principal), ("loan_interest_income", interest), ("loan_penalty_income", penalty)]
        if residual: credit.append(("unclassified_receipt_suspense", residual))
    elif typ == "contribution":
        debit, credit = [(account, amount)], [("member_contributions", amount)]
    elif typ == "interest":
        debit, credit = [(account, amount)], [("bank_interest_income" if account == "bank" else "other_interest_income", amount)]
    elif typ == "penalty":
        category = transaction.get("penalty_category") or transaction.get("payment_category") or "bc"
        income = "loan_penalty_income" if category == "loan" else ("other_penalty_income" if category == "other" else "bc_penalty_income")
        debit, credit = [(account, amount)], [(income, amount)]
    elif typ == "expense":
        category = str(transaction.get("category") or "general").strip().lower().replace(" ", "_")[:64]
        debit, credit = [(f"expense:{category}", amount)], [(account, amount)]
    elif typ in {"other_income", "misc_income", "investment_income", "dividend_income", "donation_income"} and signed_amount > 0:
        debit, credit = [(account, amount)], [("other_income", amount)]
    elif amount and signed_amount < 0:
        debit, credit = [("unclassified_outflow_suspense", amount)], [(account, amount)]
    else:
        # Never assume an unfamiliar positive transaction is earned income.
        # Keep it balanced in suspense until a known classification is supplied.
        debit, credit = [(account, amount)], [("unclassified_receipt_suspense", amount)]
    lines = [{"account_code": code, "debit_minor": int(d), "credit_minor": 0} for code, d in debit if d]
    lines += [{"account_code": code, "debit_minor": 0, "credit_minor": int(c)} for code, c in credit if c]
    debit_total = sum(x["debit_minor"] for x in lines)
    credit_total = sum(x["credit_minor"] for x in lines)
    if debit_total != credit_total:
        raise ValueError("Unbalanced journal entry")
    return {"lines": lines, "debit_minor": debit_total, "credit_minor": credit_total, "currency": "INR", "balanced": True}

def build_expense_journal(expense: dict) -> dict | None:
    amount = abs(_minor(expense, "amount_minor", "amount"))
    if amount == 0: return None
    account = "bank" if expense.get("account") == "bank" else "cash"
    category = str(expense.get("category") or "general").strip().lower().replace(" ", "_")[:64]
    lines = [
        {"account_code": f"expense:{category}", "debit_minor": amount, "credit_minor": 0},
        {"account_code": account, "debit_minor": 0, "credit_minor": amount},
    ]
    return {"lines": lines, "debit_minor": amount, "credit_minor": amount, "currency": "INR", "balanced": True}
