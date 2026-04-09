# FastAPI Template

This repository provides a FastAPI project template following best practices for building scalable and maintainable backend APIs.

---

## Architecture Overview

The project follows a **3-layered architecture**:

1. **Data Layer**
   - Contains **SQLAlchemy models** and **repositories** for database operations.
   - Responsible for persisting and fetching data.
   - Example: `data/models`, `data/repositories`.

2. **Service/Core Layer**
   - Implements **business logic**.
   - Interacts with repositories from the data layer.
   - Raises **custom exceptions** for specific domain errors.
   - Example: `service/workspace_service.py`.

3. **API Layer**
   - Exposes HTTP endpoints using **FastAPI routers**.
   - Handles request validation, serialization, and response formatting using **Pydantic schemas**.
   - Maps service exceptions to HTTP responses.

---

## Exception Handling

Exceptions are separated per layer:

1. **API Layer Exceptions** (`api/exceptions.py`)
   - Inherit from `fastapi.HTTPException`.
   - Used to return standardized HTTP responses (e.g., 404 Not Found, 401 Unauthorized).
     - Example: `NotFoundError`, `ConflictError`.

2. **Service Layer Exceptions** (`service/exceptions.py`)
   - Inherit from Python's base `Exception`.
   - Represent business/domain errors.
   - Example: `WorkspaceDoesNotExist`, `WorkspaceAlreadyExists`.

3. **Centralized Error Handling Middleware**
   - Catches uncaught exceptions.
   - Converts service-layer exceptions into API responses with proper status codes.
   - Ensures consistent logging and response format.

---

## Logging

- Structured logging is configured globally in `config/logging.py`.
- Logs are in JSON format for easy ingestion into observability systems.
- **Logging Middleware** logs request start times and metadata (without sensitive payloads).

---

## Middlewares

Included middlewares:

- **ErrorHandlerMiddleware**: centralized error handling.
- **LoggingMiddleware**: logs request metadata before execution.
- **TenantMiddleware** (optional): handles multi-tenant scenarios.
- **CORS Middleware**: configured using environment variables in `AppConfig`.

---

## API Versioning

- The project supports **API versioning** to manage breaking changes while maintaining backward compatibility.

  - Versioned API structure:
    ```
    api/
      routers/
        v1/
        workspace_router.py

        v2/
        workspace_router.py
    ```


- **Fallback mechanism**:
  - If an endpoint does not exist in `v2`, clients can still use the `v1` implementation.
  - This allows incremental upgrades without breaking existing clients.

- **Routing best practices**:
  - The `/api` prefix is applied at the **application level** (`app.py`).
  - Each router has a version-specific prefix (e.g., `/v1/workspaces`).
  - Example URL for v1: `/api/v1/workspaces`
  - Example URL for v2: `/api/v2/workspaces` (only modified endpoints need to be in v2)

- **Summary**:
  - Keep routers organized by version.
  - Only add endpoints to v2 that have changes.
  - Maintain v1 as the stable base for unchanged endpoints.
  - This approach simplifies version management and ensures backward compatibility.


## Dependency Injection (DI) & Dependencies

- Dependency injection is managed using **FastAPI's built-in `Depends`**.
- All services and repositories are provided through a **`dependencies.py` file** for consistent injection.
- Example:
  ```python
  async def get_workspace_service(
      repo: WorkspaceRepository = Depends(get_workspace_repository)
  ) -> WorkspaceService:
      return WorkspaceService(repo)
    ```

### API Layer (Schemas)
- All incoming requests and outgoing responses are validated and serialized using **Pydantic schemas**.
- Schemas define the **shape and types** of data exposed to API clients.
- Example: `WorkspaceCreate`, `WorkspaceUpdate`, `WorkspaceResponse`.

```python
from pydantic import BaseModel

class WorkspaceCreate(BaseModel):
    name: str
    description: str
```

### Service Layer (DTOs / Domain Models)
 - The service layer uses DTOs (Data Transfer Objects) or domain models for internal operations.

- These are independent from API schemas to maintain separation of concerns.

 - Conversions between schemas and DTOs are explicit, often via model_dump() or factory methods.

### Data Layer (Database Models)

 - SQLAlchemy models represent tables and handle ORM mapping to the database.

 - Services interact with repositories using domain models, not directly with API schemas.


### Pre-Commit Hooks
- Pre-commit hooks are tools that run automatically before you commit changes to your version control system.
- They help enforce code quality and consistency by running checks such as formatting, linting, and type checking

#### Setting Up Pre-Commit Hooks



Install the `pre-commit` package (globally or inside your virtual environment):

```bash
pip install pre-commit
```

Install Pre-Commit Hook in Your Repository:

```bash
pre-commit install
```
#### ✅ 2. Create a `.pre-commit-config.yaml` File
Create a `.pre-commit-config.yaml` file in the root of your repository with the following content:


#### first usage(only first time):
```bash
pre-commit run --all-files
```

#### Used hooks:

- isort: Automatically sorts imports to maintain consistency and uses the Black profile for compatibility.
- black: Formats Python code to ensure a consistent code style.
- flake8: Lints Python code to catch common errors and enforce style guidelines.
- mypy: Performs static type checking to catch type-related issues.
- check-added-large-files: Prevents committing files that are too large.
- trailing-whitespace: Removes trailing whitespace from files.
- end-of-file-fixer: Ensures files end with a single newline character.


#### Usage
- Pre-commit hooks run only on developer machines before committing code.

- They are not used in production and do not run on servers.

- Therefore, pre-commit should not be added to requirements.txt.
