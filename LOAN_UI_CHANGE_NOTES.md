# Loan UI update notes — 2026-10-19

Scope: Admin Control loan hub/history, Active Loans Management, and Create & Disburse Loan modal.

- Admin Control tabs now show live count pills for Payments, pending Loans requests, and Expenses.
- Loan history uses status pills and separate issued-principal / outstanding-interest amounts.
- Active Loans Management shows Active Loans, Principal Issued, and Total Outstanding; outstanding uses a soft amber warning tint.
- Expanded borrower details show collected principal against issued principal, plus a percentage; expand controls have larger tap targets.
- Loan form is grouped into Loan Terms and Disbursement Details. Eligibility, estimated EMI, total due, and progress are visually emphasized.
- Cash/Bank and date controls remain native HTML select/date inputs in this repository's React + Vite web frontend; they are given larger mobile tap targets. The supplied archive is not a React Native/NativeWind project, so these changes use the existing React/Tailwind-compatible web components and preserve current API/financial calculation behavior.
- Hindi translations added for new visible labels.

Validation: TypeScript/TSX transpile syntax checks passed for `frontend/src/App.tsx` and `frontend/src/i18n.ts`. Full `npm run build` could not be completed in this environment because the ZIP has no `node_modules` and npm package retrieval was unavailable/timed out; no claim of a successful production build is made.

## Cumulative interest and repayment follow-up — 2026-10-19

See `LOAN_ACCOUNTING_FOLLOWUP.md` for the detailed accounting behavior and validation status. The active loan portfolio now shows full-tenure contractual pending interest and recovery progress; repayment accrual is cumulative through the selected payment month and capped at the saved contract total. The expanded card has a direct repay shortcut. The server allocates any applicable penalty, accrued interest, then principal and posts a balanced Cash/Bank → Loan Receivable / Loan Interest Income journal split.

## Active Loans / Group Loans UI follow-up (2026-10-19)
- Active Loans summary now includes Total Interest Collected, calculated from the loan records returned by the active-loan overview.
- Both Active Loans and Group Loans use matching borrower accordion cards and show principal and contractual-interest recovery as `collected / expected Recovered (percent)` with progress bars.
- Active Loans repayment shortcut opens Group Loans with the selected loan preselected in the repayment modal, including a fallback lookup for loans beyond the first page.
- Admin Dashboard Quick Actions now routes `Group Loans` directly to `/loans`; the separate `Loan Management` quick action was removed.
- Removed the green Active Portfolio banner from Group Loans.
- The paginated loans API now includes a database-aggregated `total_interest_collected` across all loans, so the Group Loans summary does not sum only the currently displayed page.
- English/Hindi labels added for the new progress and summary labels.
