# Bharat Bachat — Loan / Kist Follow-up Fixes

Based on the latest `bharat-bachat-kist-penalty-payment-loan-quickaction-fix.zip` source.

## Included
- Admin Dashboard Loan Management deep link initializes the Loans tab from `?tab=loans`; Apply Loan continues to open the Create Loan modal.
- Save All Kist modal now exposes Payment Date (defaults to today) and Cash/Bank account selection; the existing payload uses both values.
- The Save All Kist “This collection” amount card uses stronger responsive sizing/wrapping so large rupee amounts remain visible.
- Regular BC late penalty is keyed and posted per active share owned by each collecting member, preserving idempotency and journal/feed finalization; legacy member-level penalties are not silently duplicated.
- Mobile header text is no longer ellipsized by the header title rules.
- Active Loans summary rows show only member name and status until expanded; details include Total Principal, due interest, and collected interest.
- Active Loans summary includes Total Interest for the full contracted tenure. Monthly accrued interest grows with elapsed months, collected interest accumulates, and remaining due is accrued minus collected.
- Admin loan preview uses the entered monthly rate and tenure and matches the backend's flat simple-interest contract.

## EMI arithmetic clarification
For ₹50,000 at 1% per month under flat simple interest:
- 10 months: interest ₹5,000; total ₹55,000; EMI ₹5,500.
- 12 months: interest ₹6,000; total ₹56,000; EMI ₹4,666.67.

₹4,583.33 for 12 months would mean total repayment ₹55,000 (only ₹5,000 total interest), which conflicts with a 1% monthly rate over 12 months. The preview remains consistent with the stored loan contract and backend accounting rather than showing a conflicting estimate.

## Validation
- Backend Python syntax compilation: passed.
- Accounting engine + journal unit tests: 31 passed.
- Frontend TypeScript/TSX syntax transpilation: passed for 8 source files.
- Full TypeScript typecheck / production Vite build: not run because frontend dependencies (`node_modules`) are not present in this environment.
- Live MongoDB / Render integration and current historical penalty data were not tested. Existing posted member-level penalties are not silently rewritten; historical correction should be handled as a separately audited adjustment.
