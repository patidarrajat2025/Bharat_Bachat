# Changelog

## Backend performance remediation

- Optimized Save All Kist member/share reads using request-scoped batch loading.
- Moved duplicate-share and ownership validation ahead of financial writes.
- Replaced per-share historical transaction materialization (up to 500 rows) with MongoDB aggregation in integer minor units.
- Acquired share locks in deterministic order before posting and ensured acquired locks are released in `finally`.
- Added slow Save All Kist phase timing logs without logging payment details or member IDs.
- Scoped Save All Kist status aggregation to the currently requested member page.
- Added audit and test-status documentation.

No frontend source or API response schema was intentionally changed. No financial collections or historical records were deleted or migrated.

## 2026-10-18 — Monthly Kist bulk performance follow-up
- Reduced bulk share-lock round trips by cleaning expired selected locks in one query, acquiring the selected locks with one `insert_many`, and releasing only locks owned by the request's lock token in one query. The unique `operation_key` index remains the concurrency guard.
- Parallelized independent, idempotent transaction side effects (balanced journal, financial feed, audit event, and notification writes); the request waits for every side effect and leaves the transaction pending if any side effect fails, preserving retry/recovery behavior.
- Bulk monthly Kist suppresses per-transaction **admin** notifications and upserts one group/month summary notification. Member-specific account activity notifications remain intact. Previous individual admin transaction notifications for transactions in the batch are dismissed rather than deleting financial records.
- Added timing fields for lock acquisition, aggregation, transaction insert/finalization, journal, feed, audit, notification, and completion phases to the slow bulk endpoint log.

## 2026-10-18 — Monthly Kist transaction/audit optimization
- Bulk monthly Kist now performs one grouped historical-payment aggregation for all selected shares, replacing the per-allocation aggregation query loop.
- Reused the already-validated tenant/member/active-share relationships in this protected bulk path to avoid repeating member and share lookups for every transaction; the normal transaction API validation path remains unchanged.
- Reduced journal posting round trips by atomically upserting/verifying/posting a journal in one `find_one_and_update` and then marking the source transaction's journal status. The expected balanced debit/credit totals are part of the match; conflicts fail closed.
- Bulk monthly Kist no longer creates per-contribution/per-penalty transaction audit rows or per-transaction notifications. It creates one final batch audit event with period, entry/member counts, collection amount, penalty count and penalty amount, and one admin summary notification.
- Defer per-row side-effect completion flags until every transaction/penalty in the batch has successfully finalized, then mark them complete in one tenant-scoped `update_many`. If processing fails before that point, records remain recoverable/pending rather than being falsely marked complete.
- Added transaction relation validation, idempotency lookup, and raw Mongo insert timing fields to the slow endpoint log.
- Individual transaction records, financial-feed rows, and balanced journal rows are preserved. No source financial transactions are merged or deleted.

## 2026-10-18 — BC penalty cutoff and Kist collection workflow fixes
- Fixed BC Kist late-penalty calculation to use the configured BC due date as the cutoff without applying a second 10-day grace window. With due day 10 and a configured ₹200/day rate, a collection dated day 11 is one overdue day (₹200), day 12 is two overdue days (₹400), etc. Existing per-member/per-period idempotency key remains in place to prevent duplicate penalty rows.
- Added Payment Date (defaults to today) and Cash/Bank account selection to the dashboard Kist Pending/Partial drill-down Save Payments controls; both selected values are sent to the existing bulk API.
- Added a direct dashboard Quick Action that opens the Admin Loans tab and automatically opens the Create Loan form for the selected member and amount. Existing Loan Management action remains available.
- Fixed the Save All Kist "This collection" summary card so large currency amounts can wrap/scale within the card instead of being clipped.
- No existing financial records were modified or backfilled by this patch.

## Loan UI modernization (2026-10-19)
- Added count pills to Admin Control Payments, Loans, and Expenses tabs.
- Restyled Admin Control loan history with semantic Active/Overdue/Closed pills, clearer principal/interest summaries, and a high-contrast Create Loan action card.
- Refined Active Loans Management metrics with an amber Total Outstanding card, clean borders/shadows, larger expandable touch targets, status pills, and rupee-based repayment progress labels.
- Reorganized Create Loan form into Loan Terms and Disbursement Details groups, upgraded the credit eligibility/financial summary panel, and increased account/date input tap targets.
- Added Hindi localization for the new loan UI strings; existing loan calculation and API posting logic was intentionally preserved.


## 2026-10-20 — i18n, transparency and member access
- Removed synthetic Hindi `अन्य` fallback and filled missing static UI translations.
- English is default; Hindi preference is stored only by Settings language control.
- Added read-only member access to Group Loans and group accounting drill-downs, plus member dashboard shortcuts and admin call/WhatsApp directory.
- Added tenant-scoped support contacts endpoint; admin-only write APIs remain protected.
