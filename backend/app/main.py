from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .db import connect_db, close_db, get_db
from .services import backfill_legacy_expense_allocations
from .core.config import settings
from .core.security import hash_password
from .api import auth, super_admin, group, reports

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    db = get_db()
    if not await db.users.find_one({"phone": settings.seed_superadmin_phone}):
        await db.users.insert_one({
            "phone": settings.seed_superadmin_phone,
            "password_hash": hash_password(settings.seed_superadmin_password),
            "name": "Super Admin",
            "role": "super_admin",
            "tenant_id": None,
            "active": True,
            "must_change_password": False,
            "password_changed_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        })
    import asyncio
    asyncio.create_task(backfill_legacy_expense_allocations())
    yield
    await close_db()

app = FastAPI(title="Bharat Bachat API", version="1.0.0", lifespan=lifespan)

@app.middleware("http")
async def request_timing(request: Request, call_next):
    started = __import__("time").perf_counter()
    response = await call_next(request)
    elapsed_ms = (__import__("time").perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.0f}"
    if elapsed_ms >= 1000:
        print(f"[slow-api] {request.method} {request.url.path} {elapsed_ms:.0f}ms", flush=True)
    return response
origins = [x.strip().rstrip("/") for x in settings.cors_origins.split(",") if x.strip()]
# Keep production origins explicit. Do not use `*` because JWT-bearing requests use credentials.

# Explicit production origins come from CORS_ORIGINS. Local development also permits
# private LAN origins so the same Vite build can be tested from a phone.
local_origin_regex = r"https?://(?:(?:localhost|127\.0\.0\.1)|(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3})|(?:192\.168\.\d{1,3}\.\d{1,3})|(?:172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}))(?:\:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=local_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "bharat-bachat-api"}

app.include_router(auth.router, prefix="/api")
app.include_router(super_admin.router, prefix="/api")
app.include_router(group.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
