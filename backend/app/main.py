from contextlib import asynccontextmanager
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .db import connect_db, close_db, get_db, backfill_financial_feed
from .services import backfill_legacy_expense_allocations
from .core.config import settings
from .core.security import hash_password
from .api import auth, super_admin, group, reports

logger = logging.getLogger(__name__)

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

    async def _run_background_backfill(label, coroutine):
        try:
            await coroutine
        except Exception:
            # Retrieve and log background exceptions instead of emitting
            # "Task exception was never retrieved" and silently losing context.
            logger.exception("Startup background task failed: %s", label)

    async def _backfill_tenants():
        async for t in db.tenants.find({}, {"_id":1}):
            tenant_id = str(t["_id"])
            try:
                await backfill_financial_feed(tenant_id)
            except Exception:
                # A single tenant's malformed/failed feed must not stop the
                # remaining tenants from being backfilled.
                logger.exception("Financial feed backfill failed for tenant_id=%s", tenant_id)

    async def _recover_financial_side_effects():
        # Source records are written first with a durable pending marker. If a
        # process stops after the source write, resume feed/audit/notification
        # work at startup. Every side effect is idempotent by source key.
        from .api.group import _finalize_tx_side_effects, _finalize_expense_side_effects, _apply_loan_repayment_once, insert_tx
        async for source in db.transactions.find({"side_effects_status":"pending"}):
            try:
                actor={"_id":source.get("created_by","system"),"role":source.get("created_by_role","system"),"phone":source.get("created_by_phone")}
                await _finalize_tx_side_effects(source,actor)
            except Exception:
                logger.exception("Transaction side-effect recovery failed for transaction_id=%s", source.get("_id"))
        async for source in db.transactions.find({"type":"loan_repayment","loan_apply_status":"pending"}):
            try:
                await _apply_loan_repayment_once(str(source.get("tenant_id")),source)
            except Exception:
                logger.exception("Loan repayment application recovery failed for transaction_id=%s", source.get("_id"))
        async for loan in db.loans.find({"disbursement_status":"pending"}):
            try:
                actor={"_id":loan.get("created_by","system"),"role":loan.get("created_by_role","system"),"phone":loan.get("created_by_phone")}
                tenant_id=str(loan.get("tenant_id")); loan_id=str(loan["_id"])
                await insert_tx(tenant_id,str(loan.get("member_id")),"loan_disbursement",-float(loan.get("principal",0) or 0),loan.get("account","cash"),actor,loan_id=loan_id,date=loan.get("start_date") or loan.get("created_at"),note=loan.get("purpose",""),principal=-float(loan.get("principal",0) or 0),idempotency_key=f"loan-disbursement:{loan_id}")
                await db.loans.update_one({"_id":loan["_id"],"tenant_id":tenant_id},{"$set":{"disbursement_status":"complete","disbursement_completed_at":datetime.now(timezone.utc)}})
                from .audit import audit
                await audit(tenant_id,actor,"LOAN_CREATED","loan",loan_id,{"principal":loan.get("principal",0)},event_key=f"loan-created:{loan_id}")
            except Exception:
                logger.exception("Loan disbursement recovery failed for loan_id=%s", loan.get("_id"))
        async for source in db.expenses.find({"side_effects_status":"pending"}):
            try:
                actor={"_id":source.get("created_by","system"),"role":source.get("created_by_role","system"),"phone":source.get("created_by_phone")}
                await _finalize_expense_side_effects(source,actor)
            except Exception:
                logger.exception("Expense side-effect recovery failed for expense_id=%s", source.get("_id"))

    # Complete pending financial writes before the API starts accepting new
    # payments. Recovery is bounded to explicitly pending outbox rows; the much
    # larger legacy read-model backfills remain background tasks.
    await _run_background_backfill("financial side-effect recovery", _recover_financial_side_effects())
    asyncio.create_task(_run_background_backfill(
        "legacy expense allocations", backfill_legacy_expense_allocations()
    ))
    asyncio.create_task(_run_background_backfill(
        "tenant financial feeds", _backfill_tenants()
    ))
    yield
    await close_db()

app = FastAPI(title="Bharat Bachat API", version="1.0.0", lifespan=lifespan)

# Lightweight per-process authentication throttle. This is intentionally conservative
# and complements (rather than replaces) an edge/WAF rate limit in production.
_login_attempts = {}


@app.middleware("http")
async def auth_rate_limit(request: Request, call_next):
    import time
    if request.method == "POST" and request.url.path == "/api/auth/login":
        key=request.client.host if request.client else "unknown"
        now=time.time(); window_start=now-60
        attempts=[x for x in _login_attempts.get(key,[]) if x>window_start]
        if len(attempts)>=20:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail":"Too many login attempts. Please try again shortly."},status_code=429,headers={"Retry-After":"60"})
        attempts.append(now); _login_attempts[key]=attempts
    return await call_next(request)

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
