# Loan dashboard redesign

## Implemented
- Updated Group Loans and Active Loans summary to a compact three-metric layout: Total Loans, Total Outstanding, and a live-dated amount indicator.
- Applied the dark navy expandable loan-card style to admin/group loan cards and member loan-history cards, with responsive mobile layout.
- Added Loan Penalty visibility to loan detail and repayment screens using the loan's stored due-day and per-day penalty terms.
- Repay Loan action prefills an editable total estimate (remaining principal + accrued interest through selected date + estimated penalty). Changing the repayment date recalculates that estimate.
- Repayment allocation preview shows penalty first, then accrued interest, then principal.
- Preserved the existing loan repayment API and idempotency key. Backend remains authoritative and validates the actual split.

## Important calculation note
`loan_due_date` is the configured calendar day of month (default day 10), not a separate grace-period duration. Penalty estimate follows the current backend's cumulative missed-month-cycle calculation and stored loan terms. Backend recalculates before posting; frontend estimate is informational and can differ if the server data changes.

## Verification
- TypeScript transpile syntax checks: `App.tsx` and `i18n.ts` passed.
- Backend targeted accounting/journal/tenant relation tests: 38 passed, 6 subtests passed.
- ZIP integrity check performed.
- Full frontend production build was not run because dependencies were not installed in this workspace.
