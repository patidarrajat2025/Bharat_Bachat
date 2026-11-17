# Changelog — 16 November 2026

## Group Investments

- Added a tenant-scoped Group Investments API and React page.
- Added overview, active, add, history and closed views; standard and custom categories.
- Added cash/bank purchase posting, return/sale posting, valuation updates, actual interest receipt and optional document upload.
- Purchase and return operations write the investment source record, cash/bank transaction, balanced journal, financial-feed row and audit row inside a MongoDB transaction.
- Added per-group/account purchase serialization, idempotency keys, role checks and tenant isolation.
- Investment acquisition is journaled to an investment asset, not an operating expense. Capital recovery is excluded from profit; realized gains/losses flow into the canonical profit engine and Financial Register.
- Unrealized valuation changes remain separate from profit. Received interest uses the existing interest classification.
- Added investment collections and indexes to the existing database startup index initializer.

## Dashboard

- Replaced the admin Quick Actions Withdraw shortcut with Investments.
- Fixed the duplicate upward-arrow empty-state symbol in My Loan Tracker by removing its CSS pseudo-element duplicate.
- Kept the five-item mobile navigation and forced Dashboard to remain on one line on narrow screens.

## Packaging cleanup

- Removed Docker Compose, MongoDB container/keyfile assets, Dockerfiles, Docker ignore files, Docker-only environment example, and Docker-only demo seed/setup documentation.
- Kept the Investments UI, API, accounting/journal integration, indexes, audit trail, reversal workflows, and investment regression tests unchanged.
- Removed the Docker-specific database-mode/Atlas URI guard from database startup; `DATABASE_MODE` remains only for the existing investment-document storage selection (`local` filesystem or Cloudinary).

## Verification

- Python syntax compilation: passed for backend application and API modules.
- Focused accounting/journal/tenant/investment tests: 47 passed, 6 subtests passed.
- Full backend suite could not be collected in this runtime because the environment lacks `pymongo`/`bson`. Separately, `test_financial_idempotency_models.py` reports 7 existing expectation failures because the current pre-existing models allow optional idempotency keys while those tests expect them to be required; I did not change those unrelated legacy models.
- Frontend production build was not completed: the uploaded `node_modules` contained Windows-specific command shims; reinstalling npm packages timed out in this environment.

## Known follow-up items

- Full end-to-end MongoDB transaction tests and browser/mobile UI verification still need to be run in the configured development environment.
- Investment metadata editing and explicit financial reversal workflows are not yet exposed in the new UI/API. Financial records are not hard-deleted.
- Cloudinary document upload requires Cloudinary credentials when `DATABASE_MODE` is not `local`; local storage mode stores uploaded documents under `backend/uploads`.

## Follow-up completion — investment metadata and reversals

- Added an admin-only metadata update endpoint and Edit Details UI for investment name, category, description, vendor/bank, reference, maturity, expected return and notes. Posted purchase amounts, account legs and financial history are intentionally immutable; valuation changes remain separate valuation events.
- Added an admin-only investment transaction reversal endpoint and UI action. It creates an exact inverse posted journal, a signed reversal transaction and financial-feed entry, restores the investment's capital/value/profit/loss/interest counters, and records an audit event in one MongoDB transaction.
- Reversals are limited to the latest monetary event; later monetary events must be reversed first. Later valuation snapshots remain immutable, and their current estimate is preserved while restoring the acquisition cost basis. A purchase cannot be reversed after capital has been returned. Reversal rows remain in history; source rows are marked reversed rather than deleted.
- Persisted source transaction IDs on new investment transaction records and added a unique reversal-reference index to prevent duplicate reversal records.
- Reversed acquisitions are excluded from current invested-capital and closed-investment summary totals.
- Added regression coverage for realized investment profit/loss reversal and inverse purchase/return journals.

## Follow-up verification

- Python compileall: passed.
- Focused accounting/journal/investment tests: 46 passed, 6 subtests passed.
- Frontend TypeScript project build (`node node_modules/typescript/bin/tsc -b`): passed.
- Vite production bundle could not complete because the source ZIP's `node_modules` contains Windows-specific package shims and does not include the Linux optional Rollup native package (`@rollup/rollup-linux-x64-gnu`). `npm ci` timed out in this runtime. Re-run `npm ci` and `npm run build` in a clean Linux or Windows development environment.
- MongoDB-backed endpoint integration tests and browser verification remain to be run in the developer's configured MongoDB environment; this runtime does not have `bson`/PyMongo.
