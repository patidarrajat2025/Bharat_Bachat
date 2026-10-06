# Bharat Bachat — Production Architecture

## Monorepo
- `frontend/`: React 19 + Vite + Tailwind + Framer Motion + Recharts + i18next + PWA
- `backend/`: FastAPI + Motor + MongoDB + JWT + bcrypt + ReportLab + Cloudinary
- `render.yaml`: separate static frontend and Python API services

## Runtime rules
1. Browser UI never stores passwords; only the short-lived access token and minimal user context are persisted.
2. Tenant-scoped API endpoints enforce `tenant_id` through the authenticated user.
3. GET requests are deduplicated while in flight to prevent accidental duplicate calls from responsive rerenders.
4. Mutations are never deduplicated.
5. PWA installation is supported through `beforeinstallprompt` on compatible Chromium browsers. iOS uses the native Add to Home Screen flow because Safari does not expose the Chromium install event.
6. `display: standalone` removes the normal browser chrome **after the app is installed**. A normal browser tab cannot be forced to hide its URL bar by web code.
7. All mobile controls use at least a 48px touch target.
8. Charts are isolated to Analytics.
9. Lists are card/accordion based on narrow screens and use five-item pagination.
10. Raw database identifiers are not presented as user-facing labels.

## Local
```bash
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

cd ../frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` to the API `/api` base for deployed builds. For phone-on-LAN development the frontend automatically targets the current private LAN host on port 8080 when no production API URL is configured.

## Render
Set these environment variables on the API service: `MONGODB_URI`, `MONGODB_DB`, `JWT_SECRET`, `CORS_ORIGINS`, `SEED_SUPERADMIN_PHONE`, `SEED_SUPERADMIN_PASSWORD`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`.
Set `VITE_API_BASE_URL` on the frontend service to the deployed API base ending in `/api`.
