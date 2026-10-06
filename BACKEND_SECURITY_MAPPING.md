# Bharat Bachat — Production Mapping

## Tenant vs Group ID

**Decision: `tenant_id` and BC Group ID are the same identity.**

A BC Group is represented by exactly one `tenants` document. Its MongoDB `_id` is the application's `tenant_id` / `group_id`.

This avoids maintaining two IDs for the same business boundary and makes every group-scoped record unambiguous.

Example:

```text
tenants/_id = 68...
        │
        ├── users.tenant_id
        ├── members.tenant_id
        ├── transactions.tenant_id
        ├── loans.tenant_id
        ├── loan_requests.tenant_id
        ├── expenses.tenant_id
        ├── expense_categories.tenant_id
        └── audit_logs.tenant_id
```

## Starting lifecycle

1. Application starts and connects to MongoDB Atlas.
2. If the configured Super Admin phone does not exist, the backend seeds exactly one Super Admin from environment variables.
3. Super Admin creates a BC Group. The inserted tenant `_id` becomes the group/tenant ID.
4. Super Admin creates a Group Admin under that tenant ID.
5. Admin first login is forced to change the temporary credential.
6. Group Admin creates members under the same tenant ID.
7. Member first login is forced to change the temporary credential.
8. All transactions, loans, expenses, images and audit records are stored with that tenant ID.
9. Every protected endpoint validates the signed JWT and then performs a server-side tenant ownership check.

## Ownership mapping

| Entity | Owner mapping | Image/file storage |
|---|---|---|
| Group | `tenants._id` | Cloudinary `tenants/{tenant_id}` |
| Group Admin | `users.tenant_id` | none |
| Member | `members.tenant_id` | Cloudinary `tenants/{tenant_id}/members/{member_id}` |
| Kist / money transaction | `transactions.tenant_id` + `member_id` | none |
| Loan | `loans.tenant_id` + `member_id` | none |
| Loan request | `loan_requests.tenant_id` + `member_id` | none |
| Expense | `expenses.tenant_id` | Cloudinary `tenants/{tenant_id}/expenses/{expense_id}` |
| Expense category | `expense_categories.tenant_id` | none |
| Audit | `audit_logs.tenant_id` | none |

MongoDB stores Cloudinary `secure_url` and `public_id`; binary images/PDF proofs are not stored in MongoDB.

## Authentication lifecycle

- Super Admin is seeded from environment credentials and is intentionally not reset from the UI.
- Group Admin creation sets `must_change_password=true`.
- Member creation sets `must_change_password=true`.
- Admin/member reset also sets `must_change_password=true` and updates `password_changed_at`.
- A JWT contains the password-change timestamp. If the stored timestamp changes, the old JWT is rejected.
- While `must_change_password=true`, only authentication/password-change endpoints are usable.

## Security boundary

A Group Admin can only access its own tenant. A member can only access its own member records, passbook, loans and requests. Super Admin can operate across tenants.

Frontend route hiding is only a UX feature; the backend is the authoritative authorization layer.

## Financial model

- Positive transaction amount = money entering the vault.
- Negative transaction amount = money leaving the vault.
- Each transaction has `account` = `cash` or `bank`.
- Loan disbursement is a negative transaction.
- Loan repayment is a positive transaction and stores principal/interest allocation.
- Expenses are stored separately with their account and reduce the computed balance.
- Group opening cash/bank balances are stored on the tenant document.
- Loan expected interest = principal × monthly interest rate × number of months.

## Immutable audit

The application exposes no update/delete endpoint for audit logs. Operational create/reset/status actions write append-only audit records.
