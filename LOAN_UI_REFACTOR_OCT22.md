# Loan UI refactor (22 Oct 2026)

Updated existing Bharat Bachat React/Vite UI (this repository is a web React app, not a native React Native project).

- Loan drill-down accordion is single-expand in member history and admin loan list.
- Drill-down Interest Due now uses remaining total contractual interest (`expected_interest` minus interest collected) instead of current-date accrual.
- Loan maturity month is shown alongside days remaining; maturity uses `start_date`, falling back to `created_at`.
- Existing compact principal summary, balanced metric grid, amber interest/penalty banner, slim progress bars and bottom CTA spacing retained/polished.
- Repayment summary label changed to Total Interest Payable and shows remaining contractual interest.
- Full Amount and Loan EMI Pay chips get selected styling when their corresponding amount is active.
- Currency input keeps the permanent ₹ prefix and Indian grouping; breakdown amounts are no-wrap and total row is emphasized.
- Compact allocation info disclosure remains in place.

Validation: ZIP structure checked after packaging. `npm run build` could not complete because dependencies (`react`, `vite`, and related packages) are not installed in the extracted environment; this is an environment/dependency limitation, not a successful build claim. Install dependencies in `frontend` and run `npm run build` to validate locally.
