# Data Flow Architecture

## High-Level Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    EXTERNAL AGENTS / CLIENTS                     │
│         (AI agents, LangChain, custom HTTP clients)              │
└────┬─────────────────────────────────────────────────────────────┘
     │ HTTP JSON-RPC 2.0
     ▼
┌──────────────────────────────────────────────────────────────────┐
│              MCP WRAPPER  :8002  (mcp_wrapper.py)                │
│                                                                  │
│  GET  /tools        — tool catalogue + session handshake guide   │
│  GET  /health       — wrapper + circuit breaker status           │
│  POST /mcp          — initialize / tools/list / tools/call       │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │ Session layer      mcp-session-id UUID per agent        │     │
│  │ Dispatch layer     routes 10 tools → proxy or docker    │     │
│  │ Circuit breaker    opens after 3 failures / 60s timeout │     │
│  │ Exponential backoff 5s → 60s, max 10 reconnect attempts │     │
│  │ SSE streaming      optional incremental forwarding      │     │
│  └─────────────────────────────────────────────────────────┘     │
└────┬───────────────────────────┬─────────────────────────────────┘
     │ JSON-RPC proxy (8 tools)  │ docker exec (2 tools)
     ▼                           ▼
┌─────────────────┐    ┌──────────────────────────────────────────┐
│  COGNEE MCP     │    │  cognee_mcp_server container             │
│  :8001 (internal│    │  memify          — temporal enrichment   │
│  :8000 in Docker│    │  visualize_graph — Neo4j → HTML render   │
│                 │    └──────────────────────────────────────────┘
│  10 MCP tools   │
│  HTTP/SSE :8000 │
└────┬────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────────────┐
│                   COGNEE CORE ENGINE                             │
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │  Postgres 15 │   │   Neo4j 5    │   │  OpenAI / Azure OAI  │ │
│  │  cognee_db   │   │  bolt::7687  │   │  gpt-4o + embeddings │ │
│  │  (metadata)  │   │  (graph)     │   │  (LLM synthesis)     │ │
│  └──────────────┘   └──────────────┘   └──────────────────────┘ │
│                                                                  │
│  ┌──────────────┐                                                │
│  │  LanceDB     │                                                │
│  │  1536D vecs  │                                                │
│  │  (file-backed│                                                │
│  └──────────────┘                                                │
└──────────────────────────────────────────────────────────────────┘

     ┌────────────────────────────────────────────────────────┐
     │  FASTAPI WEB  :8000  (app.py)                          │
     │  Higher-level memory API: /api/v1/memory/*             │
     │  Agent browser UI: agent_server.py :8080               │
     └────────────────────────────────────────────────────────┘
```

---

## 1. Session Handshake

Every agent must complete a handshake before calling tools.

```
Agent                          MCP Wrapper (:8002)
  │                                   │
  │  POST /mcp                        │
  │  { method: "initialize",          │
  │    clientInfo: { name: "my-agent" │
  │    protocolVersion: "2024-11-05" }│
  │ ─────────────────────────────────►│
  │                                   │  creates session UUID
  │                                   │  _sessions[uuid] = {agent_id: "my-agent", created_at: now}
  │  200  mcp-session-id: <uuid>      │
  │  { result: { capabilities: {} } } │
  │ ◄─────────────────────────────────│
  │                                   │
  │  POST /mcp                        │
  │  mcp-session-id: <uuid>           │
  │  { method: "tools/list" }         │
  │ ─────────────────────────────────►│  returns TOOL_LIST (10 tools)
  │  SSE: data: { result: { tools: [  │
  │    { name, description, schema }  │
  │  ] } }                            │
  │ ◄─────────────────────────────────│
  │                                   │
  │  POST /mcp                        │
  │  mcp-session-id: <uuid>           │
  │  { method: "tools/call",          │
  │    params: { name: "search",      │
  │      arguments: { search_query: . │
  │  } }                              │
  │ ─────────────────────────────────►│  dispatch() → proxy or docker exec
  │  SSE: data: { result: { content:  │
  │    [{ type: "text", text: "..." }]│
  │  } }                              │
  │ ◄─────────────────────────────────│
```

Sessions are evicted after `SESSION_TTL_SECONDS` (default 3600s) by a background loop.

---

## 2. Tool Dispatch

```
dispatch(tool_name, args, session_id, user)
     │
     ├── tool in _SUBPROCESS_TOOLS?  {"memify", "visualize_graph"}
     │        │
     │        YES ─► run_subprocess_tool()
     │               │
     │               ├── asyncio.run_in_executor(thread_pool, docker_exec)
     │               │   docker exec cognee_mcp_server python3 scripts/<tool>.py
     │               │
     │               └── (streaming mode) heartbeat pings every 5s while running
     │
     └── NO ─► proxy.call(method="tools/call", params={name, arguments})
               │
               ├── circuit breaker open? → raise MCPError immediately
               │
               └── ensure_connected()
                   │
                   ├── already connected → send request
                   └── disconnected → exponential backoff reconnect
                                      5s → 10s → 20s → 40s → 60s (cap)
                                      max 10 attempts, then raise
```

---

## 3. Write Path — `cognify`

```
Agent POST /mcp  tools/call  name=cognify  arguments={data: "..."}
  │
  ▼
MCP Wrapper: dispatch → proxy.call()
  │
  ▼
Cognee MCP POST /mcp  tools/call  cognify
  │
  ▼ Cognee internals (async pipeline)
  ├─ chunk text
  ├─ LLM entity extraction (OpenAI gpt-4o)
  ├─ generate 1536D embeddings
  ├─ write to Postgres (cognee_db — metadata + ledger)
  ├─ write vectors to LanceDB
  └─ write entity/relationship nodes to Neo4j
       (Entity)-[RELATES_TO]->(Entity)

Response: { content: [{ type: "text", text: "cognified." }] }
Typical latency: 30s
```

---

## 4. Read Path — `search`

```
Agent POST /mcp  tools/call  name=search
  arguments={ search_query: "...", search_type: "GRAPH_COMPLETION" }
  │
  ▼
MCP Wrapper dispatch → proxy.call()
  │
  ▼
Cognee MCP  search pipeline
  │
  ├─ embed query → 1536D vector (OpenAI)
  ├─ vector similarity in LanceDB → top-K candidate entities
  ├─ graph traversal in Neo4j (Bolt)
  │    MATCH (a)-[r]->(b) WHERE a.id IN [candidates]
  └─ LLM synthesis (gpt-4o) → natural-language answer

Response: { content: [{ type: "text", text: "<synthesized answer>" }] }
Typical latency: 5s
```

### Search Types

| Type | Backends used | LLM? |
|------|--------------|------|
| `GRAPH_COMPLETION` | LanceDB + Neo4j | Yes |
| `GRAPH_COMPLETION_COT` | LanceDB + Neo4j | Yes (chain-of-thought) |
| `GRAPH_COMPLETION_CONTEXT_EXTENSION` | LanceDB + Neo4j | Yes (follow-up rounds) |
| `GRAPH_SUMMARY_COMPLETION` | LanceDB + Neo4j + summaries | Yes |
| `TEMPORAL` | Neo4j Event nodes filtered by timestamp | Yes |
| `CYPHER` | Neo4j direct | Yes (generates Cypher) |
| `NATURAL_LANGUAGE` | Neo4j | Yes (NL→query) |
| `RAG_COMPLETION` | LanceDB only | Yes |
| `SUMMARIES` | Summaries store | No |
| `CHUNKS` | LanceDB only | No |
| `FEELING_LUCKY` | auto-selected | Maybe |

---

## 5. Temporal Enrichment Path — `memify`

```
Agent POST /mcp  tools/call  name=memify  arguments={dataset: "main_dataset"}
  │
  ▼
MCP Wrapper dispatch → run_subprocess_tool()
  │
  ├─ asyncio.run_in_executor → thread pool
  │   docker exec cognee_mcp_server python3 scripts/memify.py
  │
  │  (streaming mode: heartbeat ping every 5s)
  │   notifications/progress  { message: "memify running..." }
  │
  └─ LLM reads DocumentChunk nodes → extracts Event nodes with timestamps
     writes Event nodes to Neo4j
     enables TEMPORAL search type

Typical latency: 60–180s
```

---

## 6. Graph Visualization Path — `visualize_graph`

```
Agent POST /mcp  tools/call  name=visualize_graph
  │
  ▼
MCP Wrapper dispatch → run_subprocess_tool()
  │
  ├─ docker exec cognee_mcp_server python3 scripts/visualize_neo4j_graph.py
  │   Direct Cypher: MATCH (a)-[r]->(b) RETURN a,r,b
  │   Hides orphan nodes (no edges)
  │   Writes interactive HTML with legend panel
  │
  └─ Output written to /graph/cognee_graph.html
     (graph_output volume shared between mcp-wrapper and web)

Typical latency: 15s
```

---

## 7. Circuit Breaker State Machine

```
                  failure count < threshold
  CLOSED ─────────────────────────────────► CLOSED
    │                                          │
    │ threshold consecutive failures           │ success resets count
    ▼                                          │
  OPEN ──────────────────────────────────────►─┘
    │                                     timeout elapsed
    │                                     → next call allowed (half-open)
    └──────────────────────────────────────────► if success → CLOSED
                                                 if failure → OPEN again
```

Config: `CIRCUIT_BREAKER_THRESHOLD=3`, `CIRCUIT_BREAKER_TIMEOUT=60`

---

## 8. FastAPI Web Layer — `/api/v1/memory/*`

The `web` service provides a higher-level REST API over the same Cognee MCP backend (direct HTTP, not via MCP Wrapper).

### Endpoint → Service → Repository → MCP Tool Mapping

| API Endpoint | Service Method | MCP Tool |
|---|---|---|
| `POST /api/v1/memory/record` | `record_agent_memory()` | `save_interaction` + `cognify` |
| `POST /api/v1/memory/recall` | `recall_context()` | `search` (with circuit breaker fallback) |
| `GET /api/v1/memory/data` | `get_data_inventory()` | `list_data` |
| `DELETE /api/v1/memory/data` | `delete_memory_data()` | `delete` |
| `DELETE /api/v1/memory/prune` | `prune_all_memory()` | `prune` |
| `GET /api/v1/memory/cognify/status` | `get_cognify_status()` | `cognify_status` |

### Recall circuit breaker (3-tier degradation)

```
recall_context()
  │
  ├── try GRAPH_COMPLETION (LLM + graph)
  │       success → ContextEnvelope(is_degraded=False, method="GRAPH_COMPLETION")
  │       failure ↓
  ├── try CHUNKS (vector only, no LLM)
  │       success → ContextEnvelope(is_degraded=True, method="CHUNKS_FALLBACK")
  │       failure ↓
  └── return ContextEnvelope(is_degraded=True, method="NONE_FAILED", context_data=[])
```

---

## 9. Environment Variables Affecting Data Flow

```env
# MCP Wrapper → Cognee MCP connection
MCP_SERVER_URL=http://cognee-mcp:8000       # internal Docker DNS
MCP_HOST_HEADER=localhost:8001              # Host header override (fixes 421)
MCP_TIMEOUT=300

# Session management
MCP_WRAPPER_SESSION_TTL=3600
MCP_WRAPPER_STRICT_SESSIONS=false           # true = reject calls without session

# Resilience
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT=60
MCP_WRAPPER_RECONNECT_DELAY=5
MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS=10

# Streaming
MCP_WRAPPER_STREAMING=false                 # true = incremental SSE

# Subprocess tools
COGNEE_CONTAINER_NAME=cognee_mcp_server
GRAPH_OUTPUT_PATH=/graph/cognee_graph.html

# Cognee MCP backends (inside cognee-mcp container)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
DB_PROVIDER=postgres    DB_HOST=db    DB_NAME=cognee_db
GRAPH_DATABASE_PROVIDER=neo4j    GRAPH_DATABASE_URL=bolt://neo4j:7687
VECTOR_DB_PROVIDER=lancedb
ENABLE_BACKEND_ACCESS_CONTROL=False
ACCEPT_LOCAL_FILE_PATH=True
```

---

## 10. Complete Request/Response Table

| # | Component | In | Out |
|---|-----------|---|-----|
| 1 | External Agent | HTTP POST /mcp | JSON-RPC request |
| 2 | MCP Wrapper session layer | request | validated session |
| 3 | MCP Wrapper dispatch | tool_name + args | route decision |
| 4a | Proxy path | JSON-RPC to Cognee MCP :8000 | JSON-RPC response |
| 4b | Subprocess path | `docker exec` | stdout string |
| 5 | Cognee MCP | tool call | DB ops + LLM |
| 6 | Neo4j / Postgres / LanceDB | Bolt/SQL/file | query results |
| 7 | MCP Wrapper SSE | result text | `data: {...}\n\n` |
| 8 | External Agent | SSE event | content[0].text |

---

## 11. Migration Notes — v2.0 → v3.0 (2026-04-19)

| Area | v2.0 | v3.0 |
|------|------|------|
| Agent entry point | Direct Cognee MCP :8001 | MCP Wrapper :8002 |
| Tool count | 7 (Cognee native) | 10 (+ memify, persist_sessions, improve_answer) |
| Session management | None | `mcp-session-id` UUID, TTL eviction |
| Resilience | App-level circuit breaker | Wrapper circuit breaker + exponential backoff |
| Streaming | No | Optional SSE (`MCP_WRAPPER_STREAMING=true`) |
| Graph visualization | `docker cp` to host path | Shared `graph_output` volume |
| Tool discovery | None | `GET /tools` with category + latency metadata |
