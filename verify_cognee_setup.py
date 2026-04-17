#!/usr/bin/env python
"""
Cognee Memory Subsystem Verification Script
Validates all imports, settings, and dependencies are correctly configured.
"""

import sys
from pathlib import Path

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def check_imports():
    """Verify all Cognee memory subsystem imports."""
    print_section("CHECKING IMPORTS")

    checks = [
        ("Configuration", "from configuration.settings import cognee_settings"),
        ("Data Exceptions", "from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError"),
        ("Cognee Repository", "from data.repositories.cognee_repository import CogneeRepository"),
        ("Service Exceptions", "from service.custom_service_exceptions import MemoryServiceUnavailable"),
        ("Business Models", "from service.business_models import ContextEnvelope"),
        ("Memory Service", "from service.memory_service import MemoryService"),
        ("API Schemas", "from api.schemas.memory_schema import RecordMemoryRequest, RecallMemoryRequest, ContextResponseSchema"),
        ("API Router", "from api.routers.v1.memory_router import router as memory_router"),
        ("HTTP Client", "import httpx"),
        ("FastAPI App", "from app import app"),
    ]

    failed = []
    for name, import_stmt in checks:
        try:
            exec(import_stmt)
            print(f"  [OK] {name}")
        except Exception as e:
            print(f"  [FAIL] {name}: {str(e)[:60]}")
            failed.append((name, e))

    return len(failed) == 0, failed

def check_env_file():
    """Verify .env file exists and has required keys."""
    print_section("CHECKING ENVIRONMENT FILE")

    env_path = Path(".env")
    if not env_path.exists():
        print(f"  [FAIL] .env file not found at {env_path.absolute()}")
        return False, [".env file missing"]

    print(f"  [OK] .env file found")

    required_vars = [
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "COGNEE_MCP_ENDPOINT",
        "COGNEE_TIMEOUT_SECONDS",
    ]

    with open(env_path) as f:
        env_content = f.read()

    missing = []
    for var in required_vars:
        if var in env_content:
            print(f"  [OK] {var}")
        else:
            print(f"  [FAIL] {var} (missing)")
            missing.append(var)

    return len(missing) == 0, missing

def check_settings():
    """Verify settings are loaded correctly."""
    print_section("CHECKING SETTINGS")

    try:
        from configuration.settings import cognee_settings, db_settings, settings

        checks = [
            ("COGNEE_MCP_ENDPOINT", cognee_settings.COGNEE_MCP_ENDPOINT),
            ("COGNEE_TIMEOUT_SECONDS", cognee_settings.COGNEE_TIMEOUT_SECONDS),
            ("DB_USER", db_settings.DB_USER),
            ("DB_HOST", db_settings.DB_HOST),
            ("DB_NAME", db_settings.DB_NAME),
            ("API_PREFIX", settings.API_PREFIX),
        ]

        for name, value in checks:
            if value:
                print(f"  [OK] {name} = {value}")
            else:
                print(f"  [FAIL] {name} is empty/None")

        return True, []
    except Exception as e:
        return False, [str(e)]

def check_routes():
    """Verify memory routes are registered."""
    print_section("CHECKING REGISTERED ROUTES")

    try:
        from app import app

        expected_paths = [
            "/api/v1/memory/record",
            "/api/v1/memory/recall",
        ]

        routes = [route.path for route in app.routes if hasattr(route, 'path')]

        found = []
        missing = []
        for path in expected_paths:
            if path in routes:
                found.append(path)
                print(f"  [OK] {path}")
            else:
                missing.append(path)
                print(f"  [FAIL] {path} not found")

        return len(missing) == 0, missing
    except Exception as e:
        return False, [str(e)]

def check_dependencies():
    """Verify required Python packages are installed."""
    print_section("CHECKING DEPENDENCIES")

    packages = [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "asyncpg",
        "pydantic",
        "pydantic_settings",
        "httpx",
    ]

    failed = []
    for package in packages:
        try:
            __import__(package)
            print(f"  [OK] {package}")
        except ImportError as e:
            print(f"  [FAIL] {package}: {str(e)[:50]}")
            failed.append(package)

    return len(failed) == 0, failed

def main():
    """Run all verification checks."""
    print("\n" + "="*60)
    print("  COGNEE MEMORY SUBSYSTEM VERIFICATION")
    print("="*60)

    results = [
        ("Dependencies", check_dependencies()),
        ("Environment File", check_env_file()),
        ("Settings", check_settings()),
        ("Imports", check_imports()),
        ("Routes", check_routes()),
    ]

    print_section("VERIFICATION SUMMARY")

    all_passed = True
    for check_name, (passed, errors) in results:
        status = "PASS" if passed else "FAIL"
        symbol = "[OK]" if passed else "[FAIL]"
        print(f"  {symbol} {check_name}: {status}")
        if errors and not passed:
            for error in errors[:3]:  # Show first 3 errors
                if isinstance(error, tuple):
                    print(f"    - {error[0]}")
                else:
                    print(f"    - {error}")
        all_passed = all_passed and passed

    print_section("NEXT STEPS")

    if all_passed:
        print("  [OK] All checks passed!\n")
        print("  To start the development server:\n")
        print("  1. Start PostgreSQL:")
        print("     docker-compose up db -d\n")
        print("  2. Run FastAPI:")
        print("     uvicorn app:app --reload\n")
        print("  3. Test endpoints:")
        print("     - Docs: http://localhost:8000/api/docs")
        print("     - Memory Record: POST /api/v1/memory/record")
        print("     - Memory Recall: POST /api/v1/memory/recall\n")
        return 0
    else:
        print("  [FAIL] Some checks failed. Please review and fix the issues above.\n")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
