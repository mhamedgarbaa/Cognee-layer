from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.middlewares.logging_middleware import LoggingMiddleware, RequestIDMiddleware
from api.routers.v1.workspace_router import router as workspaces_v1_router
from api.routers.v1.memory_router import router as memory_router
from configuration.database import init_db
from configuration.logging_setup import logger
from configuration.settings import settings


def create_app() -> FastAPI:
    """Create FastAPI app with middleware, routers, and exception handling."""

    app = FastAPI(
        title=settings.TITLE,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        docs_url=settings.DOCS_URL,
        redoc_url=settings.REDOC_URL,
        openapi_url=settings.OPENAPI_URL,
    )

    # Middleware
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    # Routers
    app.include_router(
        workspaces_v1_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        memory_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )

    # Serve knowledge graph HTML with no-cache headers so every refresh
    # reflects the latest file written by visualize_graph.
    import os as _os
    _graph_file = _os.getenv("GRAPH_OUTPUT_PATH", "/graph/cognee_graph.html")

    @app.get("/graph/cognee_graph.html", response_class=FileResponse)
    async def serve_graph():
        if not _os.path.isfile(_graph_file):
            return HTMLResponse(
                "<h2>No graph yet — ask the agent to run visualize_graph first.</h2>",
                status_code=404,
            )
        return FileResponse(
            _graph_file,
            media_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    # Exception handler
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        return JSONResponse(
            status_code=422,
            content={
                "detail": exc.errors(),
                "body": exc.body,
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    return app


app = create_app()


# Startup DB tables
@app.on_event("startup")
async def on_startup():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database ready.")
