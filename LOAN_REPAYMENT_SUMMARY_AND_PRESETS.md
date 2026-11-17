# Loan dashboard and repayment summary changes

- Total Outstanding now includes remaining principal plus uncollected contractual interest for the full saved tenure.
- Due Amount is estimated as this month's scheduled principal portion + this month's scheduled interest portion + applicable calculated overdue penalty.
- Expanded loan cards show monthly principal + interest and an installment progress estimate (`paid / tenure`, with remaining count). The count is inferred from principal recovered because the loan list API does not currently expose a canonical installment schedule/payment-count field; treat it as a UI estimate until schedule records are exposed by the API.
- Repayment dialog has `Full Amount` (remaining principal + current month's scheduled interest + applicable penalty) and `Loan EMI Pay` (current month's scheduled principal + current month's scheduled interest + applicable penalty) presets.
- The repayment amount remains editable. The existing backend repayment endpoint remains the source of truth for final allocation and posting; its accrued-interest allocation can differ from the monthly estimate where prior interest is unpaid.
- No new ledger/posting endpoint was added.
