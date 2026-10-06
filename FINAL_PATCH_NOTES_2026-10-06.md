# Bharat Bachat — Final Patch 2026-10-06

Base: Bharat-Bachat-FINAL-Bulk-Kist-Progressive-Share-Profit-Expense.zip

## Applied
- Bulk Monthly Kist member/share rows redesigned for mobile: expected, paid, status and payment input no longer overlap; share-wise amount fields remain editable and bulk collection remains member-dropdown-free.
- Member dashboard profit card removed from the main dashboard and moved into the My Share detailed breakdown. Breakdown state resets when switching Group Total/My Share.
- PDF receipt, receipt bundle and passbook rendering upgraded to branded A4 layouts with Bharat Bachat header, group/member metadata, summary cards, transaction table, credit/debit styling and page footer.
- PDF attachment sharing now validates file sharing support before invoking the native share sheet. On secure HTTPS Android Chrome, the native sheet can expose installed apps such as WhatsApp/Telegram.
- PWA manifest compatibility tightened: standalone display, explicit `prefer_related_applications: false`, separate `any` and `maskable` icon purposes, and mobile web-app metadata.
- Admin/audit info icon moved farther to the edge and given a compact circular control.

## Validation
- Backend PDF module compiles with `python -m py_compile`.
- Generated sample receipt/passbook/bundle PDFs successfully.
- App TSX source transpilation check passes for `App.tsx` and `components.tsx`.
- Full `npm run build` could not be completed in the isolated build environment because the extracted project had no populated dependency cache; the user's local project should run `npm install` before `npm run build`.
