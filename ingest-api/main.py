from __future__ import annotations
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

import asyncpg
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth import ApiKeyIdentity, check_rate_limit, require_write_scope, validate_api_key
from config import settings
from kafka_producer import publish_events, stop_producer
from models import IngestRequest, IngestResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
_pg_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pg_pool, _redis
    _pg_pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=2, max_size=10)
    _redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    logger.info("ingest-api: connected to Postgres and Redis")
    yield
    await stop_producer()
    await _pg_pool.close()
    await _redis.close()


app = FastAPI(
    title="OpenTelLM Ingest API",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "validation_failed",
            "fields": [
                {"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        },
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
async def get_pg() -> asyncpg.Pool:
    return _pg_pool


async def get_redis() -> aioredis.Redis:
    return _redis


bearer = HTTPBearer()


async def authenticated(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    pg: asyncpg.Pool = Depends(get_pg),
    redis: aioredis.Redis = Depends(get_redis),
) -> ApiKeyIdentity:
    return await validate_api_key(credentials, pg, redis)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/ingest", response_model=IngestResponse, status_code=202)
async def ingest(
    body: IngestRequest,
    identity: Annotated[ApiKeyIdentity, Depends(authenticated)],
    redis: aioredis.Redis = Depends(get_redis),
):
    # Hard isolation boundary: write scope required, tenant_id + project_id
    # come from the resolved identity, NEVER from the request body.
    require_write_scope(identity)
    await check_rate_limit(redis, identity.tenant_id)

    now_ms = int(time.time() * 1000)
    enriched = []
    for ev in body.events:
        d = ev.model_dump()
        d["tenant_id"] = identity.tenant_id
        d["project_id"] = identity.project_id
        d["timestamp_ms"] = d["timestamp_ms"] or now_ms
        enriched.append(d)

    try:
        await publish_events(enriched)
    except Exception:
        # The SDKs retry on 5xx, so a clear 503 is more useful than a bare 500.
        logger.exception("ingest: failed to publish %d events to Kafka", len(enriched))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "event_bus_unavailable"},
        )

    return IngestResponse(
        accepted=len(enriched),
        trace_ids=[e["trace_id"] for e in enriched],
    )
