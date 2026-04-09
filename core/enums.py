from enum import Enum


class WorkspaceStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    ARCHIVED = "Archived"
