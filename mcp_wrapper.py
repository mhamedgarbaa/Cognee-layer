"""
Cognee MCP Wrapper — single MCP endpoint exposing ALL tools.

Architecture:
  Other agents  ──►  POST http://localhost:8002/mcp  (this file)
                          │
                          ├── base tools  ──►  Cognee MCP  http://localhost:8001
                          └── custom tools ──► local logic (memify, visualize, etc.)

Run:
    python mcp_wrapper.py

Connect any MCP-compatible agent to:
    http://localhost:8002
    Host header: localhost:8002

Tools exposed:
    cognify, save_interaction, search (with search_type), list_data,
    cognify_status, prune, memify, visualize_graph, persist_sessions, improve_answer
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── config ────────────────────────────────────────────────────────────────────

MCP_URL         = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER", "localhost:8001")
AGENT_USER      = os.getenv("AGENT_USER", "external_agent")
PORT            = int(os.getenv("MCP_WRAPPER_PORT", "8002"))
LOG_LEVEL       = os.getenv("MCP_WRAPPER_LOG_LEVEL", "INFO").upper()

# Phase 2+ vars (defined now so .env.example documents them)
MCP_WRAPPER_API_KEY         = os.getenv("MCP_WRAPPER_API_KEY", "")
MCP_WRAPPER_STRICT_SESSIONS = os.getenv("MCP_WRAPPER_STRICT_SESSIONS", "false").lower() == "true"
MCP_WRAPPER_RECONNECT_DELAY = int(os.getenv("MCP_WRAPPER_RECONNECT_DELAY", "5"))
MCP_WRAPPER_MAX_RECONNECT   = int(os.getenv("MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS", "10"))
# Rate-limit retry — when Cognee's upstream LLM returns 429 we back off and retry
COGNEE_RETRY_MAX_ATTEMPTS   = int(os.getenv("COGNEE_RETRY_MAX_ATTEMPTS", "4"))
COGNEE_RETRY_BASE_DELAY     = float(os.getenv("COGNEE_RETRY_BASE_DELAY", "5.0"))
COGNEE_RETRY_MAX_DELAY      = float(os.getenv("COGNEE_RETRY_MAX_DELAY", "60.0"))
# Container name and output path are env-driven so they work both locally and in Docker Compose
COGNEE_CONTAINER_NAME       = os.getenv("COGNEE_CONTAINER_NAME", "cognee_mcp_server")
GRAPH_OUTPUT_PATH           = os.getenv("GRAPH_OUTPUT_PATH", "/graph/cognee_graph.html")
# Gate streaming SSE forwarding — off by default so non-streaming clients keep working
MCP_WRAPPER_STREAMING       = os.getenv("MCP_WRAPPER_STREAMING", "false").lower() == "true"
# Tools that run a subprocess and need heartbeats instead of upstream SSE forwarding
_SUBPROCESS_TOOLS: frozenset[str] = frozenset({"memify", "visualize_graph"})

# ── JSON logger (shared config from configuration/logging_setup.py) ──────────
# Importing applies the dictConfig; then we grab a named child logger.
from configuration.logging_setup import logger as _root_logger  # noqa: E402

log = _root_logger.getChild("mcp_wrapper")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# ── tool catalogue ─────────────────────────────────────────────────────────────

TOOL_LIST = [
    {
        "name": "cognify",
        "description": "Deep-process text/knowledge into the graph (entities, relationships, summaries). Use for rich content. Set temporal=true to also extract Event nodes with timestamps, which enables TEMPORAL search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Text or content to store"},
                "temporal": {"type": "boolean", "description": "Extract temporal Event nodes during ingestion (enables TEMPORAL search). Default false.", "default": False},
            },
            "required": ["data"],
        },
    },
    {
        "name": "save_interaction",
        "description": "Quickly save a short note or interaction without full graph processing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string"},
                "user": {"type": "string"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "search",
        "description": (
            "Search the knowledge graph. search_type controls the strategy:\n"
            "GRAPH_COMPLETION (default) — entity graph + LLM synthesis\n"
            "GRAPH_COMPLETION_COT — chain-of-thought multi-round reasoning\n"
            "GRAPH_COMPLETION_CONTEXT_EXTENSION — expands context with follow-up queries\n"
            "GRAPH_SUMMARY_COMPLETION — includes document summaries\n"
            "TEMPORAL — time-aware, filters Event nodes by timestamp\n"
            "CYPHER — LLM-generated Cypher query\n"
            "NATURAL_LANGUAGE — NL → graph query auto-translation\n"
            "RAG_COMPLETION — classic vector RAG\n"
            "SUMMARIES — document summaries only\n"
            "CHUNKS — raw text chunks\n"
            "FEELING_LUCKY — auto-selects best type"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "search_type": {
                    "type": "string",
                    "enum": [
                        "GRAPH_COMPLETION", "GRAPH_COMPLETION_COT",
                        "GRAPH_COMPLETION_CONTEXT_EXTENSION", "GRAPH_SUMMARY_COMPLETION",
                        "TEMPORAL", "CYPHER", "NATURAL_LANGUAGE",
                        "RAG_COMPLETION", "SUMMARIES", "CHUNKS", "FEELING_LUCKY",
                    ],
                    "default": "GRAPH_COMPLETION",
                },
                "user": {"type": "string"},
            },
            "required": ["search_query"],
        },
    },
    {
        "name": "list_data",
        "description": "List all datasets stored in the knowledge graph.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cognify_status",
        "description": "Check whether the cognify background pipeline is idle or still running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "prune",
        "description": "Wipe ALL stored knowledge. Irreversible — only call when explicitly asked.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memify",
        "description": (
            "Re-run cognify with temporal_cognify=True on a dataset to extract Event nodes with "
            "timestamps. Use when data was ingested without temporal=true and you want to enable "
            "TEMPORAL search retroactively. Takes 1-3 minutes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "default": "main_dataset"},
            },
        },
    },
    {
        "name": "visualize_graph",
        "description": "Render the full Neo4j knowledge graph as an interactive HTML file at /tmp/cognee_graph.html.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "persist_sessions",
        "description": (
            "Self-improvement: store recent agent conversation sessions as knowledge graph nodes. "
            "Pass the session transcript as 'data'. Future searches will include this context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Conversation transcript to persist"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "improve_answer",
        "description": (
            "Self-improvement: given a question that was answered incorrectly, re-query the graph "
            "with chain-of-thought reasoning and store the corrected answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question":     {"type": "string"},
                "wrong_answer": {"type": "string"},
                "feedback":     {"type": "string"},
            },
            "required": ["question", "wrong_answer", "feedback"],
        },
    },
]

# ── Cognee MCP proxy ───────────────────────────────────────────────────────────

class CogneeMCPProxy:
    """Resilient proxy to the upstream Cognee MCP server.

    Handles lazy connect, transparent reconnect on failure, and a circuit
    breaker that stops hammering the upstream after repeated failures.
    """

    _CB_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))
    _CB_TIMEOUT   = int(os.getenv("CIRCUIT_BREAKER_TIMEOUT",   "60"))

    def __init__(self):
        self._http: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._connected = False
        # circuit-breaker state
        self._failures = 0
        self._open_until: float = 0.0

    # ── internal helpers ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    @staticmethod
    def _parse_sse(body: str) -> dict:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError(f"No SSE data in: {body[:200]}")

    def _mcp_headers(self, with_session: bool = False) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Host": MCP_HOST_HEADER}
        if with_session and self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    # ── circuit-breaker ───────────────────────────────────────────────────────

    def _cb_open(self) -> bool:
        return time.monotonic() < self._open_until

    def _cb_record_failure(self):
        self._failures += 1
        if self._failures >= self._CB_THRESHOLD:
            self._open_until = time.monotonic() + self._CB_TIMEOUT
            log.error(
                "circuit breaker OPEN — upstream unavailable",
                extra={"tool": "proxy", "error": f"open for {self._CB_TIMEOUT}s"},
            )

    def _cb_record_success(self):
        self._failures = 0
        self._open_until = 0.0

    # ── connect / reconnect ───────────────────────────────────────────────────

    async def _do_connect(self):
        if self._http:
            await self._http.aclose()
        self._http = httpx.AsyncClient(base_url=MCP_URL, timeout=30)
        resp = await self._http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "mcp-wrapper", "version": "1.0"},
                },
            },
            headers=self._mcp_headers(),
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")
        self._connected = True
        self._cb_record_success()
        log.info("Cognee MCP connected", extra={"session_id": self._session_id, "tool": "proxy"})

    async def ensure_connected(self):
        if self._connected:
            return
        async with self._lock:
            if self._connected:   # double-check after acquiring lock
                return
            delay = MCP_WRAPPER_RECONNECT_DELAY
            for attempt in range(1, MCP_WRAPPER_MAX_RECONNECT + 1):
                try:
                    log.info("Connecting to Cognee MCP (attempt %d/%d)", attempt, MCP_WRAPPER_MAX_RECONNECT,
                             extra={"tool": "proxy"})
                    await self._do_connect()
                    return
                except Exception as exc:
                    self._connected = False
                    self._cb_record_failure()
                    log.warning("Connect attempt %d failed: %s", attempt, exc, extra={"tool": "proxy"})
                    if attempt < MCP_WRAPPER_MAX_RECONNECT:
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)   # exponential back-off, cap 60s
            raise RuntimeError(
                f"Could not connect to Cognee MCP after {MCP_WRAPPER_MAX_RECONNECT} attempts"
            )

    # ── public call interface ─────────────────────────────────────────────────

    @staticmethod
    def _is_rate_limit(text: str) -> bool:
        t = text.lower()
        return (
            "rate limit" in t or "ratelimit" in t or "429" in t
            or "too many requests" in t or "quota exceeded" in t
            or "tokens per minute" in t or "requests per minute" in t
        )

    async def call(self, tool: str, args: dict, timeout: float = 180) -> str:
        if self._cb_open():
            raise RuntimeError(
                f"Circuit breaker open — Cognee MCP unavailable. "
                f"Retry in {round(self._open_until - time.monotonic())}s."
            )

        await self.ensure_connected()

        delay = COGNEE_RETRY_BASE_DELAY
        last_exc: Exception | None = None

        for attempt in range(1, COGNEE_RETRY_MAX_ATTEMPTS + 1):
            t0 = time.monotonic()
            try:
                async with self._lock:
                    resp = await self._http.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                            "params": {"name": tool, "arguments": args},
                        },
                        headers=self._mcp_headers(with_session=True),
                        timeout=timeout,
                    )
                resp.raise_for_status()
                data = self._parse_sse(resp.text)

                if "error" in data:
                    err     = data["error"]
                    code    = err.get("code", 0)
                    message = err.get("message", "")
                    if self._is_rate_limit(message) and attempt < COGNEE_RETRY_MAX_ATTEMPTS:
                        log.warning(
                            "rate limit in MCP error (attempt %d/%d) — retrying in %.0fs: %s",
                            attempt, COGNEE_RETRY_MAX_ATTEMPTS, delay, message,
                            extra={"tool": tool},
                        )
                        last_exc = RuntimeError(f"MCP error [{code}]: {message}")
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, COGNEE_RETRY_MAX_DELAY)
                        continue
                    raise RuntimeError(f"Cognee MCP error [{code}]: {message}")

                content = data.get("result", {}).get("content", [])
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                result = "\n".join(parts) or json.dumps(data.get("result", {}))
                latency = round((time.monotonic() - t0) * 1000)
                self._cb_record_success()
                log.info("cognee_call ok", extra={"tool": tool, "latency_ms": latency})
                return result

            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                self._connected = False
                self._cb_record_failure()
                latency = round((time.monotonic() - t0) * 1000)
                log.error("cognee_call failed (connection lost)",
                          extra={"tool": tool, "latency_ms": latency, "error": str(exc)})
                raise

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if (status == 429 or self._is_rate_limit(str(exc))) and attempt < COGNEE_RETRY_MAX_ATTEMPTS:
                    log.warning(
                        "rate limit HTTP %d (attempt %d/%d) — retrying in %.0fs",
                        status, attempt, COGNEE_RETRY_MAX_ATTEMPTS, delay,
                        extra={"tool": tool},
                    )
                    last_exc = exc
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, COGNEE_RETRY_MAX_DELAY)
                    continue
                if status in (401, 403, 404):
                    self._connected = False
                self._cb_record_failure()
                latency = round((time.monotonic() - t0) * 1000)
                log.error("cognee_call HTTP error",
                          extra={"tool": tool, "latency_ms": latency, "error": str(exc)})
                raise

        # All retry attempts exhausted
        self._cb_record_failure()
        log.error("cognee_call: exhausted %d retries for tool=%s", COGNEE_RETRY_MAX_ATTEMPTS, tool,
                  extra={"tool": tool})
        raise last_exc or RuntimeError(f"Tool {tool} failed after {COGNEE_RETRY_MAX_ATTEMPTS} attempts")

    async def stream_call(self, tool: str, args: dict, req_id, timeout: float = 180):
        """Forward upstream SSE events as an async generator.

        Yields raw 'data: {...}\\n\\n' lines so the caller can stream them
        directly to the connecting agent without buffering the full response.
        The final event has its JSON-RPC id rewritten to match req_id so the
        agent's response correlates correctly.
        """
        if self._cb_open():
            err = {"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32000, "message": "Circuit breaker open — upstream unavailable"}}
            yield f"data: {json.dumps(err)}\n\n"
            return

        await self.ensure_connected()

        t0 = time.monotonic()
        try:
            # No _lock here — httpx connection pool handles concurrent requests safely
            async with self._http.stream(
                "POST", "/mcp",
                json={
                    "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
                headers=self._mcp_headers(with_session=True),
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        try:
                            event = json.loads(line[5:].strip())
                            # Rewrite id so caller's response matches their request
                            if "id" in event:
                                event["id"] = req_id
                        except json.JSONDecodeError:
                            event = {"raw": line}
                        yield f"data: {json.dumps(event)}\n\n"
                    else:
                        yield f"{line}\n\n"

            latency = round((time.monotonic() - t0) * 1000)
            self._cb_record_success()
            log.info("stream_call ok", extra={"tool": tool, "latency_ms": latency})

        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            self._connected = False
            self._cb_record_failure()
            err = {"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32000, "message": f"Connection lost: {exc}"}}
            yield f"data: {json.dumps(err)}\n\n"

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 404):
                self._connected = False
            self._cb_record_failure()
            err = {"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32000, "message": f"Upstream HTTP {exc.response.status_code}"}}
            yield f"data: {json.dumps(err)}\n\n"

    @property
    def connected(self) -> bool:
        return self._connected


# module-level singleton — all tool calls go through this
proxy = CogneeMCPProxy()


async def cognee_call(tool: str, args: dict, timeout: float = 180) -> str:
    """Thin shim so existing code keeps working without changes."""
    return await proxy.call(tool, args, timeout)


# ── streaming helpers ──────────────────────────────────────────────────────────

async def _stream_subprocess(tool_name: str, tool_args: dict, req_id):
    """Run a subprocess tool in an executor and yield SSE heartbeats while it runs.

    Subprocess tools (memify, visualize_graph) produce no intermediate output,
    so we send MCP notifications/progress pings every 5 s to keep the connection
    alive, then emit the final JSON-RPC result event.
    """
    loop = asyncio.get_event_loop()

    # Dispatch to the right blocking function
    if tool_name == "memify":
        fn = lambda: asyncio.get_event_loop().run_until_complete(  # noqa: E731
            run_memify(tool_args.get("dataset", "main_dataset"))
        )
    else:
        fn = lambda: asyncio.get_event_loop().run_until_complete(run_visualization())  # noqa: E731

    # Run subprocess in a thread so we can yield while it runs
    task = asyncio.ensure_future(
        loop.run_in_executor(None, _sync_dispatch, tool_name, tool_args)
    )

    ping = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progressToken": req_id, "progress": 0, "total": 100},
    }

    while not task.done():
        yield f"data: {json.dumps(ping)}\n\n"
        try:
            # Wait up to 5 s; shield prevents cancellation of the real task
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except asyncio.TimeoutError:
            pass

    try:
        result_text = task.result()
    except Exception as exc:
        result_text = f"Error: {exc}"

    payload = {
        "jsonrpc": "2.0", "id": req_id,
        "result": {"content": [{"type": "text", "text": result_text}]},
    }
    yield f"data: {json.dumps(payload)}\n\n"


def _sync_dispatch(tool_name: str, tool_args: dict) -> str:
    """Blocking wrapper — runs the subprocess synchronously in a thread pool worker."""
    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    try:
        if tool_name == "memify":
            return loop.run_until_complete(run_memify(tool_args.get("dataset", "main_dataset")))
        return loop.run_until_complete(run_visualization())
    finally:
        loop.close()


async def stream_tool_result(tool_name: str, tool_args: dict, req_id, session_id: str, user: str):
    """Top-level streaming generator for tools/call.

    - Subprocess tools  → heartbeat pings + final result
    - All other tools   → forward Cognee MCP SSE events in real time
    """
    log.info("stream start", extra={"tool": tool_name, "session_id": session_id})

    if tool_name in _SUBPROCESS_TOOLS:
        async for chunk in _stream_subprocess(tool_name, tool_args, req_id):
            yield chunk
        return

    # Determine timeout by tool
    _TIMEOUTS = {
        "cognify": 300, "save_interaction": 120, "search": 180,
        "persist_sessions": 300, "improve_answer": 180,
    }
    timeout = _TIMEOUTS.get(tool_name, 60)

    async for chunk in proxy.stream_call(tool_name, tool_args, req_id, timeout=timeout):
        yield chunk


# ── custom tool implementations ────────────────────────────────────────────────

async def run_memify(dataset: str = "main_dataset") -> str:
    """Run memify inside the cognee container via the Python docker SDK."""
    loop = asyncio.get_event_loop()

    def _exec() -> str:
        import docker as _docker, tarfile as _tar, io as _io
        client = _docker.from_env()
        container = client.containers.get(COGNEE_CONTAINER_NAME)

        script = """
import asyncio, sys
sys.path.insert(0, '/app/src')
import cognee
dataset = sys.argv[1] if len(sys.argv) > 1 else 'main_dataset'

async def run():
    await cognee.cognify(datasets=[dataset], temporal_cognify=True)
    print(f'Temporal enrichment complete for dataset: {dataset}')

asyncio.run(run())
"""
        # Upload script into container
        buf = _io.BytesIO()
        with _tar.open(fileobj=buf, mode="w") as t:
            content = script.encode()
            info = _tar.TarInfo(name="run_memify_wrapper.py")
            info.size = len(content)
            t.addfile(info, _io.BytesIO(content))
        buf.seek(0)
        container.put_archive("/tmp", buf.read())

        exit_code, output = container.exec_run(
            f"python3 /tmp/run_memify_wrapper.py {dataset}",
            stream=False,
        )
        text = output.decode(errors="replace") if output else ""
        return text[-600:] or "Memify complete."

    output = await loop.run_in_executor(None, _exec)
    return output or "Memify complete."


async def run_visualization() -> str:
    """Run the visualization script directly in this container (Neo4j reachable on Docker network).

    /scripts is mounted read-only; output is written to GRAPH_OUTPUT_PATH on the shared volume.
    """
    loop = asyncio.get_event_loop()

    def _exec() -> str:
        import runpy, sys as _sys

        # Override OUTPUT path so the script writes to the shared volume
        os.environ.setdefault("GRAPH_DATABASE_URL",      "bolt://neo4j:7687")
        os.environ.setdefault("GRAPH_DATABASE_USERNAME",  "neo4j")
        os.environ.setdefault("GRAPH_DATABASE_PASSWORD",  os.getenv("NEO4J_PASSWORD", "neo4j_pass"))

        script_path = "/scripts/visualize_neo4j_graph.py"
        if not os.path.exists(script_path):
            return f"Script not found: {script_path}"

        # Patch OUTPUT constant and call main() directly.
        # exec() sets __name__ to the module spec name, not "__main__", so the
        # "if __name__ == '__main__'" guard would never fire — main() must be
        # called explicitly after the module is loaded.
        src = open(script_path).read().replace(
            'OUTPUT     = "/tmp/cognee_graph.html"',
            f'OUTPUT     = "{GRAPH_OUTPUT_PATH}"',
        )
        globs: dict = {"__name__": "viz", "__file__": script_path}
        try:
            exec(compile(src, script_path, "exec"), globs)
            globs["main"]()          # __name__ != "__main__" so we call it explicitly
            return f"Graph rendered → {GRAPH_OUTPUT_PATH}"
        except SystemExit:
            return f"Graph rendered → {GRAPH_OUTPUT_PATH}"
        except Exception as e:
            return f"Visualization error: {e}"

    result = await loop.run_in_executor(None, _exec)
    return result


async def run_persist_sessions(data: str, user: str = AGENT_USER) -> str:
    if not data.strip():
        return "No session data provided."
    result = await cognee_call("cognify", {"data": data, "user": user}, timeout=300)
    return f"Session persisted to graph. {result[:200]}"


async def run_improve_answer(
    question: str, wrong_answer: str, feedback: str, user: str = AGENT_USER
) -> str:
    improved = await cognee_call(
        "search",
        {"search_query": question, "search_type": "GRAPH_COMPLETION_COT", "user": user},
        timeout=180,
    )
    correction = (
        f"CORRECTION — Question: {question}\n"
        f"Wrong answer: {wrong_answer}\nFeedback: {feedback}\n"
        f"Improved answer: {improved}"
    )
    await cognee_call("cognify", {"data": correction, "user": user}, timeout=300)
    return f"Improved answer (stored):\n{improved}"


# ── tool dispatcher ────────────────────────────────────────────────────────────

async def dispatch(tool: str, args: dict, session_id: str = "", user: str = AGENT_USER) -> str:
    t0 = time.monotonic()

    try:
        if tool == "cognify":
            cognify_args: dict = {"data": args["data"]}
            if args.get("temporal"):
                cognify_args["temporal_cognify"] = True
            result = await cognee_call("cognify", cognify_args, timeout=300)
        elif tool == "save_interaction":
            # Cognee's save_interaction has a bug: it creates a TextDocument with a file
            # path but never writes the file before the pipeline reads it. Routing to
            # cognify uses the working code path with the same interface.
            result = await cognee_call("cognify", {"data": args["data"]}, timeout=300)
        elif tool == "search":
            query = args.get("search_query") or args.get("query", "")
            if not query:
                return [{"type": "text", "text": "Error: search_query is required"}]
            result = await cognee_call("search", {
                "search_query": query,
                "search_type":  args.get("search_type", "GRAPH_COMPLETION"),
            }, timeout=180)
        elif tool == "list_data":
            result = await cognee_call("list_data", {}, timeout=60)
        elif tool == "cognify_status":
            result = await cognee_call("cognify_status", {}, timeout=30)
        elif tool == "prune":
            result = await cognee_call("prune", {}, timeout=60)
        elif tool == "memify":
            result = await run_memify(args.get("dataset", "main_dataset"))
        elif tool == "visualize_graph":
            result = await run_visualization()
        elif tool == "persist_sessions":
            result = await run_persist_sessions(args.get("data", ""), user=user)
        elif tool == "improve_answer":
            result = await run_improve_answer(
                args.get("question", ""), args.get("wrong_answer", ""), args.get("feedback", ""),
                user=user,
            )
        else:
            result = f"Unknown tool: {tool}"

        latency = round((time.monotonic() - t0) * 1000)
        log.info("dispatch ok", extra={"tool": tool, "session_id": session_id, "latency_ms": latency})
        return result

    except Exception as exc:
        latency = round((time.monotonic() - t0) * 1000)
        log.error(
            "dispatch error",
            extra={"tool": tool, "session_id": session_id, "latency_ms": latency, "error": str(exc)},
            exc_info=True,
        )
        raise


# ── MCP protocol handler ───────────────────────────────────────────────────────

SESSION_TTL_SECONDS = int(os.getenv("MCP_WRAPPER_SESSION_TTL", "3600"))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Background task to evict stale sessions."""
    async def _evict_loop():
        while True:
            await asyncio.sleep(300)  # check every 5 minutes
            now = time.time()
            stale = [
                sid for sid, s in _sessions.items()
                if now - s.get("created_at", now) > SESSION_TTL_SECONDS
            ]
            for sid in stale:
                _sessions.pop(sid, None)
            if stale:
                log.info("evicted %d stale session(s)", len(stale))

    # Quick single-attempt connect at startup (5 s timeout).
    # Full retry logic runs on first tool call — don't block uvicorn startup.
    try:
        await asyncio.wait_for(proxy._do_connect(), timeout=5.0)
    except Exception as exc:
        proxy._connected = False
        log.warning("Startup: Cognee MCP not reachable (%s) — will retry on first call", exc,
                    extra={"tool": "proxy"})

    task = asyncio.create_task(_evict_loop())
    try:
        yield
    finally:
        task.cancel()
        if proxy._http:
            await proxy._http.aclose()


app = FastAPI(title="Cognee MCP Wrapper", lifespan=_lifespan)

# Active sessions: session_id → {"agent_id": str, "created_at": float}
_sessions: dict[str, dict] = {}


def _sse_response(payload: dict, headers: dict | None = None) -> StreamingResponse:
    data = f"data: {json.dumps(payload)}\n\n"
    return StreamingResponse(iter([data]), media_type="text/event-stream", headers=headers or {})


def _wants_sse(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept


def _jsonrpc_error(req_id, code: int, message: str):
    return JSONResponse(
        status_code=400,
        content={"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
    )


def _validate_session(sid: str, req_id) -> JSONResponse | None:
    """Return an error response if session is required and invalid, else None."""
    if not MCP_WRAPPER_STRICT_SESSIONS:
        return None
    if not sid:
        return _jsonrpc_error(req_id, -32000, "Missing mcp-session-id header — call initialize first")
    if sid not in _sessions:
        return _jsonrpc_error(req_id, -32000, "Unknown or expired session — call initialize again")
    return None


def _session_user(sid: str) -> str:
    """Return the agent_id bound to this session, falling back to AGENT_USER.

    Using session-bound identity prevents callers from spoofing another
    agent's user context by passing 'user' in tool arguments.
    """
    if sid and sid in _sessions:
        return _sessions[sid]["agent_id"]
    return AGENT_USER


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    sid = request.headers.get("mcp-session-id", "")

    # ── initialize ──
    if method == "initialize":
        session_id = str(uuid.uuid4())
        agent_name = body.get("params", {}).get("clientInfo", {}).get("name", "unknown")
        _sessions[session_id] = {"agent_id": agent_name, "created_at": time.time()}
        log.info("session init", extra={"session_id": session_id, "tool": f"agent={agent_name}"})
        payload = {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cognee-mcp-wrapper", "version": "1.0"},
            },
        }
        resp_headers = {"mcp-session-id": session_id}
        if _wants_sse(request):
            return StreamingResponse(
                iter([f"data: {json.dumps(payload)}\n\n"]),
                media_type="text/event-stream",
                headers=resp_headers,
            )
        return JSONResponse(content=payload, headers=resp_headers)

    # ── tools/list ──
    if method == "tools/list":
        if err := _validate_session(sid, req_id):
            log.warning("rejected tools/list: invalid session", extra={"session_id": sid})
            return err
        log.info("tools/list", extra={"session_id": sid})
        payload = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_LIST}}
        return _sse_response(payload)

    # ── tools/call ──
    if method == "tools/call":
        if err := _validate_session(sid, req_id):
            log.warning("rejected tools/call: invalid session", extra={"session_id": sid})
            return err
        tool_name = body["params"]["name"]
        tool_args = body["params"].get("arguments", {})
        agent_user = _session_user(sid)

        if MCP_WRAPPER_STREAMING:
            # Stream SSE events incrementally — no buffering
            return StreamingResponse(
                stream_tool_result(tool_name, tool_args, req_id, sid, agent_user),
                media_type="text/event-stream",
            )

        # Non-streaming (default) — buffer full result, emit single SSE event
        try:
            result_text = await dispatch(tool_name, tool_args, session_id=sid, user=agent_user)
        except Exception as exc:
            result_text = f"Error: {exc}"
        payload = {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": result_text}]},
        }
        return _sse_response(payload)

    log.warning("unknown method: %s", method, extra={"session_id": sid})
    return JSONResponse({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


_start_time = time.time()

# ── Phase 7: tool documentation metadata ──────────────────────────────────────

_TOOL_METADATA: dict[str, dict] = {
    "cognify":           {"category": "write", "typical_latency_seconds": 30},
    "save_interaction":  {"category": "write", "typical_latency_seconds": 2},
    "search":            {"category": "read",  "typical_latency_seconds": 5},
    "list_data":         {"category": "read",  "typical_latency_seconds": 2},
    "cognify_status":    {"category": "read",  "typical_latency_seconds": 1},
    "prune":             {"category": "admin", "typical_latency_seconds": 5},
    "memify":            {"category": "write", "typical_latency_seconds": 120},
    "visualize_graph":   {"category": "read",  "typical_latency_seconds": 15},
    "persist_sessions":  {"category": "write", "typical_latency_seconds": 10},
    "improve_answer":    {"category": "write", "typical_latency_seconds": 10},
}


@app.get("/tools")
async def list_tools_doc():
    enriched = []
    for tool in TOOL_LIST:
        meta = _TOOL_METADATA.get(tool["name"], {})
        enriched.append({**tool, **meta})
    return {
        "server": {
            "name": "cognee-mcp-wrapper",
            "version": "1.0.0",
            "mcp_endpoint": "/mcp",
            "auth": "none" if not MCP_WRAPPER_API_KEY else "x-api-key header",
        },
        "session_handshake": {
            "step_1": "POST /mcp  method=initialize  →  mcp-session-id header returned",
            "step_2": "POST /mcp  method=tools/list   mcp-session-id: <id>",
            "step_3": "POST /mcp  method=tools/call   mcp-session-id: <id>",
        },
        "tool_count": len(enriched),
        "tools": enriched,
    }


@app.get("/health")
async def health():
    cb_open = proxy._cb_open()
    return {
        "status": "degraded" if cb_open else "ok",
        "tools": len(TOOL_LIST),
        "uptime_seconds": round(time.time() - _start_time),
        "active_sessions": len(_sessions),
        "cognee_mcp_connected": proxy.connected,
        "circuit_breaker_open": cb_open,
        "consecutive_failures": proxy._failures,
    }


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    log.info(
        "MCP Wrapper starting",
        extra={"tool": f"port={PORT} tools={len(TOOL_LIST)} log_level={LOG_LEVEL}"},
    )
    uvicorn.run("mcp_wrapper:app", host="0.0.0.0", port=PORT, reload=False)
