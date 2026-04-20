# Cognee Memory MCP Stack

A self-contained, graph-backed memory microservice for AI agents.
Drop it into any agent stack and get persistent semantic memory via the
**Model Context Protocol (MCP)** — no SDK required, just HTTP.

---

## Architecture

```
External Agents / Claude / LangChain
          │
          │ HTTP JSON-RPC 2.0
          ▼
 ┌─────────────────────────┐
 │  MCP Wrapper  :8002     │  ← primary agent entry point
 │  10 tools exposed       │
 │  session management     │
 │  circuit breaker        │
 └────────────┬────────────┘
              │ proxy (8 tools) + docker exec (2 tools)
              ▼
 ┌─────────────────────────┐
 │  Cognee MCP   :8001     │  ← core memory engine
 └────────────┬────────────┘
              │
     ┌────────┴────────┐
     ▼                 ▼
  Neo4j 5          Postgres 15
  (graph)          (metadata)
     └────────┬────────┘
              ▼
           LanceDB
          (vectors)
              │
              ▼
    OpenAI / Azure OpenAI
    (LLM + embeddings)

 ┌─────────────────────────┐
 │  FastAPI Web  :8000     │  ← higher-level REST memory API
 └─────────────────────────┘
```

Five Docker services: `db` → `neo4j` → `cognee-mcp` → `mcp-wrapper` → `web`

---

## Quick Start

### Prerequisites

- Docker Desktop (or Docker + Compose v2)
- OpenAI or Azure OpenAI API key

### 1. Clone and configure

```bash
git clone <repo-url>
cd cognee-layer
cp .env.example .env
```

Minimum required in `.env`:

```env
# LLM — OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_ENDPOINT=https://api.openai.com/v1
LLM_API_KEY=sk-...

# Embeddings
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_ENDPOINT=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-...
EMBEDDING_DIMENSIONS=1536

# Databases (defaults work out of the box)
POSTGRES_USER=workspace_user
POSTGRES_PASSWORD=workspace_pass
NEO4J_PASSWORD=neo4j_pass
```

For Azure OpenAI, set `LLM_ENDPOINT` to your deployment URL and `LLM_MODEL=azure/<deployment-name>`.

For local Ollama, see the [Switching LLM Backends](#switching-llm-backends) section below.

### 2. Start the stack

```bash
docker compose up -d
```

Wait ~60s for `cognee-mcp` to become healthy, then verify:

```bash
docker compose ps
curl http://localhost:8002/health   # MCP Wrapper
curl http://localhost:8000/health   # FastAPI
```

---

## Connecting an Agent

### Discover available tools

```bash
curl http://localhost:8002/tools
```

Returns all 10 tools with `inputSchema`, `category`, `typical_latency_seconds`, and step-by-step session handshake instructions.

### Session handshake (required before tool calls)

```python
import httpx, json

BASE = "http://localhost:8002"

def parse_sse(text):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())

client = httpx.Client(timeout=60)

# Step 1 — initialize session
r = client.post(f"{BASE}/mcp", json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "my-agent", "version": "1.0"},
    },
})
session_id = r.headers["mcp-session-id"]
headers = {"mcp-session-id": session_id}

# Step 2 — store knowledge
client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "cognify", "arguments": {
        "data": "France 2030 plan allocated €30B for deep tech startups."
    }},
})

# Step 3 — query knowledge
result = parse_sse(client.post(f"{BASE}/mcp", headers=headers, json={
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "search", "arguments": {
        "search_query": "What is the France 2030 budget?",
        "search_type": "GRAPH_COMPLETION",
    }},
}).text)
print(result["result"]["content"][0]["text"])
```

### Async Python (httpx)

```python
import asyncio, httpx, json

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8002", timeout=60) as client:
        r = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "async-agent", "version": "1.0"}},
        })
        sid = r.headers["mcp-session-id"]

        r = await client.post("/mcp", headers={"mcp-session-id": sid}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "search", "arguments": {
                "search_query": "France 2030",
                "search_type": "GRAPH_COMPLETION",
            }},
        })
        for line in r.text.splitlines():
            if line.startswith("data:"):
                print(json.loads(line[5:])["result"]["content"][0]["text"])

asyncio.run(main())
```

---

## Available Tools

| Tool | Category | Latency | Description |
|------|----------|---------|-------------|
| `cognify` | write | ~30s | Deep-process text into the knowledge graph |
| `save_interaction` | write | ~2s | Quickly save a short note without full graph processing |
| `search` | read | ~5s | Query the graph (11 search types) |
| `list_data` | read | ~2s | List all stored datasets |
| `cognify_status` | read | ~1s | Check if the cognify pipeline is running or idle |
| `prune` | admin | ~5s | Wipe ALL stored knowledge (irreversible) |
| `memify` | write | ~120s | Extract temporal Event nodes from stored chunks |
| `visualize_graph` | read | ~15s | Render the full graph as interactive HTML |
| `persist_sessions` | write | ~10s | Store agent conversation transcripts as graph nodes |
| `improve_answer` | write | ~10s | Re-query with chain-of-thought and store corrected answer |

### Search types for `search`

`GRAPH_COMPLETION` · `GRAPH_COMPLETION_COT` · `GRAPH_COMPLETION_CONTEXT_EXTENSION` · `GRAPH_SUMMARY_COMPLETION` · `TEMPORAL` · `CYPHER` · `NATURAL_LANGUAGE` · `RAG_COMPLETION` · `SUMMARIES` · `CHUNKS` · `FEELING_LUCKY`

---

## FastAPI Memory API (higher-level)

The `web` service exposes a REST API over the same memory backend:

```bash
# Store a fact
curl -X POST http://localhost:8000/api/v1/memory/record \
  -H "Content-Type: application/json" \
  -d '{"user_id":"agent1","fact":"France 2030 budget is 30 billion euros"}'

# Recall with graph completion (3-tier circuit breaker fallback)
curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d '{"user_id":"agent1","query":"What is France 2030?"}'

# Swagger UI
open http://localhost:8000/api/docs
```

---

## Interactive Agent UI

Browser-based chat + document cognify interface (requires `GROQ_API_KEY`):

```bash
python agent_server.py
# open http://localhost:8080
```

| Panel | Function |
|-------|----------|
| Left — Chat | Talk to the agent; tool calls shown as pills in real-time |
| Right — Cognify Document | Paste any text and store it in the Neo4j graph |

---

## Neo4j Browser

Explore the knowledge graph visually:

```
http://localhost:7474
Login: neo4j / neo4j_pass   (or whatever NEO4J_PASSWORD is set to)
```

Useful Cypher queries:
```cypher
// All entities and relationships
MATCH (a)-[r]->(b) RETURN a,r,b LIMIT 100

// Temporal Event nodes (after memify)
MATCH (e:Event) RETURN e ORDER BY e.timestamp DESC LIMIT 20
```

---

## Integration Tests

```bash
# against local stack
pytest tests/test_stack_integration.py -v

# against a remote host
BASE_URL=http://<host>:8000 MCP_URL=http://<host>:8002 pytest tests/test_stack_integration.py -v
```

Streaming smoke test:
```bash
python scripts/test_streaming.py
```

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

## MCP Wrapper Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_WRAPPER_PORT` | `8002` | Port the wrapper listens on |
| `MCP_WRAPPER_LOG_LEVEL` | `INFO` | Log level |
| `MCP_WRAPPER_API_KEY` | _(empty)_ | API key guard — empty = no auth |
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

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `{"detail":"Not Found"}` on `/tools` or `/health` | Container running old image — run `docker compose build mcp-wrapper && docker compose up -d mcp-wrapper` |
| `421 Invalid Host header` on direct MCP calls | Use the MCP Wrapper (:8002) instead of hitting Cognee MCP (:8001) directly |
| `circuit_breaker_open: true` in `/health` | Cognee MCP is unreachable — check `docker compose logs cognee-mcp` |
| `Unknown or expired session` | Call `initialize` first and include `mcp-session-id` header on all subsequent requests |
| `DatabaseNotCreatedError` after prune | Wait ~10s after first `cognify` call; DB setup is async |
| `ContentTypeError 404` on embeddings | Set `EMBEDDING_ENDPOINT` to include the full path (e.g. `.../api/embeddings` for Ollama) |
| `IngestionError: Local files are not accepted` | Set `ACCEPT_LOCAL_FILE_PATH=True` in `.env` |
| `IntegrityError: duplicate key "data_pkey"` | Harmless — same content hashes to same UUID; pipeline continues |
| Embedding dimension mismatch after LLM switch | Wipe `cognee_data` and `cognee_storage` volumes and restart |
| Graph HTML not updated after `visualize_graph` | Both `mcp-wrapper` and `web` mount the `graph_output` volume at `/graph` |
