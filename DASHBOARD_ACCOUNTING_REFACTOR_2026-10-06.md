# Dashboard Accounting Refactor — 2026-10-06

Implemented on the supplied FINAL-3 dashboard metrics base.

## Dashboard metrics
- Group Total hero is now **Total Group Fund** and uses the backend closing/net group vault value.
- Group breakdown explicitly separates Member Principal Savings, Group Total Profit, Cash Inflow, Bank Inflow, Cash Outflow and Bank Outflow.
- My Share hero is now **My Net Value**.
- My Share breakdown explicitly separates My Principal Savings and My Profit / Earnings Share.
- Zero-valued positive/negative metrics use neutral styling.
- Group/member breakdown remains closed by default and resets when switching tabs.

## Accounting
- Backend summary exposes `net_group_vault`, `member_principal_savings`, and `group_total_profit`.
- Group net vault remains the actual cash+bank closing ledger balance, so loan disbursements and group expenses reduce it while genuine inflows increase it.
- Member profit is calculated server-side from genuine profit income allocated by active share count, less the member's allocated share of group expenses. Regular contributions are not treated as profit.
- Group-admin My Share profit is now populated from the same server-side summary instead of a separate 12-month analytics request.

## Render load improvement
- Tenant summary database reads/counts are parallelized with `asyncio.gather`.
- Dashboard/member profit no longer needs the extra 12-month analytics request just to populate the My Share card; summary now returns `member_profit`.
- Render free-tier cold starts can still add a first-request delay, but the dashboard now makes fewer sequential backend operations.
