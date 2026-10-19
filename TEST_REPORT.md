# Test Report — 2026-10-19 loan accounting/UI follow-up

## Passed
- `python -m compileall -q backend/app backend/tests`: PASS.
- `PYTHONPATH=. pytest -q tests/test_accounting_engine.py tests/test_journal.py tests/test_tenant_relation_validation.py`: 37 passed, 6 subtests passed.
- Frontend `App.tsx` and `i18n.ts` TypeScript transpile syntax checks: PASS (no syntax diagnostics).
- ZIP integrity check: PASS after packaging.

## Blocked / not run
- Frontend production build/typecheck: not run; `npm ci --offline` failed because a dependency tarball was not cached. Full `npm run build` is not verified.
- Accounting authorization test module and full backend test discovery: not run in this test invocation because the environment lacks `bson`/PyMongo.
- MongoDB integration, concurrent repayment/reversal tests, and live Render tests: not available here.

## Production validation still required
- Test first, partial, same-month repeated, next-month, late, and final-tenure repayments against a staging MongoDB.
- Confirm expected interest = original principal × saved monthly rate × tenure; cumulative interest progress increments once per posted repayment and never exceeds 100%.
- Confirm partial payments reduce contractual pending interest by only the interest component actually collected, and reduce principal only by the principal component.
- Verify Cash and Bank repayment journals are balanced and use `cash`/`bank` as the debit asset, `loan_receivable` for principal recovery, and `loan_interest_income` for interest income.
- Verify exact retries do not duplicate transactions, loan counters, journal entries, or financial-feed rows; verify reversal behavior before production rollout.
