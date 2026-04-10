# Cognee Memory Subsystem - Startup Guide

## ✅ Installation Complete

The Cognee 3-layer memory subsystem has been fully integrated. All imports are working correctly.

## Prerequisites Installed

```bash
# Added to requirements.txt:
httpx==0.27.0  # Required for async HTTP calls to Cognee MCP
```

## Environment Configuration

A `.env` file has been created with all required settings:

### Database Settings
```
DB_USER=workspace_user
DB_PASSWORD=workspace_pass
DB_HOST=localhost          # Use 'db' in docker-compose
DB_PORT=5432
DB_NAME=workspace_db
```

### Cognee Memory Settings
```
COGNEE_MCP_ENDPOINT=http://localhost:8000/mcp
COGNEE_TIMEOUT_SECONDS=10
```

> **Note**: When using Docker, change `DB_HOST=localhost` to `DB_HOST=db` and `COGNEE_MCP_ENDPOINT=http://cognee-mcp:8000/mcp`

## Development Startup

### Option 1: Local with Docker PostgreSQL

Start the PostgreSQL database:
```bash
docker-compose up db -d
# Wait for healthy status
```

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the FastAPI app:
```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

The app will start at `http://localhost:8000`
- API Docs: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`

### Option 2: Full Docker Stack (with Cognee MCP)

Update `.env` for Docker:
```bash
DB_HOST=db
COGNEE_MCP_ENDPOINT=http://cognee-mcp:8000/mcp
```

Start all services:
```bash
docker-compose up -d
```

This starts:
- PostgreSQL database (port 5432)
- Cognee MCP server (port 8001)
- FastAPI application (port 8000)

## API Endpoints

### Record Memory
**Endpoint**: `POST /api/v1/memory/record`

**Request**:
```json
{
  "user_id": "user123",
  "fact": "User prefers dark mode"
}
```

**Response**: `202 Accepted`
```json
{
  "status": "accepted",
  "message": "Memory recorded and processing."
}
```

### Recall Memory
**Endpoint**: `POST /api/v1/memory/recall`

**Request**:
```json
{
  "user_id": "user123",
  "query": "What is the user preference?"
}
```

**Response**: `200 OK`
```json
{
  "context_data": [
    "User prefers dark mode"
  ],
  "is_degraded": false,
  "retrieval_method": "GRAPH_COMPLETION"
}
```

## Testing with cURL

```bash
# Record a memory
curl -X POST http://localhost:8000/api/v1/memory/record \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "fact": "Learned that user works with FastAPI"
  }'

# Recall memory
curl -X POST http://localhost:8000/api/v1/memory/recall \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "query": "What technology does the user work with?"
  }'
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│          API LAYER (FastAPI Routers)                │
│  POST /api/v1/memory/record                         │
│  POST /api/v1/memory/recall                         │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│       SERVICE LAYER (Business Logic)                │
│  MemoryService                                      │
│  - record_agent_memory()                            │
│  - recall_context() [Circuit Breaker]               │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│       DATA LAYER (Repository Pattern)               │
│  CogneeRepository                                   │
│  - save_interaction() → Cognee MCP                  │
│  - trigger_cognify() → Cognee MCP                   │
│  - search() → Cognee MCP                            │
└─────────────────────────────────────────────────────┘
                        ↓
      ┌────────────────────────────────────┐
      │   Cognee MCP HTTP Transport        │
      │   (Docker Service)                 │
      │   - SQLite (STM)                   │
      │   - LanceDB (Embeddings)           │
      │   - Kuzu (Temporal Graph)          │
      │   - Ollama/Mistral (LLM)           │
      └────────────────────────────────────┘
```

## Circuit Breaker Fallbacks

The `recall_context()` method gracefully degrades when systems fail:

1. **Primary**: GRAPH_COMPLETION
   - Uses LLM to synthesize temporal context
   - Slowest but most intelligent
   - Status: `is_degraded=false`

2. **Fallback 1**: CHUNKS
   - Raw semantic search (no LLM)
   - Falls back if Ollama/LLM is unavailable
   - Status: `is_degraded=true`, `retrieval_method="CHUNKS_FALLBACK"`

3. **Fallback 2**: NONE_FAILED
   - Returns empty context
   - Uses immediate chat history instead of memory
   - Status: `is_degraded=true`, `retrieval_method="NONE_FAILED"`

## Troubleshooting

### ❌ "Connection refused" on Cognee MCP

**Issue**: `COGNEE_MCP_ENDPOINT` is pointing to wrong address

**Solution**:
- Local dev: Use `http://localhost:8000/mcp`
- Docker: Use `http://cognee-mcp:8000/mcp`
- Verify Cognee service: `docker ps | grep cognee`

### ❌ Database connection errors

**Issue**: PostgreSQL not running or wrong credentials in `.env`

**Solution**:
```bash
# Verify database is healthy
docker-compose ps db

# Check credentials in .env
grep DB_ .env

# Recreate database
docker-compose down db
docker-compose up db -d
```

### ❌ Timeout errors in memory recall

**Issue**: Ollama or Cognee MCP is slow

**Solution**: Increase timeout in `.env`
```bash
COGNEE_TIMEOUT_SECONDS=30
```

### ❌ Embedding Configuration Error (Docker)

**Issue**: Cognee MCP fails with `Missing: ['EMBEDDING_DIMENSIONS', 'HUGGINGFACE_TOKENIZER']`

**Solution**: docker-compose.yml now includes these required variables:
```yaml
cognee-mcp:
  environment:
    - EMBEDDING_DIMENSIONS=768        # For nomic-embed-text
    - HUGGINGFACE_TOKENIZER=nomic-ai/nomic-embed-text-v1
```

These are already set correctly in the updated docker-compose.yml.

## Architecture Files Created

```
configuration/settings.py          # ✏️ Added CogneeSettings
data/custom_data_exceptions.py     # ✏️ Added Cognee exceptions
data/repositories/cognee_repository.py  # 🆕 MCP client
service/custom_service_exceptions.py    # ✏️ Added MemoryServiceUnavailable
service/business_models.py          # 🆕 ContextEnvelope model
service/memory_service.py           # 🆕 Circuit breaker logic
api/dependencies.py                 # ✏️ Added Cognee DI wiring
api/custom_api_exceptions.py        # ✏️ Added MemoryServiceError
api/schemas/memory_schema.py        # 🆕 Request/response schemas
api/routers/v1/memory_router.py    # 🆕 REST endpoints
app.py                              # ✏️ Registered memory router
docker-compose.yml                  # ✏️ Added Cognee MCP service
requirements.txt                    # ✏️ Added httpx
.env                                # 🆕 Configuration file
example.env                         # ✏️ Updated template
```

## Next Steps

1. ✅ **Dependencies installed** (`httpx`)
2. ✅ **Environment configured** (`.env` created)
3. ✅ **All imports verified** (successfully imported)
4. 🔄 **Start PostgreSQL** and test connectivity
5. 🔄 **Configure Ollama** with Mistral + embeddings (if using Docker)
6. 🔄 **Test memory endpoints** with cURL/Postman
7. 🔄 **Monitor logs** for circuit breaker fallbacks

## Default Credentials

| Service | User | Password | Port |
|---------|------|----------|------|
| PostgreSQL | `workspace_user` | `workspace_pass` | 5432 |
| FastAPI | — | — | 8000 |
| Cognee MCP | — | — | 8001 (ext) / 8000 (int) |

---

**Status**: ✅ Ready for development
**Last Updated**: 2026-04-10
**Architecture**: 3-Layer (Data → Service → API)
