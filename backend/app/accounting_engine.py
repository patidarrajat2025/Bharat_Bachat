"""Canonical, deterministic accounting primitives. Amounts are integer paise."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date

PAISE = Decimal("100")

def to_minor(value) -> int:
    """Convert a rupee value to paise with explicit half-up rounding."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not amount.is_finite():
        raise ValueError("Monetary amount must be finite")
    return int((amount * PAISE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def from_minor(value: int) -> Decimal:
    return (Decimal(int(value)) / PAISE).quantize(Decimal("0.01"))


def simple_interest_minor(principal_minor: int, rate_percent, months: int) -> int:
    """Calculate simple interest in paise without binary-float arithmetic."""
    try:
        principal = Decimal(int(principal_minor))
        rate = Decimal(str(rate_percent if rate_percent is not None else 0))
        duration = Decimal(max(0, int(months)))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid principal, rate or duration") from exc
    if not principal.is_finite() or not rate.is_finite() or principal < 0 or rate < 0:
        raise ValueError("Principal and rate must be finite and non-negative")
    paise = principal * rate * duration / Decimal("100")
    return int(paise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def allocate_kist_payment(expected_minor: int, already_paid_minor: int, requested_minor: int, *, cap_to_remaining: bool = False) -> dict:
    """Allocate a Kist payment in paise with consistent partial/overpayment rules."""
    expected = int(expected_minor)
    already_paid = max(0, int(already_paid_minor))
    requested = int(requested_minor)
    if expected <= 0:
        raise ValueError("Expected Kist amount must be greater than zero")
    if requested <= 0:
        raise ValueError("Kist payment must be greater than zero")
    remaining = max(0, expected - already_paid)
    if remaining <= 0:
        raise ValueError("This share is already fully paid for the period")
    if cap_to_remaining:
        posted = min(requested, remaining)
    else:
        if requested > remaining:
            raise ValueError("Kist payment exceeds the remaining amount")
        posted = requested
    cumulative = already_paid + posted
    remaining_after = max(0, expected - cumulative)
    return {"posted_minor": posted, "cumulative_minor": cumulative,
            "remaining_minor": remaining_after,
            "status": "paid" if remaining_after == 0 else "partial"}


def allocate_minor(total_minor: int, numerator: int, denominator: int) -> int:
    """Allocate paise proportionally with deterministic half-up rounding."""
    if int(denominator) <= 0 or int(numerator) <= 0:
        return 0
    value = Decimal(int(total_minor)) * Decimal(int(numerator)) / Decimal(int(denominator))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def assert_idempotent_match(existing: dict, *, tenant_id: str, member_id, typ: str,
                            amount_minor: int, account, extra: dict) -> None:
    """Ensure a retry key is not reused for a materially different operation."""
    if existing.get("tenant_id") != tenant_id or existing.get("type") != typ:
        raise ValueError("Idempotency key was already used for a different operation")
    if str(existing.get("member_id") or "") != str(member_id or ""):
        raise ValueError("Idempotency key was already used for a different member")
    old_minor = existing.get("idempotency_amount_minor")
    if old_minor is None:
        old_minor = existing.get("amount_minor")
    if old_minor is None:
        old_minor = to_minor(existing.get("amount", 0) or 0)
    if int(old_minor) != int(amount_minor) or (existing.get("account") or "cash") != (account or "cash"):
        raise ValueError("Idempotency key was already used with a different amount or account")
    for key in ("loan_id", "share_id", "share_no", "expense_id", "reversal_of",
                "payment_category", "penalty_category", "period", "payment_month",
                "from_account", "to_account", "original_type"):
        if key in extra and str(existing.get(key) or "") != str(extra.get(key) or ""):
            raise ValueError(f"Idempotency key was already used with a different {key}")

def _minor_field(row: dict, minor_key: str, rupee_keys: tuple[str, ...] = ()) -> int:
    if row.get(minor_key) is not None:
        try: return int(row[minor_key])
        except (TypeError, ValueError): pass
    for key in rupee_keys:
        if row.get(key) is not None:
            return to_minor(row[key])
    return 0

def profit_components(transaction: dict) -> dict[str, int]:
    """Classify earned income once; principal and member contributions are not profit.

    Reversal records carry the original transaction's income fields with negative
    signs. Classify them using the original type and return negative components.
    """
    typ = str(transaction.get("type", ""))
    is_reversal = typ == "reversal" and bool(transaction.get("reversal_of"))
    if is_reversal:
        transaction = {**transaction, "type": transaction.get("original_type", ""),
                       "amount": abs(float(transaction.get("amount", 0) or 0))}
        if transaction.get("amount_minor") is not None:
            transaction["amount_minor"] = abs(int(transaction.get("amount_minor") or 0))
        for field in ("loan_interest_collected", "interest", "other_interest", "other_interest_value",
                      "loan_penalty_collected", "bc_regular_kist_penalty", "other_penalty", "other_penalty_value",
                      "loan_interest_minor", "interest_minor", "other_interest_minor", "loan_penalty_minor",
                      "bc_penalty_minor", "other_penalty_minor"):
            if transaction.get(field) is not None:
                transaction[field] = abs(int(transaction[field] or 0)) if field.endswith("_minor") else abs(float(transaction[field] or 0))
        typ = str(transaction.get("type", ""))
    account = transaction.get("account") or "cash"
    amount = abs(_minor_field(transaction, "amount_minor", ("amount",)))
    loan_interest = _minor_field(transaction, "loan_interest_minor", ("loan_interest_collected",))
    if loan_interest <= 0 and typ == "loan_repayment":
        loan_interest = _minor_field(transaction, "interest_minor", ("interest",))
    other_interest = _minor_field(transaction, "other_interest_minor", ("other_interest", "other_interest_value"))
    loan_penalty = _minor_field(transaction, "loan_penalty_minor", ("loan_penalty_collected",))
    bc_penalty = _minor_field(transaction, "bc_penalty_minor", ("bc_regular_kist_penalty",))
    other_penalty = _minor_field(transaction, "other_penalty_minor", ("other_penalty", "other_penalty_value"))
    bank_interest = amount if typ == "interest" and account == "bank" else 0
    if typ == "interest" and account == "bank":
        # Legacy money-in writes an other_interest field for every interest row;
        # account classification is authoritative, so bank interest is counted once.
        other_interest = 0
    elif typ == "interest" and other_interest == 0:
        other_interest = amount
    if typ == "penalty":
        # The explicit penalty category is authoritative for legacy rows. Older
        # manual-entry code wrote the amount into other_penalty even when the
        # selected category was BC or Loan; counting that field literally would
        # misclassify the income bucket. Prefer the category-specific paise field
        # when present, otherwise fall back to the transaction amount.
        category = transaction.get("penalty_category") or transaction.get("payment_category") or "bc"
        if category == "loan":
            loan_penalty = loan_penalty or amount
            bc_penalty = 0
            other_penalty = 0
        elif category == "other":
            other_penalty = other_penalty or amount
            bc_penalty = 0
            loan_penalty = 0
        else:
            bc_penalty = bc_penalty or amount
            loan_penalty = 0
            other_penalty = 0
    excluded = {"contribution", "loan_repayment", "loan_disbursement", "expense_allocation", "expense", "interest", "penalty", "reversal", "transfer", "cash_bank_transfer", "cash_deposit", "bank_deposit", "investment_disbursement", "asset_purchase", "asset_sale", "loan_writeoff"}
    # Unknown positive rows are not automatically profit. Only explicit income
    # transaction types enter profit; unknown/legacy categories remain suspense
    # until an administrator classifies them, preventing principal movements
    # from silently inflating profit.
    explicit_income_types = {"other_income", "misc_income", "investment_income", "dividend_income", "donation_income"}
    signed_amount = _minor_field(transaction, "amount_minor", ("amount",))
    other_income = max(0, signed_amount) if typ in explicit_income_types and typ not in excluded else 0
    # Explicit category values win over legacy fallbacks to avoid double counting.
    vals = {"bank_interest": bank_interest, "loan_interest": max(0, loan_interest),
            "other_interest": max(0, other_interest), "loan_penalties": max(0, loan_penalty),
            "bc_penalties": max(0, bc_penalty), "other_penalties": max(0, other_penalty),
            "other_income": other_income}
    vals["income_total"] = sum(vals.values())
    if is_reversal:
        vals = {key: -value for key, value in vals.items()}
    return vals

def profit_minor(transactions, expenses) -> int:
    """Canonical net profit in paise from source transactions and expenses."""
    income = sum(profit_components(row)["income_total"] for row in transactions)
    costs = sum(abs(_minor_field(row, "amount_minor", ("amount",))) for row in expenses)
    return income - costs


def net_profit_from_buckets_minor(*, bank_interest=0, loan_interest=0, other_interest=0,
                                 loan_penalties=0, bc_penalties=0, other_penalties=0,
                                 other_income=0, expenses=0) -> int:
    """Shared profit arithmetic for MongoDB aggregate buckets (values are rupees)."""
    income_minor = sum(to_minor(v) for v in (
        bank_interest, loan_interest, other_interest, loan_penalties,
        bc_penalties, other_penalties, other_income,
    ))
    return income_minor - to_minor(expenses)

def monthly_overdue_penalty_minor(start_date: date, payment_date: date, due_day: int, rate_minor_per_day: int = 100) -> int:
    """Cumulative fixed ₹/day charge for each missed monthly cycle after loan start month.

    Each cycle accrues from its due date through the payment date. A loan is not
    penalized in its origination month. Existing loan-specific rates are passed
    in by the caller; this helper never changes stored terms.
    """
    import calendar
    if payment_date <= start_date: return 0
    year, month = start_date.year, start_date.month
    total_days = 0
    while True:
        month += 1
        if month > 12: year += 1; month = 1
        if (year, month) > (payment_date.year, payment_date.month): break
        day = min(max(1, int(due_day or 10)), calendar.monthrange(year, month)[1])
        due = date(year, month, day)
        if payment_date > due:
            total_days += (payment_date - due).days
    return max(0, int(rate_minor_per_day)) * total_days
