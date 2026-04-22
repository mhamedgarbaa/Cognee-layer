# Cognee Memory MCP Stack — Complete Project Documentation

> Version 3.0 · Graph-backed semantic memory microservice for AI agents

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Services & Docker Compose](#4-services--docker-compose)
5. [MCP Wrapper (Primary Gateway)](#5-mcp-wrapper-primary-gateway)
6. [FastAPI Web Service](#6-fastapi-web-service)
7. [Agent Browser UI](#7-agent-browser-ui)
8. [Data Layer & Persistence](#8-data-layer--persistence)
9. [Configuration & Environment Variables](#9-configuration--environment-variables)
10. [MCP Tools Reference](#10-mcp-tools-reference)
11. [LLM & Embedding Providers](#11-llm--embedding-providers)
12. [API Reference](#12-api-reference)
13. [Testing](#13-testing)
14. [Deployment & Quick Start](#14-deployment--quick-start)
15. [Known Issues & Resolutions](#15-known-issues--resolutions)

---

## 1. Project Overview

**Cognee Memory MCP Stack** is a self-contained, graph-backed semantic memory microservice designed to give AI agents persistent, queryable memory through the **Model Context Protocol (MCP)**.

### What it does

- **Ingests** arbitrary text into a Neo4j knowledge graph (entity extraction, relationship mapping, temporal enrichment)
- **Searches** that graph using 11 configurable search strategies (graph completion, RAG, Cypher, temporal, etc.)
- **Exposes** all memory operations as 10 MCP tools via a single HTTP JSON-RPC 2.0 endpoint
- **Connects** to any MCP-compatible AI agent (Claude, LangChain, custom clients)

### Key capabilities

| Capability | Implementation |
|-----------|---------------|
| Knowledge graph | Neo4j 5 + APOC plugin |
| Vector search | LanceDB (file-backed, inside Docker volume) |
| Relational metadata | PostgreSQL 15 |
| LLM inference | OpenAI GPT-4o / Azure OpenAI / Ollama |
| Embeddings | text-embedding-3-small (1536-dim) or nomic-embed-text (768-dim) |
| MCP gateway | `mcp_wrapper.py` (HTTP JSON-RPC 2.0) |
| Browser UI | `agent_server.py` (Groq Llama-4 chat loop) |
| Resilience | Circuit breaker + exponential backoff |

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                 EXTERNAL AGENTS / CLIENTS                            │
│         (Claude, LangChain, custom HTTP clients)                     │
└────┬─────────────────────────────────────────────────────────────────┘
     │  HTTP JSON-RPC 2.0
     ▼
┌──────────────────────────────────────────────────────────────────────┐
│           MCP WRAPPER  :8002  (mcp_wrapper.py)                       │
│                                                                      │
│  GET  /tools   → tool catalogue + session handshake guide            │
│  GET  /health  → wrapper + circuit breaker status                    │
│  POST /mcp     → initialize / tools/list / tools/call               │
│                                                                      │
│  ┌─────────────────────┐  ┌───────────────────────────────────────┐  │
│  │ Session Manager     │  │ CogneeMCPProxy                        │  │
│  │  UUID per agent     │  │  Circuit breaker: 3 failures → open   │  │
│  │  TTL-based eviction │  │  Backoff: 5s → 60s (max 10 retries)  │  │
│  └─────────────────────┘  └───────────────────────────────────────┘  │
└────┬─────────────────────────────┬────────────────────────────────────┘
     │ JSON-RPC proxy (8 tools)    │ docker exec (2 tools: memify, visualize_graph)
     ▼                             ▼
┌──────────────────┐      ┌─────────────────────────┐
│  COGNEE MCP      │      │  cognee_mcp_server       │
│  :8001 (host)    │      │  container               │
│  :8000 (internal)│      └─────────────────────────┘
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  COGNEE CORE ENGINE                                 │
│                                                     │
│  PostgreSQL 15    Neo4j 5          LanceDB          │
│  (metadata)       (knowledge graph) (vectors)       │
│                                                     │
│  OpenAI / Azure OpenAI / Ollama  (LLM + embeddings) │
└─────────────────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│  FASTAPI REST  :8000  (app.py)           │
│  /api/v1/memory/*  higher-level REST API │
│  Calls MCP Wrapper internally            │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│  AGENT UI  :8080  (agent_server.py)      │
│  Browser chat (Groq Llama-4)             │
│  Document cognification panel            │
└──────────────────────────────────────────┘
```

### Request flow — External agent calling `search`

```
Agent ──POST /mcp──► MCP Wrapper
  (session check) ──► CogneeMCPProxy
    (circuit breaker OK) ──► Cognee MCP :8001
      (HTTP tool call) ──► Cognee Core Engine
        (LanceDB cosine search + Neo4j graph traversal + OpenAI synthesis)
          ──► result ──► back through chain ──► Agent
```

---

## 3. Directory Structure

```
cognee-layer/
│
├── app.py                          # FastAPI application factory & startup
├── mcp_wrapper.py                  # MCP unified gateway (primary ⭐)
├── agent_server.py                 # Browser-based agent chat UI (:8080)
│
├── api/                            # FastAPI routers & Pydantic schemas
│   ├── middlewares/
│   │   ├── auth_middleware.py
│   │   ├── error_handling_middleware.py
│   │   └── logging_middleware.py
│   ├── routers/
│   │   ├── v1/
│   │   │   ├── memory_router.py    # /api/v1/memory/* endpoints
│   │   │   └── workspace_router.py
│   │   └── v2/
│   │       └── workspace_router.py
│   └── schemas/
│       ├── memory_schema.py        # Request/response Pydantic models
│       └── workspace_schema.py
│
├── core/                           # Constants, enums, external clients
│   ├── constants.py
│   ├── enums.py
│   └── external_clients/
│       └── service_a_client.py
│
├── data/                           # Data access layer
│   ├── models/
│   │   └── workspace_model.py      # SQLAlchemy ORM models
│   └── repositories/
│       ├── cognee_repository.py    # MCP HTTP client wrapper
│       └── workspace_repository.py
│
├── service/                        # Business logic layer
│   ├── business_models.py          # Domain Pydantic models
│   ├── memory_service.py           # record / recall / prune (circuit breaker)
│   └── workspace_service.py
│
├── configuration/                  # Settings & infrastructure setup
│   ├── database.py                 # SQLAlchemy async engine + session factory
│   ├── logging_setup.py            # JSON structured logging
│   └── settings.py                 # Pydantic BaseSettings (app, DB, Cognee)
│
├── ontologies/
│   └── bpifrance_ontology.ttl      # RDF/OWL ontology for entity types
│
├── scripts/
│   ├── mcp_probe.py                # Manual MCP endpoint tester
│   ├── run_memify.py               # Temporal enrichment helper
│   ├── test_streaming.py           # SSE streaming smoke test
│   └── visualize_neo4j_graph.py    # Neo4j → interactive HTML renderer
│
├── tests/
│   ├── test_stack_integration.py   # Full-stack smoke tests
│   └── unit_tests/
│
├── data/data_bpi/                  # BPI France test data (mounted read-only)
│
├── docker-compose.yml              # 5-service orchestration
├── Dockerfile                      # FastAPI web service (multi-stage)
├── Dockerfile.wrapper              # MCP Wrapper service
├── requirements.txt                # Web service dependencies
├── requirements-wrapper.txt        # MCP Wrapper dependencies
├── .env.example                    # Environment template
└── Jenkinsfile                     # CI/CD pipeline definition
```

---

## 4. Services & Docker Compose

The stack runs as **5 Docker services** on a shared bridge network `cognee_net`.

### Service dependency order

```
db (healthy)
    └──► neo4j (healthy)
             └──► cognee-mcp (healthy)
                       └──► mcp-wrapper
                                 └──► web
```

### Service details

#### `db` — PostgreSQL 15

| Property | Value |
|----------|-------|
| Image | `postgres:15` |
| Port | `5432` |
| Databases | `workspace_db` (app data), `cognee_db` (Cognee metadata) |
| Volume | `pgdata:/var/lib/postgresql/data` |
| Init script | `scripts/postgres-init/` — creates `cognee_db` on first boot |

---

#### `neo4j` — Neo4j 5

| Property | Value |
|----------|-------|
| Image | `neo4j:5` |
| Ports | `7474` (HTTP browser), `7687` (Bolt) |
| Plugins | APOC (graph algorithms) |
| Auth | `NEO4J_USER` / `NEO4J_PASSWORD` |
| Volumes | `neo4j_data`, `neo4j_logs`, `neo4j_plugins` |

---

#### `cognee-mcp` — Cognee MCP Server

| Property | Value |
|----------|-------|
| Image | `cognee/cognee-mcp:main` |
| Port | `8001` (host) / `8000` (internal) |
| Health check | Python `urllib.request` to `/health` |
| Mounted volumes | `ontologies/` (read-only), `data/data_bpi/` (read-only) |
| Storage volumes | `cognee_data` (graph data), `cognee_storage` (LanceDB) |

Key environment variables passed to this service:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=<your key>
GRAPH_DATABASE_PROVIDER=neo4j
GRAPH_DATABASE_URL=bolt://neo4j:7687
VECTOR_DB_PROVIDER=lancedb
ONTOLOGY_FILE_PATH=/ontologies/bpifrance_ontology.ttl
ENABLE_BACKEND_ACCESS_CONTROL=False
REQUIRE_AUTHENTICATION=False
```

---

#### `mcp-wrapper` — MCP Unified Gateway

| Property | Value |
|----------|-------|
| Build | `Dockerfile.wrapper` |
| Port | `8002` |
| Docker socket | Mounted read-only (for `docker exec` tools) |
| Shared volume | `graph_output:/graph` |

---

#### `web` — FastAPI Agent Service

| Property | Value |
|----------|-------|
| Build | `Dockerfile` (multi-stage) |
| Port | `8000` |
| Command | `uvicorn app:app --reload` |
| Shared volume | `graph_output:/graph` (serves Neo4j visualization HTML) |

---

### Docker volumes

| Volume | Purpose |
|--------|---------|
| `pgdata` | PostgreSQL persistent data |
| `cognee_data` | Cognee graph/metadata files |
| `cognee_storage` | LanceDB vector storage |
| `neo4j_data` | Neo4j graph store |
| `neo4j_logs` | Neo4j logs |
| `neo4j_plugins` | Neo4j plugin JARs |
| `graph_output` | Shared HTML graph output (web + mcp-wrapper) |

---

## 5. MCP Wrapper (Primary Gateway)

**File**: [mcp_wrapper.py](mcp_wrapper.py)

The MCP Wrapper is the single point of entry for all external agents. It implements the full MCP session lifecycle over HTTP JSON-RPC 2.0.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp` | POST | MCP JSON-RPC handler |
| `/health` | GET | Wrapper + circuit breaker status |
| `/tools` | GET | All 10 tools + session handshake guide |

### Session lifecycle

```
1. POST /mcp  { "method": "initialize" }
   ← Response: { result: {...}, mcp-session-id: "<uuid>" }

2. POST /mcp  { "method": "tools/list" }
   Header: mcp-session-id: <uuid>
   ← Response: { result: { tools: [...10 tools...] } }

3. POST /mcp  { "method": "tools/call", "params": { "name": "search", "arguments": {...} } }
   Header: mcp-session-id: <uuid>
   ← Response: { result: { content: [...] } }
```

### Tool routing

| Route | Tools | Mechanism |
|-------|-------|-----------|
| **Proxy** | `cognify`, `save_interaction`, `search`, `list_data`, `cognify_status`, `prune`, `persist_sessions`, `improve_answer` | HTTP JSON-RPC forwarded to Cognee MCP |
| **Docker exec** | `memify`, `visualize_graph` | `docker exec` inside `cognee_mcp_server` container |

### Resilience components

#### Circuit breaker

```
State machine: CLOSED ──(3 failures)──► OPEN ──(60s)──► HALF_OPEN ──(success)──► CLOSED
                                                                      ──(fail)──► OPEN
```

Configurable via:
- `CIRCUIT_BREAKER_THRESHOLD=3`
- `CIRCUIT_BREAKER_TIMEOUT=60`

#### Exponential backoff reconnect

- Initial delay: `MCP_WRAPPER_RECONNECT_DELAY=5` seconds
- Max delay: 60 seconds (doubles each attempt)
- Max attempts: `MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS=10`

#### SSE streaming (optional)

Set `MCP_WRAPPER_STREAMING=true` to enable incremental SSE forwarding. Docker exec tools emit `notifications/progress` heartbeats while running.

---

## 6. FastAPI Web Service

**File**: [app.py](app.py)

The FastAPI web service provides a higher-level REST API on top of the MCP layer. It is suited for server-side agents that prefer REST over JSON-RPC.

### Application setup ([app.py](app.py))

- Creates FastAPI app with CORS, request-ID injection, and structured logging
- Mounts `/api/v1/memory/*` and `/api/v1/workspaces/*` routers
- Serves Neo4j visualization HTML at `/graph/`
- Startup event: `init_db()` (creates SQLAlchemy tables)

### Layered architecture

```
HTTP Request
    │
    ▼
api/routers/v1/memory_router.py       ← validates schemas, calls service
    │
    ▼
service/memory_service.py             ← business logic, circuit breaker, fallback
    │
    ▼
data/repositories/cognee_repository.py ← MCP HTTP client, session management
    │
    ▼
Cognee MCP :8001 / MCP Wrapper :8002
```

### Memory service ([service/memory_service.py](service/memory_service.py))

**`record_agent_memory(user_id, fact)`**
1. Calls `save_interaction` (STM, fast ~2s)
2. Triggers `cognify` asynchronously (LTM, deep graph processing ~30s)

**`recall_context(user_id, query)`** — 3-tier fallback:
1. `GRAPH_COMPLETION` (primary) — full graph traversal + LLM synthesis
2. `CHUNKS` (fallback) — raw vector search
3. Empty context (if both fail)

### Cognee repository ([data/repositories/cognee_repository.py](data/repositories/cognee_repository.py))

- `httpx.AsyncClient` pool with persistent sessions
- MCP session initialization on first request
- Forwards `mcp-session-id` header
- `_call_mcp_tool(tool_name, arguments)` — generic tool call helper

---

## 7. Agent Browser UI

**File**: [agent_server.py](agent_server.py) · **Port**: 8080

A browser-based chat interface for interactive use. Not included in Docker Compose — run manually.

### Features

- **Left panel**: Chat interface powered by Groq Llama-4 (llama-4-scout-17b-16e-instruct)
- **Right panel**: Document paste area for direct cognification
- **Tool pills**: Real-time display of tool execution during agentic loop
- **Agentic loop**: Max 8 rounds of tool calls before returning final answer

### Startup

```bash
# Set GROQ_API_KEY in .env first
python agent_server.py
# Open http://localhost:8080
```

### Connects to

MCP Wrapper at `:8002` — uses all 10 tools identically to external agents.

---

## 8. Data Layer & Persistence

### PostgreSQL (`db` service)

**Purpose**: Relational metadata store

Two databases:
- `workspace_db` — App data (SQLAlchemy models, workspace tracking)
- `cognee_db` — Cognee internal metadata (dataset registry, pipeline state)

ORM setup in [configuration/database.py](configuration/database.py):
- Async engine: `postgresql+asyncpg`
- Session factory: `async_sessionmaker`
- `init_db()` creates all tables on startup

### Neo4j (`neo4j` service)

**Purpose**: Knowledge graph (primary memory store)

- All cognified text becomes nodes and relationships
- Entity types from `ontologies/bpifrance_ontology.ttl` (RDF/OWL)
- Direct Cypher queries via `visualize_neo4j_graph.py` for visualization
- Browser UI: `http://localhost:7474` (login with `NEO4J_USER` / `NEO4J_PASSWORD`)

### LanceDB (inside `cognee_data` volume)

**Purpose**: Vector embeddings store

- File-backed, runs inside the Cognee MCP container
- Embedding dimension: **1536** (OpenAI `text-embedding-3-small`) or **768** (Ollama `nomic-embed-text`)

> **Warning**: If you switch embedding providers (e.g., Ollama → OpenAI), you must wipe `cognee_data` and `cognee_storage` volumes and restart — dimension mismatch will cause silent failures.

---

## 9. Configuration & Environment Variables

**Template**: [.env.example](.env.example)

### PostgreSQL

```env
POSTGRES_USER=workspace_user
POSTGRES_PASSWORD=workspace_pass
POSTGRES_DB=workspace_db
COGNEE_DB_NAME=cognee_db
```

### Neo4j

```env
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j_pass
```

### LLM & Embeddings (OpenAI — default)

```env
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_ENDPOINT=https://api.openai.com/v1
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_ENDPOINT=https://api.openai.com/v1
EMBEDDING_DIMENSIONS=1536
```

### MCP Wrapper

```env
MCP_WRAPPER_PORT=8002
MCP_WRAPPER_LOG_LEVEL=INFO
MCP_WRAPPER_API_KEY=              # empty = auth disabled
MCP_WRAPPER_STRICT_SESSIONS=false
MCP_WRAPPER_SESSION_TTL=3600
MCP_WRAPPER_STREAMING=false
COGNEE_CONTAINER_NAME=cognee_mcp_server
GRAPH_OUTPUT_PATH=/graph/cognee_graph.html
```

### Circuit breaker

```env
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT=60
MCP_WRAPPER_RECONNECT_DELAY=5
MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS=10
```

### Application

```env
APP_ENV=development
LOG_LEVEL=INFO
```

### Pydantic settings ([configuration/settings.py](configuration/settings.py))

| Class | Fields | Purpose |
|-------|--------|---------|
| `AppConfig` | title, version, api_prefix, cors_origins | FastAPI metadata |
| `DatabaseSettings` | host, port, user, password, db_name | PostgreSQL connection |
| `CogneeSettings` | mcp_endpoint, protocol, host_header, timeout | MCP client config |

---

## 10. MCP Tools Reference

All tools are exposed via `POST /mcp` with `method: "tools/call"`.

| Tool | Category | Typical Latency | Description |
|------|----------|----------------|-------------|
| `cognify` | write | ~30s | Deep-processes text: extracts entities, relationships, embeddings, stores in Neo4j + LanceDB |
| `save_interaction` | write | ~2s | Quickly saves a short note (routes to cognify internally) |
| `search` | read | ~5s | Queries the graph; accepts `search_query` (string) and `search_type` (enum, 11 options) |
| `list_data` | read | ~2s | Lists all datasets and data items stored in Cognee |
| `cognify_status` | read | ~1s | Returns whether the cognify pipeline is idle or actively processing |
| `prune` | admin | ~5s | **Irreversible** — wipes ALL stored knowledge (graph, vectors, metadata) |
| `memify` | write | ~120s | Extracts `Event` nodes from raw text chunks (temporal enrichment via docker exec) |
| `visualize_graph` | read | ~15s | Renders Neo4j as an interactive HTML file (served at `/graph/`) |
| `persist_sessions` | write | ~10s | Stores conversation transcripts as graph nodes |
| `improve_answer` | write | ~10s | Re-queries with chain-of-thought reasoning and stores the correction |

### `search` — search types

The `search_type` parameter accepts:
`GRAPH_COMPLETION`, `RAG`, `CHUNKS`, `SUMMARIES`, `CYPHER`, `TEMPORAL`, `KEYWORD`, `ENTITY`, `RELATIONSHIP`, `COMMUNITY`, `NONE_FAILED`

---

## 11. LLM & Embedding Providers

### OpenAI (default)

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_ENDPOINT=https://api.openai.com/v1
LLM_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

### Azure OpenAI

```env
LLM_PROVIDER=openai
LLM_MODEL=azure/<your-deployment-name>
LLM_ENDPOINT=https://<your-resource>.openai.azure.com/
LLM_API_KEY=<azure-key>
```

### Local Ollama

```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:latest
LLM_ENDPOINT=http://host.docker.internal:11434/v1
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_ENDPOINT=http://host.docker.internal:11434/api/embeddings
EMBEDDING_DIMENSIONS=768
HUGGINGFACE_TOKENIZER=Salesforce/SFR-Embedding-Mistral
```

> Ollama must be running on the host. `host.docker.internal` resolves to the host IP inside Docker.

---

## 12. API Reference

### MCP Wrapper `:8002`

#### `GET /health`

Returns wrapper status and circuit breaker state.

```json
{
  "status": "ok",
  "circuit_breaker": "CLOSED",
  "upstream": "http://cognee-mcp:8000",
  "active_sessions": 3
}
```

#### `GET /tools`

Returns all 10 tools with JSON Schema input definitions and session handshake instructions.

#### `POST /mcp`

Standard MCP JSON-RPC 2.0. Three supported methods:

**initialize**
```json
{ "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} }
```

**tools/list**
```json
{ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {} }
```
Header: `mcp-session-id: <uuid>`

**tools/call**
```json
{
  "jsonrpc": "2.0", "id": 3,
  "method": "tools/call",
  "params": { "name": "search", "arguments": { "search_query": "quarterly results", "search_type": "GRAPH_COMPLETION" } }
}
```
Header: `mcp-session-id: <uuid>`

---

### FastAPI Web Service `:8000`

#### `POST /api/v1/memory/record`

Saves a fact to memory.

```json
{ "user_id": "agent-42", "fact": "The quarterly revenue was $4.2M." }
```

#### `POST /api/v1/memory/recall`

Queries memory with 3-tier fallback.

```json
{ "user_id": "agent-42", "query": "What was the quarterly revenue?" }
```

#### `GET /api/v1/memory/data`

Lists all stored datasets.

#### `DELETE /api/v1/memory/prune`

Wipes all knowledge. Irreversible.

#### `GET /api/v1/memory/cognify/status`

Returns current cognify pipeline state.

#### `GET /graph/`

Serves the latest Neo4j interactive visualization HTML (generated by `visualize_graph` tool).

---

## 13. Testing

### Integration tests

**File**: [tests/test_stack_integration.py](tests/test_stack_integration.py)

Smoke tests for the full 5-service stack. Run against a live Docker Compose environment.

```bash
# Default (localhost ports)
pytest tests/test_stack_integration.py -v

# Custom targets
BASE_URL=http://localhost:8000 \
MCP_URL=http://localhost:8001 \
pytest tests/test_stack_integration.py -v
```

Environment variables:
| Variable | Default | Purpose |
|----------|---------|---------|
| `BASE_URL` | `http://localhost:8000` | FastAPI web service |
| `MCP_URL` | `http://cognee-mcp:8000` | Cognee MCP server |
| `TEST_TIMEOUT` | `30` | HTTP timeout in seconds |
| `MCP_HOST_HEADER` | `localhost:8001` | Host validation header |

Test categories:
1. Health checks for all 5 services
2. MCP session lifecycle (initialize → tools/list → tools/call)
3. FastAPI memory endpoints (record, recall, list_data, prune)
4. Full chain integration (agent → wrapper → Cognee MCP → databases)

### Streaming smoke test

```bash
MCP_WRAPPER_STREAMING=true python scripts/test_streaming.py
```

Verifies SSE heartbeat pings and final result delivery.

---

## 14. Deployment & Quick Start

### Prerequisites

- Docker & Docker Compose v2
- OpenAI API key (or Azure / Ollama alternative)
- 8 GB RAM minimum (Neo4j + Postgres + Cognee)

### Step-by-step

```bash
# 1. Clone and configure
git clone <repo-url>
cd cognee-layer
cp .env.example .env
# Edit .env — set OPENAI_API_KEY, passwords, etc.

# 2. Start the stack
docker compose up -d

# 3. Wait for all services to become healthy (~60s for cognee-mcp)
docker compose ps

# 4. Verify endpoints
curl http://localhost:8002/health   # MCP Wrapper
curl http://localhost:8000/health   # FastAPI
curl http://localhost:8002/tools    # Tool catalogue

# 5. Optional: Start browser UI
# (requires GROQ_API_KEY in .env)
python agent_server.py
# Open http://localhost:8080
```

### Rebuilding services

```bash
# Rebuild after code changes
docker compose build mcp-wrapper
docker compose build web
docker compose up -d mcp-wrapper web
```

### Resetting Cognee knowledge (wipe graph + vectors)

```bash
# Via API
curl -X DELETE http://localhost:8000/api/v1/memory/prune

# Or wipe Docker volumes directly (full reset)
docker compose down -v
docker compose up -d
```

### Switching embedding providers

If you change `EMBEDDING_DIMENSIONS` (e.g., 768 → 1536), you **must** wipe the Cognee volumes:

```bash
docker compose down
docker volume rm cognee-layer_cognee_data cognee-layer_cognee_storage
docker compose up -d
```

### CI/CD

The `Jenkinsfile` defines a build pipeline. Stages: lint → test → build images → push to registry.

---

## 15. Known Issues & Resolutions

| Issue | Root Cause | Resolution |
|-------|-----------|------------|
| `421 Invalid Host header` from Cognee MCP | Cognee validates the `Host` header strictly against its configured port | Set `MCP_HOST_HEADER=localhost:8001` in all proxy requests |
| Graph visualization shows isolated nodes | `get_graph_data()` only returns connected components | Use direct Cypher: `MATCH (a)-[r]->(b) RETURN a, r, b` |
| `mcp-session-id` header dropped in SSE | Not forwarded in `StreamingResponse` by default | Pass `headers={"mcp-session-id": sid}` explicitly to `StreamingResponse()` |
| Startup blocks uvicorn | Blocking reconnect loop runs before event loop is ready | Use `asyncio.wait_for(..., timeout=5)` instead of a full blocking retry loop |
| `save_interaction` fails with `RuleSet` error | Cognee internal schema validation bug on short strings | Route to `cognify` instead — same interface, more robust |
| Embedding dimension mismatch after provider switch | LanceDB index built with 768-dim cannot accept 1536-dim vectors | Wipe `cognee_data` and `cognee_storage` volumes, then restart |
| `memify` or `visualize_graph` times out | These run `docker exec` inside the container, which can be slow | Increase `MCP_WRAPPER_SESSION_TTL` and HTTP client timeout |

---

## Dependencies Summary

### [requirements.txt](requirements.txt) — Web service

```
fastapi==0.115.0       # REST framework
uvicorn==0.30.0        # ASGI server
SQLAlchemy==2.0.34     # ORM
asyncpg==0.29.0        # Postgres async driver
pydantic==2.8.2        # Validation
pydantic-settings==2.3.4
python-dotenv==1.0.1
httpx==0.27.0          # Async HTTP client
```

### [requirements-wrapper.txt](requirements-wrapper.txt) — MCP Wrapper

```
fastapi==0.115.0
uvicorn==0.30.0
httpx==0.27.0
python-dotenv==1.0.1
docker==7.1.0          # Docker Python SDK (for docker exec)
neo4j==5.23.0          # Direct Cypher queries
pyvis==0.3.2           # Interactive graph rendering
```

---

*Generated: 2026-04-21*
