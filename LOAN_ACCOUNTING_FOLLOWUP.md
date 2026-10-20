# Loan cumulative-interest and repayment follow-up — 2026-10-19

## Implemented
- Loan repayment accrual now calculates cumulative scheduled simple interest through the selected payment month, prorated against the saved full-tenure contractual interest and capped at that contractual total. Repeated partial payments in one month do not accrue the same month's interest twice.
- Backend interest allocation uses accrued interest through the payment date, then applies the existing configured overdue-penalty policy, then reduces outstanding principal. The server remains authoritative; overpayments beyond currently due penalty + accrued interest + principal are rejected.
- Repayment response `interest_remaining` now reports total contractual interest minus cumulative interest collected, matching the portfolio meaning of pending interest.
- A loan is not marked closed until principal is fully repaid and all contractual interest has been collected. This avoids prematurely closing a loan when the principal is paid but tenure interest remains contractually outstanding.
- Active Loans cards show cumulative contractual interest collected / expected, total contractual pending interest, and a dynamic recovery percentage/progress bar.
- Expanded active-loan details include a prominent “Repay Loan / किस्त जमा करें” action. It opens the repayment modal with the selected loan and member context, and displays principal remaining, contractual pending interest, and a non-authoritative estimated split.
- Repayment continues to post one `loan_repayment` transaction tagged with the selected `cash`/`bank` account. The balanced journal debits that cash/bank asset account, credits `loan_receivable` for principal recovered, and credits `loan_interest_income` for interest collected (with any penalty credited to the configured loan penalty income account). Principal is not counted as profit.
- Added Hindi/English i18n entries and a unit test for cumulative contractual-interest accrual and tenure cap.

## Accounting behavior to understand
- The portfolio's **Total Pending Interest** means total contractual interest remaining over the full loan tenure (`expected_interest - interest_collected`).
- The amount automatically allocated as interest in an individual repayment is only the scheduled interest accrued through that repayment date. Future-tenure interest is not collected early by the auto-split. If an overdue penalty is due, existing policy allocates that first, then interest, then principal.
- The modal split is an estimate only; backend calculations and the posted journal are authoritative.

## Validation
- Python compile check passed.
- Accounting engine, journal, and tenant relation tests passed (37 passed, 6 subtests passed).
- Frontend App.tsx and i18n.ts passed TypeScript transpile syntax checks.
- Full frontend `npm run build` and MongoDB/Render integration tests were not run: dependencies are not installed in this environment and npm offline installation could not retrieve the uncached package archive. Do not treat this as a verified production build.
