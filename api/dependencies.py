from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from typing import AsyncGenerator

from configuration.database import get_db_session
from configuration.settings import cognee_settings
from data.repositories.workspace_repository import WorkspaceRepository
from data.repositories.cognee_repository import CogneeRepository
from service.workspace_service import WorkspaceService
from service.memory_service import MemoryService


# --- Workspace Dependencies ---
# -----------------------------
# Repository dependency
# -----------------------------
async def get_workspace_repository(
    db: AsyncSession = Depends(get_db_session),
) -> WorkspaceRepository:
    """
    Provides a WorkspaceRepository bound to the async DB session.
    """
    return WorkspaceRepository(db)


# -----------------------------
# Service dependency
# -----------------------------
async def get_workspace_service(
    repo: WorkspaceRepository = Depends(get_workspace_repository),
) -> WorkspaceService:
    """
    Provides a WorkspaceService with the repository injected.
    """
    return WorkspaceService(repo)


# --- Cognee Memory Subsystem Dependencies ---
# HTTP client for MCP communication
async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(timeout=cognee_settings.MCP_TIMEOUT) as client:
        yield client


async def get_cognee_repository(
    client: httpx.AsyncClient = Depends(get_http_client)
) -> CogneeRepository:
    """Provides the Cognee MCP repository."""
    return CogneeRepository(
        http_client=client,
        mcp_endpoint=cognee_settings.COGNEE_MCP_ENDPOINT,
        mcp_host_header=cognee_settings.MCP_HOST_HEADER,
    )


async def get_memory_service(
    repo: CogneeRepository = Depends(get_cognee_repository)
) -> MemoryService:
    """Provides the Memory Service with the Cognee repository injected."""
    return MemoryService(repo=repo)
