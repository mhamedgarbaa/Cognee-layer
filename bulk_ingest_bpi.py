"""
Bulk-ingest all PDFs from data/data_bpi/ into the Cognee knowledge graph.
Passes container file paths directly to cognify — Cognee MCP reads and extracts
the PDFs natively. wait_idle() between files prevents QueuePool exhaustion.

Requires: docker compose up -d cognee-mcp  (to mount /data/bpi)

Run:
    python bulk_ingest_bpi.py
"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

MCP_URL            = os.getenv("MCP_URL", "http://localhost:8002/mcp")
DATA_DIR           = Path(__file__).parent / "data" / "data_bpi"
CONTAINER_DATA_DIR = "/data/bpi"
MCP_HEADERS        = {"Accept": "application/json, text/event-stream"}


def parse_sse(body: str) -> dict:
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError(f"No SSE data: {body[:200]}")


async def mcp_call(http: httpx.AsyncClient, session_id: str, tool: str, args: dict, timeout: float = 600) -> str:
    resp = await http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": args}},
        headers={**MCP_HEADERS, "mcp-session-id": session_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = parse_sse(resp.text)
    content = data.get("result", {}).get("content", [])
    return next((c["text"] for c in content if c.get("type") == "text"), "done")


async def wait_idle(
    http: httpx.AsyncClient,
    session_id: str,
    poll_interval: int = 10,
    max_wait: int = 900,   # 15 min ceiling — prevents infinite loops on stuck pipelines
) -> None:
    """Poll cognify_status until the pipeline reaches a terminal state."""
    waited = 0
    while True:
        status = await mcp_call(http, session_id, "cognify_status", {}, timeout=30)
        s = status.strip()
        # Terminal: idle, empty dict, or any finished/errored PipelineRunStatus
        if s in ("{}", "", "idle") or any(
            kw in s for kw in ("ERRORED", "COMPLETED", "FINISHED")
        ):
            return
        if waited >= max_wait:
            print(f"    wait_idle timeout after {max_wait}s — continuing anyway")
            return
        print(f"    pipeline busy ({s[:80]}) — waiting {poll_interval}s…")
        await asyncio.sleep(poll_interval)
        waited += poll_interval


async def main():
    pdfs = sorted(DATA_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {DATA_DIR}")
        return

    print(f"Found {len(pdfs)} PDFs to ingest\n")

    base_url = MCP_URL.rsplit("/mcp", 1)[0]
    async with httpx.AsyncClient(base_url=base_url) as http:
        resp = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "bulk-ingest", "version": "1.0"}}},
            headers=MCP_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        print(f"MCP session: {session_id}\n")

        for i, pdf in enumerate(pdfs, 1):
            print(f"[{i}/{len(pdfs)}] {pdf.name}")

            # Cognee parses the path as a URL internally — '#' is treated as a
            # fragment separator and truncates the filename. Create a sanitized
            # temporary copy for any file whose name contains URL-special chars.
            safe_name = re.sub(r"[#?&=+%]", "_", pdf.name)
            tmp_pdf = pdf.parent / safe_name if safe_name != pdf.name else None
            if tmp_pdf:
                shutil.copy2(pdf, tmp_pdf)

            container_path = f"{CONTAINER_DATA_DIR}/{safe_name}"

            await wait_idle(http, session_id)

            print(f"  Cognifying ...", end=" ", flush=True)
            try:
                result = await mcp_call(http, session_id, "cognify", {"data": container_path})
                print(f"OK — {result[:80]}")
            except Exception as e:
                print(f"ERROR — {e}")
            finally:
                if tmp_pdf and tmp_pdf.exists():
                    tmp_pdf.unlink()
            print()

    print("All PDFs processed.")
    print("View graph: http://localhost:8000/graph/cognee_graph.html")


if __name__ == "__main__":
    asyncio.run(main())
