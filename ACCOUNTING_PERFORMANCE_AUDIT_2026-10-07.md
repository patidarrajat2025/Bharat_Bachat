# Bharat Bachat — Accounting & Performance Audit

## Scope
Audited the latest `Bharat-Bachat-FIXED-2026-10-07.zip` at code level for MongoDB indexing, API query load, duplicate frontend requests, dashboard accounting, member/admin separation, notifications and banking-style cash-flow calculations.

## High-impact fixes applied

### Frontend request load
- Removed the Layout-level `GET /api/auth/me` + tenant lookup that ran every time a protected screen remounted.
- Analytics no longer loads every member and then calls `memberShares` once per member. Personal analytics now loads only the logged-in user's shares when the Personal tab is selected.
- Member Details now requests the selected member directly and requests only that member's loans instead of loading the complete group loan list and filtering in the browser.
- Notification unread marking changed from one PATCH per notification to one batch POST.

### Backend performance
- Group summary no longer loads up to 20,000 transactions, 10,000 expenses and 10,000 loans into Python for every dashboard request. It now uses MongoDB aggregation for financial totals and parallel count queries.
- Monthly analytics changed from up to `2 × months` sequential collection queries to two MongoDB aggregation pipelines plus share counts.
- Monthly Kist summary no longer calls `ensure_member_shares()` for every member during a GET and no longer mutates the database from a read request.
- Group loans removed the per-loan member lookup (N+1 query pattern).
- Notification GET is now read-only. Notification rows are created when the underlying transaction/audit event is written.
- Added indexes for transaction type/member/share/expense/loan access patterns, loan status, loan request status, expense account/date and notification recipient lookups.

## Accounting rules verified/fixed

### Group view
- Member Principal Savings = contribution transactions.
- Group Profit = interest + penalties + loan interest income + other genuine income − group expenses.
- Group Closing Balance = opening cash + opening bank + real ledger transactions − source expenses.
- Expense allocation rows are excluded from group cash-flow calculations so member allocations cannot double-count an expense.
- Loan disbursement reduces group cash/bank.
- Loan repayment increases group cash/bank; only its interest component contributes to profit.
- Cash and bank outflows include source expenses exactly once.

### Member / Admin My Share view
The personal passbook is now presented from the member's own accounting perspective:
- Loan disbursement = money received by the member (credit).
- Loan repayment = money paid by the member (debit).
- Group expense allocation = member debit.
- My Net Value = My Inflow − My Outflow.
- My Profit / Earnings Share remains the member's proportional share of group profit.
- Loan interest paid is derived from the interest component of loan repayment records.

This prevents loan principal from being double-counted as both a cash outflow and an outstanding loan deduction in the personal hero metric.

## Duplicate-call audit

There is no React StrictMode double-mount in `main.tsx`, so the application is not intentionally issuing development-only duplicate requests from StrictMode.

The largest real duplicate/N+1 pattern was Analytics: group analytics could request member data plus up to 100 individual share requests. That has been removed.

Dashboard screens still make several purposeful parallel requests (summary, tenant, members, Kist status, activity, personal passbook/loans). These are not duplicate calls; they supply separate screen data. The backend-heavy endpoints have been optimized so the same number of purposeful requests is much cheaper.

## Render / MongoDB note

This audit is code-level. The deployed Render MongoDB instance and its live collection contents/index statistics were not directly accessible from this workspace, so the audit cannot claim a live `explain()` result or verify the exact production document counts.

After deploying this build, MongoDB Atlas Performance Advisor / profiler should be checked for the new compound indexes and for any remaining slow query above ~100–200 ms.
