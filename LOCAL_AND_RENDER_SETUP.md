# Bharat Bachat — Local + Render Configuration

The application uses the same codebase for local development and Render production.
Secrets and environment-specific URLs are supplied through environment variables.

## Local backend

1. Copy `backend/.env.example` to `backend/.env`.
2. Put the MongoDB Atlas connection string in `MONGODB_URI`.
3. Set the Atlas database name in `MONGODB_DB`.
4. Set Cloudinary credentials.
5. From the `backend` directory, activate the virtual environment and run:

```powershell
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

Health check:

```text
http://localhost:8080/health
```

## Local frontend

Copy `frontend/.env.example` to `frontend/.env`, then run:

```powershell
npm install
npm run dev
```

The local frontend uses:

```text
http://localhost:8080/api
```

## Render backend

Use the existing `render.yaml`. In the backend Render service, set:

- `MONGODB_URI` = MongoDB Atlas connection string
- `MONGODB_DB` = `bharat_bachat`
- `JWT_SECRET` = strong random secret
- `CORS_ORIGINS` = deployed frontend URL, for example `https://bharat-bachat-web.onrender.com`
- `SEED_SUPERADMIN_PHONE`
- `SEED_SUPERADMIN_PASSWORD`
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

Do not upload or commit `backend/.env` to Render or Git.

## Render frontend

Set `VITE_API_BASE_URL` in the frontend Render service to the deployed backend API base URL, for example:

```text
https://bharat-bachat-api.onrender.com/api
```

Then deploy the backend first, confirm `/health` is working, and deploy/redeploy the frontend with the backend URL.
