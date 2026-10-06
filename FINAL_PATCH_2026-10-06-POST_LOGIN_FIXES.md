# Bharat Bachat — Post Login Operational Fixes

## Fixed
- Group dashboard / Financial Register no longer double-counts expenses caused by per-share `expense_allocation` passbook rows or legacy expense-linked transaction rows.
- Group dashboard detailed breakdown now shows Cash Inflow and Bank Inflow separately and is organized into compact sections: Money Position, Group Operations, Members & Shares.
- My Share breakdown remains member-only and closes automatically when switching Group Total / My Share.
- Notification tray now deletes all currently displayed notifications in one batch when closed via X or backdrop click.
- Latest Activity uses `created_at` ordering so newest activity appears first, with 10-row pagination and scroll containment.
- Expense and money-in forms capture the form element before async API calls and reset all fields after a successful save; expense proof upload is included before reset.
- Modal layout changed to a fixed header + independently scrollable body so loan create/approve/request dialogs do not hide their first fields behind the header.

## Backend
- `tenant_summary()` now excludes expense allocation/source rows from group inflow/outflow calculations and exposes `cash_inflow_account`, `bank_inflow`, and `transactions_count`.
- Admin `transactions` endpoint excludes member-only expense allocations and legacy expense source rows; member passbooks continue to receive their allocation rows.

## Verification
- Backend Python sources pass `python -m compileall`.
- Full frontend `npm run build` could not be executed in the packaging environment because the temporary npm dependency installation timed out and left empty `@types/*` directories. The source changes themselves do not add new frontend dependencies.
