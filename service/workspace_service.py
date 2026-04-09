from typing import List
from uuid import UUID

from api.schemas.workspace_schema import WorkspaceCreate, WorkspaceUpdatePartial
from data.custom_data_exceptions import (
    GeneralDatabaseError,
    WorkspaceAlreadyExistsDB,
    WorkspaceNotFoundDB,
)
from data.models.workspace_model import Workspace
from data.repositories.workspace_repository import WorkspaceRepository
from service.custom_service_exceptions import (
    ServiceDBError,
    WorkspaceAlreadyExists,
    WorkspaceDoesNotExist,
)


class WorkspaceService:
    """
    Service layer for workspace operations.
    Handles business logic and orchestration between API and repository.
    """

    def __init__(self, repo: WorkspaceRepository):
        self.workspace_repo = repo

    async def get_workspace_by_id(self, workspace_id: UUID) -> Workspace:
        """
        Get a workspace by ID.
        Raises WorkspaceNotFound if not found.
        """
        try:
            return await self.workspace_repo.get_workspace_by_id(workspace_id)
        except WorkspaceNotFoundDB:
            raise WorkspaceDoesNotExist()
        except GeneralDatabaseError as general_db_error:
            raise ServiceDBError(str(general_db_error)) from general_db_error

    async def get_all_workspaces(self) -> List[Workspace]:
        """Return all workspaces."""
        return await self.workspace_repo.get_all_workspaces()

    async def create_workspace(self, data: WorkspaceCreate) -> Workspace:
        """Create a new workspace from a Pydantic schema."""
        new_workspace = Workspace(**data.model_dump())
        try:
            return await self.workspace_repo.create_workspace(new_workspace)
        except WorkspaceAlreadyExistsDB as integrity_error:
            raise WorkspaceAlreadyExists(str(integrity_error)) from integrity_error

        except GeneralDatabaseError as general_db_error:
            raise ServiceDBError(str(general_db_error)) from general_db_error

    async def delete_workspace(self, workspace_id: UUID):
        """Delete a workspace by ID. Raises WorkspaceNotFound if missing."""
        try:
            return await self.workspace_repo.delete_workspace(workspace_id)
        except WorkspaceNotFoundDB:
            raise WorkspaceDoesNotExist()
        except GeneralDatabaseError as general_db_error:
            raise ServiceDBError(str(general_db_error)) from general_db_error

    async def update_workspace_by_id(
        self, workspace_id: UUID, data: WorkspaceUpdatePartial
    ) -> Workspace:
        """
        Update a workspace with fields from a Pydantic schema.
        Only updates fields that were provided (exclude_unset).
        """
        try:
            return await self.workspace_repo.update_workspace_by_id(
                workspace_id, data.model_dump(exclude_unset=True)
            )
        except WorkspaceNotFoundDB:
            raise WorkspaceDoesNotExist()
        except WorkspaceAlreadyExistsDB as integrity_error:
            raise WorkspaceAlreadyExists(str(integrity_error)) from integrity_error
        except GeneralDatabaseError as general_db_error:
            raise ServiceDBError(str(general_db_error)) from general_db_error
