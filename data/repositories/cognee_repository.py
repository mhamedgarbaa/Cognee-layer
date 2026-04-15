import httpx
from typing import Any, Dict

from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError


class CogneeRepository:
    """Repository wrapping the Cognee MCP HTTP Transport API."""

    def __init__(self, http_client: httpx.AsyncClient, mcp_endpoint: str, mcp_host_header: str | None = None):
        self.client = http_client
        self.endpoint = mcp_endpoint
        self.mcp_host_header = mcp_host_header

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

    async def list_data(self, dataset_id: str | None = None) -> list:
        """List all datasets and data items, optionally filtered by dataset_id."""
        arguments: Dict[str, Any] = {}
        if dataset_id is not None:
            arguments["dataset_id"] = dataset_id
        result = await self._call_mcp_tool("list_data", arguments)
        return result.get("data", []) if isinstance(result, dict) else []

    async def delete_data(self, data_id: str, dataset_id: str, mode: str = "soft") -> Dict[str, Any]:
        """Delete a specific data item from a dataset."""
        result = await self._call_mcp_tool("delete", {
            "data_id": data_id,
            "dataset_id": dataset_id,
            "mode": mode,
        })
        return result if isinstance(result, dict) else {"raw": result}

    async def prune(self) -> Dict[str, Any]:
        """Permanently delete ALL data from the Cognee knowledge graph."""
        result = await self._call_mcp_tool("prune", {})
        return result if isinstance(result, dict) else {"raw": result}

    async def cognify_status(self) -> Dict[str, Any]:
        """Check the status of the cognify pipeline."""
        result = await self._call_mcp_tool("cognify_status", {})
        return result if isinstance(result, dict) else {"raw": result}
