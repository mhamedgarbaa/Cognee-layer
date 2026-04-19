# Cognee Memory MCP Stack

A self-contained, graph-backed memory microservice for AI agents.
Drop it into any agent stack and get persistent semantic memory via the
**Model Context Protocol (MCP)** — no SDK required, just HTTP.

---

## What it does

- Agents write facts/documents → Cognee extracts entities and builds a **Neo4j knowledge graph**
- Agents query → semantic **graph completion** returns synthesized answers
- Swap LLM backends by changing env vars (Ollama local or Azure OpenAI cloud)

```
Your Agent  ──HTTP JSON-RPC──►  Cognee MCP :8001
                                    │
                            ┌───────┴────────┐
                         Neo4j 5          Postgres 15
                         (graph)          (metadata)
                            └───────┬────────┘
                                 LanceDB
                                (vectors)
```

---

## Quick Start

### Prerequisites

- Docker Desktop (or Docker + Compose v2)
- For local LLM: **Ollama** running on the host with models pulled (see below)
- For cloud LLM: Azure OpenAI resource with `gpt-4o` and `text-embedding-3-small` deployed

### 1. Clone and configure

```bash
git clone <repo-url>
cd cognee-layer
cp example.env .env
```

Edit `.env` — minimum required:

```env
# --- Choose your LLM backend ---

# Option A: Local Ollama (default)
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:latest
LLM_ENDPOINT=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_ENDPOINT=http://host.docker.internal:11434/api/embeddings
EMBEDDING_DIMENSIONS=768
HUGGINGFACE_TOKENIZER=Salesforce/SFR-Embedding-Mistral

# Option B: Azure OpenAI
# LLM_PROVIDER=openai
# LLM_MODEL=gpt-4o
# LLM_ENDPOINT=https://<resource>.services.ai.azure.com/...
# LLM_API_KEY=<key>
# EMBEDDING_PROVIDER=openai
# EMBEDDING_MODEL=text-embedding-3-small
# EMBEDDING_ENDPOINT=https://<resource>.openai.azure.com/...
# EMBEDDING_API_KEY=<key>
# EMBEDDING_DIMENSIONS=1536
```

### 2. Pull Ollama models (local only)

```bash
ollama pull qwen2.5
ollama pull nomic-embed-text
```

### 3. Start the stack

```bash
docker compose up -d
```

Services start in dependency order: `db` → `neo4j` → `cognee-mcp` → `web`.
Wait ~60s for `cognee-mcp` to become healthy.

```bash
docker compose ps          # all should show "healthy" or "running"
curl http://localhost:8001/health   # {"status":"ok"}
curl http://localhost:8000/health   # {"status":"ok"}
```

---

## Expose the MCP Server to Your Agent

### Python agent (httpx)

```python
import httpx, json

MCP = "http://localhost:8001"
HEADERS = {"Accept": "application/json, text/event-stream", "Host": "localhost:8001"}

def sse(body): 
    for line in body.splitlines():
        if line.startswith("data:"): return json.loads(line[5:].strip())

# 1. Initialize session
resp = httpx.post(f"{MCP}/mcp", json={
    "jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05","capabilities":{},
              "clientInfo":{"name":"my-agent","version":"1.0"}}
}, headers=HEADERS)
session_id = resp.headers["mcp-session-id"]
headers = {**HEADERS, "mcp-session-id": session_id}

# 2. Store knowledge
httpx.post(f"{MCP}/mcp", json={
    "jsonrpc":"2.0","id":2,"method":"tools/call",
    "params":{"name":"cognify","arguments":{"data":"France 2030 budget is €30B","user":"agent1"}}
}, headers=headers)

# 3. Query knowledge
result = sse(httpx.post(f"{MCP}/mcp", json={
    "jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{"name":"search","arguments":{
        "search_query":"What is France 2030?",
        "search_type":"GRAPH_COMPLETION","user":"agent1"}}
}, headers=headers).text)
print(result["result"]["content"][0]["text"])
```

### Via FastAPI memory API (higher-level)

```bash
# Store a fact
curl -X POST http://localhost:8000/api/v1/memory/record \
  -H "Content-Type: application/json" \
  -d '{"user_id":"agent1","fact":"France 2030 budget is 30 billion euros"}'

# Recall with graph completion
curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d '{"user_id":"agent1","query":"What is France 2030?"}'
```

---

## Build and Deploy as a Standalone Microservice

Use this when you want to expose **only the MCP server** (no FastAPI web layer)
so other agents on the network can connect to it.

### Step 1 — Create a minimal compose file for the MCP service only

```yaml
# docker-compose.mcp-only.yml
version: "3.9"

services:
  db:
    image: postgres:15
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-cognee_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-cognee_pass}
      POSTGRES_DB: cognee_db
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-cognee_user} -d cognee_db"]
      interval: 10s
      timeout: 5s
      retries: 5

  neo4j:
    image: neo4j:5
    restart: unless-stopped
    environment:
      NEO4J_AUTH: ${NEO4J_USER:-neo4j}/${NEO4J_PASSWORD:-neo4j_pass}
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - neo4j_data:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:7474 || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 40s

  cognee-mcp:
    image: cognee/cognee-mcp:main
    restart: unless-stopped
    ports:
      - "8001:8000"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
      neo4j:
        condition: service_healthy
    environment:
      - TRANSPORT_MODE=http
      - ALLOWED_HOSTS=*
      - LLM_PROVIDER=${LLM_PROVIDER}
      - LLM_MODEL=${LLM_MODEL}
      - LLM_ENDPOINT=${LLM_ENDPOINT}
      - LLM_API_KEY=${LLM_API_KEY}
      - EMBEDDING_PROVIDER=${EMBEDDING_PROVIDER}
      - EMBEDDING_MODEL=${EMBEDDING_MODEL}
      - EMBEDDING_ENDPOINT=${EMBEDDING_ENDPOINT}
      - EMBEDDING_API_KEY=${EMBEDDING_API_KEY:-ollama}
      - EMBEDDING_DIMENSIONS=${EMBEDDING_DIMENSIONS:-768}
      - HUGGINGFACE_TOKENIZER=${HUGGINGFACE_TOKENIZER:-Salesforce/SFR-Embedding-Mistral}
      - DB_PROVIDER=postgres
      - DB_HOST=db
      - DB_PORT=5432
      - DB_NAME=cognee_db
      - DB_USERNAME=${POSTGRES_USER:-cognee_user}
      - DB_PASSWORD=${POSTGRES_PASSWORD:-cognee_pass}
      - VECTOR_DB_PROVIDER=lancedb
      - GRAPH_DATABASE_PROVIDER=neo4j
      - GRAPH_DATABASE_URL=bolt://neo4j:7687
      - GRAPH_DATABASE_USERNAME=${NEO4J_USER:-neo4j}
      - GRAPH_DATABASE_PASSWORD=${NEO4J_PASSWORD:-neo4j_pass}
      - ENABLE_BACKEND_ACCESS_CONTROL=False
      - ACCEPT_LOCAL_FILE_PATH=True
      - REQUIRE_AUTHENTICATION=False
    volumes:
      - cognee_data:/root/.cognee_system
    healthcheck:
      test: ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

volumes:
  pgdata:
  cognee_data:
  neo4j_data:

networks:
  default:
    name: cognee_mcp_net
```

### Step 2 — Start

```bash
docker compose -f docker-compose.mcp-only.yml up -d
```

### Step 3 — Verify

```bash
curl http://localhost:8001/health
# {"status":"ok"}
```

The MCP server is now accessible at **`http://<your-host-ip>:8001`** from any agent on your network.

### Step 4 — Connect a remote agent

```bash
# From another machine on the same network
MCP_URL=http://192.168.1.x:8001 python agent_chat.py
```

Or set in the agent's env:
```env
MCP_URL=http://192.168.1.x:8001
MCP_HOST_HEADER=localhost:8001
```

---

## Interactive Agent UI

A browser-based chat + document cognify interface powered by Groq (llama-4-scout):

```bash
# requires GROQ_API_KEY in .env
python agent_server.py
# open http://localhost:8080
```

| Panel | Function |
|-------|----------|
| Left — Chat | Talk to the Llama-4 agent; tool calls shown as pills in real-time |
| Right — Cognify Document | Paste any text/document and store it in the Neo4j graph |

---

## Direct MCP Agent (CLI)

Cognifies `bpi_france_events.json` directly against the MCP server — no FastAPI layer:

```bash
python test_agent_mcp.py
# or against a remote MCP:
MCP_URL=http://192.168.1.x:8001 python test_agent_mcp.py
```

---

## Integration Tests

```bash
# against local stack
pytest tests/test_stack_integration.py -v

# against a remote host
BASE_URL=http://<host>:8000 MCP_URL=http://<host>:8001 pytest tests/test_stack_integration.py -v
```

---

## Neo4j Browser

Explore the knowledge graph visually:

```
http://localhost:7474
Login: neo4j / neo4j_pass
```

---

## Switching LLM Backends

Edit `.env`, then:

```bash
# If embedding dimensions changed (e.g. 768→1536), wipe the vector store first
docker compose down
docker volume rm cognee-layer_cognee_data

# Restart with new config
docker compose up -d
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `421 Invalid Host header` | Add `Host: localhost:8001` to all MCP requests |
| `DatabaseNotCreatedError` after prune | Wait ~10s after first `cognify` call; DB setup is async |
| `ContentTypeError 404` on embeddings | Set `EMBEDDING_ENDPOINT` to include the full path: `.../api/embeddings` |
| `IngestionError: Local files are not accepted` | Set `ACCEPT_LOCAL_FILE_PATH=True` |
| `IntegrityError: duplicate key "data_pkey"` | Harmless — same content hashes to same UUID; pipeline continues |
| `GeminiException` / wrong provider | Check `LLM_PROVIDER` in container: `docker exec cognee_mcp_server env \| grep LLM` |
| Embedding dimension mismatch | Wipe `cognee_data` volume and restart |
