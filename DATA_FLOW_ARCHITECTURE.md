# Complete Data Flow Architecture

## High-Level Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    EXTERNAL CLIENTS                              │
│  (AI Agents, FastAPI Users, Cognee Fetcher/Connector)            │
└────┬────────────────────────────────────────────────────────┬────┘
     │                                                        │
     │ REQUEST                                                │ RESPONSE
     │ (JSON)                                                 │ (JSON)
     │                                                        │
┌────▼────────────────────────────────────────────────────────▼────┐
│                    API LAYER (6 endpoints)                        │
│                                                                  │
│  ── Core ──────────────────────────────────────────────────────── │
│  POST   /api/v1/memory/record          RecordMemoryRequest       │
│  POST   /api/v1/memory/recall          RecallMemoryRequest       │
│                                                                  │
│  ── Management ───────────────────────────────────────────────── │
│  GET    /api/v1/memory/data            DataInventoryResponse     │
│  DELETE /api/v1/memory/data            DeleteMemoryRequest       │
│  DELETE /api/v1/memory/prune           PruneMemoryResponse       │
│                                                                  │
│  ── Monitoring ───────────────────────────────────────────────── │
│  GET    /api/v1/memory/cognify/status  CognifyStatusResponse     │
│                                                                  │
│  GET    /health                        Health Status             │
└────┬────────────────────────────────────────────────────────┬────┘
     │                                                        │
     │ Service calls                                          │ Response modeling
     │ (MemoryService)                                        │ (Pydantic schema)
     │                                                        │
┌────▼────────────────────────────────────────────────────────▼────┐
│                   SERVICE LAYER                                  │
│  MemoryService                                                   │
│  ├─ record_agent_memory()          → save + cognify              │
│  ├─ recall_context()               → search [Circuit Breaker]    │
│  ├─ get_data_inventory()           → list_data                   │
│  ├─ delete_memory_data()           → delete                      │
│  ├─ prune_all_memory()             → prune                       │
│  └─ get_cognify_status()           → cognify_status              │
└────┬────────────────────────────────────────────────────────┬────┘
     │                                                        │
     │ HTTP calls                                             │ Domain models
     │ (httpx.AsyncClient)                                    │ (business_models)
     │                                                        │
┌────▼────────────────────────────────────────────────────────▼────┐
│                   DATA LAYER                                     │
│  CogneeRepository                                                │
│  ├─ save_interaction()    → MCP tool: save_interaction           │
│  ├─ trigger_cognify()     → MCP tool: cognify                    │
│  ├─ search()              → MCP tool: search                     │
│  ├─ list_data()           → MCP tool: list_data                  │
│  ├─ delete_data()         → MCP tool: delete                     │
│  ├─ prune()               → MCP tool: prune                      │
│  └─ cognify_status()      → MCP tool: cognify_status             │
└────┬────────────────────────────────────────────────────────┬────┘
     │                                                        │
     │ JSON-RPC HTTP POST                                     │ JSON-RPC Response
     │ (tools/call protocol)                                  │ (result/error)
     │                                                        │
┌────▼────────────────────────────────────────────────────────▼────┐
│              COGNEE MCP (Docker Container)                       │
│  HTTP Streamable Transport (:8001)                               │
│                                                                  │
│  ── Core Operations ──────────────────────────────────────────── │
│  ├─ save_interaction()   Ingestion Engine (STM fast append)      │
│  ├─ cognify()            Cognify Pipeline (graph construction)   │
│  ├─ search()             Query Engine  (7 search types)          │
│                                                                  │
│  ── Data Management ──────────────────────────────────────────── │
│  ├─ list_data()          Data Manager  (inventory of datasets)   │
│  ├─ delete()             Data Manager  (soft/hard delete)        │
│  ├─ prune()              Data Manager  (full reset)              │
│                                                                  │
│  ── Monitoring ───────────────────────────────────────────────── │
│  └─ cognify_status()     Pipeline Monitor (job progress)         │
└────┬────────────────────────────────────────────────────────┬────┘
     │                                                        │
     │ Internals                                              │ Results
     │ (async operations)                                     │ (structured data)
     │                                                        │
┌────▼────────────────────────────────────────────────────────▼────┐
│                COGNEE CORE ENGINE (v2.0 — Plug-and-Play)         │
│                                                                  │
│  ┌─────────────────┐      ┌─────────────────┐                    │
│  │ STM (Postgres)  │      │ LTM (Neo4j 5)   │                    │
│  │ cognee_db       │      │ bolt://neo4j:   │                    │
│  │ Raw text + ts   │      │   7687 + APOC   │                    │
│  └─────────────────┘      └─────────────────┘                    │
│                                                                  │
│  ┌─────────────────┐      ┌─────────────────┐                    │
│  │ LanceDB         │      │ Azure OpenAI    │                    │
│  │ 1536D vectors   │      │ gpt-4o + embed  │                    │
│  │ (file-backed)   │      │ (HTTPS)         │                    │
│  └─────────────────┘      └─────────────────┘                    │
└──────────────────────────────────────────────────────────────────┘
```

*Runtime note (v2.0)*: Neo4j is populated synchronously during `cognify`. The prior SQLite + LanceDB fallback for empty Kuzu is no longer needed, though Postgres `graph_relationship_ledger` still provides an audit trail of relationship lineage.

---

## 1. WRITE PATH: Recording Memory

### 1.1 Client → API Layer

**Input** (What the client sends):
```json
POST /api/v1/memory/record
Content-Type: application/json

{
  "user_id": "user123",
  "fact": "User prefers dark mode and async programming"
}
```

**Class**: `RecordMemoryRequest` (from `api/schemas/memory_schema.py`)
```python
class RecordMemoryRequest(BaseModel):
    user_id: str = Field(..., description="Tenant or User ID for isolation")
    fact: str = Field(..., description="The observation to record")
```

---

### 1.2 API Layer → Service Layer

**Data transformation**:
```python
# api/routers/v1/memory_router.py
@router.post("/record", status_code=status.HTTP_202_ACCEPTED)
async def record_memory(
    request: RecordMemoryRequest,  # ← Pydantic validates & deserializes
    service: MemoryService = Depends(get_memory_service)
):
    # Extract fields
    user_id = request.user_id      # "user123"
    fact = request.fact             # "User prefers dark mode..."

    # Call service (no transformation, pass through)
    await service.record_agent_memory(user_id, fact)

    # Return immediate response
    return {
        "status": "accepted",
        "message": "Memory recorded and processing."
    }
```

**Output to client** (202 Accepted = fire-and-forget):
```json
HTTP 202 Accepted

{
  "status": "accepted",
  "message": "Memory recorded and processing."
}
```

---

### 1.3 Service Layer → Data Layer

**Call signature**:
```python
# service/memory_service.py
async def record_agent_memory(self, user_id: str, fact: str) -> None:
    """
    Stores a fact and triggers background temporal consolidation.

    Args:
        user_id: "user123" (immutable, thread through all layers)
        fact: "User prefers dark mode and async programming"
    """
    try:
        # 1. Save to STM (Short-Term Memory) - Fast append
        await self.repo.save_interaction(
            user_id=user_id,
            data=fact
        )

        # 2. Trigger LTM consolidation (background, non-blocking)
        await self.repo.trigger_cognify(
            user_id=user_id
        )

        logger.info("Memory recorded and consolidation triggered",
                   extra={"user_id": user_id})

    except CogneeConnectionError as e:
        logger.error(f"Failed to record memory: {e}")
        raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
```

---

### 1.4 Data Layer → HTTP Transport

**HTTP JSON-RPC Calls to Cognee MCP**:

#### Call 1: Save to STM
```python
# data/repositories/cognee_repository.py
async def save_interaction(self, user_id: str, data: str) -> None:
    payload = {
        "method": "tools/call",
        "params": {
            "name": "save_interaction",
            "arguments": {
                "user": user_id,        # "user123"
                "data": data            # "User prefers dark mode..."
            }
        }
    }

    response = await self.client.post(
        "http://cognee-mcp:8000/mcp",  # Or localhost:8001 locally
        json=payload,
        timeout=15
    )
```

**HTTP POST Body**:
```json
{
  "method": "tools/call",
  "params": {
    "name": "save_interaction",
    "arguments": {
      "user": "user123",
      "data": "User prefers dark mode and async programming"
    }
  }
}
```

**HTTP Response** (Success):
```json
{
  "result": {
    "status": "saved",
    "data_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

#### Call 2: Trigger Cognify
```python
async def trigger_cognify(self, user_id: str) -> None:
    payload = {
        "method": "tools/call",
        "params": {
            "name": "cognify",
            "arguments": {
                "user": user_id,                    # "user123"
                "temporal_cognify": True,           # Enable temporal reasoning
                "run_in_background": True           # Non-blocking
            }
        }
    }

    response = await self.client.post(
        "http://cognee-mcp:8000/mcp",
        json=payload,
        timeout=15
    )
```

**HTTP POST Body**:
```json
{
  "method": "tools/call",
  "params": {
    "name": "cognify",
    "arguments": {
      "user": "user123",
      "temporal_cognify": true,
      "run_in_background": true
    }
  }
}
```

**HTTP Response** (Background task launched):
```json
{
  "result": {
    "status": "processing",
    "task_id": "cognify-user123-2026-04-10T12:34:56Z",
    "message": "Background process launched"
  }
}
```

---

### 1.5 Cognee MCP Internal Processing

**What Cognee does in background** (asynchronous):

```
Input: user_id="user123", data="User prefers dark mode and async programming"

Step 1: Write to STM (Postgres — cognee_db)
  INSERT INTO interactions (user_id, data, timestamp)
  VALUES ('user123', 'User prefers dark mode...', NOW())

Step 2: Queue cognify background task

Step 3: Cognify process begins (async)
  ├─ Chunk text: ["User prefers dark mode", "async programming"]
  ├─ LLM Extract entities:
  │  {
  │    "entities": [
  │      {"name": "dark_mode", "type": "preference"},
  │      {"name": "async_programming", "type": "technology"}
  │    ],
  │    "relationships": [
  │      {"source": "User", "target": "dark_mode", "type": "PREFERS"}
  │    ],
  │    "temporal": {
  │      "valid_from": "2026-04-10T12:34:56Z",
  │      "valid_to": null
  │    }
  │  }
  ├─ Generate embeddings: 1536D vectors (Azure OpenAI text-embedding-3-small)
  ├─ Store relationship lineage in Postgres ledger (`graph_relationship_ledger`)
  ├─ Store payload vectors in LanceDB (vector DB)
  ├─ Write traversal graph to Neo4j (bolt://neo4j:7687)
  │  (User)-[PREFERS {valid_from: ..., valid_to: null}]->(dark_mode)
  └─ Store semantic embedding:
     "dark_mode_preference" -> [0.234, -0.512, 0.891, ...]  # 1536D

Output: LTM artifacts updated (Postgres ledger + LanceDB + Neo4j)
```

---

## 2. READ PATH: Recalling Memory

### 2.1 Client → API Layer

**Input** (What the client sends):
```json
POST /api/v1/memory/recall
Content-Type: application/json

{
  "user_id": "user123",
  "query": "What is the user's preference for coding?"
}
```

**Class**: `RecallMemoryRequest`
```python
class RecallMemoryRequest(BaseModel):
    user_id: str = Field(..., description="Tenant or User ID for isolation")
    query: str = Field(..., description="The temporal or semantic question")
```

---

### 2.2 API Layer → Service Layer

**Code path**:
```python
# api/routers/v1/memory_router.py
@router.post("/recall", response_model=ContextResponseSchema)
async def recall_memory(
    request: RecallMemoryRequest,
    service: MemoryService = Depends(get_memory_service)
):
    # Extract
    user_id = request.user_id      # "user123"
    query = request.query           # "What is the user's preference..."

    # Call service (with circuit breaker)
    context_envelope = await service.recall_context(user_id, query)

    # Map domain model to API schema
    return ContextResponseSchema(
        context_data=context_envelope.context_data,
        is_degraded=context_envelope.is_degraded,
        retrieval_method=context_envelope.retrieval_method
    )
```

---

### 2.3 Service Layer: Circuit Breaker Logic

```python
# service/memory_service.py
async def recall_context(self, user_id: str, query: str) -> ContextEnvelope:
    """Retrieves context with Graceful Degradation (Circuit Breaker)."""

    # ┌─ LEVEL 1: PRIMARY PATH (LLM + Graph)
    # │
    try:
        # Cognee MCP search with LLM synthesis
        results = await self.repo.search(
            user_id=user_id,           # "user123"
            query=query,               # "What is the user's preference..."
            query_type="GRAPH_COMPLETION"  # Full LLM + graph synthesis
        )

        # ✅ Success
        return ContextEnvelope(
            context_data=[str(r) for r in results],
            is_degraded=False,
            retrieval_method="GRAPH_COMPLETION"
        )

    # ├─ LEVEL 2: FALLBACK 1 (Semantic search only, no LLM)
    # │
    except (CogneeConnectionError, CogneeToolError) as e:
        logger.warning(
            f"GRAPH_COMPLETION failed, falling back to CHUNKS. Error: {e}",
            extra={"user_id": user_id}
        )

        try:
            # Fast semantic search (no LLM needed)
            fallback_results = await self.repo.search(
                user_id=user_id,
                query=query,
                query_type="CHUNKS"  # Raw text chunks, vector similarity only
            )

            # ✅ Fallback success
            return ContextEnvelope(
                context_data=[str(r) for r in fallback_results],
                is_degraded=True,
                retrieval_method="CHUNKS_FALLBACK"
            )

        # └─ LEVEL 3: FINAL FALLBACK (Complete failure)
        #
        except Exception as critical_error:
            logger.error(f"Total memory subsystem failure: {critical_error}")

            # Return empty context, agent uses chat history
            return ContextEnvelope(
                context_data=[],
                is_degraded=True,
                retrieval_method="NONE_FAILED"
            )
```

---

### 2.4 Data Layer → HTTP Transport

**HTTP JSON-RPC Call to Cognee MCP**:

```python
# data/repositories/cognee_repository.py
async def search(self, user_id: str, query: str, query_type: str) -> list:
    payload = {
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {
                "user": user_id,                # "user123"
                "query_text": query,            # "What is the user's preference..."
                "query_type": query_type,       # "GRAPH_COMPLETION" or "CHUNKS"
                "top_k": 10                     # Max results
            }
        }
    }

    response = await self.client.post(
        "http://cognee-mcp:8000/mcp",
        json=payload,
        timeout=15
    )
```

**HTTP POST Body** (Level 1):
```json
{
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {
      "user": "user123",
      "query_text": "What is the user's preference for coding?",
      "query_type": "GRAPH_COMPLETION",
      "top_k": 10
    }
  }
}
```

---

### 2.5 Cognee MCP Internal Processing

**Search Type 1: GRAPH_COMPLETION** (Primary):
```
Input: user_id="user123", query="What is the user's preference for coding?"

Step 1: Convert query to embedding (1536D, Azure OpenAI)
  "user_preference_coding" → [0.156, -0.423, 0.782, ...]

Step 2: Vector DB lookup (LanceDB)
  Find K nearest entities matching query embedding
  Results:
    1. dark_mode (similarity: 0.94)
    2. async_programming (similarity: 0.89)
    3. FastAPI (similarity: 0.78)

Step 3: Graph traversal (Neo4j via Bolt)
    MATCH (u:User)-[p:PREFERS]->(pref:Preference)
    WHERE u.id = 'user123' AND p.valid_from <= datetime()
    RETURN construct temporal subgraph
  Audit trail: Postgres `graph_relationship_ledger` (not queried on hot path)

Step 4: LLM Synthesis (Azure OpenAI gpt-4o)
  Prompt: "Based on this context, answer: What is the user's preference for coding?"
  Context: [temporal edges from graph]

  Response: "The user prefers dark mode and async programming in FastAPI"

Output to client: Synthesized answer
```

**Search Type 2: CHUNKS** (Fallback):
```
Input: user_id="user123", query="What is the user's preference for coding?"

Step 1: Vector search only (no LLM)
  Find matching text chunks by embedding similarity

Output: Raw text passages, no synthesis
```

**HTTP Response** (Level 1: Success):
```json
{
  "result": {
    "data": [
      "The user prefers dark mode and async programming in FastAPI",
      "User has shown interest in async patterns"
    ],
    "source": "graph_completion",
    "confidence": 0.94
  }
}
```

**HTTP Response** (Level 2: Fallback):
```json
{
  "result": {
    "data": [
      "User prefers dark mode and async programming",
      "async programming mentioned in context"
    ],
    "source": "chunks",
    "confidence": 0.78
  }
}
```

**HTTP Response** (Level 3: Error):
```json
{
  "error": {
    "code": -32603,
    "message": "Internal error: Search pipeline timeout"
  }
}
```

---

### 2.6 Service Layer: Data Formatting

```python
# service/memory_service.py returns ContextEnvelope
ContextEnvelope(
    context_data=[
        "The user prefers dark mode and async programming in FastAPI",
        "User has shown interest in async patterns"
    ],
    is_degraded=False,
    retrieval_method="GRAPH_COMPLETION"
)
```

---

### 2.7 API Layer: Response Formatting

**Domain model → API Schema**:
```python
ContextResponseSchema(
    context_data=[
        "The user prefers dark mode and async programming in FastAPI",
        "User has shown interest in async patterns"
    ],
    is_degraded=False,
    retrieval_method="GRAPH_COMPLETION"
)
```

**Output to client** (200 OK):
```json
HTTP 200 OK

{
  "context_data": [
    "The user prefers dark mode and async programming in FastAPI",
    "User has shown interest in async patterns"
  ],
  "is_degraded": false,
  "retrieval_method": "GRAPH_COMPLETION"
}
```

---

## 3. ERROR HANDLING DATA FLOW

### 3.1 Network Error (Cognee MCP Down)

**At Data Layer**:
```python
try:
    response = await self.client.post(
        "http://cognee-mcp:8000/mcp",
        json=payload,
        timeout=15
    )
except httpx.RequestError as e:
    raise CogneeConnectionError(
        f"Failed to connect to Cognee MCP: {str(e)}"
    )
```

**Raised exception**:
```python
CogneeConnectionError("Failed to connect to Cognee MCP: Connection refused")
```

**At Service Layer**:
```python
except (CogneeConnectionError, CogneeToolError) as e:
    # Trip to fallback
```

**At API Layer**:
```python
except MemoryServiceUnavailable as e:
    raise MemoryServiceError(detail=str(e))
    # → HTTP 503 Service Unavailable
```

**Output to client**:
```json
HTTP 503 Service Unavailable

{
  "detail": "Memory processing failed."
}
```

---

### 3.2 Timeout Error (Cognee too slow)

```python
# Data layer catches timeout
response = await self.client.post(
    ...,
    timeout=15  # 15 second timeout
)
# If no response after 15s → httpx.TimeoutException

# Service layer catches it
except (CogneeConnectionError, CogneeToolError) as e:
    # Fallback to CHUNKS (faster, no LLM)

# Returns degraded response
ContextEnvelope(
    context_data=[...],
    is_degraded=True,
    retrieval_method="CHUNKS_FALLBACK"
)
```

---

## 4. DATA STRUCTURES AT EACH LAYER

### 4.1 API Layer Schemas

```python
# Input
class RecordMemoryRequest(BaseModel):
    user_id: str
    fact: str

class RecallMemoryRequest(BaseModel):
    user_id: str
    query: str

# Output
class ContextResponseSchema(BaseModel):
    context_data: List[str]
    is_degraded: bool
    retrieval_method: str
```

### 4.2 Service Layer Models

```python
class ContextEnvelope(BaseModel):
    """Internal domain model"""
    context_data: List[str]
    is_degraded: bool
    retrieval_method: str
```

### 4.3 Data Layer (Cognee Format)

```python
# What gets sent to Cognee MCP — all 7 tools
{
    "method": "tools/call",
    "params": {
        "name": "save_interaction" | "cognify" | "search"
               | "list_data" | "delete" | "prune" | "cognify_status",
        "arguments": {
            # save_interaction:
            #   "user": "...", "data": "..."
            # cognify:
            #   "data": "..."
            # search:
            #   "search_query": "...", "search_type": "GRAPH_COMPLETION"|"CHUNKS"|..., "top_k": 10
            # list_data:
            #   "dataset_id": "..." (optional)
            # delete:
            #   "data_id": "...", "dataset_id": "...", "mode": "soft"|"hard"
            # prune / cognify_status:
            #   {} (no arguments)
        }
    }
}
```

---

## 5. TIMELINEOF A COMPLETE CYCLE

```
┌─────────────────────────────────────────────────────────────────┐
│ T+0ms: Client sends record request                             │
│ POST /api/v1/memory/record                                     │
│ {"user_id": "user123", "fact": "..."}                         │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+1ms: API validates pydantic schema                           │
│ ✓ user_id matches pattern                                      │
│ ✓ fact is non-empty string                                     │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+2ms: Service layer calls repository                          │
│ await repo.save_interaction(user_id, fact)                     │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+5ms: HTTP POST to Cognee MCP (save_interaction)              │
│ Network latency: ~3ms                                           │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+10ms: HTTP POST to Cognee MCP (cognify in background)        │
│ Returns immediately (background task)                           │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+15ms: API returns 202 Accepted to client                     │
│ {"status": "accepted", "message": "..."}                       │
└─────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────┐
│ T+0ms (Later): Client sends recall request                     │
│ POST /api/v1/memory/recall                                     │
│ {"user_id": "user123", "query": "What is the preference?"}    │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+1ms: API validates                                           │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+2ms: Service layer attempts GRAPH_COMPLETION                │
│ await repo.search(user_id, query, "GRAPH_COMPLETION")          │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+5ms: HTTP POST to Cognee MCP                                 │
│ Network: ~3ms                                                   │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+50ms: Cognee processes (Vector DB + Graph + LLM)             │
│ ├─ Vector search: 10ms                                         │
│ ├─ Graph traversal: 15ms                                       │
│ └─ LLM synthesis: 20ms                                         │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+53ms: HTTP response from Cognee                              │
│ {"result": {"data": ["The user prefers..."], ...}}            │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+56ms: Service layer returns ContextEnvelope                  │
│ is_degraded=false, retrieval_method="GRAPH_COMPLETION"        │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+57ms: API returns 200 OK to client                           │
│ {"context_data": [...], "is_degraded": false, ...}            │
└─────────────────────────────────────────────────────────────────┘

TOTAL LATENCY: ~57ms (target: <500ms)
```

---

## 6. DATA FLOW WITH FALLBACK SCENARIO

```
┌─────────────────────────────────────────────────────────────────┐
│ T+0ms: Client sends recall request                             │
│ {"user_id": "user123", "query": "What is the preference?"}    │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+2ms: Service attempts GRAPH_COMPLETION                       │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+5ms-20ms: HTTP to Cognee (Ollama LLM is DOWN)               │
│ Error: Connection timeout                                       │
│ ❌ CogneeConnectionError raised                                │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+22ms: Service catches error, falls back to CHUNKS             │
│ await repo.search(..., query_type="CHUNKS")                     │
│ (No LLM needed, much faster)                                    │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+25ms: HTTP to Cognee (Vector DB is UP ✓)                    │
│ Returns semantic search results (no LLM)                        │
│ Response: ["User prefers dark mode", "async programming"]      │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+28ms: Service returns ContextEnvelope with is_degraded=true  │
│ retrieval_method="CHUNKS_FALLBACK"                             │
└──────────┬────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│ T+29ms: API returns 200 OK (NOT error!)                        │
│ {"context_data": [...], "is_degraded": true, ...}            │
└─────────────────────────────────────────────────────────────────┘

TOTAL LATENCY: ~29ms (faster than primary!)
GRACEFUL DEGRADATION: ✓ Still returned results
```

---

## 7. COMPLETE REQUEST/RESPONSE MAPPING TABLE

| Stage | Component | IN | OUT | Type |
|-------|-----------|---|-----|------|
| **1** | Client | HTTP POST/GET/DELETE | Request Schema | JSON |
| **2** | API Router | Request Schema | Call MemoryService | Python object |
| **3** | Service | Service args | Call Repository | Python args |
| **4** | Repository | Repository args | HTTP POST to MCP | JSON-RPC |
| **5** | Cognee MCP | JSON-RPC | Internal processing | Binary (DB ops) |
| **6** | Cognee MCP | Processing complete | HTTP response | JSON |
| **7** | Repository | JSON response | Return to Service | Python list/dict |
| **8** | Service | Raw result | Return domain model | Python object |
| **9** | API | Domain model | Response Schema | Pydantic model |
| **10** | Client | HTTP 200/202 | Response JSON | JSON |

### Endpoint → Service → Repository → MCP Tool Mapping

| API Endpoint | Service Method | Repository Method | MCP Tool |
|---|---|---|---|
| `POST /memory/record` | `record_agent_memory()` | `save_interaction()` + `trigger_cognify()` | `save_interaction` + `cognify` |
| `POST /memory/recall` | `recall_context()` | `search()` | `search` |
| `GET /memory/data` | `get_data_inventory()` | `list_data()` | `list_data` |
| `DELETE /memory/data` | `delete_memory_data()` | `delete_data()` | `delete` |
| `DELETE /memory/prune` | `prune_all_memory()` | `prune()` | `prune` |
| `GET /memory/cognify/status` | `get_cognify_status()` | `cognify_status()` | `cognify_status` |

---

## 8. Environment Variables Affecting Data Flow

```
# Connection settings
COGNEE_MCP_ENDPOINT=http://cognee-mcp:8000/mcp
COGNEE_TIMEOUT_SECONDS=15

# Controls how long repository waits for response
# If Cognee doesn't respond in 15s → CogneeConnectionError
# → Circuit breaker triggers → Fallback to CHUNKS

# Database settings
DB_HOST=db              # Workspace data (separate from Cognee)
DB_USER=workspace_user
DB_NAME=workspace_db

# Inside Cognee MCP container (docker-compose.yml) — v2.0 Plug-and-Play
LLM_PROVIDER=azure                # was: ollama
LLM_MODEL=gpt-4o                  # was: mistral
LLM_ENDPOINT=https://<azure>.services.ai.azure.com/...  # was: http://host.docker.internal:11434
OPENAI_API_KEY=<azure-key>        # required

EMBEDDING_PROVIDER=openai         # was: ollama
EMBEDDING_MODEL=text-embedding-3-small  # was: nomic-embed-text
EMBEDDING_DIMENSIONS=1536         # was: 768

DB_PROVIDER=postgres              # was: sqlite
DB_HOST=db                        # Docker service DNS
DB_NAME=cognee_db                 # separate from app's workspace_db

GRAPH_DATABASE_PROVIDER=neo4j     # was: kuzu
GRAPH_DATABASE_URL=bolt://neo4j:7687
VECTOR_DB_PROVIDER=lancedb        # unchanged

# Security flags (new in v2.0)
ENABLE_BACKEND_ACCESS_CONTROL=True
ACCEPT_LOCAL_FILE_PATH=False
REQUIRE_AUTHENTICATION=False
```

---

## 9. Summary: Data Field Transformations

```
CLIENT INPUT
    │
    ▼
{"user_id": "user123", "fact": "prefers dark mode"}
    │
    ▼ Pydantic validation
    │
RecordMemoryRequest(user_id="user123", fact="prefers dark mode")
    │
    ▼ Extract fields
    │
("user123", "prefers dark mode")
    │
    ▼ HTTP JSON-RPC serialization
    │
{
  "method": "tools/call",
  "params": {
    "name": "save_interaction",
    "arguments": {"user": "user123", "data": "prefers dark mode"}
  }
}
    │
    ▼ Cognee internal storage
    │
SQLite: INSERT INTO interactions VALUES ('user123', 'prefers dark mode', NOW())
    │
    ▼ Cognify process (background)
    │
  SQLite ledger: source_node_id/destination_node_id edges
  LanceDB: typed payload nodes + 768D embeddings
  Kuzu Graph: traversal projection (optional; may be empty)
    │
    ▼ Later: Recall query
    │
{"user_id": "user123", "query": "What is preference?"}
    │
    ▼ Service layer recall_context()
    │
Try: repo.search(..., "GRAPH_COMPLETION")
    │
    ▼ Cognee search (Vector DB + Graph + LLM)
    │
["The user prefers dark mode"]
    │
    ▼ ContextEnvelope wrapping
    │
ContextEnvelope(
  context_data=["The user prefers dark mode"],
  is_degraded=False,
  retrieval_method="GRAPH_COMPLETION"
)
    │
    ▼ API response schema
    │
{
  "context_data": ["The user prefers dark mode"],
  "is_degraded": false,
  "retrieval_method": "GRAPH_COMPLETION"
}
    │
    ▼
CLIENT OUTPUT
```

## 10. MANAGEMENT PATH: Data Inventory & Deletion

### 10.1 List Data — `GET /api/v1/memory/data`

```
Client → GET /api/v1/memory/data?dataset_id=<optional UUID>
  │
  ▼ API Router
list_memory_data(dataset_id)
  │
  ▼ Service Layer
get_data_inventory(dataset_id)
  │
  ▼ Data Layer
repo.list_data(dataset_id)
  │
  ▼ HTTP JSON-RPC
{
  "method": "tools/call",
  "params": {
    "name": "list_data",
    "arguments": {"dataset_id": "<UUID>"}
  }
}
  │
  ▼ Response
{
  "datasets": [
    {"dataset_id": "abc-123", "items": [
      {"data_id": "def-456", "name": "...", "created_at": "..."}
    ]}
  ]
}
```

### 10.2 Delete Data — `DELETE /api/v1/memory/data`

```
Client → DELETE /api/v1/memory/data
  Body: {"data_id": "def-456", "dataset_id": "abc-123", "mode": "soft"}
  │
  ▼ API Router
delete_memory_data(request)
  │
  ▼ Service Layer
delete_memory_data(data_id, dataset_id, mode)
  │
  ▼ Data Layer
repo.delete_data(data_id, dataset_id, mode)
  │
  ▼ HTTP JSON-RPC
{
  "method": "tools/call",
  "params": {
    "name": "delete",
    "arguments": {
      "data_id": "def-456",
      "dataset_id": "abc-123",
      "mode": "soft"
    }
  }
}
  │
  ▼ Response
{"status": "deleted", "details": {"deleted_nodes": 3}}
```

**Deletion Modes**:
- `soft` — Removes the data item but preserves shared entity nodes
- `hard` — Also removes degree-one entity nodes that become orphaned

---

## 11. ADMIN PATH: Prune (Full Reset)

### `DELETE /api/v1/memory/prune`

```
Client → DELETE /api/v1/memory/prune
  │
  ▼ API Router → Service → Repository
  │
  ▼ HTTP JSON-RPC
{
  "method": "tools/call",
  "params": {"name": "prune", "arguments": {}}
}
  │
  ▼ Cognee MCP
  ├─ prune_data()    → Wipes all datasets, chunks, embeddings
  └─ prune_system()  → Wipes all graph nodes, edges, metadata
  │
  ▼ Response
{"status": "pruned", "message": "Knowledge graph reset complete."}
```

⚠️ **This is irreversible.** All STM, LTM, embeddings, and graph data are permanently destroyed.

---

## 12. MONITORING PATH: Cognify Status

### `GET /api/v1/memory/cognify/status`

```
Client → GET /api/v1/memory/cognify/status
  │
  ▼ API Router → Service → Repository
  │
  ▼ HTTP JSON-RPC
{
  "method": "tools/call",
  "params": {"name": "cognify_status", "arguments": {}}
}
  │
  ▼ Response
{
  "status": "completed",
  "details": {
    "active_jobs": 0,
    "completed_jobs": 5,
    "last_run": "2026-04-15T10:30:00Z",
    "pipeline": "cognify_pipeline",
    "dataset": "main_dataset"
  }
}
```

**Use cases**:
- After calling `POST /memory/record`, poll this endpoint to know when cognify finishes
- Monitor background pipeline health
- Detect stuck or failed cognify jobs

---

## 13. Key Insights

✅ **Layered data transformations**: Each layer adds structure/meaning
✅ **Immutable user_id**: Threaded through entire stack for isolation
✅ **Fire-and-forget writes**: 202 Accepted allows background processing
✅ **Circuit breaker on network boundary**: Fallback happens at Data→Service boundary
✅ **Status signaling**: `is_degraded` flag tells client whether to trust context
✅ **Zero data loss**: Even if Cognee is down, facts saved to STM first
✅ **Full tool coverage**: All 7 Cognee MCP tools are exposed through the API
✅ **Data lifecycle management**: list → inspect → delete individual items or prune all
✅ **Pipeline observability**: cognify_status enables polling-based progress tracking

---

## 14. Migration Notes — v1.1 → v2.0 (2026-04-17)

The v1.1 → v2.0 cut replaced all embedded stores with networked backends:

| Area | v1.1 | v2.0 |
|------|------|------|
| Relational | SQLite (in Cognee container volume) | Postgres 15 (`db` service, `cognee_db` database) |
| Graph | Kuzu (embedded, often empty) | Neo4j 5 (`neo4j` service, Bolt `:7687`) |
| LLM | Ollama + Mistral (`host.docker.internal:11434`) | Azure OpenAI `gpt-4o` (HTTPS) |
| Embeddings | `nomic-embed-text` 768D (Ollama) | `text-embedding-3-small` 1536D (Azure OpenAI) |
| Vector | LanceDB 768D | LanceDB 1536D |
| Security | (none) | `ENABLE_BACKEND_ACCESS_CONTROL`, `ACCEPT_LOCAL_FILE_PATH=False` |

**Data compatibility**:
- LanceDB tables from v1.1 use 768-dim vectors and are incompatible with the new embedder. Run `DELETE /api/v1/memory/prune` before first v2.0 run.
- The old Kuzu-empty fallback code path is now dead code — retire it or keep as a diagnostic probe.
- `graph_relationship_ledger` migrates from SQLite to Postgres; schema is otherwise identical.

**Startup dependencies** (critical for first boot):
```
db (healthy) ──┐
               ├──► cognee-mcp ──► web
neo4j (healthy)┘
```
A Postgres init script ([scripts/postgres-init/01-create-cognee-db.sh](scripts/postgres-init/01-create-cognee-db.sh)) provisions `cognee_db` alongside `workspace_db` on first boot only.

