import httpx
from typing import Any, Dict

from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError


class CogneeRepository:
    """Repository wrapping the Cognee MCP HTTP Transport API."""

    def __init__(self, http_client: httpx.AsyncClient, mcp_endpoint: str):
        self.client = http_client
        self.endpoint = mcp_endpoint

    async def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Helper to format the MCP JSON-RPC/HTTP request."""
        payload = {
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        try:
            response = await self.client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise CogneeToolError(f"MCP Tool Error: {data['error']}")

            return data.get("result", {})
        except httpx.RequestError as e:
            raise CogneeConnectionError(f"Failed to connect to Cognee MCP: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise CogneeToolError(f"HTTP Error {e.response.status_code} from MCP: {e.response.text}")

    async def save_interaction(self, user_id: str, data: str) -> None:
        """Fast append to Short-Term Memory (STM)."""
        await self._call_mcp_tool("save_interaction", {
            "user": user_id,
            "data": data
        })

    async def trigger_cognify(self, user_id: str) -> None:
        """Promote STM to Long-Term Memory (Temporal Graph)."""
        await self._call_mcp_tool("cognify", {
            "user": user_id,
            "temporal_cognify": True,
            "run_in_background": True
        })

    async def search(self, user_id: str, query: str, query_type: str) -> list:
        """Query the memory subsystem."""
        result = await self._call_mcp_tool("search", {
            "user": user_id,
            "query_text": query,
            "query_type": query_type
        })
        return result.get("data", [])
