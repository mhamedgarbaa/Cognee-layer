import argparse
import json

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--host-header", default=None)
    args = parser.parse_args()

    payload = {
        "jsonrpc": "2.0",
        "id": "init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1.0"},
        },
    }

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if args.host_header:
        headers["Host"] = args.host_header

    response = httpx.post(args.url, headers=headers, json=payload, timeout=30)
    print("status:", response.status_code)
    print("session:", response.headers.get("mcp-session-id"))
    print("content-type:", response.headers.get("content-type"))
    print("body:")
    print(response.text[:500])


if __name__ == "__main__":
    main()
