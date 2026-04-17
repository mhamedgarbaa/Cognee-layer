"""
BPI France Knowledge Graph Visualizer
======================================
Extracts the Cognee knowledge graph from the Docker container
and renders an interactive HTML visualization using pyvis.

Usage:
    python visualize_graph.py
"""

import json
import subprocess
import sys
import os
import networkx as nx
from pyvis.network import Network


# ──────────────────────────────────────────────
# Step 1: Extract graph data from Cognee container
# ──────────────────────────────────────────────

EXTRACT_SCRIPT = r'''
import ast
import asyncio
import json
import os
import sqlite3

DB_DIR = "/app/.venv/lib/python3.12/site-packages/cognee/.cognee_system/databases"
SQLITE_PATH = os.path.join(DB_DIR, "cognee_db")
DEFAULT_KUZU_PATH = os.path.join(DB_DIR, "cognee_graph_kuzu")


def sanitize(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v) for v in value]
    return str(value)


def parse_payload(payload):
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            try:
                return ast.literal_eval(text)
            except Exception:
                return {"payload": text}
    return {}


def normalize_id(value):
    return str(value).strip().lower().replace("-", "")


def node_type_from_table(table_name):
    mapping = {
        "DocumentChunk_text": "DocumentChunk",
        "Entity_name": "Entity",
        "EntityType_name": "EntityType",
        "TextDocument_name": "Document",
        "TextSummary_text": "TextSummary",
    }
    return mapping.get(table_name, table_name)


def edge_endpoints(obj):
    source_keys = ("source_node_id", "source", "source_id", "from_node_id", "start_node_id")
    target_keys = ("target_node_id", "target", "target_id", "to_node_id", "end_node_id")
    source = next((obj.get(k) for k in source_keys if obj.get(k)), None)
    target = next((obj.get(k) for k in target_keys if obj.get(k)), None)
    return source, target


def add_node(nodes_by_id, node):
    node_id = str(node.get("id", "")).strip()
    if not node_id:
        return
    current = nodes_by_id.get(node_id, {})
    merged = {**current, **node}
    merged["id"] = node_id
    nodes_by_id[node_id] = sanitize(merged)


async def extract_from_graph_engine():
    try:
        from cognee.infrastructure.databases.graph import get_graph_engine
        engine = await asyncio.wait_for(get_graph_engine(), timeout=20)
        raw = await asyncio.wait_for(engine.get_graph_data(), timeout=20)
        nodes_raw, edges_raw = raw if isinstance(raw, tuple) else ([], [])

        nodes = []
        for n in nodes_raw:
            if isinstance(n, dict):
                node = n
            elif hasattr(n, "__dict__"):
                node = {k: v for k, v in n.__dict__.items() if not k.startswith("_")}
            else:
                node = {"id": str(n)}
            if "id" in node:
                node["id"] = str(node["id"])
            nodes.append(sanitize(node))

        edges = []
        for e in edges_raw:
            if isinstance(e, dict):
                edge = e
            elif hasattr(e, "__dict__"):
                edge = {k: v for k, v in e.__dict__.items() if not k.startswith("_")}
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                edge = {
                    "source_node_id": str(e[0]),
                    "target_node_id": str(e[1]),
                    "relationship_name": str(e[2]) if len(e) > 2 else "related_to",
                }
            else:
                edge = {}
            edges.append(sanitize(edge))

        return nodes, edges
    except Exception:
        return [], []


def extract_from_lancedb_and_sqlite():
    nodes_by_id = {}
    id_alias = {}
    edges = []

    try:
        import lancedb

        for item in os.listdir(DB_DIR):
            sub = os.path.join(DB_DIR, item)
            if not os.path.isdir(sub):
                continue

            for file_name in os.listdir(sub):
                if not file_name.endswith(".lance.db"):
                    continue

                lance_path = os.path.join(sub, file_name)
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

                for table_name in table_names:
                    if not isinstance(table_name, str):
                        continue
                    table = db.open_table(table_name)
                    df = table.to_pandas()

                    for _, row in df.iterrows():
                        row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
                        payload = parse_payload(row_dict.get("payload"))

                        if table_name == "EdgeType_relationship_name":
                            src, tgt = edge_endpoints(payload)
                            if src and tgt:
                                src = str(src)
                                tgt = str(tgt)
                                edges.append(
                                    {
                                        "source_node_id": src,
                                        "target_node_id": tgt,
                                        "relationship_name": str(
                                            payload.get("relationship_name")
                                            or payload.get("name")
                                            or "related_to"
                                        ),
                                    }
                                )
                            continue

                        node_id = payload.get("id") or row_dict.get("id")
                        if not node_id:
                            continue
                        node_id = str(node_id)
                        node = {
                            "id": node_id,
                            "type": node_type_from_table(table_name),
                            "_table": table_name,
                        }
                        for k, v in payload.items():
                            if k not in node:
                                node[k] = sanitize(v)

                        add_node(nodes_by_id, node)
                        id_alias[normalize_id(node_id)] = node_id
    except Exception:
        pass

    try:
        conn = sqlite3.connect(SQLITE_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT source_node_id, destination_node_id, creator_function, node_label
            FROM graph_relationship_ledger
            WHERE deleted_at IS NULL
            """
        )
        for src, tgt, creator_fn, node_label in cur.fetchall():
            if not src or not tgt:
                continue

            src = str(src)
            tgt = str(tgt)
            src = id_alias.get(normalize_id(src), src)
            tgt = id_alias.get(normalize_id(tgt), tgt)

            if src not in nodes_by_id:
                add_node(nodes_by_id, {"id": src, "type": "Unknown", "name": src[:12]})
            if tgt not in nodes_by_id:
                add_node(nodes_by_id, {"id": tgt, "type": "Unknown", "name": tgt[:12]})

            edges.append(
                {
                    "source_node_id": src,
                    "target_node_id": tgt,
                    "relationship_name": str(creator_fn or node_label or "related_to"),
                }
            )
        conn.close()
    except Exception:
        pass

    unique_edges = {}
    for edge in edges:
        key = (
            edge.get("source_node_id"),
            edge.get("target_node_id"),
            edge.get("relationship_name"),
        )
        unique_edges[key] = sanitize(edge)

    return list(nodes_by_id.values()), list(unique_edges.values())


def collect_diagnostics():
    diagnostics = {
        "pipeline_grouped": [],
        "pipeline_recent": [],
        "ledger_counts": {},
    }

    try:
        conn = sqlite3.connect(SQLITE_PATH)
        cur = conn.cursor()

        cur.execute(
            """
            SELECT pipeline_name, status, COUNT(*)
            FROM pipeline_runs
            GROUP BY pipeline_name, status
            ORDER BY pipeline_name, status
            """
        )
        diagnostics["pipeline_grouped"] = [list(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT created_at, pipeline_name, status, substr(run_info, 1, 140)
            FROM pipeline_runs
            ORDER BY datetime(created_at) DESC
            LIMIT 20
            """
        )
        diagnostics["pipeline_recent"] = [list(row) for row in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM graph_relationship_ledger")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM graph_relationship_ledger WHERE deleted_at IS NOT NULL")
        deleted = cur.fetchone()[0]
        diagnostics["ledger_counts"] = {"total": total, "deleted": deleted}

        conn.close()
    except Exception as exc:
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"

    return sanitize(diagnostics)


def resolve_kuzu_path():
    """Resolve active Kuzu path from dataset_database metadata first."""
    candidates = []

    try:
        conn = sqlite3.connect(SQLITE_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT owner_id, graph_database_name
            FROM dataset_database
            WHERE graph_database_name IS NOT NULL AND graph_database_name != ''
            ORDER BY datetime(created_at) DESC
            """
        )

        for owner_id, graph_database_name in cur.fetchall():
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
                candidates.append(os.path.join(DB_DIR, owner, graph_name))

            candidates.append(os.path.join(DB_DIR, graph_name))

        conn.close()
    except Exception:
        pass

    candidates.append(DEFAULT_KUZU_PATH)

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return DEFAULT_KUZU_PATH


def collect_kuzu_stats():
    stats = {}
    kuzu_path = resolve_kuzu_path()
    stats["path"] = kuzu_path
    stats["path_exists"] = os.path.exists(kuzu_path)

    try:
        import kuzu

        if os.path.exists(kuzu_path):
            database = kuzu.Database(kuzu_path)
            connection = kuzu.Connection(database)

            result = connection.execute("MATCH (n) RETURN count(n);")
            if result.has_next():
                row = result.get_next()
                stats["nodes"] = row[0] if isinstance(row, (list, tuple)) and row else row

            result = connection.execute("MATCH ()-[r]->() RETURN count(r);")
            if result.has_next():
                row = result.get_next()
                stats["edges"] = row[0] if isinstance(row, (list, tuple)) and row else row
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if "Could not set lock on file" in message:
            stats["locked"] = True
            stats["lock_note"] = (
                "Kuzu file is locked by the running cognee_mcp_server process; "
                "direct file inspection is skipped."
            )
        else:
            stats["error"] = message

    return sanitize(stats)


async def main():
    # Primary path: LanceDB + SQLite, because some Cognee setups keep Kuzu empty.
    nodes, edges = extract_from_lancedb_and_sqlite()
    source = "lancedb_sqlite_fallback"

    # Secondary fallback: Graph engine if direct storage extraction is empty.
    if not nodes and not edges:
        graph_nodes, graph_edges = await extract_from_graph_engine()
        if graph_nodes or graph_edges:
            nodes = graph_nodes
            edges = graph_edges
            source = "graph_engine"

    result = {
        "nodes": sanitize(nodes),
        "edges": sanitize(edges),
        "source": source,
        "diagnostics": collect_diagnostics(),
        "kuzu_stats": collect_kuzu_stats(),
    }
    print(json.dumps(result))


asyncio.run(main())
'''


def extract_graph_from_container() -> dict:
    """Run a Python script inside the Cognee container to extract graph data."""
    print("📡 Extracting graph data from Cognee container...")

    result = subprocess.run(
        ["docker", "exec", "cognee_mcp_server", "python", "-c", EXTRACT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        print(f"❌ Extraction failed:\n{result.stderr}")
        sys.exit(1)

    # The JSON is on the last non-empty line (other lines are Cognee logs)
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)

    print(f"❌ No JSON found in output:\n{result.stdout[:500]}")
    sys.exit(1)


def print_extraction_diagnostics(data: dict):
    """Print storage and pipeline diagnostics gathered inside the container."""
    diagnostics = data.get("diagnostics", {}) or {}
    kuzu_stats = data.get("kuzu_stats", {}) or {}

    if not diagnostics and not kuzu_stats:
        return

    print("\n" + "=" * 50)
    print("🧪 PIPELINE / STORAGE DIAGNOSTICS")
    print("=" * 50)

    if kuzu_stats:
        if kuzu_stats.get("path"):
            print(f"  Kuzu path : {kuzu_stats.get('path')}")
            print(f"  Path exists: {kuzu_stats.get('path_exists', False)}")
        if kuzu_stats.get("locked"):
            print("  Kuzu lock : Active DB file is locked by server (expected while container is running).")
            print(f"  Note      : {kuzu_stats.get('lock_note', '')}")
        else:
            print(f"  Kuzu nodes: {kuzu_stats.get('nodes', '?')}")
            print(f"  Kuzu edges: {kuzu_stats.get('edges', '?')}")
        if kuzu_stats.get("error"):
            print(f"  Kuzu error: {kuzu_stats['error']}")

    grouped = diagnostics.get("pipeline_grouped", [])
    if grouped:
        print("\n  pipeline_runs grouped:")
        for pipeline_name, status, count in grouped:
            print(f"    {pipeline_name:20s} {status:30s} {count}")

    ledger_counts = diagnostics.get("ledger_counts", {})
    if ledger_counts:
        print("\n  graph_relationship_ledger:")
        print(f"    total  : {ledger_counts.get('total', '?')}")
        print(f"    deleted: {ledger_counts.get('deleted', '?')}")

    if diagnostics.get("error"):
        print(f"\n  Diagnostics error: {diagnostics['error']}")


# ──────────────────────────────────────────────
# Step 2: Build networkx graph
# ──────────────────────────────────────────────

# Color palette by node type
TYPE_COLORS = {
    "Entity": "#6366f1",           # indigo
    "EntityType": "#8b5cf6",       # violet
    "DocumentChunk": "#06b6d4",    # cyan
    "Document": "#0ea5e9",         # sky blue
    "DataPoint": "#10b981",        # emerald
    "Relationship": "#f59e0b",     # amber
    "default": "#94a3b8",          # slate
}

TYPE_SHAPES = {
    "Entity": "dot",
    "EntityType": "diamond",
    "DocumentChunk": "square",
    "Document": "triangle",
    "default": "dot",
}


def get_node_label(node: dict) -> str:
    """Extract a clean label from a node."""
    # Try common label fields
    for field in ("name", "text", "title", "label", "description"):
        val = node.get(field)
        if val and isinstance(val, str) and val.strip():
            # Truncate long text
            return val.strip()[:60] + ("…" if len(val.strip()) > 60 else "")
    # Fallback to type + short ID
    node_type = node.get("type", "Node")
    node_id = node.get("id", "?")[:8]
    return f"{node_type}:{node_id}"


def get_node_type(node: dict) -> str:
    """Determine the type of a node."""
    return node.get("type", node.get("_type", "default"))


def build_networkx_graph(data: dict) -> nx.DiGraph:
    """Build a NetworkX directed graph from extracted data."""
    G = nx.DiGraph()

    for node in data["nodes"]:
        node_id = node.get("id", str(id(node)))
        node_type = get_node_type(node)
        label = get_node_label(node)
        color = TYPE_COLORS.get(node_type, TYPE_COLORS["default"])
        shape = TYPE_SHAPES.get(node_type, TYPE_SHAPES["default"])

        G.add_node(
            node_id,
            label=label,
            node_type=node_type,
            color=color,
            shape=shape,
            title=f"<b>{label}</b><br>Type: {node_type}<br>ID: {node_id}",
            **{k: v for k, v in node.items() if k not in ("id",)},
        )

    for edge in data["edges"]:
        src = edge.get("source_node_id")
        tgt = edge.get("target_node_id")
        if not src or not tgt:
            continue

        rel = edge.get("relationship_name", edge.get("type", "related_to"))

        G.add_edge(
            src, tgt,
            label=rel,
            title=rel,
            color="#64748b",
            arrows="to",
        )

    return G


# ──────────────────────────────────────────────
# Step 3: Render interactive HTML visualization
# ──────────────────────────────────────────────

def render_pyvis(G: nx.DiGraph, output_path: str):
    """Render the graph as an interactive HTML file with pyvis."""
    net = Network(
        height="900px",
        width="100%",
        directed=True,
        bgcolor="#0f172a",       # dark slate background
        font_color="#e2e8f0",    # light text
        notebook=False,
    )

    # Physics settings for nice layout
    net.set_options(json.dumps({
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -80,
                "centralGravity": 0.01,
                "springLength": 200,
                "springConstant": 0.02,
                "damping": 0.4,
                "avoidOverlap": 0.8,
            },
            "solver": "forceAtlas2Based",
            "stabilization": {
                "enabled": True,
                "iterations": 300,
            },
        },
        "nodes": {
            "font": {
                "size": 14,
                "face": "Inter, system-ui, sans-serif",
                "color": "#e2e8f0",
                "strokeWidth": 3,
                "strokeColor": "#0f172a",
            },
            "borderWidth": 2,
            "borderWidthSelected": 4,
            "shadow": {
                "enabled": True,
                "color": "rgba(0,0,0,0.3)",
                "size": 10,
            },
        },
        "edges": {
            "font": {
                "size": 11,
                "face": "Inter, system-ui, sans-serif",
                "color": "#94a3b8",
                "strokeWidth": 0,
                "align": "middle",
            },
            "color": {
                "color": "#475569",
                "highlight": "#6366f1",
                "hover": "#818cf8",
            },
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.8}},
            "smooth": {"type": "curvedCW", "roundness": 0.15},
            "width": 1.5,
        },
        "interaction": {
            "hover": True,
            "tooltipDelay": 100,
            "navigationButtons": True,
            "keyboard": True,
        },
    }))

    # Add nodes
    for node_id, attrs in G.nodes(data=True):
        size = 25
        node_type = attrs.get("node_type", "default")
        if node_type == "Entity":
            size = 35
        elif node_type == "DocumentChunk":
            size = 20

        net.add_node(
            node_id,
            label=attrs.get("label", node_id[:8]),
            title=attrs.get("title", node_id),
            color=attrs.get("color", TYPE_COLORS["default"]),
            shape=attrs.get("shape", "dot"),
            size=size,
        )

    # Add edges
    for src, tgt, attrs in G.edges(data=True):
        net.add_edge(
            src, tgt,
            title=attrs.get("title", ""),
            label=attrs.get("label", ""),
        )

    # Build the legend as an HTML overlay
    legend_items = []
    for type_name, color in TYPE_COLORS.items():
        if type_name == "default":
            continue
        legend_items.append(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:{color};border-radius:50%;margin-right:6px;'
            f'vertical-align:middle;"></span>{type_name}'
        )

    legend_html = (
        '<div style="position:fixed;top:16px;left:16px;background:rgba(15,23,42,0.9);'
        'border:1px solid #334155;border-radius:12px;padding:16px 20px;'
        'font-family:Inter,system-ui,sans-serif;font-size:13px;color:#e2e8f0;'
        'z-index:9999;backdrop-filter:blur(8px);box-shadow:0 8px 32px rgba(0,0,0,0.3);">'
        '<div style="font-weight:700;font-size:16px;margin-bottom:10px;'
        'background:linear-gradient(135deg,#6366f1,#06b6d4);-webkit-background-clip:text;'
        '-webkit-text-fill-color:transparent;">🧠 BPI France Knowledge Graph</div>'
        f'<div style="font-size:12px;color:#94a3b8;margin-bottom:8px;">'
        f'{G.number_of_nodes()} nodes · {G.number_of_edges()} edges</div>'
        + '<br>'.join(legend_items)
        + '</div>'
    )

    net.save_graph(output_path)

    # Inject legend into the HTML
    with open(output_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<body>", f"<body>{legend_html}")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Graph visualization saved to: {output_path}")


# ──────────────────────────────────────────────
# Step 4: Print graph statistics
# ──────────────────────────────────────────────

def print_stats(G: nx.DiGraph):
    """Print summary statistics about the graph."""
    print("\n" + "=" * 50)
    print("📊 GRAPH STATISTICS")
    print("=" * 50)
    print(f"  Nodes : {G.number_of_nodes()}")
    print(f"  Edges : {G.number_of_edges()}")

    if G.number_of_nodes() == 0:
        print("\n  ⚠ Graph is empty! Run test_bpi.py first to ingest data.")
        return

    # Node type distribution
    type_counts = {}
    for _, attrs in G.nodes(data=True):
        t = attrs.get("node_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("\n  Node types:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:20s} : {count}")

    # Most connected nodes
    degree_sorted = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:10]
    if degree_sorted:
        print("\n  Most connected nodes (top 10):")
        for node_id, degree in degree_sorted:
            label = G.nodes[node_id].get("label", node_id[:12])
            ntype = G.nodes[node_id].get("node_type", "?")
            print(f"    {label:40s}  [{ntype}]  degree={degree}")

    # Relationship types
    rel_counts = {}
    for _, _, attrs in G.edges(data=True):
        r = attrs.get("label", "unknown")
        rel_counts[r] = rel_counts.get(r, 0) + 1

    if rel_counts:
        print("\n  Relationship types:")
        for r, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
            print(f"    {r:30s} : {count}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    # 1. Extract
    data = extract_graph_from_container()
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    source = data.get("source", "unknown")

    print(f"   Source: {source}")
    print(f"   Found {len(nodes)} nodes, {len(edges)} edges")

    if source != "graph_engine" or len(nodes) == 0 or len(edges) == 0:
        print_extraction_diagnostics(data)

    # 2. Build
    G = build_networkx_graph(data)

    # 3. Stats
    print_stats(G)

    if G.number_of_nodes() == 0:
        print("\n💡 The graph is still empty after fallback extraction.")
        print("   Check the diagnostics above for failed/stuck cognify pipeline runs.")
        sys.exit(0)

    # 4. Render
    output = os.path.join(os.path.dirname(__file__), "bpi_knowledge_graph.html")
    render_pyvis(G, output)

    # 5. Open in browser
    print(f"\n🌐 Opening in browser...")
    import webbrowser
    webbrowser.open(f"file:///{os.path.abspath(output)}")


if __name__ == "__main__":
    main()
