import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.product_entry import api_router as product_entry_api_router, web_router as product_entry_web_router
from app.api.product_import import api_router as product_import_api_router
from app.api.routes import router
from app.api.sales import router as sales_router
from app.api.auth import router as auth_router
from app.api.dashboard import router as dashboard_router
from app.api.dashboard_inventory import router as dashboard_inventory_router
from app.api.dashboard_conversations import router as dashboard_conversations_router
from app.api.dashboard_employees import router as dashboard_employees_router
from app.api.dashboard_reports import router as dashboard_reports_router
from app.api.dashboard_settings import router as dashboard_settings_router
from app.api.telephony import router as telephony_router
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services.telephony import GrandstreamTelephonyRuntime

redis_client: Redis | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global redis_client
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    telephony_runtime = GrandstreamTelephonyRuntime(settings, SessionLocal)
    application.state.telephony_runtime = telephony_runtime

    # RECOVER_INTERRUPTED_CALL_ANALYSES
    # Current deployment has one API container.
    # Its previous in-process tasks cannot survive restart.
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    "UPDATE telephony_calls "
                    "SET analysis_status = 'failed', "
                    "analysis_error = 'PROCESS_INTERRUPTED' "
                    "WHERE analysis_status = 'processing'"
                )
            )
            await session.commit()
            print(
                "INTERRUPTED ANALYSES RECOVERED:",
                result.rowcount
            )
    except Exception as exc:
        print(
            "ANALYSIS RECOVERY ERROR:",
            type(exc).__name__
        )

    await telephony_runtime.start()
    try:
        yield
    finally:
        await telephony_runtime.stop()
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID", "Idempotency-Key"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/telephony", include_in_schema=False)
async def telephony_page():
    return FileResponse(Path(__file__).parent / "static" / "telephony.html")


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "alive", "version": settings.app_version}


@app.get("/health/ready", tags=["health"])
async def ready():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if redis_client is None:
            raise RuntimeError("Redis client is not initialized")
        await redis_client.ping()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": type(exc).__name__})
    return {"status": "ready"}


app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(dashboard_inventory_router)
app.include_router(dashboard_conversations_router)
app.include_router(dashboard_employees_router)
app.include_router(dashboard_reports_router)
app.include_router(dashboard_settings_router)
app.include_router(telephony_router)
app.include_router(router)
app.include_router(sales_router)
app.include_router(product_entry_api_router)
app.include_router(product_import_api_router)
app.include_router(product_entry_web_router)
