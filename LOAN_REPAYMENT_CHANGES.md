# Loan repayment changes

- Repayment modal defaults to Loan EMI Pay (monthly principal + scheduled monthly interest + currently displayed penalty); date changes recalculate this default.
- Full Amount selects remaining principal + accrued interest through the selected date + applicable penalty.
- Repayment allocation is now interest first, penalty second, principal third; the estimate banner uses the same order.
- Loan summary due label is dynamically named for the current month/year (for example, October 2026 Due Kist).
- Group Loans and Active Loans cards use light surfaces instead of the dark override.
- Backend overdue penalty retains the loan's saved per-day penalty setting: for each overdue monthly cycle, a fully paid scheduled principal+interest installment has no remaining cycle penalty; payment of at least 50% of scheduled principal+interest applies half the configured per-day rate; payment below 50% applies the full configured rate. Previously collected penalties are deducted from the cumulative due.

Validation: Python compilation succeeded for `backend/app/api/group.py`; selected accounting/journal/tenant validation tests passed (38 passed, 6 subtests). Frontend build was not verified because dependency installation timed out in this environment.
