"""
Integration smoke tests for the v2.0 Plug-and-Play MCP stack.

Tests the full chain:
  FastAPI (web:8000) → Cognee MCP (cognee-mcp:8001) → Postgres + Neo4j + Azure OpenAI

Run with:
    pytest tests/test_stack_integration.py -v
or against a remote host:
    BASE_URL=http://<host>:8000 MCP_URL=http://<host>:8001 pytest tests/test_stack_integration.py -v
"""

import os
import time
import uuid

import httpx
import pytest

# Inside Docker: use service DNS. From host: override with MCP_URL=http://localhost:8001
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
MCP_URL = os.getenv("MCP_URL", "http://cognee-mcp:8000")
TIMEOUT = float(os.getenv("TEST_TIMEOUT", "30"))

TEST_USER = f"integration_test_{uuid.uuid4().hex[:8]}"
TEST_FACT = "The user is testing the Cognee v2.0 stack with Neo4j, Postgres, and Azure OpenAI."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    return client.request(method, path, timeout=TIMEOUT, **kwargs)


MCP_HOST_HEADER = os.getenv("MCP_HOST_HEADER", "localhost:8001")

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Host": MCP_HOST_HEADER,
}


def mcp_init(client: httpx.Client) -> str:
    """Initialize an MCP session and return the session ID."""
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-smoke", "version": "1.0"},
            },
        },
        headers=_MCP_HEADERS,
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"MCP init failed: {resp.status_code} {resp.text}"
    session_id = resp.headers.get("mcp-session-id")
    assert session_id, "No mcp-session-id header in initialize response"
    return session_id


def mcp_call(client: httpx.Client, session_id: str, tool: str, args: dict) -> dict:
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        headers={**_MCP_HEADERS, "mcp-session-id": session_id},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"MCP call {tool} failed: {resp.status_code}"
    # SSE response — strip "data: " prefix
    body = resp.text
    for line in body.splitlines():
        if line.startswith("data:"):
            import json
            return json.loads(line[5:].strip())
    pytest.fail(f"No data line in MCP SSE response: {body}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def web():
    with httpx.Client(base_url=BASE_URL) as c:
        yield c


@pytest.fixture(scope="module")
def mcp():
    with httpx.Client(base_url=MCP_URL) as c:
        yield c


@pytest.fixture(scope="module")
def mcp_session(mcp):
    return mcp_init(mcp)


# ---------------------------------------------------------------------------
# 1. Infrastructure health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_web_health(self, web):
        r = api(web, "GET", "/health")
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_mcp_health(self, mcp):
        r = mcp.get("/health", timeout=TIMEOUT)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"


# ---------------------------------------------------------------------------
# 2. MCP protocol — session + tools list
# ---------------------------------------------------------------------------

class TestMCPProtocol:
    def test_initialize_returns_session_id(self, mcp):
        sid = mcp_init(mcp)
        assert len(sid) == 32  # UUID hex

    def test_tools_list(self, mcp, mcp_session):
        resp = mcp.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={**_MCP_HEADERS, "mcp-session-id": mcp_session},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200
        import json
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                tool_names = [t["name"] for t in data["result"]["tools"]]
                expected = {"cognify", "save_interaction", "search", "list_data", "delete", "prune", "cognify_status"}
                assert expected.issubset(set(tool_names)), f"Missing tools: {expected - set(tool_names)}"
                return
        pytest.fail("No tools/list response")


# ---------------------------------------------------------------------------
# 3. FastAPI memory API — write path
# ---------------------------------------------------------------------------

class TestWritePath:
    def test_record_returns_202(self, web):
        r = api(web, "POST", "/api/v1/memory/record", json={
            "user_id": TEST_USER,
            "fact": TEST_FACT,
        })
        assert r.status_code == 202
        body = r.json()
        assert body.get("status") == "accepted"

    def test_record_multiple_facts(self, web):
        facts = [
            "User prefers dark mode in all applications.",
            "User works primarily with Python and async frameworks.",
            "User is migrating from SQLite/Kuzu to Postgres/Neo4j.",
        ]
        for fact in facts:
            r = api(web, "POST", "/api/v1/memory/record", json={
                "user_id": TEST_USER,
                "fact": fact,
            })
            assert r.status_code == 202, f"Failed for fact: {fact}"


# ---------------------------------------------------------------------------
# 4. MCP save_interaction — direct write
# ---------------------------------------------------------------------------

class TestMCPWrite:
    def test_save_interaction(self, mcp, mcp_session):
        result = mcp_call(mcp, mcp_session, "save_interaction", {
            "user": TEST_USER,
            "data": "Direct MCP write: user tested save_interaction tool.",
        })
        assert not result.get("error"), f"MCP error: {result.get('error')}"
        assert result["result"]["isError"] is False


# ---------------------------------------------------------------------------
# 5. FastAPI memory API — read path (recall)
# ---------------------------------------------------------------------------

class TestReadPath:
    def test_recall_returns_200(self, web):
        r = api(web, "POST", "/api/v1/memory/recall", json={
            "user_id": TEST_USER,
            "query": "What does the user prefer?",
        })
        assert r.status_code == 200
        body = r.json()
        assert "context_data" in body
        assert "is_degraded" in body
        assert "retrieval_method" in body

    def test_recall_retrieval_method_valid(self, web):
        r = api(web, "POST", "/api/v1/memory/recall", json={
            "user_id": TEST_USER,
            "query": "Tell me about the user's tech stack.",
        })
        body = r.json()
        valid_methods = {"GRAPH_COMPLETION", "CHUNKS_FALLBACK", "NONE_FAILED"}
        assert body["retrieval_method"] in valid_methods, \
            f"Unknown retrieval_method: {body['retrieval_method']}"

    def test_recall_unknown_user_is_not_500(self, web):
        r = api(web, "POST", "/api/v1/memory/recall", json={
            "user_id": f"nonexistent_{uuid.uuid4().hex}",
            "query": "anything",
        })
        assert r.status_code == 200  # graceful degradation, never 500


# ---------------------------------------------------------------------------
# 6. MCP cognify_status — pipeline monitoring
# ---------------------------------------------------------------------------

class TestCognifyStatus:
    def test_cognify_status_returns_result(self, mcp, mcp_session):
        result = mcp_call(mcp, mcp_session, "cognify_status", {})
        assert not result.get("error"), f"MCP error: {result.get('error')}"
        assert result["result"]["isError"] is False


# ---------------------------------------------------------------------------
# 7. MCP list_data — data inventory
# ---------------------------------------------------------------------------

class TestDataInventory:
    def test_list_data_no_error(self, mcp, mcp_session):
        result = mcp_call(mcp, mcp_session, "list_data", {})
        assert not result.get("error"), f"MCP error: {result.get('error')}"


# ---------------------------------------------------------------------------
# 8. FastAPI — validation errors (edge cases)
# ---------------------------------------------------------------------------

class TestValidation:
    def test_record_missing_user_id(self, web):
        r = api(web, "POST", "/api/v1/memory/record", json={"fact": "no user_id"})
        assert r.status_code == 422

    def test_record_missing_fact(self, web):
        r = api(web, "POST", "/api/v1/memory/record", json={"user_id": TEST_USER})
        assert r.status_code == 422

    def test_recall_missing_query(self, web):
        r = api(web, "POST", "/api/v1/memory/recall", json={"user_id": TEST_USER})
        assert r.status_code == 422

    def test_record_empty_fact_rejected_or_accepted(self, web):
        r = api(web, "POST", "/api/v1/memory/record", json={
            "user_id": TEST_USER,
            "fact": "",
        })
        assert r.status_code in (202, 422)
