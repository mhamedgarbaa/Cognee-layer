# Architecture Report: Cognee Memory MCP Stack

**Date:** 2026-04-18
**Status:** Production Ready
**Version:** 3.0.0 — Plug-and-Play Microservice (Ollama / Azure OpenAI · Postgres · Neo4j)

---

## Executive Summary

The Cognee Memory Stack is a self-contained microservice that gives any AI agent a
persistent, graph-backed memory via the **Model Context Protocol (MCP)**.

Agents send text in, get semantic graph search back. The stack handles all ingestion,
entity extraction, graph writing, vector indexing, and retrieval internally.

**v3.0.0 ships two interchangeable LLM back-ends:**

| Tier | Local (default) | Cloud |
|------|----------------|-------|
| LLM | Ollama `qwen2.5` | Azure OpenAI `gpt-4o` |
| Embeddings | Ollama `nomic-embed-text` 768D | Azure OpenAI `text-embedding-3-small` 1536D |
| Graph | Neo4j 5 (APOC) | Neo4j 5 (APOC) |
| Relational | PostgreSQL 15 | PostgreSQL 15 |
| Vector | LanceDB (file-backed) | LanceDB (file-backed) |

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  External Agents / Clients               │
│   (LangChain · AutoGen · CrewAI · agent_chat.py · etc.) │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP  JSON-RPC  SSE
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Cognee MCP Server  :8001                    │
│        (cognee/cognee-mcp:main Docker image)             │
│                                                          │
│  Tools exposed via MCP protocol:                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  cognify      │  │   search     │  │save_interaction│  │
│  │  (ingest +   │  │ (graph +     │  │ (fast text    │  │
│  │   extract)   │  │  vector)     │  │  append)      │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │cognify_status│  │  list_data   │  │    prune      │  │
│  │ (pipeline    │  │ (inventory)  │  │ (wipe all)    │  │
│  │  monitor)   │  │              │  │               │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
└──────┬─────────────────┬──────────────────┬─────────────┘
       │                 │                  │
       ▼                 ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ PostgreSQL 15│  │   Neo4j 5    │  │  LLM Provider    │
│  (cognee_db) │  │ (graph store)│  │                  │
│              │  │  Bolt :7687  │  │  Local: Ollama   │
│ Metadata,    │  │  UI   :7474  │  │  qwen2.5 + nomic │
│ datasets,    │  │  APOC plugin │  │                  │
│ pipelines    │  │              │  │  Cloud: Azure    │
└──────────────┘  └──────────────┘  │  gpt-4o + embed  │
                                     └──────────────────┘
                                              +
                                     ┌──────────────────┐
                                     │ LanceDB (volume) │
                                     │ Vector store     │
                                     │ 768D / 1536D     │
                                     └──────────────────┘
```

---

## 2. MCP Protocol

The MCP server speaks **JSON-RPC 2.0 over HTTP with SSE streaming**.

### Session handshake
```
POST /mcp
{
  "jsonrpc": "2.0", "id": 1, "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "my-agent", "version": "1.0"}
  }
}
→ Response header: mcp-session-id: <uuid32>
```

### Tool call
```
POST /mcp
Headers: mcp-session-id: <uuid32>
         Host: localhost:8001
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {"name": "search", "arguments": {"search_query": "...", "user": "agent1"}}
}
→ SSE stream:  data: {"result": {"content": [{"type": "text", "text": "..."}]}}
```

### Required headers
| Header | Value | Why |
|--------|-------|-----|
| `Host` | `localhost:8001` | ALLOWED_HOSTS validation inside Cognee |
| `mcp-session-id` | UUID from initialize | Session routing |
| `Accept` | `application/json, text/event-stream` | SSE negotiation |

---

## 3. Available MCP Tools

| Tool | Arguments | Description |
|------|-----------|-------------|
| `cognify` | `data`, `user` | Deep ingest: chunk → classify → extract entities → write Neo4j graph + LanceDB vectors |
| `save_interaction` | `data`, `user` | Fast text append (STM), triggers cognify background pipeline |
| `search` | `search_query`, `search_type`, `user` | Semantic search; `search_type=GRAPH_COMPLETION` uses Neo4j + LLM synthesis |
| `cognify_status` | — | Pipeline health; returns `{}` when idle |
| `list_data` | — | Dataset inventory |
| `prune` | — | Wipe all data (irreversible) |
| `delete` | `data_id`, `dataset_id` | Soft-delete a single data item |

---

## 4. Docker Services

| Service | Image | Port | Role |
|---------|-------|------|------|
| `db` | `postgres:15` | 5432 | Relational store: app DB + cognee_db |
| `neo4j` | `neo4j:5` | 7474 / 7687 | Graph store |
| `cognee-mcp` | `cognee/cognee-mcp:main` | 8001→8000 | MCP server |
| `web` | local build | 8000 | FastAPI agent service |

### Key config flags
| Variable | Value | Reason |
|----------|-------|--------|
| `ACCEPT_LOCAL_FILE_PATH` | `True` | Cognee saves interactions as internal files before cognify reads them |
| `ENABLE_BACKEND_ACCESS_CONTROL` | `False` | Neo4j adapter does not implement Cognee's multi-user handler |
| `REQUIRE_AUTHENTICATION` | `False` | Open for internal microservice mesh |
| `extra_hosts: host.docker.internal:host-gateway` | — | Container → host Ollama reachability |

---

## 5. Data Flow

### Write path (cognify)
```
Agent  →  POST /mcp tools/call cognify
       →  Cognee saves text as .txt in internal storage
       →  Pipeline: classify → chunk → extract_graph (LLM) → embed (nomic) → Neo4j + LanceDB
       →  SSE response: isError=False
```

### Read path (search)
```
Agent  →  POST /mcp tools/call search  search_type=GRAPH_COMPLETION
       →  Neo4j graph traversal + LLM synthesis
       →  SSE response: content[].text = synthesized answer
```

---

## 6. Configuration Matrix (LLM Providers)

### Ollama (local, default)
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

### Azure OpenAI (cloud)
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_ENDPOINT=https://<resource>.openai.azure.com/...
LLM_API_KEY=<azure-key>
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_ENDPOINT=https://<resource>.openai.azure.com/...
EMBEDDING_API_KEY=<azure-key>
EMBEDDING_DIMENSIONS=1536
```

> After switching providers, wipe the LanceDB volume (`docker volume rm cognee-layer_cognee_data`)
> to avoid dimension mismatch errors in the vector store.

---

## 7. Known Constraints

| Constraint | Detail |
|------------|--------|
| Neo4j + access control | `ENABLE_BACKEND_ACCESS_CONTROL=False` required; multi-user isolation at application level instead |
| LLM speed | Local qwen2.5: ~15–30s per cognify call. Azure gpt-4o: ~2–5s |
| Embedding dimensions | Changing providers requires volume wipe |
| Healthcheck tooling | The MCP image has neither curl nor wget; uses `python3 -c urllib.request` |
| Post-prune setup | After `prune`, first `cognify` call triggers DB recreation; `cognify_status` returns 422 until that completes |
