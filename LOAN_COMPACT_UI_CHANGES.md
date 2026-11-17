# Compact Loan UI changes

- Refactored Active Loans and Group Loans expanded cards to use a compact 3-column principal summary, compact amber Interest Due / Loan Penalty banner, and slim inline progress indicators.
- Tightened card padding, grid spacing, typography, and repayment button spacing.
- Refactored Post Loan Repayment modal's loan metrics into a compact responsive grid.
- Added a permanent ₹ prefix, INR suffix, Indian digit-grouping while typing, decimal entry support, and focus styling to the repayment amount input.
- Kept Full Amount / Loan EMI Pay quick actions and custom partial entry.
- Prevented estimated allocation currency values from wrapping, and consolidated allocation notes into a collapsible info note.

## Validation

The TypeScript build could not be completed in this environment because frontend dependencies are not installed (`react`, `react-dom`, `react-router-dom`, etc. missing). The UI changes are applied to the existing React/Vite frontend (`frontend/src/App.tsx` and `frontend/src/styles.css`); this is not a React Native source tree, so the implementation uses its existing React DOM components and CSS rather than introducing an unrelated native app structure.
