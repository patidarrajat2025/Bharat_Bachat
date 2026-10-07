# Bharat Bachat — Performance Optimization 2026-10-07

## What was changed

- Replaced Python-side tenant summary ledger scans with MongoDB aggregation.
- Replaced 12/24 monthly analytics queries with two range aggregations.
- Removed expense-allocation backfill from the member passbook read path.
- Added a one-time background migration for legacy expense allocations.
- Changed expense allocation creation to bulk MongoDB writes.
- Added targeted MongoDB indexes for tenant/member/share/type/date/expense/loan/notification query patterns.
- Stopped group-admin share reconciliation on every authenticated API request.
- Changed member listing from per-member share queries to a bulk share read with repair only when data is incomplete.
- Removed notification creation/upserts from the notification GET path; new notifications are event-driven for important transaction/expense/loan-request activity.
- Added combined read-model endpoints for dashboard, admin overview, register, ledger, loans overview, personal-loan overview, and member details.
- Removed repeated Layout `me` + tenant refresh calls on every route mount.
- Added a 15-second in-memory GET cache plus in-flight dedupe. Every successful mutation invalidates the GET cache, so user/admin writes remain immediately visible.
- Added `X-Process-Time-Ms` and slow-request logging (`>=1000ms`) to the API for production diagnostics.
- Added Render backend `healthCheckPath: /health`.

## Data-safety design

- Existing accounting endpoint contracts remain available; combined endpoints are additive.
- Financial formulas preserve the existing intended semantics: contributions, loan principal/interest, penalties, genuine income, group expenses, cash/bank balances, member profit share.
- Client cache is invalidated after every mutation.
- Legacy expense allocations are backfilled outside request paths instead of being generated during passbook reads.
- Existing UI routes and components remain intact; no visual redesign was introduced.

## Expected result

The biggest latency sources were architectural rather than missing indexes. Dashboard and member-detail navigation now use focused read models, summary/analytics work is done inside MongoDB, and route navigation no longer refetches the same GET data repeatedly during the cache window.

If a production endpoint still exceeds ~1 second, Render logs now emit `[slow-api] METHOD /path Nms`, and the response includes `X-Process-Time-Ms` so the exact backend bottleneck can be identified without guessing.
