"""Quick ingest: Record 3 BPI France facts to populate the graph, then visualize."""
import asyncio
import json
from datetime import datetime, timezone
from httpx import AsyncClient

BASE_URL = "http://localhost:8000"
TENANT_ID = "bpi_tester"


async def wait_for_cognify_idle(client: AsyncClient, max_wait_seconds: int = 900) -> bool:
    """Poll cognify status until there are no active jobs, or timeout."""
    waited = 0
    poll_interval = 10

    while waited <= max_wait_seconds:
        try:
            resp = await client.get("/api/v1/memory/cognify/status", timeout=30.0)
            if resp.status_code == 200:
                payload = resp.json() if resp.content else {}
                details = payload.get("details", {}) if isinstance(payload, dict) else {}

                active_jobs = details.get("active_jobs")
                if active_jobs is None:
                    # If schema is different, treat missing active_jobs as best-effort idle.
                    return True

                try:
                    active_jobs = int(active_jobs)
                except Exception:
                    active_jobs = 0

                if active_jobs <= 0:
                    return True

                print(f"  Cognify active_jobs={active_jobs} (waited {waited}s)...")
            else:
                print(f"  Cognify status check returned {resp.status_code}, retrying...")
        except Exception as e:
            print(f"  Cognify status check error: {type(e).__name__}: {e}")

        await asyncio.sleep(poll_interval)
        waited += poll_interval

    return False


async def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

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

        print("  Waiting for prune/cognify pipeline to become idle...")
        await wait_for_cognify_idle(client, max_wait_seconds=300)

        # Record facts one at a time and wait until background jobs settle
        for i, event in enumerate(events):
            fact = (
                f"[run_id:{run_id}] "
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

            print("  Waiting for cognify pipeline to become idle...")
            ok = await wait_for_cognify_idle(client)
            if not ok:
                print("  Warning: timed out waiting for cognify to become idle.")

        print("\nFinal status check...")
        await wait_for_cognify_idle(client, max_wait_seconds=180)
        print("Done! Run 'python visualize_graph.py' to see the graph.")


if __name__ == "__main__":
    asyncio.run(main())
