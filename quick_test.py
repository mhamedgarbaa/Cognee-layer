"""Quick connectivity test - sends 1 fact to verify the API works."""
import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        print("1. Health check...")
        r = await client.get("/health", timeout=10.0)
        print(f"   -> {r.status_code}: {r.text}")

        print("2. Sending 1 test fact (this may take 2-3 min due to Ollama LLM)...")
        r = await client.post("/api/v1/memory/record", json={
            "user_id": "quick_test",
            "fact": "Test fact: BPI France lance le programme French Tech en 2023."
        }, timeout=300.0)
        print(f"   -> {r.status_code}: {r.text}")

        print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
