from __future__ import annotations

from datetime import date as DateType
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


Role = Literal["super_admin", "group_admin", "member"]
Account = Literal["cash", "bank"]


class LoginRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=4, max_length=128)

class TenantSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    code: str | None = Field(default=None, min_length=2, max_length=30)
    logo_url: str | None = None
    opening_cash: float | None = Field(default=None, ge=0)
    opening_bank: float | None = Field(default=None, ge=0)
    kist_per_share: float | None = Field(default=None, gt=0)

class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=2, max_length=30)
    opening_cash: float = Field(default=0, ge=0)
    opening_bank: float = Field(default=0, ge=0)
    logo_url: str | None = None
    kist_per_share: float = Field(default=500, gt=0)


class AdminCreate(BaseModel):
    tenant_id: str
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(default="", max_length=60)
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=4, max_length=128)
    email: EmailStr | None = None
    shares: int = Field(default=1, ge=1, le=100)


class UserStatus(BaseModel):
    active: bool


class PasswordReset(BaseModel):
    password: str = Field(min_length=4, max_length=128)


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=4, max_length=128)
    new_password: str = Field(min_length=4, max_length=128)


class MemberCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(default="", max_length=60)
    phone: str = Field(min_length=6, max_length=20)
    secondary_phone: str = Field(default="", max_length=20)
    email: EmailStr | None = None
    address: str = Field(default="", max_length=300)
    shares: int = Field(default=1, ge=1, le=100)
    password: str = Field(min_length=4, max_length=128)


class MemberUpdate(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(default="", max_length=60)
    secondary_phone: str = Field(default="", max_length=20)
    email: EmailStr | None = None
    address: str = Field(default="", max_length=300)
    shares: int = Field(default=1, ge=1, le=100)


class ContributionCreate(BaseModel):
    member_id: str
    amount: float = Field(gt=0)
    share_no: int = Field(default=1, ge=1)
    account: Account = "cash"
    date: DateType | None = None
    note: str = ""


class MoneyInCreate(BaseModel):
    member_id: str | None = None
    amount: float = Field(gt=0)
    type: Literal["interest", "penalty"]
    account: Account = "cash"
    date: DateType | None = None
    note: str = ""


class LoanCreate(BaseModel):
    member_id: str
    principal: float = Field(gt=0)
    interest_rate: float = Field(default=2.0, ge=0, le=100)
    months: int = Field(default=2, ge=1, le=60)
    account: Account = "cash"
    purpose: str = Field(default="", max_length=300)
    date: DateType | None = None


class LoanPayment(BaseModel):
    loan_id: str
    amount: float | None = Field(default=None, gt=0)
    # Backward-compatible fields for older clients; when amount is supplied the server calculates the split.
    principal: float = Field(default=0, ge=0)
    interest: float = Field(default=0, ge=0)
    account: Account = "cash"
    date: DateType | None = None
    note: str = ""


class LoanRequestCreate(BaseModel):
    amount: float = Field(gt=0)
    purpose: str = Field(default="", max_length=300)


class LoanRequestDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = ""
    principal: float | None = Field(default=None, gt=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    months: int | None = Field(default=None, ge=1, le=60)
    account: Account = "cash"


class MonthlyKistAllocation(BaseModel):
    share_id: str = Field(min_length=1)
    amount: float = Field(gt=0)


class MonthlyKistCreate(BaseModel):
    member_id: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    allocations: list[MonthlyKistAllocation] = Field(min_length=1)
    account: Account = "cash"
    date: DateType | None = None
    note: str = Field(default="", max_length=300)


class BulkMonthlyKistEntry(BaseModel):
    member_id: str
    allocations: list[MonthlyKistAllocation] = Field(default_factory=list)

class BulkMonthlyKistCreate(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    entries: list[BulkMonthlyKistEntry] = Field(min_length=1)
    account: Account = "cash"
    date: DateType | None = None
    note: str = Field(default="", max_length=300)

class ExpenseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0)
    account: Account = "cash"
    note: str = Field(default="", max_length=300)
    date: DateType | None = None


class ExpenseCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class DateRange(BaseModel):
    from_date: DateType | None = None
    to_date: DateType | None = None
    share_no: int | None = Field(default=None, ge=1)