from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from data.custom_data_exceptions import (
    GeneralDatabaseError,
    WorkspaceAlreadyExistsDB,
    WorkspaceNotFoundDB,
)
from data.models.workspace_model import Workspace


class WorkspaceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_workspace_by_id(self, workspace_id: UUID) -> Workspace:
        """
        Fetch a workspace by its ID.

        Args:
            workspace_id (UUID): The workspace ID to retrieve.

        Returns:
            Workspace: The workspace instance.

        Raises:
            WorkspaceNotFoundDB: If no workspace exists with the given ID.
            GeneralDatabaseError: If a database error occurs.
        """
        try:
            result = await self.db.execute(
                select(Workspace).where(Workspace.id == workspace_id)
            )
            workspace = result.scalar_one_or_none()
            if not workspace:
                raise WorkspaceNotFoundDB(f"Workspace with id {workspace_id} not found")
            return workspace

        except SQLAlchemyError as workspace_retrieval_exception:
            raise GeneralDatabaseError(
                detail=f"Database error while retrieving workspace {workspace_id}: {workspace_retrieval_exception}"
            ) from workspace_retrieval_exception

    async def get_all_workspaces(self) -> List[Workspace]:
        """
        Fetch all workspaces.

        Returns:
            List[Workspace]: List of workspace instances.

        Raises:
            GeneralDatabaseError: If a database error occurs.
        """
        try:
            result = await self.db.execute(select(Workspace))
            return result.scalars().all()

        except SQLAlchemyError as workspace_list_exception:
            raise GeneralDatabaseError(
                detail=f"Database error while fetching all workspaces: {workspace_list_exception}"
            ) from workspace_list_exception

    async def create_workspace(self, workspace: Workspace) -> Workspace:
        """
        Create a new workspace.

        Args:
            workspace (Workspace): The workspace instance to create.

        Returns:
            Workspace: The newly created workspace.

        Raises:
            WorkspaceAlreadyExistsDB: If a workspace with the same unique field exists.
            GeneralDatabaseError: If a database error occurs.
        """
        self.db.add(workspace)
        try:
            await self.db.commit()
            await self.db.refresh(workspace)

        except IntegrityError as workspace_creation_integrity_error:
            await self.db.rollback()
            raise WorkspaceAlreadyExistsDB(
                detail=f"Workspace with name '{workspace.name}' already exists: {workspace_creation_integrity_error}"
            ) from workspace_creation_integrity_error

        except SQLAlchemyError as workspace_creation_exception:
            await self.db.rollback()
            raise GeneralDatabaseError(
                detail=f"Database error while creating workspace '{workspace.name}': {workspace_creation_exception}"
            ) from workspace_creation_exception

        return workspace

    async def delete_workspace(self, workspace_id: UUID) -> None:
        """
        Delete a workspace by its ID.

        Args:
            workspace_id (UUID): The workspace ID to delete.

        Raises:
            WorkspaceNotFoundDB: If no workspace exists with the given ID.
            GeneralDatabaseError: If a database error occurs.
        """
        workspace_to_delete = await self.get_workspace_by_id(workspace_id)

        try:
            await self.db.delete(workspace_to_delete)
            await self.db.commit()

        except SQLAlchemyError as workspace_deletion_exception:
            await self.db.rollback()
            raise GeneralDatabaseError(
                detail=f"Database error while deleting workspace {workspace_id}: {workspace_deletion_exception}"
            ) from workspace_deletion_exception

    async def update_workspace_by_id(self, workspace_id: UUID, data: dict) -> Workspace:
        """
        Partially update fields of a workspace by its ID.

        Args:
            workspace_id (UUID): The workspace ID to update.
            data (dict): Dictionary of fields to update.

        Returns:
            Workspace: The updated workspace instance.

        Raises:
            WorkspaceNotFoundDB: If no workspace exists with the given ID.
            WorkspaceAlreadyExistsDB: If the update violates a unique constraint.
            GeneralDatabaseError: If a database error occurs.
        """
        workspace_to_update = await self.get_workspace_by_id(workspace_id)

        for key, value in data.items():
            if hasattr(workspace_to_update, key):
                setattr(workspace_to_update, key, value)

        try:
            await self.db.commit()
            await self.db.refresh(workspace_to_update)

        except IntegrityError as workspace_update_integrity_error:
            await self.db.rollback()
            raise WorkspaceAlreadyExistsDB(
                detail=f"Update violates unique constraint for workspace {workspace_id}: "
                f"{workspace_update_integrity_error}"
            ) from workspace_update_integrity_error

        except SQLAlchemyError as workspace_update_exception:
            await self.db.rollback()
            raise GeneralDatabaseError(
                detail=f"Database error while updating workspace {workspace_id}: {workspace_update_exception}"
            ) from workspace_update_exception

        return workspace_to_update
