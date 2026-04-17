import asyncio
import json

async def main():
    from cognee.infrastructure.databases.graph import get_graph_engine
    engine = await get_graph_engine()

    # Try get_graph_data
    result = await engine.get_graph_data()
    if isinstance(result, tuple):
        nodes, edges = result
        print(f"get_graph_data: {len(nodes)} nodes, {len(edges)} edges")
    else:
        print(f"get_graph_data: {type(result).__name__}")

    # Try model independent
    try:
        result2 = await engine.get_model_independent_graph_data()
        if isinstance(result2, tuple):
            n2, e2 = result2
            print(f"get_model_independent_graph_data: {len(n2)} nodes, {len(e2)} edges")
        else:
            print(f"get_model_independent_graph_data: {type(result2).__name__}")
    except Exception as e:
        print(f"get_model_independent_graph_data: {type(e).__name__}: {e}")

    # Try metrics
    try:
        metrics = await engine.get_graph_metrics()
        print(f"get_graph_metrics: {metrics}")
    except Exception as e:
        print(f"get_graph_metrics: {type(e).__name__}: {e}")

    # Direct Kuzu query - list tables
    print("\n--- Kuzu Tables ---")
    try:
        conn = engine.connection
        result = conn.execute("CALL show_tables() RETURN *;")
        while result.has_next():
            row = result.get_next()
            print(f"  Table: {row}")
    except Exception as e:
        print(f"  show_tables: {type(e).__name__}: {e}")

    # Count nodes per table
    print("\n--- Node Counts ---")
    try:
        conn = engine.connection
        result = conn.execute("CALL show_tables() RETURN *;")
        tables = []
        while result.has_next():
            row = result.get_next()
            tables.append(row)

        for table_row in tables:
            table_name = table_row[0] if isinstance(table_row, (list, tuple)) else str(table_row)
            try:
                count_result = conn.execute(f"MATCH (n:{table_name}) RETURN count(n) AS cnt;")
                if count_result.has_next():
                    cnt = count_result.get_next()
                    print(f"  {table_name}: {cnt}")
            except Exception:
                pass  # Might be a rel table
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")

    # Try to query all nodes with a generic approach
    print("\n--- Sample Nodes ---")
    try:
        conn = engine.connection
        result = conn.execute("MATCH (n) RETURN n LIMIT 10;")
        while result.has_next():
            row = result.get_next()
            print(f"  {str(row)[:200]}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")

    # Try edges
    print("\n--- Sample Edges ---")
    try:
        conn = engine.connection
        result = conn.execute("MATCH (a)-[r]->(b) RETURN a, type(r), b LIMIT 10;")
        while result.has_next():
            row = result.get_next()
            print(f"  {str(row)[:300]}")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")

asyncio.run(main())
