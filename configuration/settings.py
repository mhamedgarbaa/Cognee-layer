from enum import Enum
from typing import List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# -------------------------------
# ENV ENUM
# -------------------------------
class AppEnv(str, Enum):
    dev = "development"
    staging = "staging"
    prod = "production"


# -------------------------------
# APP CONFIG
# -------------------------------
class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # <-- VERY IMPORTANT (ignore DB_*, LOG_*, etc)
    )

    # Core app runtime
    APP_HOST: str = Field(env="APP_HOST", default="localhost")
    APP_PORT: int = Field(env="APP_PORT", default=7777)
    APP_ENV: AppEnv = Field(env="APP_ENV", default=AppEnv.dev)
    WORKERS_COUNT: int = Field(env="WORKERS_COUNT", default=5)

    # API metadata
    TITLE: str = Field(env="API_TITLE", default="Workspace Service API")
    DESCRIPTION: str = Field(
        env="API_DESCRIPTION", default="Manages workspaces with CRUD operations."
    )
    VERSION: str = Field(env="API_VERSION", default="1.0.0")
    API_PREFIX: str = Field(env="API_PREFIX", default="/api")
    DOCS_URL: str = Field(env="API_DOCS_URL", default="/api/docs")
    REDOC_URL: str = Field(env="API_REDOC_URL", default="/api/redoc")
    OPENAPI_URL: str = Field(env="API_OPENAPI_URL", default="/api/openapi.json")

    # CORS list
    CORS_ORIGINS: List[str] = Field(
        env="CORS_ORIGINS", default=["*"], description="Allowed CORS origins"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def normalize_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        """Always return a clean list of strings."""
        # Case 1 → Comma-separated "a,b,c"
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]

        # Case 2 → Already a list
        if isinstance(v, list):
            return v

        raise ValueError("Invalid CORS_ORIGINS format. Must be string or list.")


# -------------------------------
# DATABASE CONFIG
# -------------------------------
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # <-- IMPORTANT
    )

    DB_USER: str = Field(env="DB_USER")
    DB_PASSWORD: str = Field(env="DB_PASSWORD")
    DB_HOST: str = Field(env="DB_HOST")
    DB_PORT: int = Field(env="DB_PORT")
    DB_NAME: str = Field(env="DB_NAME")

    POOL_SIZE: int = Field(env="DB_POOL_SIZE", default=5)
    MAX_OVERFLOW: int = Field(env="DB_MAX_OVERFLOW", default=10)
    POOL_TIMEOUT: int = Field(env="DB_POOL_TIMEOUT", default=30)
    POOL_RECYCLE: int = Field(env="DB_POOL_RECYCLE", default=1800)
    ECHO: bool = Field(env="DB_ECHO", default=False)

    ENABLE_MULTI_TENANT: bool = Field(env="DB_ENABLE_MULTI_TENANT", default=False)
    DEFAULT_TENANT_ID: str = Field(env="DB_DEFAULT_TENANT_ID", default="default")

    @property
    def POSTGRES_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


# -------------------------------
# COGNEE MEMORY SUBSYSTEM CONFIG
# -------------------------------
class CogneeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MCP_SERVER_URL: str = Field(
        env="MCP_SERVER_URL",
        default="http://localhost:8001",
        description="HTTP endpoint for Cognee MCP server"
    )
    MCP_ENDPOINT_OVERRIDE: Optional[str] = Field(
        env="COGNEE_MCP_ENDPOINT",
        default=None,
        description="Full Cognee MCP endpoint URL (backward-compatible override)"
    )
    MCP_PROTOCOL: str = Field(
        env="MCP_PROTOCOL",
        default="http",
        description="Protocol for MCP communication (http or sse)"
    )
    MCP_HOST_HEADER: Optional[str] = Field(
        env="MCP_HOST_HEADER",
        default="localhost:8001",
        description="Optional Host header override for MCP strict host validation"
    )
    MCP_TIMEOUT: int = Field(
        env="MCP_TIMEOUT",
        default=60,
        description="Timeout for MCP requests in seconds"
    )

    @property
    def COGNEE_MCP_ENDPOINT(self) -> str:
        """Return normalized MCP endpoint, supporting legacy and current env styles."""
        if self.MCP_ENDPOINT_OVERRIDE:
            return self.MCP_ENDPOINT_OVERRIDE.rstrip("/")

        base_url = self.MCP_SERVER_URL.rstrip("/")
        if base_url.endswith("/mcp"):
            return base_url
        return f"{base_url}/mcp"


settings = AppConfig()
db_settings = DatabaseSettings()
cognee_settings = CogneeSettings()
