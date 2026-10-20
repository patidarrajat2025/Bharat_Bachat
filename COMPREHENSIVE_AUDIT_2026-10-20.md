# Bharat Bachat — Comprehensive UI/i18n & Member Read-Only Update

Date: 2026-10-20
Base: `bharat-bachat-dashboard-kist-loan-fixes-v6.1-repay-date-build-fix.zip`

## Changes

- Removed the automatic Hindi word fallback that converted unknown words to `अन्य`. Hindi mode now uses vetted dictionary strings; an untranslated key remains its source label instead of producing repeated/incorrect Hindi.
- Added Hindi translations for all literal `tr('...')` labels found in `frontend/src/App.tsx` and `frontend/src/components.tsx` (static-key scan: 0 missing Hindi dictionary keys).
- Language defaults to English. Only the Settings language toggle persists preference through `bb-settings-lang`; the login/header language toggles were removed.
- Added member dashboard shortcuts: Apply for Loan, Group Loans, My Passbook (PDF/share actions remain on passbook screen), and Sanchalak Directory with direct call/WhatsApp links.
- Added a read-only tenant-scoped support-contact endpoint for active group admins.
- Members can open Group Loans and inspect group accounting GET views (BC fund/accounting register, expenses, active loans, interest and closing balances). Member accounting access is read-only; mutation endpoints continue to require admin roles. The repayment modal is blocked for member role even if a repayment query parameter is manually added.
- Group Loans read endpoint is available to authenticated tenant members; its write actions remain hidden and backend mutation APIs retain their admin authorization.
- Added a Financial Register drill-down link to the dashboard group operations section.
- Existing loan interest progress, total interest collected metric, Group Loans card layout, and removal of the Active Portfolio footer are preserved from v6.1.

## Validation

- Python `compileall` passed.
- Backend accounting/journal/tenant relation tests: 38 passed, 6 subtests passed.
- Frontend TSX/TS syntax transpilation passed for `App.tsx`, `i18n.ts`, `components.tsx`, `api.ts`, and `main.tsx`.
- Literal `tr()` key scan: 0 missing Hindi dictionary entries across App.tsx/components.tsx.
- Source search confirms no generated `|| 'अन्य'` translation fallback and no `Active Portfolio` UI string.
- Full `npm run build` was not verified: dependency installation timed out in this environment and the local dependency tree remained incomplete. The authorization test module could not be imported locally because `bson`/PyMongo is not installed here; its expectation was updated to reflect read-only member transparency. Live MongoDB/Render testing was not performed.

## Security boundary

Members receive read-only financial GET views by explicit product requirement. Financial writes, loan disbursement, kist posting, loan repayment and transaction reversal remain on separate admin-authorized mutation endpoints. Member loan application remains available through the personal loan workflow.
