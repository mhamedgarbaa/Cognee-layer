import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from configuration.logging_setup import logger


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to generate a unique request ID for each incoming request."""

    async def dispatch(self, request: Request, call_next):
        # Generate a request ID if not provided
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log incoming requests before reaching endpoints."""

    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "request_id", "unknown")

        # Log only method and path (no payload or headers)
        logger.info(
            f"Incoming request: {request.method} {request.url.path}",
            extra={"request_id": request_id},
        )

        # Continue processing request
        response = await call_next(request)
        return response
