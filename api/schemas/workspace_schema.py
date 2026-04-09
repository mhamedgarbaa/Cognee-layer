import uuid
from typing import Optional

from pydantic import BaseModel, Field

from core.enums import WorkspaceStatus


class WorkspaceBase(BaseModel):
    name: str = Field(..., min_length=1)
    description: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdatePartial(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[WorkspaceStatus] = None


class WorkspaceResponse(WorkspaceBase):
    id: uuid.UUID


class Config:
    from_attributes = True
