# Bharat Bachat

Production-oriented PWA monorepo for rural SHG / Bachat Gat management.

## Stack
React + Vite + Tailwind CSS + Framer Motion + Recharts + i18next / FastAPI + Motor + MongoDB / JWT + bcrypt / ReportLab + Cloudinary.

## Included UX
- Standalone PWA manifest and maskable icons
- Mobile-first glass UI with safe-area support
- 48px touch targets and compact bottom navigation
- Hindi/English localization dictionary with centralized `tr()` helper
- Light/dark theme and accent customization
- Member cards instead of wide mobile tables
- Kebab action sheets and dismissible error states
- Five-item pagination for long lists
- Analytics-only charts
- Passbook PDF generation and Web Share API fallback
- Role-aware notifications and tenant-scoped authorization
- Render deployment configuration

## Build
```bash
cd frontend
npm ci
npm run build
```

## API
```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

See `ARCHITECTURE.md` and `LOCAL_AND_RENDER_SETUP.md` for environment configuration.
