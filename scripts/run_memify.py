"""
Temporal graph enrichment — direct Cypher pipeline.

Reads DocumentChunk nodes from Neo4j, extracts temporal events via LLM,
writes Event nodes + HAS_EVENT edges back to Neo4j.

Why direct Cypher (not cognee.memify tasks):
  extract_subgraph_chunks yields str, but extract_events_and_timestamps
  expects DocumentChunk objects — type mismatch in Cognee 0.5.2.

Run inside the container:
  docker exec cognee_mcp_server bash -c "python3 /tmp/run_memify.py [dataset]"
"""

import asyncio
import os
import sys
import uuid

os.environ.setdefault("DB_PROVIDER", "postgres")
os.environ.setdefault("DB_HOST", "db")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "cognee_db")
os.environ.setdefault("DB_USERNAME", "workspace_user")
os.environ.setdefault("DB_PASSWORD", "workspace_pass")
os.environ.setdefault("GRAPH_DATABASE_PROVIDER", "neo4j")
os.environ.setdefault("GRAPH_DATABASE_URL", "bolt://neo4j:7687")
os.environ.setdefault("GRAPH_DATABASE_USERNAME", "neo4j")
os.environ.setdefault("GRAPH_DATABASE_PASSWORD", "neo4j_pass")
os.environ.setdefault("VECTOR_DB_PROVIDER", "lancedb")
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "False")
os.environ.setdefault("ACCEPT_LOCAL_FILE_PATH", "True")

dataset = sys.argv[1] if len(sys.argv) > 1 else "main_dataset"


async def main():
    from cognee.infrastructure.databases.graph import get_graph_engine
    from cognee.infrastructure.llm.extraction import extract_event_graph
    from cognee.tasks.temporal_graph.models import EventList
    from cognee.modules.engine.utils.generate_event_datapoint import generate_event_datapoint

    graph_engine = await get_graph_engine()

    print(f"Running temporal memify on dataset: {dataset}")
    print("Step 1 — fetch DocumentChunk nodes from Neo4j")

    chunks = await graph_engine.query(
        "MATCH (n:DocumentChunk) RETURN n.id AS id, n.text AS text"
    )
    print(f"  Found {len(chunks)} DocumentChunk nodes")

    if not chunks:
        print("No DocumentChunk nodes found — cognify some data first.")
        return

    print("Step 2 — extract temporal events via LLM and write to Neo4j")
    total_events = 0

    for chunk in chunks:
        chunk_id = chunk.get("id")
        text = chunk.get("text", "")
        if not text or not text.strip():
            continue

        try:
            event_list = await extract_event_graph(text, EventList)
        except Exception as exc:
            print(f"  [warn] LLM extraction failed for chunk {chunk_id}: {exc}")
            continue

        if not event_list or not event_list.events:
            continue

        for raw_event in event_list.events:
            event_dp = generate_event_datapoint(raw_event)
            event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, event_dp.name))

            timestamp_str = ""
            time_from_str = ""
            time_to_str = ""
            if event_dp.at:
                timestamp_str = event_dp.at.timestamp_str
            elif event_dp.during:
                time_from_str = event_dp.during.time_from.timestamp_str
                time_to_str = event_dp.during.time_to.timestamp_str

            # MERGE so re-running is idempotent
            await graph_engine.query(
                """
                MERGE (e:Event {id: $event_id})
                SET e.name        = $name,
                    e.description = $description,
                    e.location    = $location,
                    e.timestamp   = $timestamp,
                    e.time_from   = $time_from,
                    e.time_to     = $time_to
                WITH e
                MATCH (c:DocumentChunk {id: $chunk_id})
                MERGE (c)-[:HAS_EVENT]->(e)
                """,
                params={
                    "event_id":    event_id,
                    "name":        event_dp.name,
                    "description": event_dp.description or "",
                    "location":    event_dp.location or "",
                    "timestamp":   timestamp_str,
                    "time_from":   time_from_str,
                    "time_to":     time_to_str,
                    "chunk_id":    chunk_id,
                },
            )
            total_events += 1
            print(f"  + Event: {event_dp.name[:70]}")

    print(f"\nDone. Added {total_events} Event nodes linked to DocumentChunk nodes.")
    print("GRAPH_COMPLETION search will now reason about temporal context.")


if __name__ == "__main__":
    asyncio.run(main())
