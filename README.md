# Cognee Memory MCP Stack

An enterprise-grade, graph-backed temporal memory microservice for AI agents.
Designed to be consumed exclusively through a **Context Adapter Layer** —
which compiles, filters, and compresses context before delivering it to the agent.

---

## Architecture

```
 ┌──────────────────────────────────┐
 │   Enterprise Agentic System      │
 │   (CEO / Admin agent)            │
 └────────────────┬─────────────────┘
                  │ MCP
                  ▼
 ┌──────────────────────────────────┐
 │   Context Adapter Layer          │  ← compiler node restructures query
 │                                  │    retrieves from Cognee + GraphRAG
 │                                  │    filters, refines, compresses context
 │                                  │    delivers exactly what the agent needs
 └────────┬─────────────────────────┘
          │ HTTP JSON-RPC 2.0 (internal)
          ▼
 ┌─────────────────────────┐
 │  MCP Wrapper  :8002     │  ← memory tool gateway
 │  session management     │    circuit breaker, retry, docker-exec tools
 │  session cache (FS)     │
 └────────────┬────────────┘
              │ proxy + docker exec
              ▼
 ┌─────────────────────────┐
 │  Cognee MCP   :8001     │  ← core memory engine (ECL pipeline)
 └────────────┬────────────┘
              │
     ┌────────┴────────┐
     ▼                 ▼
  Neo4j 5          Postgres 15
  (graph)          (metadata + relational)
     └────────┬────────┘
              ▼
           LanceDB
          (vectors, file-backed)
              │
              ▼
    Azure OpenAI / OpenAI
    (LLM completions + embeddings)

 ┌─────────────────────────┐
 │  FastAPI Web  :8000     │  ← REST memory API + graph UI
 └─────────────────────────┘
```

Five Docker services boot in dependency order:
`db` → `neo4j` → `cognee-mcp` → `mcp-wrapper` → `web`

> **Authentication**: handled entirely by the Context Adapter Layer.
> The Cognee stack runs without API key enforcement — it is an internal service
> not exposed to the public network.

---

## Quick Start

### Prerequisites

- Docker Desktop (or Docker + Compose v2)
- OpenAI or Azure OpenAI API key

### 1. Clone and configure

```bash
git clone <repo-url>
cd cognee-layer
cp .env.example .env   # then edit .env
```

Minimum required in `.env`:

```env
# LLM (Azure OpenAI example)
LLM_PROVIDER=openai
LLM_MODEL=openai/gpt-4o-mini
LLM_ENDPOINT=https://<resource>.services.ai.azure.com/models
LLM_API_KEY=<key>

# Embeddings
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_ENDPOINT=https://<resource>.services.ai.azure.com/models
EMBEDDING_API_KEY=<key>
EMBEDDING_DIMENSIONS=1536

# Databases (defaults work out of the box)
POSTGRES_USER=workspace_user
POSTGRES_PASSWORD=workspace_pass
NEO4J_PASSWORD=neo4j_pass
```

For plain OpenAI, set `LLM_ENDPOINT=https://api.openai.com/v1`.
For local Ollama, see [Switching LLM Backends](#switching-llm-backends).

### 2. Start the stack

```bash
docker compose up -d
```

Wait ~60 s for `cognee-mcp` to become healthy, then verify:

```bash
docker compose ps
curl http://localhost:8002/health   # MCP Wrapper
curl http://localhost:8000/health   # FastAPI
```

---


## Available MCP Tools

Call all tools via `POST /mcp` on the wrapper (`:8002`).

### Memory — Write

| Tool | Latency | Description |
|------|---------|-------------|
| `cognify` | ~30 s | Deep-process text into the knowledge graph (temporal by default). Pass `temporal: true` to tag all extracted events with timestamps. |
| `save_interaction` | ~2 s | Fast-append a short note to Short-Term Memory without full graph processing. |
| `remember` | ~30 s | Alias for `cognify` — store a fact with full temporal graph processing. |
| `memify` | ~120 s | Extract temporal `Event` nodes from stored chunks (runs inside cognee container). |
| `improve` | ~60 s | 4-stage quality loop: apply feedback weights → persist sessions in graph → sync traces → rebuild session index. Auto-injects current session_id. |

### Memory — Read

| Tool | Latency | Description |
|------|---------|-------------|
| `search` | ~5 s | Query the knowledge graph. Supports 14 search types (see below). |
| `recall` | ~5 s | Alias for `search` with `GRAPH_COMPLETION` default. |
| `list_data` | ~2 s | List all stored datasets and data items. |
| `cognify_status` | ~1 s | Check whether the cognify pipeline is running or idle. |
| `visualize_graph` | ~15 s | Render the full Neo4j graph as interactive HTML at `/graph`. |

### Memory — Delete

| Tool | Latency | Description |
|------|---------|-------------|
| `forget_memory` | ~3 s | Soft-delete a specific data item by UUID. |
| `delete` | ~3 s | Delete a data item or dataset (soft or hard mode). |
| `prune` | ~5 s | Wipe ALL stored knowledge — irreversible. |

### Observability

| Tool | Latency | Description |
|------|---------|-------------|
| `submit_feedback` | ~5 s | Rate a previous recall result (1 = wrong, 5 = perfect). Stored in session cache and used by `improve`. |
| `record_trace` | ~3 s | Record an agent action step for full observability (function name, params, status, error). |

### Search types for `search` / `recall`

`GRAPH_COMPLETION` · `GRAPH_COMPLETION_COT` · `GRAPH_COMPLETION_CONTEXT_EXTENSION` · `GRAPH_COMPLETION_DECOMPOSITION` · `GRAPH_SUMMARY_COMPLETION` · `TEMPORAL` · `TRIPLET_COMPLETION` · `CYPHER` · `NATURAL_LANGUAGE` · `RAG_COMPLETION` · `SUMMARIES` · `CHUNKS` · `CHUNKS_LEXICAL` · `FEELING_LUCKY`

---

## Connecting an Agent

### Discover available tools

```bash
curl http://localhost:8002/tools
```

Returns all tools with `inputSchema`, `category`, `typical_latency_seconds`, and handshake instructions.

### Session handshake

```python
import httpx, json

BASE = "http://localhost:8002"

def parse_sse(text):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())

client = httpx.Client(timeout=60)

# 1 — initialize session
r = client.post(f"{BASE}/mcp", json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "enterprise-brain", "version": "1.0"},
    },
})
session_id = r.headers["mcp-session-id"]
headers    = {"mcp-session-id": session_id}

# 2 — store knowledge
client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "cognify", "arguments": {
        "data": "Q3 revenue was €12.4M, up 18% YoY.",
        "temporal": True,
    }},
})

# 3 — query knowledge
result = parse_sse(client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "search", "arguments": {
        "search_query": "What was Q3 revenue?",
        "search_type": "GRAPH_COMPLETION",
    }},
}).text)
print(result["result"]["content"][0]["text"])

# 4 — rate the answer (qa_id comes from the recall response metadata)
client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
    "params": {"name": "submit_feedback", "arguments": {
        "qa_id":          "<uuid-from-recall>",
        "feedback_score": 5,
        "user_id":        "ceo",
    }},
})

# 5 — run the improve loop to promote feedback into the graph
client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
    "params": {"name": "improve", "arguments": {}},
})
```

### Async Python

```python
import asyncio, httpx, json

BASE = "http://localhost:8002"

async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        r = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "async-agent", "version": "1.0"}},
        })
        sid     = r.headers["mcp-session-id"]
        headers = {"mcp-session-id": sid}

        r = await client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "search", "arguments": {
                "search_query": "Q3 revenue",
                "search_type": "TEMPORAL",
            }},
        })
        for line in r.text.splitlines():
            if line.startswith("data:"):
                print(json.loads(line[5:])["result"]["content"][0]["text"])

asyncio.run(main())
```

---

## FastAPI REST Memory API

The `web` service (`:8000`) exposes a higher-level REST API.

```bash
# Store a fact
curl -X POST http://localhost:8000/api/v1/memory/record \
  -H "Content-Type: application/json" \
  -d '{"user_id":"ceo","fact":"Q3 revenue €12.4M, +18% YoY"}'

# Recall with 3-tier circuit-breaker fallback
curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d '{"user_id":"ceo","query":"What was Q3 revenue?"}'

# Submit feedback on a recall result
curl -X POST http://localhost:8000/api/v1/memory/feedback \
  -H "Content-Type: application/json" \
  -d '{"user_id":"ceo","session_id":"<sid>","qa_id":"<qid>","feedback_score":5}'

# Record an agent trace step
curl -X POST http://localhost:8000/api/v1/memory/trace \
  -H "Content-Type: application/json" \
  -d '{"user_id":"ceo","session_id":"<sid>","origin_function":"search","status":"success","memory_query":"Q3 revenue"}'

# Swagger UI
open http://localhost:8000/api/docs
```

### All REST endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/memory/record` | Store a fact → STM + temporal graph |
| `POST` | `/api/v1/memory/recall` | Retrieve context (graph completion + fallback) |
| `POST` | `/api/v1/memory/feedback` | Rate a recall result (1–5) |
| `POST` | `/api/v1/memory/trace` | Record an agent action for observability |
| `GET`  | `/api/v1/memory/data` | List all datasets |
| `DELETE` | `/api/v1/memory/data` | Delete a data item by UUID |
| `DELETE` | `/api/v1/memory/datasets/{id}` | Delete all items in a dataset |
| `DELETE` | `/api/v1/memory/prune` | Wipe all knowledge |
| `GET`  | `/api/v1/memory/cognify/status` | Check cognify pipeline status |
| `GET`  | `/graph/stats` | Neo4j entity / relation counts |
| `GET`  | `/graph/data` | Raw graph JSON (nodes + edges) |
| `POST` | `/graph/build` | Build interactive HTML graph |
| `GET`  | `/graph` | Serve the HTML graph viewer |
| `GET`  | `/ontology/info` | Check current ontology file |
| `POST` | `/ontology/upload` | Upload a `.ttl` / `.owl` ontology — auto-reloads cognee-mcp |
| `POST` | `/ontology/commit` | Commit raw TTL text — auto-reloads cognee-mcp |
| `POST` | `/add` | Proxy: cognify text via mcp-wrapper |
| `POST` | `/search` | Proxy: search via mcp-wrapper |
| `GET`  | `/health` | Liveness check (no key required) |

---

## Session Caching

MCP sessions are stored in the filesystem cache (`/app/cognee_data/cache`) inside the cognee-mcp container.

- TTL: 24 h (`SESSION_TTL_SECONDS=86400`)
- Each session stores Q&A pairs, feedback scores, and agent traces
- The `improve` tool promotes cached sessions into the permanent Neo4j graph

Session IDs are generated as UUID4 on MCP `initialize` and must be passed as the `mcp-session-id` header on every subsequent request.

---

## Ontology Management

Upload a domain ontology (OWL / Turtle) to ground entity extraction in a controlled vocabulary.

```bash
# Upload from file
curl -X POST http://localhost:8000/ontology/upload \
  -F "file=@my_ontology.ttl"

# Or commit raw TTL text
curl -X POST http://localhost:8000/ontology/commit \
  -H "Content-Type: application/json" \
  -d '{"content": "@prefix ex: <http://example.org/> .\n..."}'
```

Both endpoints write the file to the shared `./ontologies` volume and automatically restart `cognee-mcp` so the new ontology takes effect immediately — no manual container restart needed.

### Admin reload (manual)

```bash
curl -X POST http://localhost:8002/admin/reload-cognee
```

---

## Knowledge Graph Viewer

```bash
# Build and view the graph
curl -X POST http://localhost:8000/graph/build
# open http://localhost:8000/graph
```

Or use the `visualize_graph` MCP tool directly.

---

## Neo4j Browser

Explore the knowledge graph visually:

```
http://localhost:7474
Login: neo4j / neo4j_pass   (or NEO4J_PASSWORD value)
```

Useful Cypher queries:
```cypher
// All entities and relationships
MATCH (a)-[r]->(b) RETURN a,r,b LIMIT 100

// Temporal Event nodes (after memify)
MATCH (e:Event) RETURN e ORDER BY e.timestamp DESC LIMIT 20

// Feedback-rated nodes
MATCH (n) WHERE n.feedback_score IS NOT NULL RETURN n LIMIT 50
```

---

## MCP Wrapper Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_WRAPPER_PORT` | `8002` | Port the wrapper listens on |
| `MCP_WRAPPER_LOG_LEVEL` | `INFO` | Log level |
| `MCP_WRAPPER_STRICT_SESSIONS` | `false` | Reject tool calls without a valid session |
| `MCP_WRAPPER_SESSION_TTL` | `3600` | Session eviction TTL (seconds) |
| `MCP_WRAPPER_STREAMING` | `false` | Incremental SSE streaming |
| `MCP_WRAPPER_RECONNECT_DELAY` | `5` | Initial reconnect delay (seconds) |
| `MCP_WRAPPER_MAX_RECONNECT_ATTEMPTS` | `10` | Max reconnect attempts |
| `CIRCUIT_BREAKER_THRESHOLD` | `3` | Failures before circuit opens |
| `CIRCUIT_BREAKER_TIMEOUT` | `60` | Seconds circuit stays open |
| `COGNEE_CONTAINER_NAME` | `cognee_mcp_server` | Container name for docker exec tools |
| `GRAPH_OUTPUT_PATH` | `/graph/cognee_graph.html` | Graph HTML output path |

---

## Switching LLM Backends

### Local Ollama

```bash
ollama pull qwen2.5
ollama pull nomic-embed-text
```

```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:latest
LLM_ENDPOINT=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_ENDPOINT=http://host.docker.internal:11434/api/embeddings
EMBEDDING_DIMENSIONS=768
HUGGINGFACE_TOKENIZER=Salesforce/SFR-Embedding-Mistral
```

If embedding dimensions changed (e.g. 1536→768), wipe the vector store first:

```bash
docker compose down
docker volume rm cognee-layer_cognee_data cognee-layer_cognee_storage
docker compose up -d
```

---

## Integration Tests

```bash
# against local stack
pytest tests/test_stack_integration.py -v

# against a remote host
BASE_URL=http://<host>:8000 MCP_URL=http://<host>:8002 \
  pytest tests/test_stack_integration.py -v
```

Streaming smoke test:
```bash
python scripts/test_streaming.py
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `{"detail":"Not Found"}` on `/tools` or `/health` | Container running old image — `docker compose build mcp-wrapper && docker compose up -d mcp-wrapper` |
| `421 Invalid Host header` on direct MCP calls | Use the MCP Wrapper (:8002), not Cognee MCP (:8001) directly |
| `circuit_breaker_open: true` in `/health` | Cognee MCP unreachable — check `docker compose logs cognee-mcp` |
| `Unknown or expired session` | Call `initialize` first; include `mcp-session-id` on all subsequent requests |
| `DatabaseNotCreatedError` after prune | Wait ~10 s after first `cognify`; DB setup is async |
| `ContentTypeError 404` on embeddings | Set `EMBEDDING_ENDPOINT` to the full path (e.g. `.../api/embeddings` for Ollama) |
| `IngestionError: Local files not accepted` | Set `ACCEPT_LOCAL_FILE_PATH=True` in `.env` |
| `IntegrityError: duplicate key "data_pkey"` | Harmless — same content hashes to same UUID; pipeline continues |
| Embedding dimension mismatch after LLM switch | Wipe `cognee_data` and `cognee_storage` volumes and restart |
| Graph HTML not updated after `visualize_graph` | Both `mcp-wrapper` and `web` mount the `graph_output` volume at `/graph` |
| Ontology not picked up after upload | Check `/ontology/info`; if stuck, call `POST /admin/reload-cognee` on the wrapper manually |
