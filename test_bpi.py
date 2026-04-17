import asyncio
import json
import logging
import re
from httpx import AsyncClient

logging.basicConfig(level=logging.INFO)

BASE_URL = "http://localhost:8000"
TENANT_ID = "bpi_tester"

# How long to wait for cognify to finish (seconds)
COGNIFY_POLL_TIMEOUT = 300
COGNIFY_POLL_INTERVAL = 15

# Max wait between records — must be long enough for each cognify background
# task to finish before the next one starts (SQLite can't handle concurrent writes).
# With Ollama + qwen2.5, each cognify takes 2-4 minutes.
INTER_RECORD_DELAY = 180


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
        print(f"  ✘ Prune request error: {type(e).__name__}: {e!r}")


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
            print(f"  ✘ [{i + 1}/{len(events)}] {type(e).__name__}: {e!r}")

        # Wait for the background cognify to finish before sending the next
        # record — SQLite inside Cognee can't handle concurrent writes.
        if i < len(events) - 1:
            await _wait_between_records(client, i + 1, len(events))

    print(f"\n  Summary: {success}/{len(events)} facts recorded successfully.")
    return success


async def _wait_between_records(client: AsyncClient, current: int, total: int):
    """Wait between records, polling cognify/status until pipeline is idle."""
    await asyncio.sleep(15)
    elapsed = 15
    last_log = 0
    while elapsed < INTER_RECORD_DELAY:
        try:
            resp = await client.get("/api/v1/memory/cognify/status", timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                details = data.get("details", {})
                content = details.get("content", [])
                status_text = ""
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            status_text = item.get("text", "")
                # Empty "{}" means pipeline is idle
                if status_text.strip() in ("{}", ""):
                    print(f"    ⏳ Pipeline idle after {elapsed}s — sending next [{current + 1}/{total}]")
                    return
        except Exception:
            pass  # Connection errors are expected while pipeline is busy
        # Log progress every 30s so the user knows it's alive
        if elapsed - last_log >= 30:
            print(f"    ⏳ [{elapsed}s] Waiting for cognify to finish before next record...")
            last_log = elapsed
        await asyncio.sleep(10)
        elapsed += 10
    print(f"    ⏳ {INTER_RECORD_DELAY}s max wait — sending next [{current + 1}/{total}]")


async def step_wait_for_cognify(client: AsyncClient):
    """Step 2: Poll cognify/status until the pipeline finishes or timeout."""
    print("\n" + "=" * 60)
    print("STEP 2: Waiting for Cognify pipeline to complete...")
    print("=" * 60)
    elapsed = 0
    last_status = "unknown"

    while elapsed < COGNIFY_POLL_TIMEOUT:
        try:
            resp = await client.get("/api/v1/memory/cognify/status", timeout=120.0)
            if resp.status_code == 200:
                data = resp.json()
                last_status = data.get("status", "unknown")
                details = data.get("details", {})

                # Check the MCP content for pipeline state
                content = details.get("content", [])
                is_done = False
                status_text = ""

                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            status_text = item.get("text", "")

                # Parse the text for completion indicators
                if status_text:
                    try:
                        status_data = json.loads(status_text)
                        if isinstance(status_data, dict):
                            pipeline_status = status_data.get("status", "")
                            if pipeline_status in ("completed", "done", "finished", "idle"):
                                is_done = True
                            elif pipeline_status in ("running", "processing"):
                                print(f"  ⏳ [{elapsed}s] Pipeline still running...")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # If status_text is empty "{}", cognify likely completed
                if status_text.strip() in ("{}", ""):
                    # Empty response = no active pipeline = done
                    is_done = True

                if is_done:
                    print(f"  ✔ Pipeline settled after ~{elapsed}s (status: {last_status})")
                    return True
            else:
                print(f"  ⚠ Status check returned HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ⚠ Status poll error: {type(e).__name__}: {e!r}")

        await asyncio.sleep(COGNIFY_POLL_INTERVAL)
        elapsed += COGNIFY_POLL_INTERVAL

    print(f"  ⚠ Pipeline did not confirm completion after {COGNIFY_POLL_TIMEOUT}s (last: {last_status})")
    print("    Proceeding with recall anyway — results may be incomplete.")
    return False


async def step_list_data(client: AsyncClient):
    """Step 3: List all datasets, then list items in each dataset."""
    print("\n" + "=" * 60)
    print("STEP 3: Listing stored data inventory...")
    print("=" * 60)

    dataset_ids = []

    # First call: get dataset overview
    try:
        resp = await client.get("/api/v1/memory/data", timeout=60.0)
        if resp.status_code == 200:
            data = resp.json()
            datasets = data.get("datasets", [])
            print(f"  ✔ Found {len(datasets)} dataset entry/entries")

            # Extract dataset IDs from the response
            # The response may contain formatted text strings — parse UUIDs
            for ds in datasets:
                text = str(ds)
                # Look for UUID patterns (dataset IDs)
                uuids = re.findall(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    text,
                    re.IGNORECASE,
                )
                for uid in uuids:
                    if uid not in dataset_ids:
                        dataset_ids.append(uid)
                        print(f"    • Dataset ID: {uid}")
        else:
            print(f"  ✘ List failed (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"  ✘ List request error: {e}")

    # Second call: get data items for each discovered dataset
    data_items = []  # list of (data_id, dataset_id)
    for ds_id in dataset_ids:
        try:
            resp = await client.get(
                "/api/v1/memory/data",
                params={"dataset_id": ds_id},
                timeout=60.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("datasets", [])
                print(f"\n  📂 Dataset {ds_id} contents ({len(items)} item(s)):")
                for item in items:
                    item_text = str(item)
                    # Extract data item UUIDs
                    item_uuids = re.findall(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                        item_text,
                        re.IGNORECASE,
                    )
                    preview = item_text[:120]
                    print(f"    — {preview}...")
                    for uid in item_uuids:
                        if uid != ds_id:
                            data_items.append((uid, ds_id))
            else:
                print(f"  ✘ List items failed for {ds_id} (HTTP {resp.status_code})")
        except Exception as e:
            print(f"  ✘ Error listing items for {ds_id}: {e}")

    print(f"\n  Total data items discovered: {len(data_items)}")
    return data_items


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
                method = data["retrieval_method"]
                degraded = data["is_degraded"]
                results = data["context_data"]
                status = "✔" if not degraded else "⚠"
                print(f"  {status} Retrieval method: {method} | Degraded: {degraded}")
                print(f"    Results ({len(results)} items):")
                for item in results[:5]:
                    preview = str(item)[:120]
                    print(f"      — {preview}")
                if not results:
                    print("      (no results)")
            else:
                print(f"  ✘ Search failed (HTTP {resp.status_code}): {resp.text}")
        except Exception as e:
            print(f"  ✘ Search request error: {e}")


async def step_delete(client: AsyncClient, data_items: list):
    """Step 5: Attempt to delete a single data item (if any exist)."""
    print("\n" + "=" * 60)
    print("STEP 5: Testing data deletion...")
    print("=" * 60)

    if not data_items:
        print("  ⚠ No data items available to test deletion — skipping.")
        return

    data_id, dataset_id = data_items[0]
    print(f"  Deleting data_id={data_id} from dataset_id={dataset_id} (soft)...")
    try:
        resp = await client.request("DELETE", "/api/v1/memory/data", json={
            "data_id": data_id,
            "dataset_id": dataset_id,
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

        # Step 2 — Wait for cognify pipeline to finish
        await step_wait_for_cognify(client)

        # Step 3 — List stored data (and collect data item IDs for deletion test)
        data_items = await step_list_data(client)

        # Step 4 — Semantic recall
        await step_recall(client)

        # Step 5 — Delete a single item
        await step_delete(client, data_items)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_bpi())
