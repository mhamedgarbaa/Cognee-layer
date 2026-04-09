# api/routers/v1/workspace_router.py
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from api.custom_api_exceptions import (
    ConflictError,
    NotFoundError,
    WorkspaceInternalServerError,
)
from api.dependencies import get_workspace_service
from api.schemas.workspace_schema import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdatePartial,
)
from service.custom_service_exceptions import (
    ServiceDBError,
    WorkspaceAlreadyExists,
    WorkspaceDoesNotExist,
)
from service.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["WORKSPACES"])


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a workspace by ID",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Workspace not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "NOT_FOUND",
                        "message": "Workspace does not exist.",
                    }
                }
            },
        }
    },
)
async def get_workspace(
    workspace_id: UUID = Path(...),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.get_workspace_by_id(workspace_id)
    except WorkspaceDoesNotExist as workspace_not_exist_exception:
        raise NotFoundError(detail=str(workspace_not_exist_exception))
    except ServiceDBError as general_db_error:
        raise WorkspaceInternalServerError(detail=str(general_db_error))


@router.get(
    "/",
    response_model=List[WorkspaceResponse],
    status_code=status.HTTP_200_OK,
    summary="Get all workspaces",
)
async def get_all_workspaces(
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    return await workspace_service.get_all_workspaces()


@router.post(
    "/",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Workspace already exists",
            "content": {
                "application/json": {
                    "example": {
                        "error": "CONFLICT",
                        "message": "Workspace already exists.",
                    }
                }
            },
        }
    },
)
async def create_workspace(
    data: WorkspaceCreate,
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        return await workspace_service.create_workspace(data)
    except WorkspaceAlreadyExists as workspace_already_exists:
        raise ConflictError(detail=str(workspace_already_exists))
    except ServiceDBError as general_workspace_creation_error:
        raise WorkspaceInternalServerError(detail=str(general_workspace_creation_error))


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially update an existing workspace",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Workspace not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "NOT_FOUND",
                        "message": "Workspace does not exist.",
                    }
                }
            },
        }
    },
)
async def update_workspace_partial(
    workspace_id: UUID = Path(..., description="The ID of the workspace to update"),
    data: WorkspaceUpdatePartial = ...,  # type: ignore
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    """
    Update only the fields provided in the request body.
    Fields not included will remain unchanged.
    """
    try:
        return await workspace_service.update_workspace_by_id(workspace_id, data)
    except WorkspaceDoesNotExist as workspace_does_not_exist:
        raise NotFoundError(detail=str(workspace_does_not_exist))
    except WorkspaceAlreadyExists as workspace_already_exists:
        raise ConflictError(detail=str(workspace_already_exists))
    except ServiceDBError as general_db_error:
        raise WorkspaceInternalServerError(detail=str(general_db_error))


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workspace",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Workspace not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "NOT_FOUND",
                        "message": "Workspace does not exist.",
                    }
                }
            },
        }
    },
)
async def delete_workspace(
    workspace_id: UUID = Path(...),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
):
    try:
        await workspace_service.delete_workspace(workspace_id)
    except WorkspaceDoesNotExist as workspace_does_not_exist:
        raise NotFoundError(detail=str(workspace_does_not_exist))
    except ServiceDBError as delete_workspace_db_error:
        raise WorkspaceInternalServerError(detail=str(delete_workspace_db_error))
