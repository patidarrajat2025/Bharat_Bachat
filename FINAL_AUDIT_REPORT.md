# Bharat Bachat — Backend Audit & Remediation Report

## Scope

Reviewed the supplied project ZIP, with emphasis on FastAPI/MongoDB write paths, financial-feed reconciliation, journal posting, reporting queries, and the Save All Kist flow. The code was changed conservatively where improvements could be made without a live database or a full staging environment.

## Implemented changes

### Save All Kist (`POST /group/{tenant_id}/monthly-kist-bulk`)

- Member records are batch-loaded for the request instead of issuing one `get_member()` query per entry.
- Active shares are batch-loaded across the selected members. The legacy `ensure_member_shares()` repair path is called only for members whose configured share count is incomplete.
- Duplicate share selections and member/share ownership are validated before the first financial write, reducing avoidable partial writes caused by malformed requests.
- Period start/end boundaries are computed once per request.
- Per-share historical payment totals are aggregated in MongoDB in integer minor units instead of transferring, sorting, and summing up to 500 transaction documents in Python. This also removes the old 500-record truncation risk.
- Share operation locks are acquired in deterministic order before the first write, so a lock conflict can fail before this batch starts posting. Acquired locks are released in a `finally` block.
- Lock TTL is bounded based on batch allocation count, up to 15 minutes. This is a mitigation, not a substitute for a distributed lease-renewal mechanism; very long-running batches still need staging validation.
- The penalty check is attempted for each selected member, including retry/recovery cases where the kist was already posted. The existing idempotent penalty helper remains authoritative.
- Slow requests (at least one second) emit timing logs for total request, posting, and penalty phases without logging member IDs, amounts, or payloads.

### Save All Kist status (`GET /group/{tenant_id}/monthly-kist-bulk-status`)

- The transaction aggregation is scoped to member IDs on the requested page instead of grouping monthly kist history for the entire tenant for every page request.

## Findings retained for follow-up

### Financial collections

- `transactions` is the operational business-event source.
- `journal_entries` provides a distinct double-entry accounting record and should not be removed without a proven replacement.
- `financial_feed` is a denormalized reporting projection used by activity/register endpoints. It can be justified for read performance, but it must remain rebuildable and must not become an independent source of financial truth.
- The supplied code has `ensure_financial_feed_ready()` logic that performs count/total/account-bucket comparisons and legacy normalization. This can be expensive on large histories. It is not removed because production data and all consumers were not available to validate a safe migration.
- `db.py` performs legacy normalization updates in `create_indexes()`. These are idempotent but can scan data at startup. Moving them to explicit migrations requires a deployment/migration process and must be staged.
- Report paths load up to 100,000 transaction rows for passbook/receipt-bundle statements. This preserves full historical running-balance behavior but can consume substantial memory. A date-range-aware aggregation redesign needs separate correctness tests for opening balances, ordering, reversals, and no-date-filter exports.
- Existing index definitions provide a foundation, but index additions/removals must be based on MongoDB `explain("executionStats")` against representative production-like data. No query plans were available in this environment.

## Architecture recommendation

Keep one authoritative business event in `transactions`; keep balanced, source-keyed `journal_entries` for double-entry accounting; retain `financial_feed` only as a rebuildable read model if measured query benefits justify its maintenance cost. Do not delete collections or backfill/rewrite historical data without a dry-run reconciliation report and rollback plan.

## Important limits

No live MongoDB credentials or staging dataset were supplied. Consequently, this report does not claim measured latency improvement, verified production query plans, successful concurrent multi-instance tests, or full production readiness. The architecture-wide transaction/journal/feed migration is deliberately not performed automatically because it could affect real financial history.

## 2026-10-18 functional follow-up patch
- BC Kist penalty: configured `bc_due_date` is the cutoff; no second grace period is subtracted. With due day 10 and ₹200/day, day 11 yields one overdue day.
- Dashboard Pending/Partial Kist drill-down now sends its selected payment date and Cash/Bank account to the existing bulk API.
- Dashboard quick actions now include a direct Apply Loan action that opens the Admin Create Loan modal.
- Save All Kist collection summary allows larger currency values to wrap/scale instead of clipping.
- Unit tests and syntax checks listed in `TEST_REPORT.md` passed; frontend production build and live MongoDB integration remain unverified.
