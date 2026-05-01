"""
Custom knowledge-graph builder — vis-network edition.

Flow:
  1. Fetch all nodes / edges from Neo4j.
  2. Write a self-contained HTML file that uses vis-network (CDN loaded by the
     browser at open time — no build-time network needed).

Usage:
    python graph_builder.py [output_path] [node_limit]
"""

import asyncio
import json
import math
import os
import pathlib
import sys

_NEO4J_URI  = os.getenv("GRAPH_DATABASE_URL",      "bolt://neo4j:7687")
_NEO4J_USER = os.getenv("GRAPH_DATABASE_USERNAME",  "neo4j")
_NEO4J_PASS = os.getenv("GRAPH_DATABASE_PASSWORD",  "neo4j_pass")

TYPE_COLORS: dict[str, str] = {
    "Entity":        "#10b981",
    "EntityType":    "#f59e0b",
    "DocumentChunk": "#3b82f6",
    "TextDocument":  "#a855f7",
    "TextSummary":   "#94a3b8",
    "Event":         "#ef4444",
    "Timestamp":     "#f97316",
    "NodeSet":       "#06b6d4",
    "Interval":      "#ec4899",
}
DEFAULT_COLOR = "#818cf8"

EDGE_COLORS: dict[str, str] = {
    "IS_PART_OF":      "#166534",
    "HAS_CHUNK":       "#0e7490",
    "HAS_ENTITY":      "#1d4ed8",
    "HAS_ENTITY_TYPE": "#6d28d9",
    "DESCRIBES":       "#9a3412",
    "REFERS_TO":       "#9d174d",
    "IS_ABOUT":        "#92400e",
    "SUMMARIZES":      "#065f46",
}
DEFAULT_EDGE_COLOR = "#475569"

_INTERNAL_LABELS = {"__Node__", "__Entity__", "__Community__", "__Chunk__"}


async def _fetch(limit: int) -> tuple[dict, list]:
    from neo4j import AsyncGraphDatabase
    driver = AsyncGraphDatabase.driver(_NEO4J_URI, auth=(_NEO4J_USER, _NEO4J_PASS))
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    async with driver.session() as s:
        res = await s.run(
            "MATCH (n) RETURN elementId(n) AS id, labels(n) AS lbls, properties(n) AS props LIMIT $lim",
            lim=limit,
        )
        async for rec in res:
            nid   = str(rec["id"])
            lbls  = rec["lbls"] or []
            props = {k: str(v)[:300] for k, v in (rec["props"] or {}).items()}
            node_type = next((l for l in lbls if l not in _INTERNAL_LABELS), lbls[0] if lbls else "Node")
            name = props.get("name") or props.get("label") or props.get("title") or props.get("description") or node_type
            nodes[nid] = {"label": str(name)[:60], "type": node_type, "properties": props}
        node_ids = set(nodes)
        res = await s.run(
            "MATCH (n)-[r]->(m) RETURN elementId(r) AS eid, elementId(n) AS src, elementId(m) AS tgt, type(r) AS rel LIMIT $lim",
            lim=limit * 4,
        )
        seen: set[str] = set()
        async for rec in res:
            src, tgt = str(rec["src"]), str(rec["tgt"])
            if src in node_ids and tgt in node_ids:
                eid = str(rec["eid"])
                if eid not in seen:
                    seen.add(eid)
                    edges.append({"id": eid, "source": src, "target": tgt, "label": rec["rel"] or ""})
    await driver.close()
    return nodes, edges


def _compute_layout(nodes: dict, edges: list, scale: int = 3000) -> dict:
    """Run spring layout in Python at build time. k~2.5 spreads 1000+ nodes well."""
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from(nodes.keys())
        for e in edges:
            if e["source"] in nodes and e["target"] in nodes:
                G.add_edge(e["source"], e["target"])
        print(f"[graph_builder] Running spring layout on {G.number_of_nodes()} nodes…")
        pos = nx.spring_layout(G, k=4.0, iterations=120, seed=42, scale=5000)
        print("[graph_builder] Layout done.")
        return {nid: (float(xy[0]), float(xy[1])) for nid, xy in pos.items()}
    except Exception as exc:
        print(f"[graph_builder] Layout skipped ({exc})")
        return {}


def _build_vis_data(nodes: dict, edges: list) -> tuple[list, list]:
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1

    positions = _compute_layout(nodes, edges)
    has_pos   = bool(positions)

    vis_nodes = []
    for nid, node in nodes.items():
        deg   = degree.get(nid, 0)
        size  = max(14, min(40, 14 + int(math.log1p(deg) * 6)))
        color = TYPE_COLORS.get(node["type"], DEFAULT_COLOR)
        entry: dict = {
            "id":    nid,
            "label": node["label"],
            "type":  node["type"],
            "properties": node["properties"],
            "color": {"background": color, "border": color,
                      "highlight": {"background": "#ffffff", "border": color},
                      "hover":     {"background": "#ffffff", "border": color}},
            "size":  size,
            "font":  {"color": "#e2e8f0", "size": 12, "face": "Courier New,monospace",
                      "strokeWidth": 3, "strokeColor": "#020206"},
            "borderWidth": 2,
            "shadow": {"enabled": True, "color": color + "66", "size": 10, "x": 0, "y": 0},
        }
        if has_pos and nid in positions:
            entry["x"] = positions[nid][0]
            entry["y"] = positions[nid][1]
            entry["physics"] = False   # fixed position — browser physics not needed
        vis_nodes.append(entry)

    vis_edges = []
    for i, e in enumerate(edges):
        ec = EDGE_COLORS.get(e["label"], DEFAULT_EDGE_COLOR)
        vis_edges.append({
            "id": i, "from": e["source"], "to": e["target"],
            "title": e["label"],   # hover only — no label rendered on canvas
            "arrows": "to",
            "color": {"color": ec, "highlight": "#38bdf8", "hover": "#38bdf8"},
            "width": 1.5,
        })
    return vis_nodes, vis_edges


def _build_legend_html(nodes: dict) -> str:
    present = {v["type"] for v in nodes.values()}
    rows = []
    for t, c in TYPE_COLORS.items():
        if t in present:
            rows.append(f'<div class="lg-row"><span class="lg-dot" style="background:{c}"></span>{t}</div>')
    other = present - set(TYPE_COLORS)
    if other:
        rows.append(f'<div class="lg-row"><span class="lg-dot" style="background:{DEFAULT_COLOR}"></span>Other ({len(other)})</div>')
    return "\n".join(rows)


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Knowledge Graph — Cognee</title>
<link href="https://unpkg.com/vis-network@9.1.9/dist/dist/vis-network.min.css" rel="stylesheet"/>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;}
html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#020206;color:#e2e8f0;font-family:'Courier New',Courier,monospace;}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(56,189,248,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,.05) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0;}

/* header */
#hdr{position:absolute;top:0;left:0;right:0;z-index:20;display:flex;align-items:center;gap:15px;padding:10px 20px;background:rgba(2,2,6,.88);border-bottom:1px solid #1e293b;backdrop-filter:blur(6px);box-shadow:0 0 20px rgba(56,189,248,.12);}
#logo{font-size:15px;font-weight:700;color:#38bdf8;text-shadow:0 0 8px #38bdf8;white-space:nowrap;}
#sw{position:relative;flex:1;max-width:420px;}
#search{width:100%;padding:8px 15px 8px 35px;background:rgba(15,23,42,.6);border:1px solid #38bdf8;border-radius:4px;color:#e2e8f0;font-size:13px;font-family:inherit;outline:none;box-shadow:inset 0 0 10px rgba(56,189,248,.1);transition:box-shadow .2s;}
#search:focus{box-shadow:0 0 14px rgba(56,189,248,.5),inset 0 0 10px rgba(56,189,248,.2);}
#sico{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:#38bdf8;pointer-events:none;}
#mc{font-size:12px;color:#38bdf8;text-shadow:0 0 5px #38bdf8;white-space:nowrap;min-width:90px;}
#statslbl{font-size:11px;color:#475569;white-space:nowrap;}

/* graph area */
#gw{position:absolute;top:0;left:0;right:0;bottom:0;z-index:1;transition:right .3s ease;}
#net{width:100%;height:100%;}

/* loading overlay */
#loading{position:absolute;inset:0;z-index:50;background:rgba(2,2,6,.92);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;}
#loading-title{font-size:14px;color:#38bdf8;letter-spacing:.1em;text-shadow:0 0 8px #38bdf8;}
#loading-sub{font-size:11px;color:#475569;}
#prog-bar{width:280px;height:4px;background:#1e293b;border-radius:2px;overflow:hidden;}
#prog-fill{height:100%;width:0%;background:linear-gradient(90deg,#3b82f6,#38bdf8);border-radius:2px;transition:width .1s;}

/* detail panel */
#dp{position:absolute;top:0;right:-340px;width:320px;bottom:0;z-index:30;background:rgba(6,8,18,.94);border-left:1px solid #1e3a5f;backdrop-filter:blur(12px);box-shadow:-4px 0 30px rgba(56,189,248,.1);transition:right .3s ease;display:flex;flex-direction:column;}
#dp.open{right:0;}
#dp-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 10px;border-bottom:1px solid #1e293b;flex-shrink:0;}
#dp-title{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#38bdf8;}
#dp-close{background:none;border:none;color:#475569;cursor:pointer;font-size:18px;line-height:1;padding:2px 6px;border-radius:3px;transition:color .15s,background .15s;}
#dp-close:hover{color:#38bdf8;background:rgba(56,189,248,.1);}
#dp-body{flex:1;overflow-y:auto;padding:16px;}
#dp-body::-webkit-scrollbar{width:4px;}
#dp-body::-webkit-scrollbar-thumb{background:#1e3a5f;border-radius:2px;}
.dn-name{font-size:17px;font-weight:700;color:#f1f5f9;margin-bottom:8px;word-break:break-word;line-height:1.3;}
.dn-badge{display:inline-block;padding:3px 10px;border-radius:3px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-bottom:14px;}
.dp-sec{margin-bottom:14px;}
.dp-sec-title{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#475569;border-bottom:1px solid #1e293b;padding-bottom:4px;margin-bottom:8px;}
.dp-row{display:flex;gap:8px;padding:3px 0;font-size:12px;border-bottom:1px dashed rgba(30,41,59,.5);}
.dp-key{color:#475569;min-width:70px;flex-shrink:0;font-weight:600;}
.dp-val{color:#cbd5e1;word-break:break-all;}
.dp-id{color:#38bdf8;font-size:10px;word-break:break-all;}
.chip{display:inline-block;padding:3px 9px;margin:2px;background:rgba(15,23,42,.8);border:1px solid #334155;border-radius:4px;font-size:11px;color:#94a3b8;cursor:pointer;transition:all .15s;}
.chip:hover{border-color:#38bdf8;color:#e2e8f0;background:rgba(56,189,248,.08);}

/* legend */
#legend{position:absolute;top:60px;left:20px;z-index:20;background:rgba(2,2,6,.88);border:1px solid #1e293b;border-radius:6px;padding:10px 14px;backdrop-filter:blur(6px);font-size:11px;line-height:1.9;}
.lg-row{display:flex;align-items:center;gap:7px;}
.lg-dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex-shrink:0;}
</style>
</head>
<body>

<!-- loading screen -->
<div id="loading">
  <div id="loading-title">🧠 Building Knowledge Graph</div>
  <div id="prog-bar"><div id="prog-fill"></div></div>
  <div id="loading-sub">Stabilising layout…</div>
</div>

<header id="hdr" style="display:none">
  <span id="logo">🧠 Cognee Knowledge Graph</span>
  <div id="sw">
    <svg id="sico" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
    </svg>
    <input type="text" id="search" placeholder="Search nodes..." autocomplete="off"/>
  </div>
  <span id="mc"></span>
  <span id="statslbl">__STATS__</span>
</header>

<div id="gw"><div id="net"></div></div>
<div id="legend" style="display:none">__LEGEND__</div>

<div id="dp">
  <div id="dp-head">
    <span id="dp-title">Node Detail</span>
    <button id="dp-close">&#10005;</button>
  </div>
  <div id="dp-body"></div>
</div>

<script>
var VIS_NODES = __VIS_NODES__;
var VIS_EDGES = __VIS_EDGES__;

var NODE_MAP = {};
VIS_NODES.forEach(function(n){ NODE_MAP[n.id] = n; });

var nodes = new vis.DataSet(VIS_NODES);
var edges = new vis.DataSet(VIS_EDGES);

var network = new vis.Network(document.getElementById('net'), {nodes:nodes, edges:edges}, {
  nodes:{
    shape: "dot",
    borderWidth: 2,
    shadow: true,
    font: {color:"#e2e8f0", size:12, face:"Courier New,monospace", strokeWidth:3, strokeColor:"#020206"}
  },
  edges:{
    smooth: {type:"continuous"},   // 'dynamic' creates hidden nodes that force physics on — use continuous
    arrows: {to:{enabled:true, scaleFactor:0.7}},
    font:  {color:"#64748b", size:10, align:"middle"},
    color: {color:"#1e3a5f", highlight:"#38bdf8", hover:"#38bdf8"},
    width: 1.5
  },
  physics:{ enabled: false },   // positions pre-computed server-side — no browser physics
  interaction:{
    hover: true,
    tooltipDelay: 80,
    navigationButtons: true,
    keyboard: true,
    hideEdgesOnDrag: true,
    zoomView: true,
    dragView: true
  },
  layout:{ improvedLayout: false }
});

// No stabilisation needed — show UI right away
setTimeout(showUI, 400);

// ── loading bar ──────────────────────────────────────────────────────────────
network.on('stabilizationProgress', function(p){
  document.getElementById('prog-fill').style.width = (p.iterations/p.total*100)+'%';
  document.getElementById('loading-sub').textContent =
    'Stabilising… '+Math.round(p.iterations/p.total*100)+'%';
});

network.on('stabilizationIterationsDone', function(){
  network.setOptions({physics:{enabled:false}});
  showUI();
});

network.on('stabilized', function(){
  network.setOptions({physics:{enabled:false}});
  showUI();
});

var uiShown = false;
function showUI(){
  if (uiShown) return;
  uiShown = true;
  document.getElementById('loading').style.display    = 'none';
  document.getElementById('hdr').style.display        = 'flex';
  document.getElementById('legend').style.display     = 'block';
  network.fit({animation: false});
}

// ── helpers ──────────────────────────────────────────────────────────────────
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function nodeColor(n){ var c=n.color; return (c&&typeof c==='object') ? (c.background||'#818cf8') : (c||'#818cf8'); }
function getDegree(nid){ var d=0; VIS_EDGES.forEach(function(e){if(e.from===nid||e.to===nid)d++;}); return d; }
function getNeighbours(nid){
  var nbrs=[];
  VIS_EDGES.forEach(function(e){
    if(e.from===nid && NODE_MAP[e.to])   nbrs.push(NODE_MAP[e.to]);
    if(e.to===nid   && NODE_MAP[e.from]) nbrs.push(NODE_MAP[e.from]);
  });
  return nbrs;
}

// ── detail panel ─────────────────────────────────────────────────────────────
var dp  = document.getElementById('dp');
var dpb = document.getElementById('dp-body');
var gw  = document.getElementById('gw');

function openPanel(n){
  var col=nodeColor(n), deg=getDegree(n.id), nbrs=getNeighbours(n.id), props=n.properties||{};
  var html='<div class="dn-name">'+esc(n.label)+'</div>';
  html+='<span class="dn-badge" style="background:'+col+'22;color:'+col+';border:1px solid '+col+'66">'+esc(n.type||'Node')+'</span>';
  html+='<div class="dp-sec"><div class="dp-sec-title">Graph</div>';
  html+='<div class="dp-row"><span class="dp-key">Degree</span><span class="dp-val">'+deg+'</span></div>';
  html+='<div class="dp-row"><span class="dp-key">ID</span><span class="dp-val dp-id">'+esc(n.id)+'</span></div></div>';
  var pairs=Object.keys(props);
  if(pairs.length){
    html+='<div class="dp-sec"><div class="dp-sec-title">Properties</div>';
    pairs.slice(0,30).forEach(function(k){
      html+='<div class="dp-row"><span class="dp-key">'+esc(k)+'</span><span class="dp-val">'+esc(String(props[k]).slice(0,200))+'</span></div>';
    });
    html+='</div>';
  }
  if(nbrs.length){
    html+='<div class="dp-sec"><div class="dp-sec-title">Connected ('+nbrs.length+')</div><div style="margin-top:4px">';
    nbrs.slice(0,60).forEach(function(nb){
      var nc=nodeColor(nb);
      html+='<span class="chip" data-id="'+esc(nb.id)+'" style="border-color:'+nc+'44;color:'+nc+'">'+esc((nb.label||nb.type||'').slice(0,28))+'</span>';
    });
    html+='</div></div>';
  }
  dpb.innerHTML=html;
  dp.classList.add('open');
  gw.style.right='320px';
}

function closePanel(){ dp.classList.remove('open'); gw.style.right='0'; }
document.getElementById('dp-close').addEventListener('click', closePanel);

dpb.addEventListener('click', function(e){
  var chip=e.target.closest('.chip[data-id]');
  if(!chip) return;
  var n=NODE_MAP[chip.dataset.id];
  if(!n) return;
  network.selectNodes([n.id]);
  network.focus(n.id,{scale:1.4,animation:{duration:400}});
  openPanel(n);
});

network.on('click', function(params){
  if(params.nodes.length){ var n=NODE_MAP[params.nodes[0]]; if(n) openPanel(n); }
  else closePanel();
});

// ── search ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', function(e){
  var q=e.target.value.trim().toLowerCase();
  if(!q){
    nodes.update(VIS_NODES.map(function(n){return{id:n.id,opacity:1};}));
    document.getElementById('mc').textContent='';
    return;
  }
  var matched=[];
  nodes.update(VIS_NODES.map(function(n){
    var hit=(n.label||'').toLowerCase().includes(q)||(n.type||'').toLowerCase().includes(q);
    if(hit) matched.push(n.id);
    return{id:n.id,opacity:hit?1:.06};
  }));
  document.getElementById('mc').textContent=matched.length+' MATCH'+(matched.length!==1?'ES':'');
  if(matched.length===1) network.focus(matched[0],{scale:1.5,animation:{duration:500}});
});
</script>
</body>
</html>
"""


def generate_html(nodes: dict, edges: list) -> str:
    vis_nodes, vis_edges = _build_vis_data(nodes, edges)
    stats  = f"{len(vis_nodes)} nodes &nbsp;|&nbsp; {len(vis_edges)} edges"
    legend = _build_legend_html(nodes)
    html   = _HTML_TEMPLATE
    html   = html.replace("__VIS_NODES__", json.dumps(vis_nodes, ensure_ascii=False))
    html   = html.replace("__VIS_EDGES__", json.dumps(vis_edges, ensure_ascii=False))
    html   = html.replace("__STATS__",     stats)
    html   = html.replace("__LEGEND__",    legend)
    return html


async def build(output_path: str, limit: int = 5000) -> str:
    print(f"[graph_builder] Fetching up to {limit} nodes from Neo4j…")
    nodes, edges = await _fetch(limit)
    print(f"[graph_builder]   {len(nodes)} nodes, {len(edges)} edges")
    if not nodes:
        return "Graph is empty — run cognify first."
    print(f"[graph_builder] Writing HTML to {output_path}")
    html = generate_html(nodes, edges)
    tmp  = pathlib.Path(output_path).parent / f".kg_tmp_{os.getpid()}.html"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, output_path)
    return f"Graph ready → {output_path}"


if __name__ == "__main__":
    out   = sys.argv[1] if len(sys.argv) > 1 else "/graph/cognee_graph.html"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    asyncio.run(build(out, limit))
