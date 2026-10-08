from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .db import connect_db, close_db, get_db, backfill_financial_feed
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
    asyncio.create_task(backfill_financial_feed())
    yield
    await close_db()

app = FastAPI(title="Bharat Bachat API", version="1.0.0", lifespan=lifespan)

@app.middleware("http")
async def request_timing(request: Request, call_next):
    # Request lifecycle timing is intentionally logged for every API call so
    # Render logs can show exactly where time is spent from request start to end.
    # Query strings are excluded to avoid leaking user-supplied values.
    import time
    import secrets

    started = time.perf_counter()
    request_id = secrets.token_hex(4)
    path = request.url.path
    is_api = path.startswith("/api/")
    if is_api:
        print(f"[api:start] id={request_id} {request.method} {path}", flush=True)

    response = None
    error = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error = exc
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response is not None:
            response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.0f}"
            response.headers["X-Request-Id"] = request_id
        if is_api:
            status = response.status_code if response is not None else 500
            suffix = f" error={type(error).__name__}" if error else ""
            print(
                f"[api:end] id={request_id} {request.method} {path} "
                f"status={status} total_ms={elapsed_ms:.0f}{suffix}",
                flush=True,
            )
            if elapsed_ms >= 1000:
                print(
                    f"[slow-api] id={request_id} {request.method} {path} "
                    f"{elapsed_ms:.0f}ms",
                    flush=True,
                )
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
