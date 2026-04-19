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

You have access to the following tools:
- **cognify**: Store and deeply process a piece of knowledge into the graph (use for facts, documents, complex content).
- **save_interaction**: Quickly save a user interaction or short note (lighter than cognify).
- **search**: Search the knowledge graph with a natural-language query.
- **list_data**: Show what datasets are currently stored in the graph.
- **prune**: Wipe all stored knowledge (use only when explicitly asked).

Guidelines:
- When a user shares information they want remembered, use `cognify` or `save_interaction`.
- When a user asks a question that might be answered by stored knowledge, ALWAYS call `search` first.
- Be concise and direct. After using a tool, summarize what you found or did.
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
        # Extract text content from the MCP result
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
            "description": "Store and deeply process knowledge into the Cognee graph (entity extraction, relationship mapping). Use for facts, documents, or multi-sentence content worth remembering long-term.",
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
            "description": "Search the knowledge graph with a natural-language query. Call this before answering any question that might be in memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
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
]


# ── tool dispatcher ────────────────────────────────────────────────────────────

async def dispatch(mcp: MCPClient, name: str, args: dict) -> str:
    print(f"\n  [tool: {name}] args={json.dumps(args, ensure_ascii=False)[:120]}")
    try:
        if name == "cognify":
            result = await mcp.call("cognify", {"data": args["data"], "user": AGENT_USER}, timeout=300)
        elif name == "save_interaction":
            result = await mcp.call("save_interaction", {"data": args["data"], "user": AGENT_USER}, timeout=120)
        elif name == "search":
            result = await mcp.call(
                "search",
                {"search_query": args["query"], "search_type": "GRAPH_COMPLETION", "user": AGENT_USER},
                timeout=180,
            )
        elif name == "list_data":
            result = await mcp.call("list_data", {}, timeout=60)
        elif name == "prune":
            result = await mcp.call("prune", {}, timeout=60)
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
    print("  MCP server:", MCP_URL)
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
