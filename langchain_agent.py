"""
LangChain agent connected to Cognee via MCP wrapper (port 8002).

Install deps:
    pip install langchain-mcp-adapters langchain-groq langgraph

Run:
    python langchain_agent.py
"""

import asyncio
import os

import httpx
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

load_dotenv()

MCP_URL      = os.getenv("MCP_URL", "http://localhost:8002/mcp")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

WRITE_TOOLS = {"cognify", "save_interaction", "memify", "persist_sessions", "improve_answer"}

SYSTEM_PROMPT = """You are a memory assistant connected to a Cognee knowledge graph.

RULES — follow them strictly:
1. ALWAYS call the `search` tool before answering ANY factual question. Never answer from your own knowledge alone.
2. If the user shares information to remember, call `cognify` (rich content) or `save_interaction` (short note).
3. If search returns nothing useful, say so clearly, then answer from your own knowledge as a fallback.
4. Be concise — summarize tool results in 1-2 sentences.
"""


async def _trigger_visualize(http: httpx.AsyncClient, session_id: str) -> None:
    """Fire visualize_graph in the background after a write tool completes."""
    try:
        await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 999, "method": "tools/call",
                  "params": {"name": "visualize_graph", "arguments": {}}},
            headers={"Accept": "application/json, text/event-stream",
                     "mcp-session-id": session_id},
            timeout=30,
        )
        print("  [graph updated]")
    except Exception:
        pass


def wrap_with_auto_visualize(tool: BaseTool, http: httpx.AsyncClient, session_id: str) -> BaseTool:
    """Wrap a write tool so visualize_graph fires after it completes."""
    original_invoke = tool._run
    original_ainvoke = tool._arun

    async def _arun_wrapped(*args, **kwargs):
        result = await original_ainvoke(*args, **kwargs)
        asyncio.create_task(_trigger_visualize(http, session_id))
        return result

    tool._arun = _arun_wrapped
    return tool


async def main():
    llm = ChatGroq(api_key=GROQ_API_KEY, model=GROQ_MODEL, temperature=0.3)

    mcp_client = MultiServerMCPClient(
        {
            "cognee": {
                "url": MCP_URL,
                "transport": "streamable_http",
            }
        }
    )
    tools = await mcp_client.get_tools()
    print(f"Discovered {len(tools)} tools: {[t.name for t in tools]}")

    # Get a dedicated HTTP client + session for background visualize calls
    base_url = MCP_URL.rsplit("/mcp", 1)[0]
    http = httpx.AsyncClient(base_url=base_url)
    init = await http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "auto-viz", "version": "1.0"}}},
        headers={"Accept": "application/json, text/event-stream"},
        timeout=15,
    )
    viz_session_id = init.headers.get("mcp-session-id", "")

    # Wrap write tools with auto-visualize
    wrapped_tools = [
        wrap_with_auto_visualize(t, http, viz_session_id) if t.name in WRITE_TOOLS else t
        for t in tools
    ]

    agent = create_react_agent(llm, wrapped_tools, prompt=SYSTEM_PROMPT)

    print("\nCognee LangChain Agent — type 'quit' to exit\n")
    print("Graph auto-updates at http://localhost:8000/graph/cognee_graph.html after every write.\n")
    try:
        while True:
            user_input = input("You: ").strip()
            if not user_input or user_input.lower() in {"quit", "exit"}:
                break

            result = await agent.ainvoke({"messages": [{"role": "user", "content": user_input}]})
            answer = result["messages"][-1].content
            print(f"\nAgent: {answer}\n")
    finally:
        await http.aclose()


if __name__ == "__main__":
    if not GROQ_API_KEY:
        print("ERROR: set GROQ_API_KEY in .env")
    else:
        asyncio.run(main())
