import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.product_entry import api_router as product_entry_api_router, web_router as product_entry_web_router
from app.api.routes import router
from app.core.config import settings
from app.db.session import engine

redis_client: Redis | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global redis_client
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield
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
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
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


app.include_router(router)
app.include_router(product_entry_api_router)
app.include_router(product_entry_web_router)
