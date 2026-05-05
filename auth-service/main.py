from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.database import close_pool, init_pool
from core.redis import close_redis, init_redis
from routers import auth as auth_router
from routers import projects as projects_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await init_redis()
    logger.info("auth-service: connected to Postgres and Redis")
    yield
    await close_pool()
    await close_redis()


app = FastAPI(
    title="OpenTelLM Auth Service",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


# Field-level validation errors → 422 with a clean shape
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "validation_failed",
            "fields": [
                {
                    "loc": [str(p) for p in err["loc"]],
                    "msg": err["msg"],
                    "type": err["type"],
                }
                for err in exc.errors()
            ],
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(auth_router.router)
app.include_router(projects_router.router)
