from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
from starlette.types import ASGIApp

from configuration.logging_setup import logger
from service.custom_service_exceptions import AppException


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Middleware for centralized error handling.

    Catches AppExceptions, Pydantic validation errors, and unexpected exceptions,
    logs them, and returns consistent JSON responses with optional request_id.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        request_id = getattr(request.state, "request_id", None)
        try:
            return await call_next(request)

        except ValidationError as validation_error:
            # Pydantic request validation error
            logger.warning(
                f"Validation error for request {request.url.path}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "errors": validation_error.errors(),
                },
            )
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Validation error",
                    "errors": validation_error.errors(),
                    "request_id": request_id,
                },
            )

        except AppException as custom_app_exception:
            # Custom application exception
            logger.warning(
                f"Application error: {custom_app_exception.detail}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": custom_app_exception.status_code,
                },
            )
            return JSONResponse(
                status_code=custom_app_exception.status_code,
                content={
                    "detail": custom_app_exception.detail,
                    "request_id": request_id,
                },
                headers=custom_app_exception.headers or {},
            )

        except Exception as generic_exception:
            # Unexpected exception
            logger.error(
                f"Unhandled exception: {str(generic_exception)}",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            return JSONResponse(
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
            )
