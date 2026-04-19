"""
Cognee Memory Agent — web UI server
Runs on http://localhost:8080

Layout:
  Left  — chat with the Llama-4 agent (Groq), backed by Cognee MCP tools
  Right — paste / type a document and cognify it directly into the knowledge graph

Run:
    python agent_server.py
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from groq import Groq
from pydantic import BaseModel

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MCP_URL         = os.getenv("MCP_URL", "http://localhost:8001")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER", "localhost:8001")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
AGENT_USER      = os.getenv("AGENT_USER", "chat_user")

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Host": MCP_HOST_HEADER,
}

SYSTEM_PROMPT = """You are a helpful memory assistant connected to a Cognee knowledge graph.

Tools available — READ (return data you can use):
- search: Query the knowledge graph. ALWAYS call this before answering factual questions.
- list_data: Show what datasets are stored.
- cognify_status: Check if a pipeline is still running.

Tools available — WRITE (perform actions, return only a status string):
- cognify: Deep-process and store content into the knowledge graph (entities, relationships).
- save_interaction: Save a short note quickly (no deep processing).
- memify: Enrich the graph with temporal Event nodes. Returns only a status message, not data.
- visualize_graph: Render the graph as HTML. Returns only a status message.
- prune: Wipe all memory (only when explicitly asked).

Rules:
- ALWAYS call search to answer factual questions — never answer from memory alone.
- memify and visualize_graph are background operations. After calling them, confirm they ran; do NOT try to read data from their result.
- If the user asks "what is in the graph" or "show me events", call search, not memify.
- When the user asks to remember something, use cognify for rich content or save_interaction for short notes.
- Be concise. Summarize search results; don't dump raw output.
- If search returns nothing relevant, say so clearly.
"""

# ── MCP client ────────────────────────────────────────────────────────────────

class MCPClient:
    def __init__(self):
        self._http: httpx.AsyncClient | None = None
        self.session_id: str | None = None
        self._id = 0
        self._lock = asyncio.Lock()

    async def connect(self):
        self._http = httpx.AsyncClient(base_url=MCP_URL, timeout=30)
        self._id = 0
        resp = await self._http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "agent-ui", "version": "1.0"},
                },
            },
            headers=MCP_HEADERS,
        )
        resp.raise_for_status()
        self.session_id = resp.headers.get("mcp-session-id")

    async def close(self):
        if self._http:
            await self._http.aclose()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    @staticmethod
    def _sse(body: str) -> dict:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError(f"No SSE data: {body[:200]}")

    async def call(self, tool: str, args: dict, timeout: float = 120) -> str:
        async with self._lock:
            resp = await self._http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
                headers={**MCP_HEADERS, "mcp-session-id": self.session_id},
                timeout=timeout,
            )
        resp.raise_for_status()
        data = self._sse(resp.text)
        content = data.get("result", {}).get("content", [])
        parts = [
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts) or json.dumps(data.get("result", {}))


# ── app state ─────────────────────────────────────────────────────────────────

mcp = MCPClient()
groq_client: Groq | None = None

# per-client conversation history keyed by session_id
sessions: dict[str, list[dict]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global groq_client
    groq_client = Groq(api_key=GROQ_API_KEY)
    await mcp.connect()
    print(f"MCP connected  session={mcp.session_id}")
    yield
    await mcp.close()

app = FastAPI(lifespan=lifespan)

# ── tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "cognify",
            "description": "Store and deeply process knowledge into the graph (entities, relationships). Use for rich content.",
            "parameters": {
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_interaction",
            "description": "Quickly save a short user note or interaction (no deep graph processing).",
            "parameters": {
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search the knowledge graph. Choose search_type based on the question:\n"
                "- GRAPH_COMPLETION: default — entity/relationship graph traversal + LLM synthesis\n"
                "- GRAPH_COMPLETION_COT: chain-of-thought multi-round reasoning (complex questions)\n"
                "- GRAPH_COMPLETION_CONTEXT_EXTENSION: expands context with follow-up queries\n"
                "- GRAPH_SUMMARY_COMPLETION: includes text summaries in context\n"
                "- TEMPORAL: time-aware — filters Event nodes by timestamp (use for 'when' questions)\n"
                "- CYPHER: LLM generates a Cypher query (structured graph questions)\n"
                "- NATURAL_LANGUAGE: NL → graph query auto-translation\n"
                "- RAG_COMPLETION: classic vector RAG\n"
                "- SUMMARIES: retrieve document summaries only\n"
                "- CHUNKS: retrieve raw text chunks\n"
                "- FEELING_LUCKY: auto-selects best search type"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
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
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_data",
            "description": "List all datasets in the knowledge graph.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cognify_status",
            "description": "Check whether the cognify background pipeline is idle or still processing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prune",
            "description": "Wipe ALL stored knowledge. Only call when the user explicitly asks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visualize_graph",
            "description": "Render the full Neo4j knowledge graph as an interactive HTML visualization.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memify",
            "description": (
                "Temporal graph enrichment: reads DocumentChunk nodes, extracts Event nodes with "
                "timestamps via LLM, writes them to Neo4j. Call after cognify to enable TEMPORAL "
                "search. Takes 1-3 minutes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset name (default: main_dataset)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "persist_sessions",
            "description": (
                "Self-improvement: converts recent conversation sessions into new graph nodes. "
                "The agent's own Q&A interactions become searchable knowledge for future queries. "
                "Call periodically to keep the graph up to date with recent interactions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset name (default: main_dataset)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improve_answer",
            "description": (
                "Self-improvement: when a previous answer was wrong or incomplete, provide the "
                "original question, the wrong answer, and user feedback. The system re-queries "
                "the graph with chain-of-thought reasoning to generate a corrected answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question":       {"type": "string", "description": "The original question"},
                    "wrong_answer":   {"type": "string", "description": "The incorrect answer that was given"},
                    "feedback":       {"type": "string", "description": "User feedback explaining what was wrong"},
                },
                "required": ["question", "wrong_answer", "feedback"],
            },
        },
    },
]

# ── tool dispatch ─────────────────────────────────────────────────────────────

async def dispatch(tool: str, args: dict) -> str:
    if tool == "cognify":
        return await mcp.call("cognify", {"data": args.get("data", ""), "user": AGENT_USER}, timeout=300)
    if tool == "save_interaction":
        return await mcp.call("save_interaction", {"data": args.get("data", ""), "user": AGENT_USER}, timeout=120)
    if tool == "search":
        return await mcp.call(
            "search",
            {
                "search_query": args.get("query", ""),
                "search_type": args.get("search_type", "GRAPH_COMPLETION"),
                "user": AGENT_USER,
            },
            timeout=180,
        )
    if tool == "list_data":
        return await mcp.call("list_data", {}, timeout=60)
    if tool == "cognify_status":
        return await mcp.call("cognify_status", {}, timeout=30)
    if tool == "prune":
        return await mcp.call("prune", {}, timeout=60)
    if tool == "visualize_graph":
        return await run_visualization()
    if tool == "memify":
        return await run_memify(args.get("dataset", "main_dataset"))
    if tool == "persist_sessions":
        return await run_persist_sessions(args.get("dataset", "main_dataset"))
    if tool == "improve_answer":
        return await run_improve_answer(
            args.get("question", ""),
            args.get("wrong_answer", ""),
            args.get("feedback", ""),
        )
    return f"Unknown tool: {tool}"


async def run_memify(dataset: str = "main_dataset") -> str:
    """Run graph enrichment (summarize_text) inside the cognee-mcp container."""
    loop = asyncio.get_event_loop()

    def _exec():
        result = subprocess.run(
            ["docker", "exec", "cognee_mcp_server", "bash", "-c",
             f"python3 /tmp/run_memify.py {dataset}"],
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout + result.stderr

    output = await loop.run_in_executor(None, _exec)
    last_lines = "\n".join(output.strip().splitlines()[-6:])
    return last_lines or "Memify complete."


async def run_persist_sessions(dataset: str = "main_dataset") -> str:
    """Cognify all in-memory conversation sessions into the knowledge graph."""
    if not sessions:
        return "No sessions to persist yet."
    all_text = []
    for history in sessions.values():
        turns = []
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content:
                turns.append(f"User: {content}")
            elif role == "assistant" and content:
                turns.append(f"Assistant: {content}")
        if turns:
            all_text.append("\n".join(turns))
    if not all_text:
        return "Sessions are empty — nothing to persist."
    combined = "\n\n---\n\n".join(all_text)
    result = await mcp.call("cognify", {"data": combined, "user": AGENT_USER}, timeout=300)
    return f"Persisted {len(all_text)} session(s) to graph. {result[:200]}"


async def run_improve_answer(question: str, wrong_answer: str, feedback: str) -> str:
    """Re-query the graph with COT reasoning and store the correction."""
    if not question:
        return "Error: question is required."
    improved = await mcp.call(
        "search",
        {"search_query": question, "search_type": "GRAPH_COMPLETION_COT", "user": AGENT_USER},
        timeout=180,
    )
    correction = (
        f"CORRECTION — Question: {question}\n"
        f"Wrong answer: {wrong_answer}\n"
        f"Feedback: {feedback}\n"
        f"Improved answer: {improved}"
    )
    await mcp.call("save_interaction", {"data": correction, "user": AGENT_USER}, timeout=120)
    return f"Improved answer (stored in graph):\n{improved}"


async def run_visualization() -> str:
    """Execute the visualization script inside the cognee-mcp container and cache the HTML."""
    loop = asyncio.get_event_loop()
    def _exec():
        subprocess.run(
            ["docker", "exec", "cognee_mcp_server", "bash", "-c", "python3 /tmp/visualize.py"],
            capture_output=True, timeout=60,
        )
        result = subprocess.run(
            ["docker", "cp", "cognee_mcp_server:/tmp/cognee_graph.html",
             "c:/Users/mhame/Cognee-layer/cognee_graph_neo4j.html"],
            capture_output=True, timeout=10,
        )
        return result.returncode

    rc = await loop.run_in_executor(None, _exec)
    if rc == 0:
        return "Graph rendered. Open it at: http://localhost:8080/api/graph"
    return "Visualization failed — check docker logs cognee_mcp_server"

# ── API models ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""

class CognifyRequest(BaseModel):
    content: str

# ── streaming chat endpoint ───────────────────────────────────────────────────

async def agent_stream(message: str, session_id: str) -> AsyncGenerator[str, None]:
    history = sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # agentic loop — keep going until no more tool calls
    while True:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2048,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            history.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                yield sse("tool_start", {"tool": tool_name, "args": args})

                result = await dispatch(tool_name, args)

                yield sse("tool_done", {"tool": tool_name, "result": result[:400]})

                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue  # loop — let model process tool results

        answer = msg.content or ""
        history.append({"role": "assistant", "content": answer})
        yield sse("answer", {"text": answer})
        break


@app.post("/api/chat")
async def chat(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    return StreamingResponse(
        agent_stream(req.message, sid),
        media_type="text/event-stream",
        headers={"X-Session-Id": sid},
    )


@app.post("/api/cognify")
async def cognify_doc(req: CognifyRequest):
    if not req.content.strip():
        return {"status": "error", "message": "Content is empty."}
    try:
        # save_interaction stores raw text and triggers the cognify pipeline.
        # The cognify MCP tool expects URLs/file paths, not raw text strings.
        await mcp.call("save_interaction", {"data": req.content, "user": AGENT_USER}, timeout=120)
        result = await mcp.call("cognify", {"data": req.content, "user": AGENT_USER}, timeout=300)
        return {"status": "ok", "message": result[:500]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/api/graph", response_class=HTMLResponse)
async def serve_graph():
    """Render a fresh graph visualization and serve it inline."""
    await run_visualization()
    path = "c:/Users/mhame/Cognee-layer/cognee_graph_neo4j.html"
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        from fastapi.responses import HTMLResponse as HR
        return HR(
            content=content,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except FileNotFoundError:
        return "<h2>No graph yet — ask the agent to visualize_graph first.</h2>"


@app.get("/api/status")
async def cognify_status():
    try:
        result = await mcp.call("cognify_status", {}, timeout=30)
        idle = result.strip() in ("{}", "")
        return {"idle": idle, "raw": result[:200]}
    except Exception as exc:
        return {"idle": False, "raw": str(exc)}


@app.get("/api/datasets")
async def datasets():
    try:
        result = await mcp.call("list_data", {}, timeout=30)
        return {"result": result[:1000]}
    except Exception as exc:
        return {"error": str(exc)}

# ── frontend ──────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Cognee Agent</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#0f1117;--surface:#1a1d27;--surface2:#242736;
    --border:#2e3147;--accent:#6c63ff;--accent2:#4ecca3;
    --text:#e8eaf6;--muted:#6b7280;--danger:#ef4444;--warn:#f59e0b;
  }
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;
       height:100vh;display:flex;flex-direction:column;overflow:hidden}

  header{background:var(--surface);border-bottom:1px solid var(--border);
         padding:12px 24px;display:flex;align-items:center;gap:12px;flex-shrink:0}
  .logo{width:32px;height:32px;background:linear-gradient(135deg,var(--accent),var(--accent2));
        border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px}
  header h1{font-size:16px;font-weight:600}
  .badge{background:var(--surface2);border:1px solid var(--border);border-radius:20px;
         padding:2px 10px;font-size:11px;color:var(--muted);margin-left:auto}
  .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;margin-right:6px}

  main{flex:1;display:flex;gap:0;overflow:hidden}

  /* ── chat panel ── */
  .chat-panel{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border)}
  .panel-header{padding:14px 20px;border-bottom:1px solid var(--border);
                font-size:12px;font-weight:600;text-transform:uppercase;
                letter-spacing:.08em;color:var(--muted);background:var(--surface)}
  .messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:14px}
  .messages::-webkit-scrollbar{width:4px}
  .messages::-webkit-scrollbar-track{background:transparent}
  .messages::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}

  .msg{display:flex;gap:10px;max-width:88%}
  .msg.user{align-self:flex-end;flex-direction:row-reverse}
  .avatar{width:30px;height:30px;border-radius:50%;flex-shrink:0;display:flex;
          align-items:center;justify-content:center;font-size:13px;font-weight:700}
  .msg.user .avatar{background:var(--accent)}
  .msg.agent .avatar{background:var(--surface2);border:1px solid var(--border)}
  .bubble{padding:10px 14px;border-radius:14px;font-size:14px;line-height:1.55;white-space:pre-wrap}
  .msg.user .bubble{background:var(--accent);border-radius:14px 4px 14px 14px}
  .msg.agent .bubble{background:var(--surface2);border:1px solid var(--border);
                     border-radius:4px 14px 14px 14px}

  .tool-pill{display:inline-flex;align-items:center;gap:6px;background:var(--surface);
             border:1px solid var(--border);border-radius:20px;padding:4px 12px;
             font-size:11px;color:var(--muted);margin:4px 0;align-self:flex-start}
  .tool-pill .icon{font-size:12px}
  .tool-pill.done{border-color:var(--accent2);color:var(--accent2)}
  .tool-pill.running{border-color:var(--warn);color:var(--warn)}

  .chat-input{padding:16px 20px;border-top:1px solid var(--border);background:var(--surface);
              display:flex;gap:10px;align-items:flex-end}
  textarea.chat-box{flex:1;background:var(--surface2);border:1px solid var(--border);
                    border-radius:10px;color:var(--text);font-size:14px;resize:none;
                    padding:10px 14px;outline:none;font-family:inherit;min-height:44px;max-height:120px;
                    line-height:1.5}
  textarea.chat-box:focus{border-color:var(--accent)}
  textarea.chat-box::placeholder{color:var(--muted)}
  .send-btn{background:var(--accent);border:none;border-radius:10px;
            width:44px;height:44px;cursor:pointer;display:flex;align-items:center;
            justify-content:center;font-size:18px;flex-shrink:0;transition:opacity .2s}
  .send-btn:hover{opacity:.85}
  .send-btn:disabled{opacity:.4;cursor:not-allowed}

  /* ── cognify panel ── */
  .cognify-panel{width:360px;flex-shrink:0;display:flex;flex-direction:column;background:var(--surface)}
  .cognify-body{flex:1;padding:20px;display:flex;flex-direction:column;gap:14px;overflow-y:auto}
  .field-label{font-size:11px;font-weight:600;text-transform:uppercase;
               letter-spacing:.08em;color:var(--muted);margin-bottom:6px}
  textarea.doc-box{flex:1;background:var(--surface2);border:1px solid var(--border);
                   border-radius:10px;color:var(--text);font-size:13px;resize:none;
                   padding:12px 14px;outline:none;font-family:inherit;min-height:220px;
                   line-height:1.5}
  textarea.doc-box:focus{border-color:var(--accent2)}
  textarea.doc-box::placeholder{color:var(--muted)}
  .cognify-btn{background:linear-gradient(135deg,var(--accent2),#36b37e);border:none;
               border-radius:10px;color:#0a2018;font-weight:700;font-size:14px;
               padding:12px;cursor:pointer;transition:opacity .2s;display:flex;
               align-items:center;justify-content:center;gap:8px}
  .cognify-btn:hover{opacity:.9}
  .cognify-btn:disabled{opacity:.4;cursor:not-allowed}
  .status-box{background:var(--surface2);border:1px solid var(--border);border-radius:10px;
              padding:12px 14px;font-size:12px;color:var(--muted);line-height:1.6;min-height:60px}
  .status-box.ok{border-color:var(--accent2);color:var(--accent2)}
  .status-box.err{border-color:var(--danger);color:var(--danger)}
  .status-box.running{border-color:var(--warn);color:var(--warn)}
  .spinner{display:inline-block;width:10px;height:10px;border:2px solid currentColor;
           border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  .datasets-btn{background:none;border:1px solid var(--border);border-radius:8px;
                color:var(--muted);font-size:12px;padding:7px 12px;cursor:pointer;
                display:flex;align-items:center;gap:6px;transition:border-color .2s}
  .datasets-btn:hover{border-color:var(--accent);color:var(--text)}
  .datasets-result{background:var(--surface2);border:1px solid var(--border);border-radius:10px;
                   padding:12px;font-size:11px;color:var(--muted);white-space:pre-wrap;
                   max-height:200px;overflow-y:auto;display:none}
  .datasets-result.visible{display:block}
</style>
</head>
<body>

<header>
  <div class="logo">🧠</div>
  <h1>Cognee Memory Agent</h1>
  <span class="badge"><span class="dot"></span>llama-4-scout · Neo4j</span>
</header>

<main>
  <!-- ── chat panel ── -->
  <div class="chat-panel">
    <div class="panel-header">💬 Chat</div>
    <div class="messages" id="messages">
      <div class="msg agent">
        <div class="avatar">🤖</div>
        <div class="bubble">Hi! I'm your Cognee memory assistant. Ask me anything, or tell me something to remember. I can search the knowledge graph, store new facts, and more.</div>
      </div>
    </div>
    <div class="chat-input">
      <textarea class="chat-box" id="chatInput" placeholder="Ask something or tell me a fact to remember…" rows="1"></textarea>
      <button class="send-btn" id="sendBtn" title="Send">➤</button>
    </div>
  </div>

  <!-- ── cognify panel ── -->
  <div class="cognify-panel">
    <div class="panel-header">📄 Cognify Document</div>
    <div class="cognify-body">
      <div>
        <div class="field-label">Paste document or text</div>
        <textarea class="doc-box" id="docInput"
          placeholder="Paste any document, article, notes, or data here.&#10;&#10;The agent will extract entities and relationships and store them in the Neo4j knowledge graph."></textarea>
      </div>
      <button class="cognify-btn" id="cognifyBtn">
        <span>⚡</span> Cognify into Graph
      </button>
      <div>
        <div class="field-label">Status</div>
        <div class="status-box" id="statusBox">Ready</div>
      </div>
      <div>
        <button class="datasets-btn" id="datasetsBtn">📂 View stored datasets</button>
        <div class="datasets-result" id="datasetsResult"></div>
      </div>
      <div>
        <button class="datasets-btn" id="graphBtn" style="margin-top:6px;border-color:var(--accent2);color:var(--accent2)">🕸 Visualize Knowledge Graph</button>
      </div>
    </div>
  </div>
</main>

<script>
const messagesEl = document.getElementById('messages');
const chatInput  = document.getElementById('chatInput');
const sendBtn    = document.getElementById('sendBtn');
const docInput   = document.getElementById('docInput');
const cognifyBtn = document.getElementById('cognifyBtn');
const statusBox  = document.getElementById('statusBox');
const datasetsBtn    = document.getElementById('datasetsBtn');
const datasetsResult = document.getElementById('datasetsResult');

let sessionId = '';
let busy = false;

// ── helpers ──

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMsg(role, html, id) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  if (id) div.id = id;
  const av = document.createElement('div');
  av.className = 'avatar';
  av.textContent = role === 'user' ? '👤' : '🤖';
  const bub = document.createElement('div');
  bub.className = 'bubble';
  bub.innerHTML = html;
  div.appendChild(av);
  div.appendChild(bub);
  messagesEl.appendChild(div);
  scrollBottom();
  return bub;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addToolPill(label, done) {
  const p = document.createElement('div');
  p.className = `tool-pill ${done ? 'done' : 'running'}`;
  p.innerHTML = `<span class="icon">${done ? '✓' : '<span class="spinner"></span>'}</span>${escHtml(label)}`;
  messagesEl.appendChild(p);
  scrollBottom();
  return p;
}

// ── chat ──

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || busy) return;

  busy = true;
  sendBtn.disabled = true;
  chatInput.value = '';
  chatInput.style.height = 'auto';

  addMsg('user', escHtml(text));

  const agentBubble = addMsg('agent', '<span class="spinner"></span>');
  let agentText = '';
  const pills = {};

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (!sessionId) sessionId = resp.headers.get('X-Session-Id') || '';

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      const lines = buf.split('\n\n');
      buf = lines.pop();

      for (const chunk of lines) {
        const eventLine = chunk.split('\n').find(l => l.startsWith('event:'));
        const dataLine  = chunk.split('\n').find(l => l.startsWith('data:'));
        if (!eventLine || !dataLine) continue;

        const event = eventLine.slice(6).trim();
        let data;
        try { data = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }

        if (event === 'tool_start') {
          const label = `${data.tool}(${Object.keys(data.args || {}).join(', ')})`;
          const pill = addToolPill(label, false);
          pills[data.tool] = pill;
          agentBubble.innerHTML = '<span class="spinner"></span>';
        }
        if (event === 'tool_done') {
          const pill = pills[data.tool];
          if (pill) {
            pill.className = 'tool-pill done';
            pill.querySelector('.icon').textContent = '✓';
          }
        }
        if (event === 'answer') {
          agentText = data.text;
          agentBubble.innerHTML = escHtml(agentText).replace(/\n/g, '<br>');
        }
      }
    }
    if (!agentText) agentBubble.innerHTML = '(no response)';

  } catch (err) {
    agentBubble.innerHTML = `<span style="color:var(--danger)">Error: ${escHtml(err.message)}</span>`;
  }

  busy = false;
  sendBtn.disabled = false;
  scrollBottom();
}

chatInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
});
sendBtn.addEventListener('click', sendMessage);

// ── cognify ──

cognifyBtn.addEventListener('click', async () => {
  const content = docInput.value.trim();
  if (!content) {
    setStatus('Please paste some content first.', 'err');
    return;
  }
  cognifyBtn.disabled = true;
  setStatus('<span class="spinner"></span> Sending to Cognee…', 'running');

  try {
    const resp = await fetch('/api/cognify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await resp.json();
    if (data.status === 'ok') {
      setStatus('✓ ' + escHtml(data.message || 'Cognified successfully.'), 'ok');
      docInput.value = '';
    } else {
      setStatus('✗ ' + escHtml(data.message || 'Unknown error'), 'err');
    }
  } catch (err) {
    setStatus('✗ ' + escHtml(err.message), 'err');
  }
  cognifyBtn.disabled = false;
});

function setStatus(html, cls) {
  statusBox.className = `status-box ${cls}`;
  statusBox.innerHTML = html;
}

// ── datasets ──

document.getElementById('graphBtn').addEventListener('click', () => {
  window.open('/api/graph?t=' + Date.now(), '_blank');
});

datasetsBtn.addEventListener('click', async () => {
  datasetsResult.classList.toggle('visible');
  if (!datasetsResult.classList.contains('visible')) return;
  datasetsResult.textContent = 'Loading…';
  try {
    const resp = await fetch('/api/datasets');
    const data = await resp.json();
    datasetsResult.textContent = data.result || data.error || 'Empty.';
  } catch (err) {
    datasetsResult.textContent = 'Error: ' + err.message;
  }
});
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


# ── run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set (check .env or environment)")
        sys.exit(1)
    print(f"Starting agent UI on http://localhost:8080")
    uvicorn.run("agent_server:app", host="0.0.0.0", port=8080, reload=False)
