# Cognee Memory Subsystem — Configuration Guide

> **Project**: BPI France Knowledge Base Agent  
> **Architecture**: Cognee-only temporal memory with MCP protocol  
> **Last Updated**: 2026-04-14

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [System Components](#2-system-components)
3. [Docker Compose Setup](#3-docker-compose-setup)
4. [Environment Variables Reference](#4-environment-variables-reference)
5. [LLM Configuration (Critical)](#5-llm-configuration-critical)
6. [Embedding Configuration](#6-embedding-configuration)
7. [MCP Protocol Configuration](#7-mcp-protocol-configuration)
8. [Database Configuration](#8-database-configuration)
9. [Data Flow Walkthrough](#9-data-flow-walkthrough)
10. [Resilience & Graceful Degradation](#10-resilience--graceful-degradation)
11. [Known Pitfalls & Troubleshooting](#11-known-pitfalls--troubleshooting)
12. [Operational Checklist](#12-operational-checklist)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Cognee Memory Subsystem                        │
│                                                                      │
│  ┌─────────────────┐                    ┌────────────────────┐       │
│  │ Cognee Fetcher   │                    │ Connector Gateway  │       │
│  │ (Reads/Queries)  │                    │ (Writes/Updates)   │       │
│  └────────┬─────────┘                    └────────┬───────────┘       │
│           │ HTTP/SSE                              │ HTTP/SSE         │
│           └──────────────┬────────────────────────┘                  │
│                          ▼                                           │
│              ┌───────────────────────┐                               │
│              │   Cognee MCP Server   │                               │
│              │   (API Boundary)      │                               │
│              └───────────┬───────────┘                               │
│                          ▼                                           │
│              ┌───────────────────────┐                               │
│              │  Circuit Breaker &    │                               │
│              │  Health Router        │                               │
│              └──┬────────────────┬───┘                               │
│                 │                │                                    │
│      LTM Down/  │                │  Write Fast                       │
│      Timeout    │                │                                   │
│                 ▼                ▼                                    │
│  ┌──────────────────┐  ┌──────────────────────────┐                 │
│  │ Graceful          │  │  Short-Term Memory (STM) │                 │
│  │ Degradation/      │  │  save_interaction()      │                 │
│  │ Fallback to CHUNKS│  │  NoSQL / Fast Append     │                 │
│  └──────────────────┘  └──────────┬───────────────┘                 │
│                                   │ Append                           │
│                                   ▼                                  │
│                     ┌──────────────────────────┐                    │
│                     │  Relational DB (SQLite)   │                    │
│                     │  Raw Interaction History  │                    │
│                     └──────────┬───────────────┘                    │
│                                │ Read Unprocessed Logs               │
│                                ▼                                     │
│              ┌─────────────────────────────────┐                    │
│              │  Memory Consolidation Lifecycle  │                    │
│              │  Async Promotion (Watermark/     │                    │
│              │  Triggers on N interactions)     │                    │
│              └───────────┬─────────────────────┘                    │
│                          │ Execute                                   │
│                          ▼                                           │
│     ┌────────────────────────────────────────────┐                  │
│     │       cognify(temporal_cognify=True)        │                  │
│     │       Extracts Entities & Triples           │                  │
│     │                                              │                  │
│     │  Pipeline Steps:                             │                  │
│     │  1. classify_documents                       │                  │
│     │  2. extract_chunks_from_documents            │                  │
│     │  3. extract_graph_from_data (LLM)            │                  │
│     │  4. summarize_text (LLM)                     │                  │
│     │  5. add_data_points (embeddings)             │                  │
│     └────────────┬───────────────────────────────┘                  │
│                  │                                                    │
│   ┌──────────────┼──────────────────────────────┐                   │
│   │              ▼                              │                    │
│   │  Long-Term Memory (LTM)                     │                    │
│   │  search(query_type=GRAPH_COMPLETION)         │                    │
│   │                                              │                    │
│   │  ┌──────────┬──────────┬──────────┬────────┐│                    │
│   │  │ Traverse │ Write    │ Semantic │ Write  ││                    │
│   │  │ Events   │ Nodes/   │ Match    │ Embed- ││                    │
│   │  │          │ Edges    │          │ dings  ││                    │
│   │  └──────────┴──────────┴──────────┴────────┘│                    │
│   │                                              │                    │
│   │   Graph DB (Kuzu)        Vector DB (LanceDB)│                    │
│   │   Knowledge Graph        Embeddings          │                    │
│   │   Temporal Events                            │                    │
│   └──────────────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. System Components

| Component | Container | Image | Purpose |
|-----------|-----------|-------|---------|
| **Cognee MCP Server** | `cognee_mcp_server` | `cognee/cognee-mcp:main` | Memory Control Protocol server — handles save_interaction, cognify, search |
| **Workspace API** | `workspace_api` | Custom `Dockerfile` | FastAPI application — exposes `/api/v1/memory/record` and `/api/v1/memory/recall` |
| **PostgreSQL** | `workspace_db` | `postgres:15` | Workspace metadata storage (not used by Cognee internally) |
| **Ollama** | Host machine | N/A | Runs LLM and embedding models locally |

### Internal Cognee Storage (inside MCP container)

| Storage | Provider | Purpose |
|---------|----------|---------|
| **Relational DB** | SQLite (aiosqlite) | Interaction history, user records, dataset metadata |
| **Graph DB** | Kuzu | Knowledge graph — entities, relationships, temporal events |
| **Vector DB** | LanceDB | Embedding storage for semantic search |

---

## 3. Docker Compose Setup

### Container Networking

All containers communicate over a shared Docker bridge network (`cognee_net`).  
Ollama runs on the **host machine** and is reached via `host.docker.internal`.

```
┌─────────────────────────────────────────────┐
│              cognee_net (bridge)             │
│                                             │
│  workspace_api ◄──► cognee-mcp ◄──► db      │
│       :8000            :8000        :5432   │
│         │                │                  │
└─────────│────────────────│──────────────────┘
          │                │
          │   host.docker.internal:11434
          │                │
     ┌────▼────────────────▼────┐
     │       HOST MACHINE       │
     │    Ollama (:11434)       │
     └──────────────────────────┘
```

### Port Mapping

| Service | Container Port | Host Port | Notes |
|---------|---------------|-----------|-------|
| `cognee-mcp` | 8000 | **8001** | MCP server (exposed on 8001 to avoid conflict) |
| `web` (workspace_api) | 8000 | **8000** | Your FastAPI application |
| `db` | 5432 | 5432 | PostgreSQL |

> **Critical**: The `workspace_api` container connects to cognee-mcp via Docker DNS (`http://cognee-mcp:8000`), NOT via the host port 8001.

### Volumes

| Volume | Mountpoint | Purpose |
|--------|-----------|---------|
| `pgdata` | `/var/lib/postgresql/data` | PostgreSQL persistence |
| `cognee_data` | `/root/.cognee_system` | Cognee's SQLite, Kuzu, LanceDB data |

> **Warning**: The `cognee_data` volume persists across container recreations. If you encounter `UNIQUE constraint` errors on startup (e.g., `users.email`), it's because the default user already exists from a previous run. This is harmless.

---

## 4. Environment Variables Reference

### 4.1 Cognee MCP Server Variables

These are set in `docker-compose.yml` under the `cognee-mcp` service:

| Variable | Value | Description |
|----------|-------|-------------|
| `TRANSPORT_MODE` | `http` | MCP transport: `http` (recommended) or `sse` |
| `ALLOWED_HOSTS` | `localhost,cognee-mcp,...,*` | Hosts allowed to connect (use `*` in dev) |
| `LLM_PROVIDER` | `ollama` | LLM backend provider |
| `LLM_MODEL` | `qwen2.5:latest` | **Must be a model good at structured JSON output** |
| `LLM_ENDPOINT` | `http://host.docker.internal:11434/v1` | Ollama OpenAI-compatible endpoint |
| `LLM_API_KEY` | `ollama` | Dummy key (Ollama doesn't require auth) |
| `LLM_FORMAT` | `json` | Forces JSON output mode |
| `EMBEDDING_PROVIDER` | `ollama` | Embedding backend |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model |
| `EMBEDDING_ENDPOINT` | `http://host.docker.internal:11434/api/embed` | Ollama embedding endpoint |
| `EMBEDDING_DIMENSIONS` | `768` | Must match the model's output dimensions |
| `HUGGINGFACE_TOKENIZER` | `nomic-ai/nomic-embed-text-v1` | Tokenizer for chunking |
| `DB_PROVIDER` | `sqlite` | Internal relational storage |
| `VECTOR_DB_PROVIDER` | `lancedb` | Internal vector storage |
| `GRAPH_DATABASE_PROVIDER` | `kuzu` | Internal graph storage |

### 4.2 Workspace API Variables

These are set in `docker-compose.yml` under the `web` service and/or in `.env`:

| Variable | Value | Description |
|----------|-------|-------------|
| `MCP_SERVER_URL` | `http://cognee-mcp:8000` (Docker) / `http://localhost:8001` (host) | MCP server base URL |
| `MCP_PROTOCOL` | `http` | Communication protocol |
| `MCP_HOST_HEADER` | `localhost:8001` | Host header override (required if Cognee validates Host) |
| `MCP_TIMEOUT` | `300` | Request timeout in seconds (high because LLM is slow) |

### 4.3 Database Variables

| Variable | Value | Description |
|----------|-------|-------------|
| `DB_USER` | `workspace_user` | PostgreSQL username |
| `DB_PASSWORD` | `workspace_pass` | PostgreSQL password |
| `DB_HOST` | `db` | Docker service name |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `workspace_db` | Database name |
| `DB_POOL_SIZE` | `5` | Connection pool size |

---

## 5. LLM Configuration (Critical)

### Why LLM Choice Matters

Cognee uses the LLM for **three critical tasks**, each requiring reliable structured JSON output:

1. **Graph extraction** (`extract_graph_from_data`) — Extracts entities and relationships
2. **Rule generation** (`save_user_agent_interaction`) — Generates `RuleSet` Pydantic model via `instructor` library
3. **Text summarization** (`summarize_text`) — Summarizes document chunks

The **rule generation** step uses the `instructor` library with strict Pydantic validation. The LLM must produce JSON that exactly matches the `RuleSet` schema, including:
- `version`: must be an **integer** (not a string like `"0.1"`)
- `rules`: array with each rule having a required `text` field
- Top-level keys must be flat (not wrapped in `{"RuleSet": {...}}`)

### Recommended Models (Ollama)

| Model | JSON Compliance | Speed | VRAM | Recommendation |
|-------|----------------|-------|------|----------------|
| `qwen2.5:latest` (7B) | ✅ Good | Fast | ~5 GB | **Recommended** — best balance |
| `qwen2.5:14b` | ✅ Excellent | Medium | ~10 GB | Better accuracy if you have VRAM |
| `llama3.1:8b` | ✅ Good | Fast | ~5 GB | Good alternative |
| `mistral:latest` (7B) | ❌ Poor | Fast | ~5 GB | **Do NOT use** — fails RuleSet validation consistently |

### How to Switch Models

1. **Pull the model on host**: `ollama pull qwen2.5:latest`
2. **Update `.env`**: `LLM_MODEL="qwen2.5:latest"`
3. **Update `docker-compose.yml`**: `LLM_MODEL=${LLM_MODEL:-qwen2.5:latest}`
4. **Recreate container**: `docker compose up -d --force-recreate cognee-mcp`
5. **Verify**: `docker exec cognee_mcp_server env | findstr LLM_MODEL`

> **⚠️ Important**: `docker compose restart` does NOT re-read `.env` changes!  
> You MUST use `docker compose up -d --force-recreate` to apply new environment variables.

---

## 6. Embedding Configuration

### Ollama Embedding Setup

```bash
# Pull the embedding model
ollama pull nomic-embed-text
```

| Parameter | Value | Notes |
|-----------|-------|-------|
| `EMBEDDING_MODEL` | `nomic-embed-text` | 768-dimensional embeddings |
| `EMBEDDING_ENDPOINT` | `http://host.docker.internal:11434/api/embed` | Uses Ollama's native `/api/embed` (not `/v1`) |
| `EMBEDDING_DIMENSIONS` | `768` | Must match model output exactly |
| `HUGGINGFACE_TOKENIZER` | `nomic-ai/nomic-embed-text-v1` | Required for proper text chunking |

> **Note**: The embedding endpoint uses `/api/embed` (Ollama native), NOT `/v1/embeddings` (OpenAI-compatible). This is a common source of confusion.

---

## 7. MCP Protocol Configuration

### MCP Communication Flow

The workspace API communicates with Cognee MCP using the **JSON-RPC 2.0 over HTTP** protocol:

```
1. POST /mcp  →  initialize (get session ID)
2. POST /mcp  →  notifications/initialized
3. POST /mcp  →  tools/call (save_interaction, cognify, search)
```

### Session Management

- Each interaction creates a new MCP session
- Session ID is returned in the `mcp-session-id` response header
- All subsequent tool calls must include this header

### Available MCP Tools

| Tool | Arguments | Description |
|------|-----------|-------------|
| `save_interaction` | `user`, `data` | Fast append to STM (Short-Term Memory) |
| `cognify` | `user`, `data`, `temporal_cognify`, `run_in_background` | Promote to LTM (knowledge graph extraction) |
| `search` | `search_query`, `search_type`, `top_k` | Query the memory subsystem |

### Search Types

| Type | Description | Uses LLM? |
|------|-------------|-----------|
| `GRAPH_COMPLETION` | Synthesized temporal graph retrieval | Yes |
| `CHUNKS` | Raw semantic vector search (fallback) | No |

### Host Header Issue

Cognee MCP validates the `Host` HTTP header. When connecting container-to-container:
- Request goes to `http://cognee-mcp:8000/mcp` (Docker DNS)
- But Cognee expects `Host: localhost:8001` (the published port)
- Solution: Set `MCP_HOST_HEADER=localhost:8001` to override

---

## 8. Database Configuration

### PostgreSQL (Workspace Layer)

Used by the FastAPI workspace service for CRUD operations. **Not used by Cognee internally.**

```
postgresql+asyncpg://workspace_user:workspace_pass@db:5432/workspace_db
```

### Cognee Internal Storage

All inside the `cognee_data` Docker volume (`/root/.cognee_system`):

| Database | Provider | Content |
|----------|----------|---------|
| SQLite | aiosqlite | Users, datasets, interaction logs, data records |
| Kuzu | kuzu | Knowledge graph (entities, relationships, triples) |
| LanceDB | lancedb | Vector embeddings for semantic search |

> **These are not configurable** beyond provider selection. Cognee manages them internally.

---

## 9. Data Flow Walkthrough

### Write Path: `POST /api/v1/memory/record`

```
Client
  │
  ▼
workspace_api (FastAPI)
  │  MemoryService.record_agent_memory()
  │
  ├──► CogneeRepository.save_interaction()       [STM — fast path]
  │      │
  │      └──► MCP tools/call: save_interaction
  │             │
  │             ├── Stores raw interaction in SQLite
  │             └── Triggers async rule generation (via LLM)
  │
  └──► CogneeRepository.trigger_cognify()         [LTM — background]
         │
         └──► MCP tools/call: cognify
                │
                ├── Pipeline: classify → chunk → extract graph → summarize
                ├── Writes entities/relationships to Kuzu
                └── Writes embeddings to LanceDB
```

### Read Path: `POST /api/v1/memory/recall`

```
Client
  │
  ▼
workspace_api (FastAPI)
  │  MemoryService.recall_context()
  │
  ├──► [Primary] search(query_type=GRAPH_COMPLETION)   [60s timeout]
  │      Uses LLM to synthesize answer from knowledge graph
  │
  ├──► [Fallback] search(query_type=CHUNKS)            [20s timeout]
  │      Raw semantic search — bypasses LLM bottleneck
  │
  └──► [Final] Return empty context
         Agent falls back to immediate chat history
```

---

## 10. Resilience & Graceful Degradation

### Circuit Breaker Pattern

The `MemoryService` implements a 3-tier fallback:

| Tier | Method | Condition | Uses LLM? |
|------|--------|-----------|-----------|
| 1 | `GRAPH_COMPLETION` | Primary — full graph synthesis | Yes |
| 2 | `CHUNKS_FALLBACK` | If tier 1 times out or fails | No |
| 3 | `NONE_FAILED` | Total subsystem failure | No |

### Non-Critical Error Handling

The `save_interaction` step sometimes triggers a `RuleSet` validation error from Cognee's internal rule generator (via `instructor` library). This is caught and treated as non-critical:

```python
# memory_service.py — patterns detected as non-critical
patterns = [
    r"validation error for RuleSet",
    r"Field required \[type=missing",
    r"Input should be a valid integer",
    r"InstructorRetryException",
    r"Failed to Save interaction: <failed_attempts>",
]
```

The data is still saved; only the automated rule association fails silently.

---

## 11. Known Pitfalls & Troubleshooting

### ❌ `docker compose restart` does NOT reload `.env`

**Problem**: Changed `LLM_MODEL` in `.env` but container still uses old model.  
**Solution**: Always use `docker compose up -d --force-recreate cognee-mcp`.  
**Verify**: `docker exec cognee_mcp_server env | findstr LLM_MODEL`

### ❌ Mistral produces invalid JSON for RuleSet

**Problem**: Cognee's `instructor` library needs strict Pydantic-compliant JSON. Mistral 7B consistently fails:
- Outputs `version: "0.1"` (string instead of int)
- Wraps in `{"RuleSet": {...}}` instead of flat structure
- Produces truncated JSON

**Solution**: Use `qwen2.5:latest` or `llama3.1:8b`.

### ❌ `UNIQUE constraint failed: users.email` on startup

**Problem**: Container recreation triggers default user insertion, but SQLite data persists in Docker volume.  
**Impact**: Non-critical — Cognee continues after this error. First API call may return 503, second succeeds.  
**Solution**: Wait a few seconds after container start, or ignore the error.

### ❌ `UNIQUE constraint failed: data.id` on duplicate data

**Problem**: Re-ingesting identical content produces the same hash → same `data.id`.  
**Impact**: Non-critical — Cognee uses INSERT instead of UPSERT. Pipeline continues with other data.  
**Solution**: This is a Cognee-internal limitation with SQLite. Only new/unique content is indexed.

### ❌ First request after container recreate returns 503

**Problem**: Container takes ~30s to fully initialize (SQLite migrations, model loading).  
**Solution**: The `healthcheck` in docker-compose has `start_period: 60s`. Wait for health check to pass before sending requests.

### ❌ `TimeoutError` during embedding generation

**Problem**: When sending multiple facts rapidly (~5s apart), concurrent cognify pipelines overwhelm Ollama. The embedding endpoint (`/api/embed`) times out because Ollama is busy with LLM inference (graph extraction + summarization).  
**Symptoms**: `OllamaEmbeddingEngine.py:130 — TimeoutError`, `attempt #2; slept for 8.68`  
**Impact**: The specific embedding fails, but pipelines recover and complete for other data.  
**Solution**: Space out fact ingestion by at least **20 seconds** between calls. Use 30s+ wait before searching. Example:
```python
await asyncio.sleep(20.0)  # Between facts
await asyncio.sleep(30)    # Before search query
```

### ❌ `Dataset is already being processed`

**Problem**: Multiple cognify runs triggered concurrently for the same dataset.  
**Impact**: Non-critical — Cognee skips duplicate pipeline runs. Data is not lost.  
**Solution**: Increase delay between fact ingestion calls.

### ❌ `No ontology file provided` warning

**Problem**: Cognee logs `No owl ontology will be attached to the graph` on every cognify.  
**Impact**: Informational only. Cognee works without a custom ontology file — it auto-discovers schema.  
**Solution**: No action needed unless you want custom OWL ontology.

---

## 12. Operational Checklist

### Before First Run

- [ ] Ollama is running on host: `ollama serve`
- [ ] LLM model is pulled: `ollama pull qwen2.5:latest`
- [ ] Embedding model is pulled: `ollama pull nomic-embed-text`
- [ ] `.env` has `LLM_MODEL="qwen2.5:latest"` (not `mistral`)
- [ ] Docker Compose is up: `docker compose up -d`
- [ ] Health check passes: `curl http://localhost:8000/health`

### After Changing `.env`

- [ ] Run `docker compose up -d --force-recreate cognee-mcp` (NOT `restart`)
- [ ] Verify: `docker exec cognee_mcp_server env | findstr LLM_MODEL`
- [ ] Wait ~30s for container initialization
- [ ] Test: `python quick_test.py`

### After Clean Reset

```bash
# Stop everything
docker compose down

# Remove Cognee data (SQLite, Kuzu, LanceDB) for fresh start
docker volume rm cognee-layer_cognee_data

# Restart
docker compose up -d
```

### Monitoring

```bash
# Watch Cognee MCP logs in real-time
docker logs -f cognee_mcp_server

# Watch workspace API logs
docker logs -f workspace_api

# Check Ollama is accessible from container
docker exec cognee_mcp_server curl -s http://host.docker.internal:11434/api/tags
```

---

## File Structure Reference

```
cognee-layer/
├── app.py                          # FastAPI entry point
├── docker-compose.yml              # Container orchestration
├── Dockerfile                      # workspace_api build
├── .env                            # Environment variables (⚠️ not committed)
├── requirements.txt                # Python dependencies
│
├── api/
│   ├── dependencies.py             # FastAPI DI (HTTP client, repos, services)
│   ├── schemas/
│   │   └── memory_schema.py        # Request/Response Pydantic models
│   └── routers/v1/
│       └── memory_router.py        # POST /record, POST /recall
│
├── configuration/
│   ├── settings.py                 # AppConfig, DatabaseSettings, CogneeSettings
│   ├── database.py                 # SQLAlchemy async engine
│   └── logging_setup.py            # Structured logging
│
├── data/repositories/
│   └── cognee_repository.py        # MCP JSON-RPC client (session, tools/call)
│
├── service/
│   ├── memory_service.py           # Business logic + fallback + error handling
│   └── business_models.py          # ContextEnvelope domain model
│
└── tests/
    ├── quick_test.py               # Single fact smoke test
    └── test_bpi.py                 # Multi-fact BPI France integration test
```
