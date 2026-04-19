"""
Minimal agent that talks directly to the Cognee MCP server (no FastAPI layer)
and cognifies bpi_france_events.json through MCP tools.

Run from host (MCP exposed on localhost:8001):
    python test_agent_mcp.py

Run from inside the web container (MCP on Docker DNS):
    MCP_URL=http://cognee-mcp:8000 MCP_HOST_HEADER=localhost:8001 python test_agent_mcp.py
"""

import asyncio
import json
import os
import sys
import uuid
from typing import Any

import httpx

# Windows consoles default to cp1252 — force utf-8 so ✔ / ✘ don't crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MCP_URL = os.getenv("MCP_URL", "http://localhost:8001")
MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER", "localhost:8001")
TENANT = os.getenv("AGENT_TENANT", f"bpi_agent_{uuid.uuid4().hex[:6]}")
DATASET = os.getenv("AGENT_DATASET", "bpi_france_events")
BPI_FILE = os.getenv("BPI_FILE", "bpi_france_events.json")

POLL_INTERVAL = 10
POLL_TIMEOUT = 600

HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Host": MCP_HOST_HEADER,
}


class MCPAgent:
    """Tiny agent wrapping MCP JSON-RPC calls — initialize, tools/call, SSE parsing."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.session_id: str | None = None
        self._rpc_id = 0

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    @staticmethod
    def _parse_sse(body: str) -> dict:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError(f"No data line in MCP response: {body[:300]}")

    async def initialize(self) -> None:
        resp = await self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "bpi-agent", "version": "1.0"},
                },
            },
            headers=HEADERS,
            timeout=60,
        )
        resp.raise_for_status()
        self.session_id = resp.headers.get("mcp-session-id")
        if not self.session_id:
            raise RuntimeError("No mcp-session-id returned by initialize")
        print(f"✔ MCP session: {self.session_id}")

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 300) -> dict:
        assert self.session_id, "call initialize() first"
        resp = await self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={**HEADERS, "mcp-session-id": self.session_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        return self._parse_sse(resp.text)

    async def list_tools(self) -> list[str]:
        assert self.session_id
        resp = await self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}},
            headers={**HEADERS, "mcp-session-id": self.session_id},
            timeout=60,
        )
        resp.raise_for_status()
        data = self._parse_sse(resp.text)
        return [t["name"] for t in data["result"]["tools"]]


def fact_from_event(event: dict) -> str:
    """Build a normalized fact string while tolerating sparse synthetic events."""
    date = event.get("date", "date inconnue")
    title = event.get("title", "Evenement sans titre")
    category = event.get("category", "non classe")
    description = event.get("description", "Aucune description.")

    entities = event.get("entities") or []
    if not isinstance(entities, list):
        entities = [str(entities)]
    entities_text = ", ".join(str(entity) for entity in entities) if entities else "Aucune"

    amount = event.get("amount")
    amount_text = str(amount) if amount not in (None, "") else "N/A"

    return (
        f"Le {date}, {title} ({category}). "
        f"{description} Entites: {entities_text}. "
        f"Montant: {amount_text}."
    )


async def wait_for_idle(agent: MCPAgent) -> None:
    """Poll cognify_status until pipeline is idle ({}), or timeout."""
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        result = await agent.call_tool("cognify_status", {})
        content = result.get("result", {}).get("content", [])
        text = ""
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                break
        if text.strip() in ("{}", ""):
            print(f"  ⏳ pipeline idle after {elapsed}s")
            return
        if "DatabaseNotCreatedError" in text:
            # Cognee setup() hasn't completed yet — keep waiting.
            print(f"  ⏳ [{elapsed}s] db not ready yet — retrying")
        else:
            print(f"  ⏳ [{elapsed}s] pipeline busy — {text[:80]}")
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    print(f"  ⚠ timeout after {POLL_TIMEOUT}s — continuing anyway")


async def main() -> None:
    with open(BPI_FILE, encoding="utf-8") as f:
        events = json.load(f)

    print("=" * 60)
    print("BPI France — direct MCP agent cognify")
    print("=" * 60)
    print(f"MCP: {MCP_URL}   tenant: {TENANT}   events: {len(events)}")

    async with httpx.AsyncClient(base_url=MCP_URL) as client:
        agent = MCPAgent(client)
        await agent.initialize()

        tools = await agent.list_tools()
        print(f"✔ tools exposed: {sorted(tools)}")

        # 1. Clean slate — prune drops the graph tables, so we immediately run
        #    a seed cognify to force Cognee's setup() before any status poll.
        print("\n[1] prune + setup seed")
        prune = await agent.call_tool("prune", {})
        print(f"    prune error={prune.get('error')}  isError={prune.get('result', {}).get('isError')}")
        seed = await agent.call_tool(
            "cognify",
            {"data": "Cognee setup seed", "user": TENANT},
            timeout=300,
        )
        print(f"    seed cognify isError={seed.get('result', {}).get('isError')}")
        # cognify returns before setup() finishes — wait for DB tables to exist
        # before polling cognify_status, otherwise we get DatabaseNotCreatedError.
        await asyncio.sleep(8)
        await wait_for_idle(agent)

        # 2. Ingest every event via save_interaction, waiting for the pipeline
        #    between records (Cognee schedules cognify in the background).
        print(f"\n[2] save_interaction × {len(events)}")
        for i, event in enumerate(events, 1):
            fact = fact_from_event(event)
            result = await agent.call_tool(
                "save_interaction",
                {"user": TENANT, "data": fact},
                timeout=600,
            )
            is_err = result.get("result", {}).get("isError", True)
            mark = "✔" if not is_err else "✘"
            event_title = event.get("title", f"Event {i}") if isinstance(event, dict) else f"Event {i}"
            print(f"    {mark} [{i}/{len(events)}] {event_title}")
            if i < len(events):
                await wait_for_idle(agent)

        # 3. Final drain
        print("\n[3] wait for final cognify drain")
        await wait_for_idle(agent)

        # 4. List data
        print("\n[4] list_data")
        listed = await agent.call_tool("list_data", {})
        content = listed.get("result", {}).get("content", [])
        for item in content[:3]:
            text = item.get("text", "") if isinstance(item, dict) else str(item)
            print(f"    • {text[:200]}")

        # 5. Search — exercise the graph
        print("\n[5] search")
        queries = [
            "Quels programmes sont liés à la French Tech ?",
            "Quel est le budget de France 2030 ?",
            "   ",
        ]
        for q in queries:
            print(f"\n    Q: {q}")
            result = await agent.call_tool(
                "search",
                {"search_query": q, "search_type": "GRAPH_COMPLETION", "user": TENANT},
                timeout=180,
            )
            content = result.get("result", {}).get("content", [])
            for item in content[:2]:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                print(f"    A: {text[:400]}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
