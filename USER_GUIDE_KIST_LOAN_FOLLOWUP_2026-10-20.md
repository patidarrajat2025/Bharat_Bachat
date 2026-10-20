# Bharat Bachat follow-up changes — 20 October 2026

## Implemented

1. **Member dashboard — Monthly Kist Status**
   - Member view now fetches group-wide monthly Kist status through the read-only bulk status endpoint.
   - Collected, Pending and Partial card totals/counts are computed across the active group members, rather than from the logged-in member's personal Kist summary.
   - The same status drill-down remains available to members for member-wise and share-wise reconciliation.
   - Collection inputs, Fill All Remaining, Save Payments, PDF receipt download and WhatsApp receipt actions remain admin-only in the UI.
   - The status endpoint now permits authenticated tenant members to read the group status; it does not permit posting payments. The member request path does not create missing share records as a side effect.

2. **App User Guide in More**
   - Added `/user-guide` and a Book icon item in the mobile More sheet.
   - The page adapts its guidance to the logged-in role and explains dashboard views, Kist status, passbook, loan workflow, expenses/settings for admins, member read-only limitations and safe financial-entry practices.
   - Added Hindi translations for the guide's main labels and instructions.

3. **Member Active Loan drill-down**
   - The dashboard's Active Loans accounting drill-down now returns the member's own active and closed loan records, rather than filtering only active loans.
   - Member screen title/subtitle and empty state reflect loan history.
   - Repay Loan action remains hidden for members. Admin view remains active-loan focused and retains admin repayment controls.

## Verification

- `python -m compileall -q backend/app backend/tests` — passed.
- Accounting engine, journal and tenant relation tests — 38 passed, 6 subtests passed.
- TypeScript transpile/syntax checks for `App.tsx`, `components.tsx`, `api.ts` — passed.
- Full Vite/TypeScript production build not run because this workspace does not have frontend dependencies installed; live MongoDB/Render integration not tested.
- Dashboard card totals cap the counted paid amount at each share's expected amount to match the accounting summary behavior for overpayments.
