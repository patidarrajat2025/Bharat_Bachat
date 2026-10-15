"""Contract tests: financial mutation requests must carry an idempotency key."""
import pytest
from pydantic import ValidationError
from app.models import (
    ContributionCreate, MonthlyKistCreate, BulkMonthlyKistCreate,
    MoneyInCreate, LoanCreate, LoanPayment, ExpenseCreate,
)


@pytest.mark.parametrize("model,payload", [
    (ContributionCreate, {"member_id": "m1", "amount": 100}),
    (MonthlyKistCreate, {"member_id": "m1", "period": "2026-10", "allocations": [{"share_id": "s1", "amount": 100}]}),
    (BulkMonthlyKistCreate, {"period": "2026-10", "entries": [{"member_id": "m1", "allocations": [{"share_id": "s1", "amount": 100}]}]}),
    (MoneyInCreate, {"amount": 100, "type": "interest"}),
    (LoanCreate, {"member_id": "m1", "principal": 1000}),
    (LoanPayment, {"loan_id": "l1", "amount": 100}),
    (ExpenseCreate, {"category": "Office", "amount": 100}),
])
def test_financial_mutation_requires_idempotency_key(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("model,payload", [
    (ContributionCreate, {"member_id": "m1", "amount": 100}),
    (MoneyInCreate, {"amount": 100, "type": "interest"}),
    (LoanCreate, {"member_id": "m1", "principal": 1000}),
    (LoanPayment, {"loan_id": "l1", "amount": 100}),
    (ExpenseCreate, {"category": "Office", "amount": 100}),
])
def test_financial_mutation_accepts_operation_key(model, payload):
    request = model.model_validate({**payload, "idempotency_key": "operation-2026-10-15-001"})
    assert request.idempotency_key == "operation-2026-10-15-001"
