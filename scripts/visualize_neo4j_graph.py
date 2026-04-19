"""
Cognee graph visualization — direct Neo4j Cypher queries.

Bypasses get_graph_data() (which misses some edge types) and queries
Neo4j directly so every node and every relationship is included.

Nodes with zero edges are hidden by default (pass --all to show them).

Run inside the container:
    docker cp scripts/visualize_neo4j_graph.py cognee_mcp_server:/tmp/visualize.py
    docker exec cognee_mcp_server python3 /tmp/visualize.py
"""

import json
import os
import sys

NEO4J_URL  = os.getenv("GRAPH_DATABASE_URL",      "bolt://neo4j:7687")
NEO4J_USER = os.getenv("GRAPH_DATABASE_USERNAME",  "neo4j")
NEO4J_PASS = os.getenv("GRAPH_DATABASE_PASSWORD",  "neo4j_pass")
OUTPUT     = "/tmp/cognee_graph.html"
SHOW_ALL   = "--all" in sys.argv   # pass --all to include orphan nodes

LABEL_COLORS = {
    "DocumentChunk": "#4CAF50",
    "TextDocument":  "#2196F3",
    "TextSummary":   "#03A9F4",
    "Entity":        "#FF9800",
    "EntityType":    "#FF5722",
    "Event":         "#9C27B0",
    "NodeSet":       "#607D8B",
    "Timestamp":     "#795548",
    "Interval":      "#9E9E9E",
}
DEFAULT_COLOR = "#546E7A"


def _label(node_labels, props):
    """Pick the best display label from Neo4j node labels + type property."""
    for lbl in LABEL_COLORS:
        if lbl in node_labels:
            return lbl
    return props.get("type", next(iter(node_labels), "Node"))


def main():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASS))

    with driver.session() as session:
        # ── all nodes ────────────────────────────────────────────────────────
        node_rows = session.run("MATCH (n) RETURN elementId(n) AS eid, labels(n) AS lbls, properties(n) AS props")
        raw_nodes = {row["eid"]: (row["lbls"], row["props"]) for row in node_rows}

        # ── all relationships ─────────────────────────────────────────────────
        edge_rows = session.run(
            "MATCH (a)-[r]->(b) "
            "RETURN elementId(a) AS src, elementId(b) AS tgt, type(r) AS rel, properties(r) AS props"
        )
        raw_edges = [(row["src"], row["tgt"], row["rel"], row["props"]) for row in edge_rows]

    driver.close()

    # ── find connected node IDs ───────────────────────────────────────────────
    connected = set()
    for src, tgt, _, _ in raw_edges:
        connected.add(src)
        connected.add(tgt)

    orphan_count = sum(1 for eid in raw_nodes if eid not in connected)
    print(f"  {len(raw_nodes)} nodes total, {len(raw_edges)} edges, {orphan_count} orphans")
    if not SHOW_ALL:
        print("  Hiding orphan nodes (pass --all to show them)")

    # ── build vis.js datasets ─────────────────────────────────────────────────
    vis_nodes = []
    for eid, (lbls, props) in raw_nodes.items():
        if not SHOW_ALL and eid not in connected:
            continue
        lbl = _label(lbls, props)
        color = LABEL_COLORS.get(lbl, DEFAULT_COLOR)
        name = props.get("name") or props.get("text", "")[:60] or str(eid)
        tooltip = (
            f"<b>{lbl}</b><br>"
            + "<br>".join(
                f"{k}: {str(v)[:80]}"
                for k, v in props.items()
                if k not in ("id",) and v not in (None, "", [])
            )
        )
        vis_nodes.append({
            "id":    eid,
            "label": str(name)[:40],
            "title": tooltip,
            "color": color,
            "group": lbl,
        })

    vis_edges = []
    for i, (src, tgt, rel, _) in enumerate(raw_edges):
        vis_edges.append({
            "id":     i,
            "from":   src,
            "to":     tgt,
            "label":  rel,
            "arrows": "to",
        })

    shown_nodes = len(vis_nodes)
    shown_edges = len(vis_edges)
    print(f"  Rendering {shown_nodes} nodes, {shown_edges} edges")

    nodes_json = json.dumps(vis_nodes)
    edges_json = json.dumps(vis_edges)

    orphan_note = f" ({orphan_count} isolated nodes hidden)" if not SHOW_ALL and orphan_count else ""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Cognee Knowledge Graph</title>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<link  href="https://unpkg.com/vis-network@9.1.9/dist/dist/vis-network.min.css" rel="stylesheet">
<style>
  body  {{ margin:0; background:#0d1117; color:#cdd9e5; font-family:sans-serif; }}
  #info {{ position:fixed; top:10px; left:10px; background:#161b22; padding:10px 14px;
           border-radius:6px; font-size:13px; z-index:10; line-height:1.7; }}
  #legend {{ position:fixed; top:10px; right:10px; background:#161b22; padding:10px 14px;
             border-radius:6px; font-size:12px; z-index:10; line-height:1.8; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }}
  #net  {{ width:100vw; height:100vh; }}
</style>
</head>
<body>
<div id="info">
  <b>Cognee Knowledge Graph</b><br>
  {shown_nodes} nodes &nbsp;|&nbsp; {shown_edges} edges{orphan_note}
</div>
<div id="legend">
  {''.join(f'<div><span class="dot" style="background:{c}"></span>{lbl}</div>' for lbl, c in LABEL_COLORS.items())}
  <div><span class="dot" style="background:{DEFAULT_COLOR}"></span>Other</div>
</div>
<div id="net"></div>
<script>
var nodes = new vis.DataSet({nodes_json});
var edges = new vis.DataSet({edges_json});
var options = {{
  nodes: {{ shape:"dot", size:14, font:{{ color:"#cdd9e5", size:12 }} }},
  edges: {{ font:{{ color:"#8b949e", size:10, align:"middle" }},
            color:{{ color:"#30363d" }}, smooth:{{ type:"dynamic" }} }},
  physics: {{ stabilization:{{ iterations:200 }},
              barnesHut:{{ gravitationalConstant:-4000, springLength:140, springConstant:0.04 }} }},
  interaction: {{ hover:true, tooltipDelay:100, navigationButtons:true }}
}};
new vis.Network(document.getElementById("net"), {{nodes:nodes, edges:edges}}, options);
</script>
</body>
</html>"""

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
