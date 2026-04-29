"""
Interactive agent powered by Groq (llama-4-scout) with Cognee MCP as its memory backend.

The agent can:
  - store knowledge into the Cognee graph   → cognify / save_interaction tools
  - recall knowledge from the graph          → search tool
  - inspect what's stored                    → list_data tool
  - wipe all memory                          → prune tool

Run:
    GROQ_API_KEY=<your-key> python agent_chat.py
or set GROQ_API_KEY in .env and run:
    python agent_chat.py
"""

import asyncio
import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MCP_URL      = os.getenv("MCP_URL", "http://localhost:8002")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
AGENT_USER   = os.getenv("AGENT_USER", "chat_user")

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

SYSTEM_PROMPT = """You are a helpful memory assistant connected to a Cognee knowledge graph via the MCP Wrapper.

You have access to the following tools:

WRITE:
- **cognify**: Deep-process text into the graph (entity extraction, relationships). Use for facts, documents, rich content. Takes ~30s.
- **save_interaction**: Quickly save a short note without full graph processing. Takes ~2s.
- **memify**: Enrich stored chunks with temporal Event nodes (timestamps). Enables TEMPORAL search. Takes 1-3 minutes.
- **persist_sessions**: Store a conversation transcript as graph nodes for future recall.
- **improve_answer**: Re-query with chain-of-thought reasoning and store the corrected answer.

READ:
- **search**: Search the knowledge graph. Supports search_type: GRAPH_COMPLETION (default), TEMPORAL, CHUNKS, RAG_COMPLETION, CYPHER, and more.
- **list_data**: List all datasets currently stored in the graph.
- **cognify_status**: Check whether the cognify pipeline is idle or still running.
- **visualize_graph**: Render the full knowledge graph as an interactive HTML file.

ADMIN:
- **prune**: Wipe ALL stored knowledge. Irreversible — only call when explicitly asked.

Guidelines:
- When a user shares information to remember, use `cognify` (rich content) or `save_interaction` (short notes).
- When a user asks a question, ALWAYS call `search` first before answering.
- For time-related queries ("what happened last week?"), use search_type=TEMPORAL after running `memify`.
- Be concise. After using a tool, summarize what you found or did in 1-2 sentences.
- If search returns nothing useful, say so and answer from your own knowledge.
"""

# ── MCP client ────────────────────────────────────────────────────────────────

class MCPClient:
    def __init__(self, client: httpx.AsyncClient):
        self._http = client
        self.session_id: str | None = None
        self._id = 0

    def _nid(self) -> int:
        self._id += 1
        return self._id

    @staticmethod
    def _sse(body: str) -> dict:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError(f"No SSE data line: {body[:200]}")

    async def connect(self) -> None:
        resp = await self._http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": self._nid(), "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-chat", "version": "1.0"},
                },
            },
            headers=MCP_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        self.session_id = resp.headers.get("mcp-session-id")
        if not self.session_id:
            raise RuntimeError("No mcp-session-id from initialize")

    async def call(self, tool: str, args: dict[str, Any], timeout: float = 120) -> str:
        resp = await self._http.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": self._nid(), "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
            headers={**MCP_HEADERS, "mcp-session-id": self.session_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = self._sse(resp.text)
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"MCP error [{err.get('code')}]: {err.get('message')}")
        content = data.get("result", {}).get("content", [])
        parts = [
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts) if parts else json.dumps(data.get("result", {}))


# ── tool definitions (passed to Groq) ─────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "cognify",
            "description": "Store and deeply process knowledge into the Cognee graph (entity extraction, relationship mapping). Use for facts, documents, or multi-sentence content worth remembering long-term. Set temporal=true when the content contains dates/times and you want TEMPORAL search to work on it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "The content to store and process."},
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_interaction",
            "description": "Quickly save a short user note or interaction to memory (lighter than cognify, no deep graph processing).",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "The note or interaction to save."},
                },
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
                "- GRAPH_COMPLETION (default): entity graph traversal + LLM synthesis\n"
                "- TEMPORAL: time-aware, filters Event nodes by timestamp (use for 'when' questions)\n"
                "- RAG_COMPLETION: classic vector RAG over document chunks\n"
                "- CHUNKS: raw text chunk retrieval\n"
                "- SUMMARIES: retrieve document summaries only\n"
                "- CYPHER: LLM generates a Cypher query for structured graph questions\n"
                "- GRAPH_COMPLETION_COT: chain-of-thought multi-round reasoning (complex questions)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                    "search_type": {
                        "type": "string",
                        "enum": [
                            "GRAPH_COMPLETION", "GRAPH_COMPLETION_COT", "TEMPORAL",
                            "RAG_COMPLETION", "CHUNKS", "SUMMARIES", "CYPHER",
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
            "description": "List all datasets currently stored in the Cognee graph.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prune",
            "description": "Wipe ALL stored knowledge from the graph. Irreversible — only call when the user explicitly asks to clear memory.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cognify_status",
            "description": "Check whether the cognify background pipeline is idle or still running.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memify",
            "description": "Enrich an existing knowledge graph by creating triplet embeddings (Entity → Relationship → Entity). Enables SearchType.TRIPLET_COMPLETION queries. Run after cognify on a dataset. Takes 5-10 minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset name to enrich (default: main_dataset)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visualize_graph",
            "description": "Render the full Neo4j knowledge graph as an interactive HTML file. Call when the user asks to see or explore the graph visually.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "persist_sessions",
            "description": "Store a conversation transcript as knowledge graph nodes so it can be recalled in future sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Conversation transcript to persist."},
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "improve_answer",
            "description": "Re-query the graph with chain-of-thought reasoning and store the corrected answer. Use when a previous answer was wrong.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question":     {"type": "string", "description": "The original question."},
                    "wrong_answer": {"type": "string", "description": "The incorrect answer that was given."},
                    "feedback":     {"type": "string", "description": "What was wrong and what the correct answer should be."},
                },
                "required": ["question", "wrong_answer", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graphiti_ingest",
            "description": "Ingest documents into the Graphiti temporal episode graph to track how facts evolve over time. Each text becomes a timestamped episode in Neo4j. Use for documents where content changes across versions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of texts/documents to ingest as timestamped episodes.",
                    },
                },
                "required": ["texts"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graphiti_search",
            "description": "Search the Graphiti temporal episode graph for how facts evolved over time. Best for: 'how did X change?', 'what was the status of Y in period Z?', 'what changed between documents?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query about fact evolution over time."},
                },
                "required": ["query"],
            },
        },
    },
]


# ── tool dispatcher ────────────────────────────────────────────────────────────

async def dispatch(mcp: MCPClient, name: str, args: dict) -> str:
    print(f"\n  [tool: {name}] args={json.dumps(args, ensure_ascii=False)[:120]}")
    try:
        if name == "cognify":
            cognify_args = {"data": args["data"]}
            temporal = args.get("temporal")
            if temporal is True or str(temporal).lower() == "true":
                cognify_args["temporal"] = True
            result = await mcp.call("cognify", cognify_args, timeout=300)
        elif name == "save_interaction":
            result = await mcp.call("save_interaction", {"data": args["data"]}, timeout=120)
        elif name == "search":
            result = await mcp.call(
                "search",
                {
                    "search_query": args["query"],
                    "search_type": args.get("search_type", "GRAPH_COMPLETION"),
                },
                timeout=180,
            )
        elif name == "list_data":
            result = await mcp.call("list_data", {}, timeout=60)
        elif name == "prune":
            result = await mcp.call("prune", {}, timeout=60)
        elif name == "cognify_status":
            result = await mcp.call("cognify_status", {}, timeout=30)
        elif name == "memify":
            result = await mcp.call("memify", {"dataset": args.get("dataset", "main_dataset")}, timeout=300)
        elif name == "visualize_graph":
            result = await mcp.call("visualize_graph", {}, timeout=60)
        elif name == "persist_sessions":
            result = await mcp.call("persist_sessions", {"data": args["data"]}, timeout=120)
        elif name == "improve_answer":
            result = await mcp.call("improve_answer", {
                "question":     args["question"],
                "wrong_answer": args["wrong_answer"],
                "feedback":     args["feedback"],
            }, timeout=120)
        elif name == "graphiti_ingest":
            result = await mcp.call("graphiti_ingest", {"texts": args["texts"]}, timeout=300)
        elif name == "graphiti_search":
            result = await mcp.call("graphiti_search", {"query": args["query"]}, timeout=120)
        else:
            result = f"Unknown tool: {name}"
        print(f"  [tool: {name}] done — {result[:120].replace(chr(10), ' ')}")
        return result
    except Exception as exc:
        err = f"Tool {name} failed: {exc}"
        print(f"  [tool: {name}] ERROR — {exc}")
        return err


# ── agentic loop ──────────────────────────────────────────────────────────────

async def chat_loop(mcp: MCPClient) -> None:
    groq = Groq(api_key=GROQ_API_KEY)
    history: list[dict] = []

    print("\n" + "═" * 60)
    print("  Cognee Memory Agent  (model: llama-4-scout)")
    print("  MCP Wrapper:", MCP_URL, " | tools: 10")
    print("  Type 'quit' or Ctrl-C to exit.")
    print("═" * 60 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye"}:
            print("Bye.")
            break

        history.append({"role": "user", "content": user_input})

        # agentic tool-use loop — keep calling until the model stops requesting tools
        while True:
            response = groq.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=2048,
            )

            msg = response.choices[0].message

            if msg.tool_calls:
                # Add assistant message with tool_calls to history
                history.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                })

                # Execute each requested tool
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    result = await dispatch(mcp, tc.function.name, args)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                # loop back — let the model process tool results
                continue

            # No more tool calls — final answer
            answer = msg.content or "(no response)"
            history.append({"role": "assistant", "content": answer})
            print(f"\nAgent: {answer}\n")
            break


# ── entrypoint ────────────────────────────────────────────────────────────────

async def main() -> None:
    if not GROQ_API_KEY:
        print("ERROR: set GROQ_API_KEY environment variable.")
        sys.exit(1)

    async with httpx.AsyncClient(base_url=MCP_URL) as http:
        mcp = MCPClient(http)
        print(f"Connecting to MCP at {MCP_URL} ...")
        await mcp.connect()
        print(f"MCP session: {mcp.session_id}")
        await chat_loop(mcp)


if __name__ == "__main__":
    asyncio.run(main())
