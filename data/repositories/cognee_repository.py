import httpx
import json
import ast
import re
from typing import Any, Dict

from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError


class CogneeRepository:
    """Repository wrapping the Cognee MCP HTTP Transport API."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        mcp_endpoint: str,
        mcp_host_header: str | None = None,
    ):
        self.client = http_client
        self.endpoint = mcp_endpoint
        self.mcp_host_header = mcp_host_header
        self.session_id: str | None = None
        self._request_counter = 0

    def _next_id(self, prefix: str) -> str:
        self._request_counter += 1
        return f"{prefix}-{self._request_counter}"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.mcp_host_header:
            headers["Host"] = self.mcp_host_header
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    @staticmethod
    def _parse_response_payload(response: httpx.Response) -> Dict[str, Any]:
        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # MCP streamable HTTP responses are SSE frames; we need the JSON payload in "data:" lines.
            payload: Dict[str, Any] | None = None
            for raw_line in response.text.splitlines():
                line = raw_line.strip()
                if line.startswith("data:"):
                    candidate = line[5:].strip()
                    if candidate:
                        payload = json.loads(candidate)

            if payload is None:
                raise CogneeToolError("Invalid MCP SSE response: no data payload found")
            return payload

        return response.json()

    async def _initialize_session(self) -> None:
        if self.session_id:
            return

        initialize_payload = {
            "jsonrpc": "2.0",
            "id": self._next_id("init"),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "cognee-layer",
                    "version": "1.0.0",
                },
            },
        }

        response = await self.client.post(
            self.endpoint,
            headers=self._headers(),
            json=initialize_payload,
        )
        response.raise_for_status()
        data = self._parse_response_payload(response)

        if "error" in data:
            raise CogneeToolError(f"MCP initialize error: {data['error']}")

        self.session_id = response.headers.get("mcp-session-id")
        if not self.session_id:
            raise CogneeToolError("MCP initialize succeeded but session ID is missing")

        # Notify server that client initialization is complete.
        initialized_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        await self.client.post(
            self.endpoint,
            headers=self._headers(),
            json=initialized_payload,
        )

    async def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Helper to format the MCP JSON-RPC/HTTP request."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id("call"),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        try:
            await self._initialize_session()
            async with self.client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    data: Dict[str, Any] | None = None
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if line.startswith("data:"):
                            candidate = line[5:].strip()
                            if candidate:
                                data = json.loads(candidate)
                                break

                    if data is None:
                        raise CogneeToolError("Invalid MCP SSE response: no data payload found")
                else:
                    body = await response.aread()
                    data = json.loads(body)

            if "error" in data:
                raise CogneeToolError(f"MCP Tool Error: {data['error']}")
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("isError"):
                # Print or log the whole result to see why it failed
                error_content = str(result.get("content", []))
                raise CogneeToolError(f"MCP tool '{tool_name}' returned isError=true. Details: {error_content}")

            return result
        except httpx.RequestError as e:
            raise CogneeConnectionError(f"Failed to connect to Cognee MCP: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise CogneeToolError(f"HTTP Error {e.response.status_code} from MCP: {e.response.text}")

    # ──────────────────────────────────────────────
    # Core Memory Operations
    # ──────────────────────────────────────────────

    async def save_interaction(self, user_id: str, data: str) -> None:
        """Fast append to Short-Term Memory (STM)."""
        await self._call_mcp_tool("save_interaction", {
            "user": user_id,
            "data": data
        })

    async def trigger_cognify(self, user_id: str, data: str) -> None:
        """Promote STM to Long-Term Memory (Temporal Graph)."""
        await self._call_mcp_tool("cognify", {
            "user": user_id,
            "data": data,
            "temporal_cognify": True,
            "run_in_background": True
        })

    @staticmethod
    def _extract_search_result_payload(parsed: Any) -> list:
        """Normalize parsed MCP search payload into a flat list of result items."""
        if isinstance(parsed, dict):
            if "search_result" in parsed and isinstance(parsed["search_result"], list):
                return parsed["search_result"]
            return [parsed]

        if isinstance(parsed, list):
            normalized: list = []
            for item in parsed:
                if isinstance(item, dict) and "search_result" in item and isinstance(item["search_result"], list):
                    normalized.extend(item["search_result"])
                else:
                    normalized.append(item)
            return normalized

        return [parsed]

    @staticmethod
    def _parse_text_content(text: str) -> list:
        """Parse MCP text content that may be JSON, Python-literal, or plain text."""
        cleaned = text.strip()
        if not cleaned:
            return []

        lowered = cleaned.lower()
        if "field required" in lowered or lowered.startswith("error"):
            return []

        try:
            parsed = json.loads(cleaned)
            return CogneeRepository._extract_search_result_payload(parsed)
        except json.JSONDecodeError:
            # MCP often returns Python repr values, e.g. UUID('...'). Make them literal-eval friendly.
            normalized = re.sub(r"UUID\('([^']+)'\)", r"'\1'", cleaned)
            try:
                parsed = ast.literal_eval(normalized)
                return CogneeRepository._extract_search_result_payload(parsed)
            except (ValueError, SyntaxError):
                return [cleaned]

    @classmethod
    def _normalize_search_output(cls, result: Dict[str, Any]) -> list:
        """Normalize Cognee MCP search output across response formats."""
        if "data" in result and isinstance(result["data"], list):
            return result["data"]

        content = result.get("content", [])
        normalized: list = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    normalized.extend(cls._parse_text_content(item.get("text", "")))
                elif isinstance(item, str):
                    normalized.extend(cls._parse_text_content(item))

        return normalized

    async def search(self, user_id: str, query: str, query_type: str, top_k: int = 10) -> list:
        """Query the memory subsystem."""
        try:
            # Current MCP schema.
            result = await self._call_mcp_tool("search", {
                "search_query": query,
                "search_type": query_type,
                "top_k": top_k,
            })
        except CogneeToolError:
            # Legacy payload shape used in older Cognee MCP variants.
            result = await self._call_mcp_tool("search", {
                "user": user_id,
                "query_text": query,
                "query_type": query_type,
                "top_k": top_k,
            })
        return self._normalize_search_output(result)

    # ──────────────────────────────────────────────
    # Management & Admin Operations
    # ──────────────────────────────────────────────

    async def list_data(self, dataset_id: str | None = None) -> list:
        """List all datasets and data items, optionally filtered by dataset_id."""
        arguments: Dict[str, Any] = {}
        if dataset_id is not None:
            arguments["dataset_id"] = dataset_id
        result = await self._call_mcp_tool("list_data", arguments)
        # Normalize: result may be a dict with content or a direct list
        if isinstance(result, dict):
            return self._normalize_search_output(result) or result.get("data", [])
        return []

    async def delete_data(self, data_id: str, dataset_id: str, mode: str = "soft") -> Dict[str, Any]:
        """Delete a specific data item from a dataset."""
        result = await self._call_mcp_tool("delete", {
            "data_id": data_id,
            "dataset_id": dataset_id,
            "mode": mode,
        })
        return result if isinstance(result, dict) else {"raw": result}

    async def delete_dataset(self, dataset_id: str, mode: str = "soft") -> Dict[str, Any]:
        """Delete all data items belonging to a dataset.

        Fetches every item in the dataset via list_data, then deletes each one.
        Returns a summary of how many items were removed.
        """
        items = await self.list_data(dataset_id=dataset_id)
        deleted, failed = 0, 0
        for item in items:
            item_id = item.get("id") or item.get("data_id") if isinstance(item, dict) else None
            if not item_id:
                continue
            try:
                await self.delete_data(data_id=str(item_id), dataset_id=dataset_id, mode=mode)
                deleted += 1
            except Exception:
                failed += 1
        return {"dataset_id": dataset_id, "deleted": deleted, "failed": failed}

    async def prune(self) -> Dict[str, Any]:
        """Permanently delete ALL data from the Cognee knowledge graph."""
        result = await self._call_mcp_tool("prune", {})
        return result if isinstance(result, dict) else {"raw": result}

    async def cognify_status(self) -> Dict[str, Any]:
        """Check the status of the cognify pipeline."""
        result = await self._call_mcp_tool("cognify_status", {})
        return result if isinstance(result, dict) else {"raw": result}

    # ──────────────────────────────────────────────
    # Feedback & Trace Operations
    # ──────────────────────────────────────────────

    async def submit_feedback(
        self,
        user_id: str,
        session_id: str,
        qa_id: str,
        feedback_score: int,
        feedback_text: str | None = None,
    ) -> Dict[str, Any]:
        """Submit quality feedback for a previous recall result."""
        arguments: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "qa_id": qa_id,
            "feedback_score": feedback_score,
        }
        if feedback_text is not None:
            arguments["feedback_text"] = feedback_text
        result = await self._call_mcp_tool("submit_feedback", arguments)
        return result if isinstance(result, dict) else {"raw": result}

    async def record_trace(
        self,
        user_id: str,
        session_id: str,
        origin_function: str,
        status: str = "success",
        memory_query: str = "",
        method_params: Dict[str, Any] | None = None,
        error_message: str = "",
    ) -> Dict[str, Any]:
        """Record an agent trace step for observability."""
        arguments: Dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "origin_function": origin_function,
            "status": status,
            "memory_query": memory_query,
            "method_params": method_params or {},
            "error_message": error_message,
        }
        result = await self._call_mcp_tool("record_trace", arguments)
        return result if isinstance(result, dict) else {"raw": result}
