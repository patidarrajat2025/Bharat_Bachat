# Loan repayment UI consistency fix — 2026-10-21

## Scope
- Both Admin dashboard paths are covered: Dashboard → Active Loans → expand borrower → Repay Loan, and Dashboard → Group Loans → expand borrower → Repay Loan.
- The Active Loans shortcut opens the selected loan in Group Loans; both paths therefore use the same canonical repayment modal and `api.loanPayment` request.
- The repayment form now shows principal remaining, interest payable through the selected date, monthly interest rate, and interest already collected.
- The amount preview explicitly labels principal reduction as an estimate before penalty and displays the server allocation order: applicable overdue penalty → accrued interest → principal. This avoids implying that the client estimate includes penalty.
- Cash/Bank and payment date remain available. Existing idempotency key and backend repayment/journal flow are preserved.
- Existing early-settlement guidance remains visible: future-month interest is waived; the server validates the final split and closes the loan when the canonical settlement conditions are met.

## Validation
- Frontend `App.tsx` and `i18n.ts` TypeScript transpile syntax checks passed.
- Backend accounting/journal/tenant relation tests: 38 passed, 6 subtests passed (`PYTHONPATH=. pytest -q tests/test_accounting_engine.py tests/test_journal.py tests/test_tenant_relation_validation.py`).
- Full frontend `npm run build` was not verified because `npm ci` timed out while installing dependencies in this environment. Do not treat this as a confirmed production build.
