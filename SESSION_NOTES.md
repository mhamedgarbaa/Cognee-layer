# Session Notes — Plug-and-Play MCP Stack + BPI Agent

Last updated: 2026-04-18
Branch: `feature/postgres-neo4j-llm-testing`

## Goal

Transition the Cognee memory layer from the embedded stack (SQLite + Kuzu + Ollama) to a
production microservice stack (PostgreSQL + Neo4j + Azure OpenAI), wire an agent
directly against the MCP server, and cognify `bpi_france_events.json` end-to-end.

---

## 1. Stack changes

| Layer | Before | After |
|-------|--------|-------|
| Relational | SQLite (embedded) | PostgreSQL 15 (`workspace_db` + `cognee_db`) |
| Graph | Kuzu (embedded) | Neo4j 5 + APOC (`bolt://neo4j:7687`) |
| Vector | LanceDB | LanceDB (unchanged, 1536 dims) |
| LLM | Ollama (qwen2.5) | Azure OpenAI (`gpt-4o` via `LLM_ENDPOINT`) |
| Embeddings | local | Azure OpenAI (`text-embedding-3-small`, 1536D) |

Services in `docker-compose.yml`: `db`, `neo4j`, `cognee-mcp`, `web`.

### Key configuration caveats we hit

- **`LLM_PROVIDER` must be `openai`, not `azure`.** Cognee's enum only accepts
  `openai`; Azure routing is done through `LLM_ENDPOINT`.
- **`ENABLE_BACKEND_ACCESS_CONTROL=False` is required with Neo4j.** The Neo4j graph
  adapter doesn't implement Cognee's multi-user access-control handler. Leaving it
  `True` makes `search` fail with:
  > The selected graph dataset to database handler does not work with the configured graph database provider.
- **Container healthchecks must use `python3 -c urllib.request.urlopen(...)`.** The
  `cognee/cognee-mcp:main` image has neither `curl` nor `wget`.
- **Host-header validation.** Cognee's `ALLOWED_HOSTS` check rejects requests that
  don't carry a `Host: localhost:8001` header — every MCP call from the client
  must set it.
- **`cognee_db` provisioning.** The Postgres init script only runs on a fresh
  `pgdata` volume. On an existing volume we created it manually:
  ```bash
  docker compose exec -T db psql -U workspace_user -d workspace_db \
    -c "CREATE DATABASE cognee_db OWNER workspace_user;"
  ```

---

## 2. Integration test suite

`tests/test_stack_integration.py` — 16 tests across 8 classes, all green:

- `TestHealth` — `/health` on web + cognee-mcp
- `TestMCPProtocol` — MCP initialize + `tools/list`
- `TestWritePath` — FastAPI `/api/v1/memory/record` → 202
- `TestMCPWrite` — direct MCP `save_interaction`
- `TestReadPath` — `/api/v1/memory/recall` (graph completion + degraded modes)
- `TestCognifyStatus`, `TestDataInventory`, `TestValidation`

Run:
```bash
pytest tests/test_stack_integration.py -v
```

---

## 3. Direct-MCP agent — `test_agent_mcp.py`

A tiny agent that bypasses the FastAPI layer and talks straight to the MCP
server on `localhost:8001`. Demonstrates what "exposing MCP to an agent" means
in practice:

1. `POST /mcp initialize` → captures `mcp-session-id`.
2. `tools/list` → confirms available tools:
   `['cognify', 'cognify_status', 'delete', 'list_data', 'prune', 'save_interaction', 'search']`.
3. `prune` — clean slate.
4. `save_interaction` × 11 (one per BPI event), polling `cognify_status` between
   each to wait for the background pipeline to idle.
5. `list_data` → dataset inventory.
6. `search` × 3 French queries with `search_type=GRAPH_COMPLETION`.

Run from host:
```bash
python test_agent_mcp.py
```

### Result of the first run

- Prune OK.
- 11/11 save_interaction calls succeeded (all 11 BPI events ingested).
- `list_data` showed `main_dataset` with a dataset ID.
- `search` initially failed with the access-control error above; fixed by
  flipping `ENABLE_BACKEND_ACCESS_CONTROL` to `False` and recreating the
  `cognee-mcp` container.

---

## 4. Known issue — `DatabaseNotCreatedError` after prune

Seen error:
```
Failed to get cognify status: DatabaseNotCreatedError:
The database has not been created yet. Please call `await setup()` first.
```

**Cause.** `prune` drops the Cognee graph/vector tables. Calling
`cognify_status` / `search` before anything has re-seeded them returns 422.

**Fix (implemented in `test_agent_mcp.py`).** After prune, call `cognify` with a
seed payload before any status poll. This forces Cognee's `setup()` to run and
recreates the graph tables:

```python
# after prune
await agent.call_tool("cognify", {"data": "Cognee setup seed", "user": tenant})
await wait_for_idle(agent)   # wait for setup to finish, then ingest real data
```

`wait_for_idle` also now detects `DatabaseNotCreatedError` explicitly and labels
it "db not ready yet" rather than showing the raw error line.

---

## 5. Files touched / created

| File | Change |
|------|--------|
| `docker-compose.yml` | 4-service stack, Azure OpenAI wiring, Neo4j, healthchecks, access-control flag |
| `.env` / `.env.example` | New env schema (Postgres + Neo4j + Azure OpenAI) |
| `scripts/postgres-init/01-create-cognee-db.sh` | Provisions `cognee_db` on fresh volume |
| `ARCHITECTURE_REPORT.md` | Updated to v2.0 architecture |
| `DATA_FLOW_ARCHITECTURE.md` | Updated flows + §14 migration notes |
| `tests/test_stack_integration.py` | New — 16 integration tests |
| `test_agent_mcp.py` | New — direct-MCP agent for the BPI JSON |
| `SESSION_NOTES.md` | This file |

---

## 6. Security note

The Azure OpenAI key was committed verbatim to `.env` and earlier to a default
in `docker-compose.yml`. It must be rotated before this branch is pushed to any
shared remote.

---

# Session 2 — 2026-04-18: Web UI, Microservice Docs, Graph Viz, Temporal Memory

## Goal

- Build a browser-based agent UI (chat + cognify panel)
- Document microservice deployment (standalone Docker image)
- Add graph visualization and memify as tools
- Implement temporal memory enrichment so agent sessions are indexed over time

---

## 7. LLM backend switch — Ollama (local)

Switched from Azure OpenAI to local Ollama to avoid API key costs during dev.

| Variable | Value |
|----------|-------|
| `LLM_PROVIDER` | `ollama` |
| `LLM_MODEL` | `qwen2.5:latest` |
| `LLM_ENDPOINT` | `http://host.docker.internal:11434/v1` |
| `EMBEDDING_PROVIDER` | `ollama` |
| `EMBEDDING_MODEL` | `nomic-embed-text` |
| `EMBEDDING_ENDPOINT` | `http://host.docker.internal:11434/api/embeddings` |
| `EMBEDDING_DIMENSIONS` | `768` |
| `HUGGINGFACE_TOKENIZER` | `Salesforce/SFR-Embedding-Mistral` |

**Caveat:** `EMBEDDING_ENDPOINT` must include the full path `/api/embeddings` —
Cognee's `OllamaEmbeddingEngine` posts to it verbatim; the root URL returns 404.

**Caveat:** `HUGGINGFACE_TOKENIZER` is required whenever `EMBEDDING_DIMENSIONS`
is set; omitting it causes a Cognee `ValidationError` on startup.

**Caveat:** Docker → host Ollama routing requires:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

---

## 8. Web UI — `agent_server.py`

FastAPI on port 8080. Two-panel HTML UI served at `/`:

| Panel | Function |
|-------|----------|
| Left — Chat | Groq llama-4-scout agent with tool-use loop, SSE streaming |
| Right — Cognify Document | Paste text → `cognify` tool → Neo4j graph + vectors |

Tools exposed to the Groq agent:
`cognify`, `save_interaction`, `search`, `list_data`, `cognify_status`,
`prune`, `visualize_graph`, `memify`

Key fix: `cognify` dispatches directly to MCP (not via `save_interaction` first).
Calling both with the same content caused `IntegrityError: duplicate key "data_pkey"`.

Run:
```bash
python agent_server.py
# open http://localhost:8080
```

Requires `GROQ_API_KEY` in `.env`.

---

## 9. Graph visualization — `scripts/visualize_neo4j_graph.py`

Uses `cognee.visualize_graph()` (networkx + pyvis) to render the Neo4j graph as
interactive HTML.

Run inside the container:
```bash
docker cp scripts/visualize_neo4j_graph.py cognee_mcp_server:/tmp/visualize.py
docker exec cognee_mcp_server bash -c "python3 /tmp/visualize.py"
# HTML written to /tmp/cognee_graph.html
```

Also exposed as the `visualize_graph` tool in `agent_server.py` via the
`/api/graph` endpoint.

**Git Bash path mangling on Windows:** paths like `/app/scripts/` become
`C:/Program Files/Git/app/scripts/` when passed to `docker exec`. Fix: copy
scripts to `/tmp/` first with `docker cp`, then run from `/tmp/`.

---

## 10. Temporal memory — `scripts/run_memify.py`

**Purpose:** After cognifying agent interactions, enrich the Neo4j graph with
temporal `Event` nodes so `GRAPH_COMPLETION` can reason about *when* things
happened across sessions over time.

**Why this matters:** The main goal of Cognee in this stack is temporal memory —
interactions saved during agent sessions should be indexed with timestamps and
linked as Event nodes, enabling time-aware retrieval.

**Why not `cognee.memify()` tasks:**
`extract_subgraph_chunks` yields raw strings; `extract_events_and_timestamps`
expects `DocumentChunk` objects — type mismatch in Cognee 0.5.2. The task
chain cannot be wired as written.

**Actual implementation:** Direct Cypher pipeline:
1. `MATCH (n:DocumentChunk) RETURN n.id, n.text` — read all chunks from Neo4j
2. `extract_event_graph(text, EventList)` — LLM extracts events with timestamps
3. `MERGE (e:Event {...})` + `MERGE (c)-[:HAS_EVENT]->(e)` — write back to Neo4j

Idempotent (`MERGE`). Re-running only adds new events.

Run:
```bash
docker cp scripts/run_memify.py cognee_mcp_server:/tmp/run_memify.py
docker exec cognee_mcp_server bash -c "python3 /tmp/run_memify.py [dataset]"
```

Also exposed as the `memify` tool in `agent_server.py`.

**Verified:** Event node with timestamp written to Neo4j and linked to
DocumentChunk via `HAS_EVENT` edge.

---

## 11. Microservice deployment docs

`README.md` and `ARCHITECTURE_REPORT.md` updated to v3.0.0:
- Full system diagram
- MCP protocol reference (session handshake, required headers, tool table)
- `docker-compose.mcp-only.yml` for standalone MCP-server-only deployment
- Config matrix for Ollama (local) vs Azure OpenAI (cloud)
- Known constraints table

---

## 12. Files touched / created (Session 2)

| File | Change |
|------|--------|
| `agent_server.py` | New — FastAPI web UI + Groq agent + SSE streaming |
| `agent_chat.py` | New — CLI chat interface using Groq + MCP |
| `test_agent_mcp.py` | Updated — Windows UTF-8 fix, post-prune seed logic |
| `scripts/visualize_neo4j_graph.py` | New — graph HTML renderer |
| `scripts/run_memify.py` | New — temporal event enrichment via direct Cypher |
| `docker-compose.yml` | Ollama env vars, `extra_hosts`, `ACCEPT_LOCAL_FILE_PATH` |
| `.env` | Switched to Ollama backend, added `GROQ_API_KEY` |
| `README.md` | Full rewrite — quick start, microservice deployment steps |
| `ARCHITECTURE_REPORT.md` | Full rewrite — v3.0.0, config matrix, constraints |

---

## 13. Key errors fixed (Session 2)

| Error | Fix |
|-------|-----|
| `UnicodeEncodeError cp1252` | `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` |
| `DatabaseNotCreatedError` after prune | Seed cognify before first status poll; 8s sleep |
| `Groq BadRequestError: tool not in request.tools` | Added `cognify_status` to `TOOLS` list |
| `ContentTypeError 404` on embeddings | Full path in `EMBEDDING_ENDPOINT`: `.../api/embeddings` |
| `ValidationError: HUGGINGFACE_TOKENIZER missing` | Added `Salesforce/SFR-Embedding-Mistral` |
| `IngestionError: Local files not accepted` | Set `ACCEPT_LOCAL_FILE_PATH=True` |
| `IntegrityError: duplicate key "data_pkey"` | Removed duplicate `save_interaction` pre-call |
| `Git Bash path mangling` | Use `docker cp` to `/tmp/`, run from `/tmp/` |
| `ImportError: extract_events_and_entities` | Correct name: `extract_events_and_timestamps` |
| `memify task chain type mismatch` | Replaced with direct Cypher pipeline in `run_memify.py` |
| `cognee.setup() AttributeError` | Removed — not a public API in Cognee 0.5.2 |

Graphiti-core in Cognee 0.5.2
graphiti-core is NOT installed — pip show graphiti-core exits with code 1. The code exists but is a dead import at runtime.

What the 3 files do
File	Role
build_graph_with_temporal_awareness.py	Takes a list of texts, connects to Neo4j via Graphiti(url, "neo4j", password), adds each as a timestamped episode
search_graph_with_temporal_awareness.py	Runs graphiti.search(query) then closes the connection
index_graphiti_objects.py	Pulls Neo4j nodes/edges from Graphiti's schema, transforms them into Cognee DataPoint objects, and indexes them into LanceDB so standard SearchType.* queries also work on Graphiti episodes
How to enable it
Graphiti is an optional extra — it needs to be installed separately:


# in Dockerfile.wrapper or a custom cognee image
RUN pip install "cognee[graphiti]"
# or directly:
RUN pip install graphiti-core
Then you can call it from memify or a new tool inside the container:


from cognee.tasks.temporal_awareness import (
    build_graph_with_temporal_awareness,
    search_graph_with_temporal_awareness,
)

# ingest texts as timestamped episodes into Neo4j
graphiti = await build_graph_with_temporal_awareness(["text1...", "text2..."])

# query
results = await search_graph_with_temporal_awareness(graphiti, "what happened in 2024?")
vs. the current temporal_cognify=True approach
temporal_cognify=True	Graphiti
Storage	Event nodes inside Cognee graph	Separate Graphiti episode schema in Neo4j
Install	Already works	Needs pip install graphiti-core
Query	SearchType.TEMPORAL	graphiti.search() or after index_and_transform_graphiti_nodes_and_edges() also SearchType.*
Best for	Enriching existing chunks	Full episode timeline from scratch
Do you want to install graphiti-core into the cognee-mcp image and wire it up as a tool?