# Cognee Memory System — Implementation Summary

## Stack Overview (v3.0 — MCP Wrapper)

Five Docker services, all healthy:

| Service | Container | Port | Role |
|---------|-----------|------|------|
| `db` | `workspace_db` | 5432 | PostgreSQL — app DB + Cognee relational store |
| `neo4j` | `workspace_neo4j` | 7474 / 7687 | Neo4j 5 — knowledge graph |
| `cognee-mcp` | `cognee_mcp_server` | 8001 | Cognee MCP — core memory engine |
| `mcp-wrapper` | `mcp_wrapper` | 8002 | MCP Wrapper — unified tool gateway |
| `web` | `workspace_api` | 8000 | FastAPI — agent-facing HTTP API |

Startup dependency order:
```
db (healthy) ──┐
               ├──► cognee-mcp (healthy) ──► mcp-wrapper ──► web
neo4j (healthy)┘
```

---

## What's Complete

### Phase 0 — Logging Foundation
- `configuration/logging_setup.py` extended: `JsonFormatter` forwards MCP-specific extra fields (`request_id`, `tool`, `session_id`, `latency_ms`, `error`) into structured JSON logs.

### Phase 1 — Session Management
- `mcp-session-id` UUID generated on `initialize`, returned as response header.
- Sessions stored in `_sessions` dict: `{session_id: {agent_id, created_at}}`.
- `_validate_session()` rejects `tools/list` and `tools/call` without a valid session.
- Background eviction loop clears sessions older than `SESSION_TTL_SECONDS` (default 3600s).

### Phase 2 — Tool Dispatch Layer
- `dispatch(tool_name, args, session_id, user)` routes all 10 tools.
- **Proxy tools** (8): `cognify`, `save_interaction`, `search`, `list_data`, `cognify_status`, `prune`, `persist_sessions`, `improve_answer` — forwarded via `CogneeMCPProxy.call()`.
- **Subprocess tools** (2): `memify`, `visualize_graph` — executed via `docker exec` inside `cognee_mcp_server`.

### Phase 3 — Health Endpoint
- `GET /health` returns: `status`, `tools`, `uptime_seconds`, `active_sessions`, `cognee_mcp_connected`, `circuit_breaker_open`, `consecutive_failures`.

### Phase 4 — Resilient Proxy (`CogneeMCPProxy`)
- Lazy connect on first call; transparent reconnect on failure.
- **Circuit breaker**: opens after `CIRCUIT_BREAKER_THRESHOLD` (default 3) consecutive failures, stays open for `CIRCUIT_BREAKER_TIMEOUT` (default 60s).
- **Exponential backoff**: delays double from `MCP_WRAPPER_RECONNECT_DELAY` (default 5s) up to 60s cap, up to `MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS` (default 10) attempts.
- Double-checked locking in `ensure_connected()` prevents thundering herd.
- Startup: quick 5s connect attempt; if unreachable, continues to serve and retries on first call.

### Phase 5 — Skipped (authentication deferred)

### Phase 6 — SSE Streaming
- `MCP_WRAPPER_STREAMING=true` enables incremental SSE forwarding via `stream_call()`.
- Subprocess tools (`memify`, `visualize_graph`) emit periodic `notifications/progress` heartbeat pings while the blocking subprocess runs in a thread pool executor.
- Default (`false`): buffers full result, emits single SSE event — compatible with all clients.
- `mcp-session-id` correctly propagated in `StreamingResponse` headers.

### Phase 7 — Tool Documentation Endpoint
- `GET /tools` returns all 10 tools enriched with `category` (`read`/`write`/`admin`) and `typical_latency_seconds`.
- Includes server metadata and step-by-step session handshake instructions.
- No authentication required (informational).

---

## Files Added / Modified

| File | Change |
|------|--------|
| `mcp_wrapper.py` | New — 800+ line MCP wrapper (Phases 0–7) |
| `Dockerfile.wrapper` | New — `python:3.11-slim`, copies wrapper + configuration/ |
| `requirements-wrapper.txt` | New — `fastapi`, `uvicorn`, `httpx`, `python-dotenv` |
| `docker-compose.yml` | Added `mcp-wrapper` service + `graph_output` volume on `web` |
| `configuration/logging_setup.py` | Extended `JsonFormatter` for MCP extra fields |
| `agent_server.py` | Replaced hardcoded Windows paths with env vars |
| `scripts/visualize_neo4j_graph.py` | Rewrote to use direct Neo4j Cypher (fixes isolated nodes) |
| `scripts/test_streaming.py` | New — smoke test for streaming mode |
| `.env.example` | Documented all new MCP wrapper env vars |

---

## 10 Exposed Tools

| Tool | Category | Typical Latency |
|------|----------|----------------|
| `cognify` | write | 30s |
| `save_interaction` | write | 2s |
| `search` | read | 5s |
| `list_data` | read | 2s |
| `cognify_status` | read | 1s |
| `prune` | admin | 5s |
| `memify` | write | 120s |
| `visualize_graph` | read | 15s |
| `persist_sessions` | write | 10s |
| `improve_answer` | write | 10s |

---

## Resolved Issues

| Issue | Resolution |
|-------|------------|
| `421 Invalid Host header` | `MCP_HOST_HEADER=localhost:8001` env var sent on all proxy requests |
| Graph isolated nodes | Replaced `get_graph_data()` with direct Cypher `MATCH (a)-[r]->(b)` |
| `mcp-session-id` dropped on SSE | Pass `headers=` at `StreamingResponse(...)` construction |
| Startup blocking uvicorn | Quick `asyncio.wait_for(connect, timeout=5)` instead of full retry loop |
| `MCP_WRAPPER_STREAMING` not in container | Added to `docker-compose.yml` environment block |
| Windows CMD curl multiline | Replaced with Python test scripts |

---

## Endpoint Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp` | POST | MCP JSON-RPC endpoint (initialize / tools/list / tools/call) |
| `/health` | GET | Wrapper health + circuit breaker state |
| `/tools` | GET | All 10 tools with metadata + session handshake guide |

---

## Key Environment Variables

```env
# MCP Wrapper
MCP_WRAPPER_PORT=8002
MCP_WRAPPER_LOG_LEVEL=INFO
MCP_WRAPPER_API_KEY=                        # empty = no auth
MCP_WRAPPER_STRICT_SESSIONS=false
MCP_WRAPPER_SESSION_TTL=3600
MCP_WRAPPER_STREAMING=false
MCP_WRAPPER_RECONNECT_DELAY=5
MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS=10

# Circuit breaker
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT=60

# Docker exec tools
COGNEE_CONTAINER_NAME=cognee_mcp_server
GRAPH_OUTPUT_PATH=/graph/cognee_graph.html
```

---

## Project Structure (current)

```
cognee-layer/
├── app.py                          # FastAPI entry point
├── agent_server.py                 # Browser-based agent UI (port 8080)
├── mcp_wrapper.py                  # MCP Wrapper — unified tool gateway
├── docker-compose.yml              # 5-service stack
├── Dockerfile                      # FastAPI web service
├── Dockerfile.wrapper              # MCP Wrapper service
├── requirements.txt
├── requirements-wrapper.txt
├── .env / .env.example
├── api/                            # FastAPI routers + schemas
├── service/                        # Business logic + circuit breaker
├── data/                           # CogneeRepository
├── configuration/                  # Settings + JsonFormatter
├── scripts/
│   ├── postgres-init/              # DB provisioning on first boot
│   ├── visualize_neo4j_graph.py    # Direct Cypher graph renderer
│   └── test_streaming.py           # SSE streaming smoke test
├── ontologies/                     # RDF/OWL ontology files
└── tests/                          # Integration tests
```
