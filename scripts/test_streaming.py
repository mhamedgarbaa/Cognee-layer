"""Smoke-test for MCP wrapper streaming mode."""
import asyncio
import json
import os
import httpx

BASE = os.getenv("MCP_WRAPPER_URL", "http://localhost:8002")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        # initialize
        r = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "clientInfo": {"name": "stream-test", "version": "1.0"},
                "capabilities": {}, "protocolVersion": "2024-11-05",
            },
        })
        sid = r.headers.get("mcp-session-id")
        print(f"session: {sid}")

        # streaming tools/call
        event_count = 0
        async with client.stream(
            "POST", "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "cognify_status", "arguments": {}}},
            headers={"mcp-session-id": sid, "Accept": "text/event-stream"},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event_count += 1
                data = json.loads(line[5:].strip())
                method = data.get("method", "(result)")
                print(f"  event {event_count}: keys={list(data.keys())}  method={method}")
                if "result" in data:
                    print(f"  => {str(data['result'])[:100]}")

        print(f"Total events: {event_count}")


if __name__ == "__main__":
    asyncio.run(main())
