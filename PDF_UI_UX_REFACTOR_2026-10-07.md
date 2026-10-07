# Bharat Bachat — PDF + UI/UX Refactor (2026-10-07)

- Passbook/receipt PDFs now share one professional A4 bank-statement visual system.
- Passbook includes 2x2 summary cards, detailed transaction table, zebra rows, notes, and signature/stamp area.
- Credits are emerald, debits are red, closing balance is navy, and zero/neutral values remain soft grey.
- Bharat Bachat logo is bundled into the backend PDF asset path so Render PDF generation does not depend on the frontend deployment.
- Dashboard Group Total and member dashboard both use the same Group Total / My Share metric component; Group Profit is shown as a dedicated positive/negative/neutral metric.
- Analytics uses animated skeleton cards during scope/share/year data transitions.
- Search inputs have clear (X) actions when populated.
- PDF downloads show an immediate in-app toast after the PDF is generated/downloaded.
- Login password Show/Hide control is vertically centered and input line-height is stable for mobile keyboard viewport changes.
- Action Sheet / More drawer icons use a consistent 24px size.
- Analytics charts have additional bottom breathing room to keep month labels/legend content readable.
