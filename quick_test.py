"""Quick connectivity test - sends 1 fact to verify the API works."""
import asyncio
from datetime import datetime, timezone
import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        print("1. Health check...")
        r = await client.get("/health", timeout=10.0)
        print(f"   -> {r.status_code}: {r.text}")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fact = f"Test fact [{run_id}]: BPI France lance le programme French Tech en 2023."

        print("2. Sending 1 test fact (this may take 2-3 min due to Ollama LLM)...")
        r = await client.post("/api/v1/memory/record", json={
            "user_id": "quick_test",
            "fact": fact,
        }, timeout=300.0)
        print(f"   -> {r.status_code}: {r.text}")
        print(f"   -> run_id: {run_id}")

        print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
