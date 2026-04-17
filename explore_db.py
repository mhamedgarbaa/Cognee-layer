import subprocess
import sys

EXTRACT_SCRIPT = r'''
import sqlite3
import os

print("=== SQLite: cognee_db ===")
db_dir = "/app/.venv/lib/python3.12/site-packages/cognee/.cognee_system/databases"
db_path = f"{db_dir}/cognee_db"
default_kuzu_path = f"{db_dir}/cognee_graph_kuzu"


def resolve_kuzu_path():
    candidates = []
    try:
        conn_local = sqlite3.connect(db_path)
        cur_local = conn_local.cursor()
        cur_local.execute(
            """
            SELECT owner_id, graph_database_name
            FROM dataset_database
            WHERE graph_database_name IS NOT NULL AND graph_database_name != ''
            ORDER BY datetime(created_at) DESC
            """
        )
        for owner_id, graph_database_name in cur_local.fetchall():
            graph_name = str(graph_database_name).strip() if graph_database_name else ""
            if not graph_name:
                continue

            if owner_id:
                owner = str(owner_id).strip()
                if len(owner) == 32 and "-" not in owner:
                    owner = (
                        f"{owner[0:8]}-{owner[8:12]}-{owner[12:16]}-"
                        f"{owner[16:20]}-{owner[20:32]}"
                    )
                candidates.append(os.path.join(db_dir, owner, graph_name))

            candidates.append(os.path.join(db_dir, graph_name))

        conn_local.close()
    except Exception:
        pass

    candidates.append(default_kuzu_path)

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return default_kuzu_path


if os.path.exists(db_path):
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
else:
    print(f"File not found: {db_path}")

print("\n=== LanceDB ===")
try:
    import lancedb
    import pandas as pd
    if os.path.exists(db_dir):
        for item in os.listdir(db_dir):
            sub = os.path.join(db_dir, item)
            if os.path.isdir(sub):
                for f in os.listdir(sub):
                    if f.endswith(".lance.db"):
                        lance_path = os.path.join(sub, f)
                        print(f"\nLanceDB: {lance_path}")
                        db = lancedb.connect(lance_path)
                        if hasattr(db, "list_tables"):
                            list_tables_result = db.list_tables()
                            if hasattr(list_tables_result, "tables"):
                                table_names = list(list_tables_result.tables)
                            elif isinstance(list_tables_result, dict) and "tables" in list_tables_result:
                                table_names = list(list_tables_result["tables"])
                            elif isinstance(list_tables_result, (list, tuple)):
                                table_names = list(list_tables_result)
                            else:
                                table_names = []
                        else:
                            table_names = db.table_names()
                        print(f"  Tables: {table_names}")
                        for tname in table_names:
                            if not isinstance(tname, str):
                                continue
                            tbl = db.open_table(tname)
                            df = tbl.to_pandas()
                            print(f"  {tname}: {len(df)} rows, columns={list(df.columns)}")
                            if len(df) > 0:
                                for _, row in df.head(3).iterrows():
                                    row_dict = {}
                                    for c in df.columns:
                                        val = row[c]
                                        if c != "vector": # skip long vectors
                                            row_dict[c] = str(val)[:100]
                                    print(f"    {row_dict}")
except Exception as e:
    print(f"LanceDB error: {e}")

print("\n=== Kuzu Graph ===")
try:
    import kuzu
    kuzu_path = resolve_kuzu_path()
    print(f"  Path: {kuzu_path}")
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
            pass

        # Sample nodes
        try:
            result = connection.execute("MATCH (n) RETURN n LIMIT 5;")
            while result.has_next():
                row = result.get_next()
                print(f"  Node: {str(row)[:300]}")
        except Exception as e:
            pass

except Exception as e:
    message = f"{type(e).__name__}: {e}"
    if "Could not set lock on file" in message:
        print("  Kuzu lock: Active DB file is locked by server (expected while cognee_mcp_server is running).")
        print("  Note     : Stop/restart the container if you need direct file-level Kuzu inspection.")
    else:
        print(f"Kuzu error: {message}")
'''

def main():
    print("📡 Extracting DB data from Cognee container...")
    result = subprocess.run(
        ["docker", "exec", "cognee_mcp_server", "python", "-c", EXTRACT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)

if __name__ == "__main__":
    main()
