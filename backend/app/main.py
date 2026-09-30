from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError

settings = get_settings()
logger = logging.getLogger("mmcat.api")


def _request_id(request: Request) -> UUID:
    candidate = request.headers.get("X-Request-ID")
    try:
        return UUID(candidate) if candidate else uuid4()
    except ValueError:
        return uuid4()


def _error(
    request: Request, status_code: int, code: str, message: str, fields: dict | None = None
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "fields": fields or {},
                "requestId": str(request.state.request_id),
            }
        },
    )
    response.headers["X-Request-ID"] = str(request.state.request_id)
    return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("mmcat API starting", extra={"environment": settings.app_env})
    yield
    logger.info("mmcat API stopped")


app = FastAPI(
    title="猫子的世界 API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(item).rstrip("/") for item in settings.cors_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = _request_id(request)
    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request.state.request_id)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'; img-src 'self' https: data:; "
        "media-src 'self' https:; style-src 'self' 'unsafe-inline'; connect-src 'self' https:"
    )
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error(request, exc.status_code, exc.code, exc.message, exc.fields)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = {"errors": exc.errors(include_url=False)}
    return _error(request, 422, "VALIDATION_ERROR", "请求参数不合法。", fields)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, _: IntegrityError) -> JSONResponse:
    return _error(request, 409, "RESOURCE_CONFLICT", "资源状态冲突，请刷新后重试。")


@app.exception_handler(Exception)
async def unknown_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", extra={"request_id": str(request.state.request_id)})
    return _error(request, 500, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试。")


app.include_router(api_router, prefix="/api/v1")
