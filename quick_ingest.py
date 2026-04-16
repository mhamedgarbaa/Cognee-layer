"""Quick ingest: Record 3 BPI France facts to populate the graph, then visualize."""
import asyncio
import json
from httpx import AsyncClient

BASE_URL = "http://localhost:8000"
TENANT_ID = "bpi_tester"


async def main():
    with open("bpi_france_events.json", "r", encoding="utf-8") as f:
        events = json.load(f)

    # Only take 3 events to speed things up
    events = events[:3]

    async with AsyncClient(base_url=BASE_URL) as client:
        # Prune first
        print("Pruning...")
        try:
            resp = await client.delete("/api/v1/memory/prune", timeout=300.0)
            print(f"  Prune: {resp.status_code}")
        except Exception as e:
            print(f"  Prune error: {type(e).__name__}: {e}")

        await asyncio.sleep(3)

        # Record facts one at a time with long waits
        for i, event in enumerate(events):
            fact = (
                f"Le {event['date']}, {event['title']} ({event['category']}). "
                f"Description: {event['description']} "
                f"Entites: {', '.join(event['entities'])}. "
                f"Montant: {event['amount']}."
            )
            print(f"\nRecording [{i+1}/{len(events)}]: {event['title']}...")
            try:
                resp = await client.post("/api/v1/memory/record", json={
                    "user_id": TENANT_ID,
                    "fact": fact,
                }, timeout=120.0)
                print(f"  Status: {resp.status_code}")
            except Exception as e:
                print(f"  Error: {type(e).__name__}: {e}")

            # Long wait for cognify to finish
            if i < len(events) - 1:
                print(f"  Waiting 120s for cognify pipeline...")
                await asyncio.sleep(120)

        # Final wait for last cognify
        print("\nWaiting 180s for final cognify to complete...")
        await asyncio.sleep(180)
        print("Done! Run 'python visualize_graph.py' to see the graph.")


if __name__ == "__main__":
    asyncio.run(main())
