"""Quick end-to-end test for graphiti_ingest via the MCP wrapper."""
import asyncio, json, httpx

MCP_URL = "http://localhost:8002"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

async def main():
    async with httpx.AsyncClient(base_url=MCP_URL, timeout=180) as http:
        # initialize
        resp = await http.post("/mcp", json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1.0"}}
        }, headers=MCP_HEADERS)
        resp.raise_for_status()
        sid = resp.headers.get("mcp-session-id")
        print(f"Session: {sid}")

        # call graphiti_ingest
        resp = await http.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "graphiti_ingest", "arguments": {"texts": ["Meyssen is fired"]}}
        }, headers={**MCP_HEADERS, "mcp-session-id": sid})
        resp.raise_for_status()

        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                content = data.get("result", {}).get("content", [])
                for c in content:
                    print("RESULT:", c.get("text", ""))
                if "error" in data:
                    print("ERROR:", data["error"])

asyncio.run(main())
