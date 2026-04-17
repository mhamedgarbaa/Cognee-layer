class AppException(Exception):
    """Base exception for service layer."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class WorkspaceDoesNotExist(AppException):
    """Raised when trying to fetch a workspace that doesn't exist."""

    def __init__(self, detail: str = "Workspace does not exist"):
        super().__init__(detail)


class WorkspaceAlreadyExists(AppException):
    """Raised when trying to create a workspace that already exists."""

    def __init__(self, detail: str = "Workspace already exists"):
        super().__init__(detail)


class InvalidWorkspaceOperation(AppException):
    """Raised for invalid workspace operations."""

    def __init__(self, detail: str = "Invalid workspace operation"):
        super().__init__(detail)


class ServiceDBError(AppException):
    """Raised for general database errors in the service layer."""

    def __init__(self, detail: str = "Database error occurred in service layer"):
        super().__init__(detail)


class MemoryServiceUnavailable(AppException):
    """Raised when the memory subsystem is entirely offline."""

    def __init__(self, detail: str = "Memory service unavailable"):
        super().__init__(detail)
