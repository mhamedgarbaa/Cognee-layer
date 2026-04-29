import os
import time

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

_NEO4J_URI       = os.getenv("GRAPH_DATABASE_URL",      "bolt://neo4j:7687")
_NEO4J_USER      = os.getenv("GRAPH_DATABASE_USERNAME",  "neo4j")
_NEO4J_PASS      = os.getenv("GRAPH_DATABASE_PASSWORD",  "neo4j_pass")
_MCP_WRAPPER_URL = os.getenv("MCP_WRAPPER_URL",         "http://mcp-wrapper:8002")
_GRAPH_FILE      = os.getenv("GRAPH_OUTPUT_PATH",        "/graph/cognee_graph.html")


async def _neo4j_stats() -> dict:
    """Return entity / relation / memified-node counts from Neo4j."""
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASS))
        async with driver.session() as s:
            e  = await (await s.run("MATCH (n) RETURN count(n) AS c")).single()
            r  = await (await s.run("MATCH ()-[r]->() RETURN count(r) AS c")).single()
            m  = await (await s.run(
                "MATCH (n) WHERE 'Event' IN labels(n) OR n.created_at IS NOT NULL "
                "RETURN count(n) AS c"
            )).single()
        await driver.close()
        return {
            "entities":      e["c"] if e else 0,
            "relations":     r["c"] if r else 0,
            "memified_nodes": m["c"] if m else 0,
        }
    except Exception as exc:
        logger.warning("Neo4j stats query failed: %s", exc)
        return {"entities": 0, "relations": 0, "memified_nodes": 0}


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.TITLE,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        docs_url=settings.DOCS_URL,
        redoc_url=settings.REDOC_URL,
        openapi_url=settings.OPENAPI_URL,
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/health")
    async def health_check():
        t0 = time.monotonic()
        stats = await _neo4j_stats()
        ping_ms = round((time.monotonic() - t0) * 1000)
        return {
            "status":  "connected",
            "pingMs":  ping_ms,
            "details": stats,          # {entities, relations, memified_nodes}
        }

    # ── Graph stats ───────────────────────────────────────────────────────────
    @app.get("/graph/stats")
    async def graph_stats():
        s = await _neo4j_stats()
        return {
            "entities":     s["entities"],
            "relations":    s["relations"],
            "memifiedNodes": s["memified_nodes"],
        }

    # ── Serve knowledge-graph HTML ────────────────────────────────────────────
    @app.get("/graph/cognee_graph.html", response_class=FileResponse)
    async def serve_graph():
        if not os.path.isfile(_GRAPH_FILE):
            return HTMLResponse(
                "<h2>No graph yet — run visualize_graph first.</h2>",
                status_code=404,
            )
        return FileResponse(
            _GRAPH_FILE,
            media_type="text/html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # ── Ontology info ─────────────────────────────────────────────────────────
    @app.get("/ontology/info")
    async def ontology_info():
        import pathlib
        # The file is written here by /ontology/upload (web container path)
        local_path = pathlib.Path("/app/ontologies/ontology.ttl")
        exists = local_path.is_file()
        return {
            "path":   "/ontologies/ontology.ttl",  # path as seen by cognee-mcp
            "format": "text/turtle",
            "exists": exists,
            "size":   local_path.stat().st_size if exists else 0,
        }

    # ── Ontology upload (TTL / OWL only) ─────────────────────────────────────
    from fastapi import UploadFile, File as FastAPIFile
    ONTOLOGY_DIR = "/app/ontologies"
    ALLOWED_ONTOLOGY_EXTS = {".ttl", ".owl"}

    @app.post("/ontology/upload")
    async def upload_ontology(file: UploadFile = FastAPIFile(...)):
        import pathlib
        ext = pathlib.Path(file.filename).suffix.lower()
        if ext not in ALLOWED_ONTOLOGY_EXTS:
            return JSONResponse(
                {"error": f"Unsupported format '{ext}'. Use .ttl or .owl"},
                status_code=400,
            )
        raw = await file.read()
        dest = pathlib.Path(ONTOLOGY_DIR) / "ontology.ttl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return {
            "path":     f"/ontologies/{dest.name}",
            "filename": file.filename,
            "size":     len(raw),
        }

    # ── Ontology commit (generated or edited TTL) → shared volume ────────────
    @app.post("/ontology/commit")
    async def commit_ontology(request: Request):
        import pathlib
        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return JSONResponse({"error": "No content provided"}, status_code=400)
        dest = pathlib.Path(ONTOLOGY_DIR) / "ontology.ttl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return {
            "path": "/ontologies/ontology.ttl",
            "size": len(content.encode("utf-8")),
            "injected": True,
        }

    # ── Text extraction (PDF / any text file) for cognify ────────────────────
    @app.post("/cognify/extract")
    async def extract_text_for_cognify(file: UploadFile = FastAPIFile(...)):
        import pathlib
        raw = await file.read()
        ext = pathlib.Path(file.filename).suffix.lower()
        text = ""
        if ext == ".pdf":
            try:
                import pypdf, io
                reader = pypdf.PdfReader(io.BytesIO(raw))
                text = "\n\n".join(
                    page.extract_text() or "" for page in reader.pages
                ).strip()
            except Exception as exc:
                return JSONResponse({"error": f"PDF extraction failed: {exc}"}, status_code=422)
        else:
            text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            return JSONResponse({"error": "No text content extracted."}, status_code=422)
        return {"text": text, "filename": file.filename, "chars": len(text)}

    # ── Cognify proxy (for UI /add) — calls mcp-wrapper via JSON-RPC ─────────
    @app.post("/add")
    async def add_data(request: Request):
        import httpx, json as _json
        body = await request.json()
        data = body.get("data") or _json.dumps(body)
        try:
            async with httpx.AsyncClient(base_url=_MCP_WRAPPER_URL, timeout=300) as c:
                resp = await c.post("/mcp", json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "tools/call",
                    "params": {"name": "cognify", "arguments": {"data": data}},
                })
            # MCP wrapper returns SSE: "data: {...}\n\n"
            result_text = ""
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = _json.loads(line[5:].strip())
                    if "error" in payload:
                        return JSONResponse(
                            {"status": "error", "error": payload["error"].get("message", "MCP error")},
                            status_code=502,
                        )
                    content = payload.get("result", {}).get("content", [])
                    result_text = " ".join(
                        item.get("text", "") for item in content if item.get("type") == "text"
                    )
            return {"status": "success", "response": result_text[:500]}
        except Exception as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=502)

    # ── Search proxy (for UI /search) — calls mcp-wrapper via JSON-RPC ───────
    @app.post("/search")
    async def search_data(request: Request):
        import httpx, json as _json
        body = await request.json()
        query = body.get("query", "")
        try:
            async with httpx.AsyncClient(base_url=_MCP_WRAPPER_URL, timeout=120) as c:
                resp = await c.post("/mcp", json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {
                        "search_query": query,
                        "search_type":  body.get("search_type", "GRAPH_COMPLETION"),
                    }},
                })
            result_text = ""
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    payload = _json.loads(line[5:].strip())
                    content = payload.get("result", {}).get("content", [])
                    result_text = " ".join(
                        item.get("text", "") for item in content if item.get("type") == "text"
                    )
            return {"status": "success", "results": result_text[:2000]}
        except Exception as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=502)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(workspaces_v1_router, prefix=f"{settings.API_PREFIX}/v1")
    app.include_router(memory_router,        prefix=f"{settings.API_PREFIX}/v1")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail":     exc.errors(),
                "body":       exc.body,
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    return app


app = create_app()


@app.on_event("startup")
async def on_startup():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database ready.")
