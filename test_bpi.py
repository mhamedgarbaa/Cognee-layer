import asyncio
import json
import logging
from httpx import AsyncClient

logging.basicConfig(level=logging.INFO)

BASE_URL = "http://localhost:8000"
TENANT_ID = "bpi_tester"


async def step_prune(client: AsyncClient):
    """Step 0: Prune all data to start from a clean slate."""
    print("\n" + "=" * 60)
    print("STEP 0: Pruning all existing memory data...")
    print("=" * 60)
    try:
        resp = await client.delete("/api/v1/memory/prune", timeout=300.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✔ Prune successful — status: {data['status']}, message: {data['message']}")
        else:
            print(f"  ✘ Prune failed (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ✘ Prune request error: {e}")


async def step_record(client: AsyncClient, events: list):
    """Step 1: Inject all BPI events as facts into memory."""
    print("\n" + "=" * 60)
    print(f"STEP 1: Recording {len(events)} facts into Memory...")
    print("=" * 60)
    success = 0
    for i, event in enumerate(events):
        fact = (
            f"Le {event['date']}, {event['title']} ({event['category']}). "
            f"Description: {event['description']} "
            f"Entités: {', '.join(event['entities'])}. "
            f"Montant: {event['amount']}."
        )
        try:
            resp = await client.post("/api/v1/memory/record", json={
                "user_id": TENANT_ID,
                "fact": fact,
            }, timeout=120.0)

            if resp.status_code == 202:
                print(f"  ✔ [{i + 1}/{len(events)}] {event['title']}")
                success += 1
            else:
                print(f"  ✘ [{i + 1}/{len(events)}] HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"  ✘ [{i + 1}/{len(events)}] Error: {e}")

        # Allow cognify pipeline to process between records
        await asyncio.sleep(5.0)

    print(f"\n  Summary: {success}/{len(events)} facts recorded successfully.")
    return success


async def step_cognify_status(client: AsyncClient):
    """Step 2: Check cognify pipeline status."""
    print("\n" + "=" * 60)
    print("STEP 2: Checking Cognify pipeline status...")
    print("=" * 60)
    try:
        resp = await client.get("/api/v1/memory/cognify/status", timeout=60.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✔ Pipeline status: {data['status']}")
            if data.get("details"):
                for k, v in data["details"].items():
                    print(f"    • {k}: {v}")
        else:
            print(f"  ✘ Status check failed (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ✘ Status request error: {e}")


async def step_list_data(client: AsyncClient):
    """Step 3: List all datasets and data items."""
    print("\n" + "=" * 60)
    print("STEP 3: Listing stored data inventory...")
    print("=" * 60)
    try:
        resp = await client.get("/api/v1/memory/data", timeout=60.0)
        if resp.status_code == 200:
            data = resp.json()
            datasets = data.get("datasets", [])
            print(f"  ✔ Found {len(datasets)} dataset(s)")
            for ds in datasets:
                if isinstance(ds, dict):
                    print(f"    • Dataset: {ds.get('id', 'N/A')} — {ds.get('name', 'N/A')}")
                else:
                    print(f"    • {ds}")
            return datasets
        else:
            print(f"  ✘ List failed (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ✘ List request error: {e}")
    return []


async def step_recall(client: AsyncClient):
    """Step 4: Search for relevant facts using semantic recall."""
    print("\n" + "=" * 60)
    print("STEP 4: Testing semantic recall...")
    print("=" * 60)

    queries = [
        "Quels sont les programmes liés à la French Tech ou à l'innovation ?",
        "Quels partenariats internationaux BPI France a-t-elle signés ?",
        "Quel est le bilan de France 2030 ?",
    ]

    for query in queries:
        print(f"\n  Query: {query}")
        try:
            resp = await client.post("/api/v1/memory/recall", json={
                "user_id": TENANT_ID,
                "query": query,
            }, timeout=180.0)

            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✔ Retrieval method: {data['retrieval_method']}")
                print(f"    Is degraded: {data['is_degraded']}")
                print(f"    Results ({len(data['context_data'])} items):")
                for item in data["context_data"][:5]:
                    preview = str(item)[:120]
                    print(f"      — {preview}...")
            else:
                print(f"  ✘ Search failed (HTTP {resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"  ✘ Search request error: {e}")


async def step_delete(client: AsyncClient, datasets: list):
    """Step 5: Attempt to delete a single data item (if any exist)."""
    print("\n" + "=" * 60)
    print("STEP 5: Testing data deletion...")
    print("=" * 60)

    if not datasets:
        print("  ⚠ No datasets available to test deletion — skipping.")
        return

    # Try to find a valid data_id and dataset_id from the inventory
    target_dataset = None
    target_data = None
    for ds in datasets:
        if isinstance(ds, dict):
            ds_id = ds.get("id")
            items = ds.get("data", ds.get("items", []))
            if ds_id and items:
                target_dataset = ds_id
                first_item = items[0]
                target_data = first_item.get("id") if isinstance(first_item, dict) else str(first_item)
                break

    if not target_dataset or not target_data:
        print("  ⚠ Could not find a data item ID to delete — skipping.")
        return

    print(f"  Deleting data_id={target_data} from dataset_id={target_dataset} (soft)...")
    try:
        resp = await client.request("DELETE", "/api/v1/memory/data", json={
            "data_id": target_data,
            "dataset_id": target_dataset,
            "mode": "soft",
        }, timeout=60.0)

        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✔ Delete status: {data['status']}")
            if data.get("details"):
                print(f"    Details: {data['details']}")
        else:
            print(f"  ✘ Delete failed (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ✘ Delete request error: {e}")


async def test_bpi():
    """Full integration test for BPI France events through the Cognee memory layer."""
    # Load events
    try:
        with open("bpi_france_events.json", "r", encoding="utf-8") as f:
            events = json.load(f)
    except FileNotFoundError:
        print("ERROR: bpi_france_events.json not found in project root.")
        return

    print("╔══════════════════════════════════════════════════════════╗")
    print("║       BPI France — Cognee Memory Integration Test       ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  Events to ingest : {len(events):>3}                                 ║")
    print(f"║  Tenant ID        : {TENANT_ID:<35} ║")
    print(f"║  API Base          : {BASE_URL:<34} ║")
    print("╚══════════════════════════════════════════════════════════╝")

    async with AsyncClient(base_url=BASE_URL) as client:
        # Step 0 — Clean slate
        await step_prune(client)
        await asyncio.sleep(3)

        # Step 1 — Record all events
        recorded = await step_record(client, events)
        if recorded == 0:
            print("\n⚠ No facts were recorded. Aborting remaining steps.")
            return

        # Step 2 — Check cognify status
        print("\nWaiting 10s for cognify pipeline to settle...")
        await asyncio.sleep(10)
        await step_cognify_status(client)

        # Step 3 — List stored data
        datasets = await step_list_data(client)

        # Step 4 — Semantic recall
        await step_recall(client)

        # Step 5 — Delete a single item
        await step_delete(client, datasets)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_bpi())
