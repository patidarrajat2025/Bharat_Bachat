# Monthly Kist performance and audit follow-up — 2026-10-18

## Changes in this archive

- Historical paid amounts are fetched with one grouped Mongo aggregation for all selected member/share allocations.
- Already batch-validated member/share ownership is reused only by the bulk monthly-Kist route to avoid repeating relation lookups for each transaction.
- Journal posting uses an atomic `find_one_and_update` with the expected balanced debit/credit totals in its filter, followed by a source transaction journal-status update. A conflicting journal fails closed.
- Bulk contribution and BC penalty transaction finalization suppresses per-row transaction audit events and individual notifications. The batch writes one summary notification and one final audit event.
- Per-transaction side-effect completion is deferred and written with one tenant-scoped batch update only after the batch's transaction side effects finish successfully.
- Slow logs include `aggregate_s`, `mongo_insert_s`, `relation_s`, `idempotency_s`, `journal_s`, `feed_s`, `audit_s`, `notification_s`, and `completion_s` in addition to the existing overall timing fields.

## Important integrity behaviour

- Individual source transactions, financial-feed entries, and journal entries are not deleted or combined.
- The batch does not mark transactions complete if any awaited side effect fails before batch completion.
- Locks remain scoped to selected shares and are released by the request's lock token.
- The summary notification is admin-facing; per-transaction notifications are suppressed for the bulk collection path to meet the requested single-notification behaviour.
- The final audit contains aggregate batch details, not a list of every member contribution.

## Verification

- `python -m compileall -q backend/app`: PASS.
- `PYTHONPATH=. pytest -q tests/test_accounting_engine.py tests/test_journal.py`: 30 passed, 6 subtests passed.
- Full backend test collection is still blocked in this environment because PyMongo/BSON is not installed (`ModuleNotFoundError: bson`).
- No live MongoDB benchmark was possible here; deploy to staging and run the same 50-allocation test to measure actual latency and confirm the expected one-summary notification / one-batch audit behaviour.

## 2026-10-18 — Follow-up functional fixes
- BC Kist penalty cutoff corrected: `bc_due_date` is the grace cutoff, so overdue days begin on the next calendar day. The application no longer subtracts a second 10-day grace period.
- Pending/partial dashboard Kist drill-down now lets the admin choose payment date and Cash/Bank account; default date is the current local date and default account is Cash.
- Dashboard Quick Actions include a direct Apply Loan entry that opens Admin > Loans > Create Loan.
- Save All Kist summary currency text is responsive and not clipped for larger amounts.
- Frontend production build and MongoDB integration checks remain unverified in this environment.
