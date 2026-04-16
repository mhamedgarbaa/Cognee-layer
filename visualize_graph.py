"""
BPI France Knowledge Graph Visualizer
======================================
Extracts the Cognee knowledge graph from the Docker container
and renders an interactive HTML visualization using pyvis.

Usage:
    python visualize_graph.py
"""

import asyncio
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
import asyncio, json

async def main():
    from cognee.infrastructure.databases.graph import get_graph_engine
    engine = await get_graph_engine()
    raw = await engine.get_graph_data()

    nodes_raw, edges_raw = raw if isinstance(raw, tuple) else ([], [])

    nodes = []
    for n in nodes_raw:
        node = {}
        if isinstance(n, dict):
            node = n
        elif hasattr(n, "__dict__"):
            node = {k: v for k, v in n.__dict__.items() if not k.startswith("_")}
        else:
            node = {"id": str(n)}

        # Ensure id is a string
        if "id" in node:
            node["id"] = str(node["id"])

        # Serialize any non-JSON-safe values
        safe = {}
        for k, v in node.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = str(v)
        nodes.append(safe)

    edges = []
    for e in edges_raw:
        edge = {}
        if isinstance(e, dict):
            edge = e
        elif hasattr(e, "__dict__"):
            edge = {k: v for k, v in e.__dict__.items() if not k.startswith("_")}
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            edge = {"source_node_id": str(e[0]), "target_node_id": str(e[1])}
            if len(e) > 2:
                edge["relationship_name"] = str(e[2])

        # String-ify IDs
        for key in ("source_node_id", "target_node_id", "id"):
            if key in edge:
                edge[key] = str(edge[key])

        # Serialize
        safe = {}
        for k, v in edge.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = str(v)
        edges.append(safe)

    print(json.dumps({"nodes": nodes, "edges": edges}))

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
    print(f"   Found {len(data['nodes'])} nodes, {len(data['edges'])} edges")

    # 2. Build
    G = build_networkx_graph(data)

    # 3. Stats
    print_stats(G)

    if G.number_of_nodes() == 0:
        print("\n💡 The graph is empty. Run 'python test_bpi.py' to ingest BPI France data first.")
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
