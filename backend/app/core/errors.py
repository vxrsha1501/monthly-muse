"""Domain exceptions and the uniform error envelope {error:{code,message,details,request_id}}."""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("monthlymuse.error")


class AppError(Exception):
    """Base domain error mapped to an HTTP status by the global handler."""

    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFound(AppError):
    def __init__(self, resource: str = "resource", details: dict | None = None):
        super().__init__("not_found", f"{resource} not found", 404, details)


class ValidationFailed(AppError):
    def __init__(self, message: str = "Validation failed", details: dict | None = None):
        super().__init__("validation_failed", message, 422, details)


class Unauthorized(AppError):
    def __init__(self, message: str = "Could not validate credentials"):
        super().__init__("unauthorized", message, 401)


class Forbidden(AppError):
    def __init__(self, message: str = "Not allowed"):
        super().__init__("forbidden", message, 403)


class Conflict(AppError):
    def __init__(self, message: str = "Resource already exists", details: dict | None = None):
        super().__init__("conflict", message, 409, details)


class RateLimited(AppError):
    def __init__(self, message: str = "Too many requests, slow down", details: dict | None = None):
        super().__init__("rate_limited", message, 429, details)


class ProviderUnavailable(AppError):
    """AI provider failure - 503 with a user-friendly message (Section 10.2)."""

    def __init__(self, message: str = "The AI service is unavailable; template options were prepared instead.",
                 details: dict | None = None):
        super().__init__("provider_unavailable", message, 503, details)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        if exc.status_code >= 500:
            logger.error("app_error", extra={"code": exc.code, "path": str(request.url), "request_id": request_id})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details,
                               "request_id": request_id}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        logger.exception("unhandled_error", extra={"path": str(request.url), "request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Something went wrong on our side.",
                               "details": {}, "request_id": request_id}},
        )
