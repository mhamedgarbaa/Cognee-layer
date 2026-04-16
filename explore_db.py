import sqlite3
import json
import os

# ── SQLite ──
db_path = "/app/.venv/lib/python3.12/site-packages/cognee/.cognee_system/databases/cognee_db"
print("=== SQLite: cognee_db ===")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables: {tables}")
for t in tables:
    cur.execute(f"SELECT count(*) FROM [{t}]")
    cnt = cur.fetchone()[0]
    if cnt > 0:
        print(f"\n  {t}: {cnt} rows")
        cur.execute(f"PRAGMA table_info([{t}])")
        cols = [r[1] for r in cur.fetchall()]
        print(f"  Columns: {cols}")
        cur.execute(f"SELECT * FROM [{t}] LIMIT 3")
        for row in cur.fetchall():
            print(f"    {str(row)[:300]}")
conn.close()

# ── LanceDB ──
print("\n=== LanceDB ===")
try:
    import lancedb
    dataset_dir = "/app/.venv/lib/python3.12/site-packages/cognee/.cognee_system/databases"
    for item in os.listdir(dataset_dir):
        sub = os.path.join(dataset_dir, item)
        if os.path.isdir(sub):
            for f in os.listdir(sub):
                if f.endswith(".lance.db"):
                    lance_path = os.path.join(sub, f)
                    print(f"\nLanceDB: {lance_path}")
                    db = lancedb.connect(lance_path)
                    table_names = db.table_names()
                    print(f"  Tables: {table_names}")
                    for tname in table_names:
                        tbl = db.open_table(tname)
                        df = tbl.to_pandas()
                        print(f"  {tname}: {len(df)} rows, columns={list(df.columns)}")
                        if len(df) > 0:
                            for _, row in df.head(3).iterrows():
                                row_dict = {}
                                for c in df.columns:
                                    val = row[c]
                                    if c == "vector":
                                        row_dict[c] = f"[{len(val)} dims]"
                                    else:
                                        row_dict[c] = str(val)[:100]
                                print(f"    {row_dict}")
except Exception as e:
    print(f"LanceDB error: {type(e).__name__}: {e}")

# ── Kuzu ──
print("\n=== Kuzu Graph ===")
try:
    import kuzu
    kuzu_path = "/app/.venv/lib/python3.12/site-packages/cognee/.cognee_system/databases/cognee_graph_kuzu"
    if os.path.exists(kuzu_path):
        database = kuzu.Database(kuzu_path)
        connection = kuzu.Connection(database)

        # List tables
        result = connection.execute("CALL show_tables() RETURN *;")
        tables_info = []
        while result.has_next():
            tables_info.append(result.get_next())
        print(f"  Tables: {tables_info}")

        # Count nodes
        try:
            result = connection.execute("MATCH (n) RETURN count(n);")
            if result.has_next():
                print(f"  Total nodes: {result.get_next()}")
        except Exception as e:
            print(f"  Count nodes error: {e}")

        # Sample nodes
        try:
            result = connection.execute("MATCH (n) RETURN n LIMIT 5;")
            while result.has_next():
                row = result.get_next()
                print(f"  Node: {str(row)[:300]}")
        except Exception as e:
            print(f"  Sample nodes error: {e}")

        # Sample edges
        try:
            result = connection.execute("MATCH (a)-[r]->(b) RETURN labels(a), a.id, r, labels(b), b.id LIMIT 5;")
            while result.has_next():
                row = result.get_next()
                print(f"  Edge: {str(row)[:300]}")
        except Exception as e:
            print(f"  Sample edges error: {e}")
except Exception as e:
    print(f"Kuzu error: {type(e).__name__}: {e}")
