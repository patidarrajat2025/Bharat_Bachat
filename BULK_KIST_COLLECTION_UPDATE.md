# Bulk Monthly Kist Collection

- Added a production-style Save All Kist workflow for monthly collections.
- Bulk status endpoint loads active members and every active share in one request.
- Expected remaining amount is prefilled for every unpaid/partial share.
- Search filters visible members without clearing selections/entered amounts.
- Partial collection is supported by clearing/editing an individual share amount.
- Backend validates tenant/member/share ownership, expected monthly amount and remaining balance before writing transactions.
- Bulk write creates normal share-linked contribution transactions so passbook, activity, analytics and receipts continue to work.
- Existing single-member share-wise collection flow remains available.
