# Bharat Bachat — Share-wise Kist Update

## Final share architecture
- `tenant_id` is the canonical BC Group ID.
- `member_id` identifies the person.
- `share_id` identifies an individual active share.
- `share_no` is the human-readable number within a member (Share 1, Share 2, ...).
- `share_code` is a durable human-support code such as `SHR-XXXXXXXX-01`.

## Member onboarding
When an Admin creates a member with `shares = N`, Share 1..N are created automatically. Existing members are lazily migrated to the `shares` collection the first time their member/share data is loaded.

Increasing shares creates only missing share records. Reducing below active shares is blocked so historical ownership cannot be silently removed.

## Monthly Kist
Each group has `kist_per_share` (default ₹500, configurable by Super Admin).

Admin flow:
1. Admin Control → Payments → Monthly Kist Collection.
2. Select member.
3. Select month.
4. System loads every active share and its expected/paid/pending state.
5. Enter one amount per share, or click `Pay All Remaining`.
6. Partial payments are supported.
7. Backend rejects over-collection and duplicate full payment for a share/month.

Each new Kist transaction stores the exact `share_id`, `share_no`, period, expected amount, cumulative paid amount and status.

## Personal views
Members can choose:
- All Active Shares
- Share 1
- Share 2
- ...

The selector is available on Personal Dashboard, Personal Analytics and Passbook. Passbook PDF uses the same share filter.

## Compatibility
Legacy contribution transactions without `share_id` are still recognized using `member_id + share_no + transaction date` for monthly Kist status, so existing data is not discarded.
