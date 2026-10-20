# Loan overview and early-settlement follow-up

## Changes
- Removed the duplicate/ambiguous `Total Interest` metric from Group Loans Overview; retained `Total Interest Collected`, which aggregates collections across active and closed loans.
- Standardized Active Loans Management borrower-card drill-down structure to match Group Loans: borrower accordion, status, principal recovery, loan details, interest recovery indicator and repayment action.
- Implemented early full-settlement behavior in the canonical loan repayment application path. If outstanding principal reaches zero and all interest accrued through the selected payment month has been paid, the loan closes; future-month interest is waived and the stored expected interest is adjusted to the settled accrued amount. The original contractual interest amount is preserved in audit fields (`original_contractual_interest` and `original_contractual_interest_minor`).
- Repayment modal now previews interest payable through the selected payment date, rather than presenting full-tenure interest as due now. Both Group Loans and Admin loan-history repayment entry points show the early-settlement rule.
- Added English/Hindi strings for the new repayment labels and guidance.

## Verification
- `python -m compileall -q backend/app backend/tests`: passed.
- `pytest -q tests/test_accounting_engine.py tests/test_journal.py tests/test_tenant_relation_validation.py`: 38 passed, 6 subtests passed.
- TypeScript transpile syntax checks for `frontend/src/App.tsx` and `frontend/src/i18n.ts`: passed.
- Full `npm run build`, live MongoDB repayment flow, and deployed Render validation were not run in this environment.
