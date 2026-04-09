from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from configuration.database import get_db_session
from data.repositories.workspace_repository import WorkspaceRepository
from service.workspace_service import WorkspaceService


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
