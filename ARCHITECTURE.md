# Bharat Bachat architecture migration

This branch performs the first safe extraction stage while retaining legacy entry points to avoid changing runtime contracts.

## Frontend
- `frontend/src/components.tsx` is now a compatibility barrel.
- Each previously exported reusable component has an implementation file in `frontend/src/components/`.
- `App.tsx` remains the legacy page/router composition file. Its domain screens are not yet fully extracted because they share substantial state, helpers, and API workflows; extracting them requires a dependency-aware migration and route-level tests.

## Backend
- `backend/app/api/v1/router.py` aggregates existing routers without changing their URL prefixes.
- `backend/app/api/group.py` and `backend/app/services.py` remain the current implementation sources. They are not yet fully decomposed into independent router/service/repository modules. Do not treat the backend as fully refactored.

## Safe next migration steps
1. Install dependencies from the committed lockfiles in a clean environment.
2. Run frontend typecheck/build and backend unit/integration tests.
3. Extract one page/domain at a time, first identifying its imports, shared helpers, store dependencies, and route contract.
4. Split group endpoints by domain while preserving exact paths and authorization dependencies.
5. Introduce repositories behind existing service functions and compare behavior using integration tests.

Never change tenant filters, financial journal semantics, idempotency keys, recovery markers, or response schemas as part of a file-only refactor.
