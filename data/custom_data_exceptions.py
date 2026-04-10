class WorkspaceNotFoundDB(Exception):
    """Raised when a workspace is not found in the database."""

    def __init__(self, detail: str = "Workspace not found in DB"):
        self.detail = detail
        super().__init__(detail)


class WorkspaceAlreadyExistsDB(Exception):
    """Raised when trying to insert a duplicate workspace into DB."""

    def __init__(self, detail: str = "Workspace already exists in DB"):
        self.detail = detail
        super().__init__(detail)


class DatabaseConnectionError(Exception):
    """Raised when there is an issue connecting to the database."""

    def __init__(self, detail: str = "Failed to connect to database"):
        self.detail = detail
        super().__init__(detail)


class GeneralDatabaseError(Exception):
    """Raised when there is a sqlalchemy error."""

    def __init__(self, detail: str = "Failed to commit the transaction"):
        self.detail = detail
        super().__init__(detail)


# --- Cognee Memory Subsystem Exceptions ---
class CogneeConnectionError(Exception):
    """Raised when the MCP server is unreachable or times out."""
    pass


class CogneeToolError(Exception):
    """Raised when the Cognee MCP tool returns an error payload."""
    pass
