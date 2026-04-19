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
import os
import subprocess
import sys
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MCP_URL         = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER", "localhost:8001")
AGENT_USER      = os.getenv("AGENT_USER", "external_agent")
PORT            = int(os.getenv("MCP_WRAPPER_PORT", "8002"))

# ── tool catalogue ─────────────────────────────────────────────────────────────

TOOL_LIST = [
    {
        "name": "cognify",
        "description": "Deep-process text/knowledge into the graph (entities, relationships, summaries). Use for rich content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Text or content to store"},
                "user": {"type": "string", "description": "Tenant/user identifier (optional)"},
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
            "Temporal enrichment: reads DocumentChunk nodes, extracts Event nodes with timestamps "
            "via LLM, writes them to Neo4j. Enables TEMPORAL search. Takes 1-3 minutes."
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

_http: httpx.AsyncClient | None = None
_session_id: str | None = None
_id = 0
_lock = asyncio.Lock()


def _next_id() -> int:
    global _id
    _id += 1
    return _id


def _parse_sse(body: str) -> dict:
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError(f"No SSE data in: {body[:200]}")


async def cognee_call(tool: str, args: dict, timeout: float = 180) -> str:
    global _http, _session_id
    if _http is None:
        _http = httpx.AsyncClient(base_url=MCP_URL, timeout=30)
        resp = await _http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": _next_id(), "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "mcp-wrapper", "version": "1.0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream", "Host": MCP_HOST_HEADER},
        )
        resp.raise_for_status()
        _session_id = resp.headers.get("mcp-session-id")

    async with _lock:
        resp = await _http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": _next_id(), "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": MCP_HOST_HEADER,
                "mcp-session-id": _session_id,
            },
            timeout=timeout,
        )
    resp.raise_for_status()
    data = _parse_sse(resp.text)
    content = data.get("result", {}).get("content", [])
    parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    return "\n".join(parts) or json.dumps(data.get("result", {}))


# ── custom tool implementations ────────────────────────────────────────────────

async def run_memify(dataset: str = "main_dataset") -> str:
    loop = asyncio.get_event_loop()
    def _exec():
        r = subprocess.run(
            ["docker", "exec", "cognee_mcp_server", "bash", "-c",
             f"python3 /tmp/run_memify.py {dataset}"],
            capture_output=True, text=True, timeout=300,
        )
        return r.stdout + r.stderr
    output = await loop.run_in_executor(None, _exec)
    return "\n".join(output.strip().splitlines()[-6:]) or "Memify complete."


async def run_visualization() -> str:
    loop = asyncio.get_event_loop()
    def _exec():
        subprocess.run(
            ["docker", "exec", "cognee_mcp_server", "bash", "-c", "python3 /tmp/visualize.py"],
            capture_output=True, timeout=60,
        )
        return subprocess.run(
            ["docker", "cp", "cognee_mcp_server:/tmp/cognee_graph.html",
             "c:/Users/mhame/Cognee-layer/cognee_graph_neo4j.html"],
            capture_output=True, timeout=10,
        ).returncode
    rc = await loop.run_in_executor(None, _exec)
    return "Graph rendered at /tmp/cognee_graph.html" if rc == 0 else "Visualization failed."


async def run_persist_sessions(data: str) -> str:
    if not data.strip():
        return "No session data provided."
    result = await cognee_call("cognify", {"data": data, "user": AGENT_USER}, timeout=300)
    return f"Session persisted to graph. {result[:200]}"


async def run_improve_answer(question: str, wrong_answer: str, feedback: str) -> str:
    improved = await cognee_call(
        "search",
        {"search_query": question, "search_type": "GRAPH_COMPLETION_COT", "user": AGENT_USER},
        timeout=180,
    )
    correction = (
        f"CORRECTION — Question: {question}\n"
        f"Wrong answer: {wrong_answer}\nFeedback: {feedback}\n"
        f"Improved answer: {improved}"
    )
    await cognee_call("save_interaction", {"data": correction, "user": AGENT_USER}, timeout=120)
    return f"Improved answer (stored):\n{improved}"


# ── tool dispatcher ────────────────────────────────────────────────────────────

async def dispatch(tool: str, args: dict) -> str:
    user = args.get("user", AGENT_USER)

    if tool == "cognify":
        return await cognee_call("cognify", {"data": args["data"], "user": user}, timeout=300)
    if tool == "save_interaction":
        return await cognee_call("save_interaction", {"data": args["data"], "user": user}, timeout=120)
    if tool == "search":
        return await cognee_call("search", {
            "search_query": args.get("search_query", ""),
            "search_type":  args.get("search_type", "GRAPH_COMPLETION"),
            "user": user,
        }, timeout=180)
    if tool == "list_data":
        return await cognee_call("list_data", {}, timeout=60)
    if tool == "cognify_status":
        return await cognee_call("cognify_status", {}, timeout=30)
    if tool == "prune":
        return await cognee_call("prune", {}, timeout=60)
    if tool == "memify":
        return await run_memify(args.get("dataset", "main_dataset"))
    if tool == "visualize_graph":
        return await run_visualization()
    if tool == "persist_sessions":
        return await run_persist_sessions(args.get("data", ""))
    if tool == "improve_answer":
        return await run_improve_answer(
            args.get("question", ""), args.get("wrong_answer", ""), args.get("feedback", "")
        )
    return f"Unknown tool: {tool}"


# ── MCP protocol handler ───────────────────────────────────────────────────────

app = FastAPI(title="Cognee MCP Wrapper")

# Active sessions: session_id → {}
_sessions: dict[str, dict] = {}


def _sse_response(payload: dict) -> StreamingResponse:
    data = f"data: {json.dumps(payload)}\n\n"
    return StreamingResponse(iter([data]), media_type="text/event-stream")


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    # ── initialize ──
    if method == "initialize":
        session_id = str(uuid.uuid4())
        _sessions[session_id] = {}
        payload = {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cognee-mcp-wrapper", "version": "1.0"},
            },
        }
        return _sse_response(payload) if _wants_sse(request) else JSONResponse(
            content=payload, headers={"mcp-session-id": session_id}
        )

    # ── tools/list ──
    if method == "tools/list":
        payload = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOL_LIST}}
        return _sse_response(payload)

    # ── tools/call ──
    if method == "tools/call":
        tool_name = body["params"]["name"]
        tool_args = body["params"].get("arguments", {})
        try:
            result_text = await dispatch(tool_name, tool_args)
        except Exception as exc:
            result_text = f"Error: {exc}"
        payload = {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": result_text}]},
        }
        return _sse_response(payload)

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}})


def _wants_sse(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept


@app.get("/health")
async def health():
    return {"status": "ok", "tools": len(TOOL_LIST)}


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"Cognee MCP Wrapper starting on http://0.0.0.0:{PORT}")
    print(f"MCP endpoint: POST http://localhost:{PORT}/mcp")
    print(f"Tools: {[t['name'] for t in TOOL_LIST]}")
    uvicorn.run("mcp_wrapper:app", host="0.0.0.0", port=PORT, reload=False)
