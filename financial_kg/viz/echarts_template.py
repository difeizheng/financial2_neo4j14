"""ECharts HTML template for propagation graph animation."""
from __future__ import annotations

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"


def render_propagation_html(
    graph_json: str,
    height: str = "800px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Return a complete HTML string embedding ECharts propagation graph.

    graph_json: JSON string from build_propagation_data(), injected as JS variable.
    """
    # part1: f-string for Python variables (height, echarts_cdn only).
    # Ends just before graph_json injection to avoid Python interpreting
    # JSON braces like {"lit": false} as f-string placeholders.
    part1 = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0f1117; font-family: 'Segoe UI', sans-serif; overflow: hidden; }}
  #wrap {{ position: relative; width: 100%; height: {height}; }}
  #chart {{ width: 100%; height: 100%; }}
  #controls {{
    position: absolute; top: 12px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.92); border: 1px solid #2a3050;
    border-radius: 8px; padding: 10px 14px; color: #cdd6f4;
    display: flex; flex-direction: column; gap: 8px; min-width: 220px;
  }}
  .ctrl-row {{ display: flex; align-items: center; gap: 8px; }}
  .ctrl-label {{ font-size: 12px; color: #a6adc8; min-width: 70px; }}
  .ctrl-val {{ font-size: 12px; color: #89b4fa; min-width: 24px; text-align: right; }}
  input[type=range] {{ flex: 1; accent-color: #89b4fa; cursor: pointer; }}
  button {{
    padding: 5px 12px; border-radius: 5px; border: none; cursor: pointer;
    font-size: 12px; font-weight: 600; transition: opacity .15s;
  }}
  button:hover {{ opacity: 0.85; }}
  #btn-play {{ background: #a6e3a1; color: #1e1e2e; }}
  #btn-reset {{ background: #45475a; color: #cdd6f4; }}
  #btn-fs {{ background: #89b4fa; color: #1e1e2e; }}
  .btn-row {{ display: flex; gap: 6px; }}
  #stats {{
    position: absolute; bottom: 10px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.85); border-radius: 6px;
    padding: 5px 10px; font-size: 11px; color: #6c7086;
  }}
  #warn {{
    position: absolute; bottom: 10px; right: 12px; z-index: 10;
    background: rgba(250,179,135,0.15); border: 1px solid #fab387;
    border-radius: 6px; padding: 5px 10px; font-size: 11px; color: #fab387;
    display: none;
  }}
</style>
</head>
<body>
<div id="wrap">
  <div id="chart"></div>
  <div id="controls">
    <div class="btn-row">
      <button id="btn-play">&#9654; 播放</button>
      <button id="btn-reset">&#8634; 重置</button>
      <button id="btn-fs">&#x26F6; 全屏</button>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">显示深度</span>
      <input type="range" id="sl-depth" min="1" max="20" value="20">
      <span class="ctrl-val" id="lbl-depth">20</span>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">动画速度</span>
      <input type="range" id="sl-speed" min="1" max="5" value="3">
      <span class="ctrl-val" id="lbl-speed">3x</span>
    </div>
  </div>
  <div id="stats">节点: 0 | 边: 0</div>
  <div id="warn">&#9888; 图谱已截断</div>
</div>
<script src="{echarts_cdn}"></script>
<script>
var graphData = """

    # part2: raw string — no Python variables, no f-string escaping needed.
    part2 = r"""
;

var chart = echarts.init(document.getElementById('chart'), 'dark', {renderer: 'canvas'});
var allNodes = graphData.nodes;
var allEdges = graphData.edges;
var depthLevels = graphData.depth_levels;
var rootId = graphData.root_id;
var maxDataDepth = graphData.max_depth;
var stats = graphData.stats;

var SPEED_DELAYS = [2000, 1200, 700, 400, 200];
var animTimer = null;
var isPlaying = false;
var currentAnimDepth = 0;
var displayDepth = 20;

var nodeIndex = {};
allNodes.forEach(function(n, i) { nodeIndex[n.id] = i; });

function buildDisplayNodes(litSet, depthLimit) {
  return allNodes.map(function(n) {
    var hidden = n.depth > depthLimit;
    var isLit = litSet.has(n.id) || n.id === rootId;
    return Object.assign({}, n, {
      symbolSize: hidden ? 0 : (isLit ? n.symbolSize : 4),
      itemStyle: {
        opacity: hidden ? 0 : (isLit ? 1 : 0.15),
        color: hidden ? 'transparent' : undefined,
      },
      label: {show: isLit && !hidden},
    });
  });
}

function buildDisplayEdges(litSet, depthLimit) {
  return allEdges.map(function(e) {
    var srcNode = allNodes[nodeIndex[e.source]];
    var tgtNode = allNodes[nodeIndex[e.target]];
    var srcDepth = srcNode ? srcNode.depth : 0;
    var tgtDepth = tgtNode ? tgtNode.depth : 0;
    var hidden = srcDepth > depthLimit || tgtDepth > depthLimit;
    var isLit = litSet.has(e.target) || e.target === rootId;
    return Object.assign({}, e, {
      lineStyle: {
        opacity: hidden ? 0 : (isLit ? 0.8 : 0.08),
        width: isLit ? 1.5 : 0.8,
      },
    });
  });
}

var litNodes = new Set([rootId]);

function getOption(nodes, edges) {
  return {
    backgroundColor: '#0f1117',
    legend: {
      data: graphData.categories.map(function(c) { return c.name; }),
      textStyle: {color: '#a6adc8'},
      top: 8, right: 12,
    },
    tooltip: {
      trigger: 'item',
      formatter: function(params) {
        if (params.dataType !== 'node') return '';
        var d = params.data;
        var lines = [
          '<b>' + (d.id || '') + '</b>',
          'Sheet: ' + (d.sheet || ''),
          '深度: ' + (d.depth !== undefined ? d.depth : ''),
        ];
        if (d.value_old !== null && d.value_old !== undefined)
          lines.push('旧値: ' + d.value_old);
        if (d.value_new !== null && d.value_new !== undefined)
          lines.push('新値: ' + d.value_new);
        if (d.formula) lines.push('公式: ' + d.formula.substring(0, 60));
        if (d.indicator_name) lines.push('指标: ' + d.indicator_name);
        return lines.join('<br>');
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      data: nodes,
      links: edges,
      categories: graphData.categories,
      roam: true,
      draggable: true,
      force: {
        repulsion: 120,
        gravity: 0.05,
        edgeLength: [40, 160],
        friction: 0.6,
        layoutAnimation: true,
      },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 7],
      emphasis: {focus: 'adjacency', lineStyle: {width: 3}},
      animationDurationUpdate: 300,
      animationEasingUpdate: 'cubicInOut',
      label: {
        show: false,
        position: 'right',
        fontSize: 10,
        color: '#cdd6f4',
      },
    }],
  };
}

chart.setOption(getOption(
  buildDisplayNodes(litNodes, displayDepth),
  buildDisplayEdges(litNodes, displayDepth)
));

document.getElementById('stats').textContent =
  '节点: ' + stats.total_nodes + ' | 边: ' + stats.total_edges;
if (stats.truncated) document.getElementById('warn').style.display = 'block';

function getSpeedDelay() {
  return SPEED_DELAYS[parseInt(document.getElementById('sl-speed').value) - 1];
}

function animateNextLayer() {
  currentAnimDepth++;
  var key = String(currentAnimDepth);
  var layer = depthLevels[key];
  if (!layer || currentAnimDepth > displayDepth) {
    stopAnimation();
    document.getElementById('btn-play').innerHTML = '&#9654; 播放';
    return;
  }
  layer.forEach(function(id) { litNodes.add(id); });
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
  animTimer = setTimeout(animateNextLayer, getSpeedDelay());
}

function stopAnimation() {
  if (animTimer) { clearTimeout(animTimer); animTimer = null; }
  isPlaying = false;
}

document.getElementById('btn-play').addEventListener('click', function() {
  if (isPlaying) {
    stopAnimation();
    this.innerHTML = '&#9654; 播放';
  } else {
    isPlaying = true;
    this.innerHTML = '&#9646;&#9646; 暂停';
    animateNextLayer();
  }
});

document.getElementById('btn-reset').addEventListener('click', function() {
  stopAnimation();
  document.getElementById('btn-play').innerHTML = '&#9654; 播放';
  currentAnimDepth = 0;
  litNodes = new Set([rootId]);
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
});

var slDepth = document.getElementById('sl-depth');
var lblDepth = document.getElementById('lbl-depth');
slDepth.max = maxDataDepth || 20;
slDepth.value = maxDataDepth || 20;
displayDepth = parseInt(slDepth.value);
lblDepth.textContent = displayDepth;

slDepth.addEventListener('input', function() {
  displayDepth = parseInt(this.value);
  lblDepth.textContent = displayDepth;
  chart.setOption({
    series: [{
      data: buildDisplayNodes(litNodes, displayDepth),
      links: buildDisplayEdges(litNodes, displayDepth),
    }],
  });
});

document.getElementById('sl-speed').addEventListener('input', function() {
  document.getElementById('lbl-speed').textContent = this.value + 'x';
});

document.getElementById('btn-fs').addEventListener('click', function() {
  var wrap = document.getElementById('wrap');
  if (!document.fullscreenElement) {
    wrap.requestFullscreen && wrap.requestFullscreen();
  } else {
    document.exitFullscreen && document.exitFullscreen();
  }
});
document.addEventListener('fullscreenchange', function() {
  setTimeout(function() { chart.resize(); }, 100);
});

window.addEventListener('resize', function() { chart.resize(); });
</script>
</body>
</html>"""

    return part1 + graph_json + part2


def render_graph_html(
    graph_json: str,
    height: str = "700px",
    echarts_cdn: str = _ECHARTS_CDN,
    default_layout: str = "force",
) -> str:
    """Return a complete HTML string for a general ECharts knowledge graph.

    Supports force/circular/radial layout switching, force freeze after stabilization,
    and fullscreen toggle.

    graph_json: JSON string from echarts_graph.py builders, injected as JS variable.
    default_layout: initial layout mode — 'force' | 'circular' | 'radial'.
    """
    part1 = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0f1117; font-family: 'Segoe UI', sans-serif; overflow: hidden; }}
  #wrap {{ position: relative; width: 100%; height: {height}; }}
  #chart {{ width: 100%; height: 100%; }}
  #controls {{
    position: absolute; top: 12px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.92); border: 1px solid #2a3050;
    border-radius: 8px; padding: 10px 14px; color: #cdd6f4;
    display: flex; flex-direction: column; gap: 8px; min-width: 240px;
  }}
  .ctrl-row {{ display: flex; align-items: center; gap: 8px; }}
  .ctrl-label {{ font-size: 12px; color: #a6adc8; min-width: 60px; }}
  .ctrl-val {{ font-size: 12px; color: #89b4fa; min-width: 24px; text-align: right; }}
  select {{
    flex: 1; background: #1e1e2e; color: #cdd6f4; border: 1px solid #2a3050;
    border-radius: 4px; padding: 4px 8px; font-size: 12px; cursor: pointer;
  }}
  button {{
    padding: 5px 12px; border-radius: 5px; border: none; cursor: pointer;
    font-size: 12px; font-weight: 600; transition: opacity .15s;
  }}
  button:hover {{ opacity: 0.85; }}
  #btn-fs {{ background: #89b4fa; color: #1e1e2e; }}
  #btn-freeze {{ background: #f9e2af; color: #1e1e2e; }}
  .btn-row {{ display: flex; gap: 6px; }}
  #stats {{
    position: absolute; bottom: 10px; left: 12px; z-index: 10;
    background: rgba(20,24,36,0.85); border-radius: 6px;
    padding: 5px 10px; font-size: 11px; color: #6c7086;
  }}
</style>
</head>
<body>
<div id="wrap">
  <div id="chart"></div>
  <div id="controls">
    <div class="btn-row">
      <button id="btn-freeze">&#9646;&#9646; 冻结</button>
      <button id="btn-fs">&#x26F6; 全屏</button>
    </div>
    <div class="ctrl-row">
      <span class="ctrl-label">布局</span>
      <select id="sel-layout">
        <option value="force"{" selected" if default_layout == "force" else ""}>力导向</option>
        <option value="circular"{" selected" if default_layout == "circular" else ""}>环形</option>
        <option value="radial"{" selected" if default_layout == "radial" else ""}>径向</option>
        <option value="tree"{" selected" if default_layout == "tree" else ""}>分层树</option>
        <option value="mindmap"{" selected" if default_layout == "mindmap" else ""}>思维导图</option>
        <option value="layered"{" selected" if default_layout == "layered" else ""}>分层层次</option>
        <option value="concentric"{" selected" if default_layout == "concentric" else ""}>同心圆</option>
      </select>
    </div>
  </div>
  <div id="stats">节点: 0 | 边: 0</div>
</div>
<script src="{echarts_cdn}"></script>
<script>
var graphData = """

    part2 = r"""
;

var chart = echarts.init(document.getElementById('chart'), 'dark', {renderer: 'canvas'});
var allNodes = graphData.nodes;
var allEdges = graphData.edges;
var categories = graphData.categories;
var rootId = graphData.root_id || '';
var stats = graphData.stats;

var currentLayout = '""" + default_layout + r"""';
var frozen = false;
var freezeTimer = null;

// Build node index
var nodeIndex = {};
allNodes.forEach(function(n, i) { nodeIndex[n.id] = i; });

function computeRadialPositions() {
  var positions = {};
  // Group by depth
  var groups = {};
  var maxDepth = 0;
  allNodes.forEach(function(n) {
    var d = n.depth || 0;
    if (!groups[d]) groups[d] = [];
    groups[d].push(n);
    if (d > maxDepth) maxDepth = d;
  });

  var cx = 0, cy = 0;
  // Root node at center
  if (rootId) {
    positions[rootId] = {x: cx, y: cy};
  }

  var radiusStep = 120;
  for (var d = 0; d <= maxDepth; d++) {
    var group = groups[d];
    if (!group) continue;
    var r = d * radiusStep;
    if (d === 0 && rootId) {
      // Root already placed, skip other depth-0 nodes or place them nearby
      group.forEach(function(n) {
        if (n.id === rootId) return;
        positions[n.id] = {x: cx + 40, y: cy + 40};
      });
      continue;
    }
    var count = group.length;
    for (var i = 0; i < count; i++) {
      var angle = (2 * Math.PI * i / count) - Math.PI / 2;
      positions[group[i].id] = {
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle),
      };
    }
  }
  // Place ungrouped nodes
  allNodes.forEach(function(n) {
    if (!positions[n.id]) {
      positions[n.id] = {x: (Math.random() - 0.5) * 400, y: (Math.random() - 0.5) * 400};
    }
  });
  return positions;
}

function computeTreePositions() {
  var positions = {};
  if (allNodes.length === 0) return positions;

  // Build BFS tree from root
  var rootNode = null;
  if (rootId) {
    for (var i = 0; i < allNodes.length; i++) {
      if (allNodes[i].id === rootId) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) {
    var hasInc = {};
    allEdges.forEach(function(e) { hasInc[e.target] = true; });
    for (var i = 0; i < allNodes.length; i++) {
      if (!hasInc[allNodes[i].id]) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) rootNode = allNodes[0];

  // BFS: build children map and depth
  var children = {};
  var nodeDepth = {};
  var visited = {};
  nodeDepth[rootNode.id] = 0;
  var queue = [rootNode.id];
  children[rootNode.id] = [];

  while (queue.length > 0) {
    var nid = queue.shift();
    if (visited[nid]) continue;
    visited[nid] = true;
    var d = nodeDepth[nid];
    if (!children[nid]) children[nid] = [];
    allEdges.forEach(function(e) {
      if (e.source === nid && !visited[e.target]) {
        nodeDepth[e.target] = d + 1;
        children[nid].push(e.target);
        children[e.target] = [];
        queue.push(e.target);
      }
    });
  }

  // Post-order: compute subtree width (leaf count), then assign positions
  var leafCount = {};
  function countLeaves(id) {
    if (leafCount[id] !== undefined) return leafCount[id];
    var ch = children[id] || [];
    if (ch.length === 0) { leafCount[id] = 1; return 1; }
    var total = 0;
    for (var i = 0; i < ch.length; i++) total += countLeaves(ch[i]);
    leafCount[id] = total;
    return total;
  }
  countLeaves(rootNode.id);

  // Assign positions top-down: parent centered above children
  var colW = 200;  // horizontal spacing
  var rowH = 160;  // vertical spacing per depth level

  function assignPos(id, x, y) {
    positions[id] = {x: x, y: y};
    var ch = children[id] || [];
    if (ch.length === 0) return;
    // Total width of this subtree
    var totalW = (countLeaves(id) - 1) * colW;
    var startX = x - totalW / 2;
    var childY = y + rowH;
    var curX = startX;
    for (var i = 0; i < ch.length; i++) {
      var cw = (countLeaves(ch[i]) - 1) * colW;
      var childCenterX = curX + cw / 2;
      assignPos(ch[i], childCenterX, childY);
      curX += cw + colW;
    }
  }
  assignPos(rootNode.id, 0, 0);

  // Unvisited nodes at bottom
  var maxY = 0;
  for (var id in positions) {
    if (positions[id].y > maxY) maxY = positions[id].y;
  }
  allNodes.forEach(function(n) {
    if (!positions[n.id]) {
      positions[n.id] = {x: (Math.random() - 0.5) * 200, y: maxY + rowH};
    }
  });

  return positions;
}

function computeMindmapPositions() {
  var positions = {};
  if (allNodes.length === 0) return positions;

  // Find root
  var rootNode = null;
  if (rootId) {
    for (var i = 0; i < allNodes.length; i++) {
      if (allNodes[i].id === rootId) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) {
    var hasInc = {};
    allEdges.forEach(function(e) { hasInc[e.target] = true; });
    for (var i = 0; i < allNodes.length; i++) {
      if (!hasInc[allNodes[i].id]) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) rootNode = allNodes[0];

  // BFS tree
  var children = {};
  var visited = {};
  children[rootNode.id] = [];
  var queue = [rootNode.id];
  while (queue.length > 0) {
    var nid = queue.shift();
    if (visited[nid]) continue;
    visited[nid] = true;
    if (!children[nid]) children[nid] = [];
    allEdges.forEach(function(e) {
      if (e.source === nid && !visited[e.target]) {
        children[nid].push(e.target);
        if (!children[e.target]) children[e.target] = [];
        queue.push(e.target);
      }
    });
  }

  // Split root children into left and right groups
  var rootCh = children[rootNode.id] || [];
  var leftCh = [], rightCh = [];
  for (var i = 0; i < rootCh.length; i++) {
    if (i % 2 === 0) rightCh.push(rootCh[i]);
    else leftCh.push(rootCh[i]);
  }

  // Subtree leaf count
  var leafCount = {};
  function countLeaves(id) {
    if (leafCount[id] !== undefined) return leafCount[id];
    var ch = children[id] || [];
    if (ch.length === 0) { leafCount[id] = 1; return 1; }
    var total = 0;
    for (var i = 0; i < ch.length; i++) total += countLeaves(ch[i]);
    leafCount[id] = total;
    return total;
  }
  // Count for all trees
  for (var i = 0; i < rightCh.length; i++) countLeaves(rightCh[i]);
  for (var i = 0; i < leftCh.length; i++) countLeaves(leftCh[i]);

  var colW = 200;
  var rowH = 100;

  // Assign right subtree
  function assignRight(id, depth, yStart) {
    var x = depth * colW;
    var ch = children[id] || [];
    if (ch.length === 0) {
      positions[id] = {x: x, y: yStart};
      return yStart + rowH;
    }
    var nextY = yStart;
    for (var i = 0; i < ch.length; i++) {
      nextY = assignRight(ch[i], depth + 1, nextY);
    }
    // Parent Y = center of children range
    positions[id] = {x: x, y: (yStart + nextY - rowH) / 2};
    return nextY;
  }

  // Assign left subtree (mirror)
  function assignLeft(id, depth, yStart) {
    var x = -depth * colW;
    var ch = children[id] || [];
    if (ch.length === 0) {
      positions[id] = {x: x, y: yStart};
      return yStart + rowH;
    }
    var nextY = yStart;
    for (var i = 0; i < ch.length; i++) {
      nextY = assignLeft(ch[i], depth + 1, nextY);
    }
    positions[id] = {x: x, y: (yStart + nextY - rowH) / 2};
    return nextY;
  }

  // Root at center
  positions[rootNode.id] = {x: 0, y: 0};

  var curY = 0;
  for (var i = 0; i < rightCh.length; i++) {
    curY = assignRight(rightCh[i], 1, curY);
  }
  curY = 0;
  for (var i = 0; i < leftCh.length; i++) {
    curY = assignLeft(leftCh[i], 1, curY);
  }

  // Center Y
  var minY = Infinity, maxY = -Infinity;
  for (var id in positions) {
    if (positions[id].y < minY) minY = positions[id].y;
    if (positions[id].y > maxY) maxY = positions[id].y;
  }
  var centerY = (minY + maxY) / 2;
  for (var id in positions) {
    positions[id].y -= centerY;
  }

  // Unvisited
  allNodes.forEach(function(n) {
    if (!positions[n.id]) {
      positions[n.id] = {x: (Math.random() - 0.5) * 200, y: maxY + 100};
    }
  });

  return positions;
}

function computeLayeredPositions() {
  var positions = {};
  if (allNodes.length === 0) return positions;

  // Build adjacency for depth computation (longest path from sources)
  var inDeg = {};
  var children = {};
  var nodeIds = {};
  allNodes.forEach(function(n) {
    inDeg[n.id] = 0;
    children[n.id] = [];
    nodeIds[n.id] = true;
  });
  allEdges.forEach(function(e) {
    if (nodeIds[e.source] && nodeIds[e.target]) {
      children[e.source].push(e.target);
      inDeg[e.target]++;
    }
  });

  // Topological order (Kahn's) + longest path depth
  var depth = {};
  var queue = [];
  for (var id in inDeg) {
    if (inDeg[id] === 0) { depth[id] = 0; queue.push(id); }
  }
  var qi = 0;
  while (qi < queue.length) {
    var u = queue[qi++];
    for (var ci = 0; ci < (children[u] || []).length; ci++) {
      var v = children[u][ci];
      depth[v] = Math.max(depth[v] || 0, (depth[u] || 0) + 1);
      inDeg[v]--;
      if (inDeg[v] === 0) queue.push(v);
    }
  }
  // Handle cycles (unvisited nodes) — assign max depth + 1
  var maxD = 0;
  for (var id in depth) { if (depth[id] > maxD) maxD = depth[id]; }
  allNodes.forEach(function(n) {
    if (depth[n.id] === undefined) depth[n.id] = maxD + 1;
  });

  // Group by depth
  var layers = {};
  var maxDepth = 0;
  for (var id in depth) {
    var d = depth[id];
    if (!layers[d]) layers[d] = [];
    layers[d].push(id);
    if (d > maxDepth) maxDepth = d;
  }

  // Barycenter heuristic: reorder within each layer to reduce edge crossings
  // Iterate 3 passes: alternate sweep up/down
  function barycenterOrder(layerIds, prevLayerIds, edgeDir) {
    // edgeDir: 'forward' (edges go from prev to this layer) or 'backward'
    var posMap = {};
    for (var i = 0; i < prevLayerIds.length; i++) posMap[prevLayerIds[i]] = i;
    return layerIds.slice().sort(function(a, b) {
      var aSum = 0, aCnt = 0, bSum = 0, bCnt = 0;
      var nbrs = edgeDir === 'forward'
        ? children[a] : [];
      // For each neighbor in prev layer, get its position
      for (var ni = 0; ni < nbrs.length; ni++) {
        if (posMap[nbrs[ni]] !== undefined) { aSum += posMap[nbrs[ni]]; aCnt++; }
      }
      var nbrs2 = edgeDir === 'forward'
        ? children[b] : [];
      for (var ni = 0; ni < nbrs2.length; ni++) {
        if (posMap[nbrs2[ni]] !== undefined) { bSum += posMap[nbrs2[ni]]; bCnt++; }
      }
      var aAvg = aCnt > 0 ? aSum / aCnt : aSum;
      var bAvg = bCnt > 0 ? bSum / bCnt : bSum;
      return aAvg - bAvg;
    });
  }

  // Build reverse children map for barycenter
  var parents = {};
  for (var id in nodeIds) parents[id] = [];
  allEdges.forEach(function(e) {
    if (nodeIds[e.source] && nodeIds[e.target]) parents[e.target].push(e.source);
  });

  for (var pass = 0; pass < 3; pass++) {
    for (var d = 1; d <= maxDepth; d++) {
      var layer = layers[d];
      if (!layer) continue;
      var prevLayer = layers[d - 1];
      if (!prevLayer) continue;
      // Compute barycenter based on connections to previous layer
      var prevPos = {};
      for (var i = 0; i < prevLayer.length; i++) prevPos[prevLayer[i]] = i;
      layer.sort(function(a, b) {
        var aSum = 0, aCnt = 0, bSum = 0, bCnt = 0;
        var pa = parents[a] || [];
        for (var pi = 0; pi < pa.length; pi++) {
          if (prevPos[pa[pi]] !== undefined) { aSum += prevPos[pa[pi]]; aCnt++; }
        }
        var pb = parents[b] || [];
        for (var pi = 0; pi < pb.length; pi++) {
          if (prevPos[pb[pi]] !== undefined) { bSum += prevPos[pb[pi]]; bCnt++; }
        }
        var aAvg = aCnt > 0 ? aSum / aCnt : 999999;
        var bAvg = bCnt > 0 ? bSum / bCnt : 999999;
        return aAvg - bAvg;
      });
    }
  }

  // Assign positions: x = depth * colWidth, y = index in layer * adaptive rowHeight
  var colW = 300;
  var maxLayerSize = 0;
  for (var d = 0; d <= maxDepth; d++) {
    if (layers[d] && layers[d].length > maxLayerSize) maxLayerSize = layers[d].length;
  }

  // Adaptive rowHeight: leave room for small nodes (4-8px) in each layer
  // Target: fit within typical viewport, nodes ~6px apart minimum
  var targetMaxH = Math.min(allNodes.length * 8, 3000); // cap total height
  var rowH = maxLayerSize > 1 ? Math.max(10, targetMaxH / maxLayerSize) : 60;

  for (var d = 0; d <= maxDepth; d++) {
    var layer = layers[d] || [];
    var totalH = (layer.length - 1) * rowH;
    var startY = -totalH / 2;
    for (var i = 0; i < layer.length; i++) {
      positions[layer[i]] = {x: d * colW, y: startY + i * rowH};
    }
  }

  // Unvisited nodes at bottom-right
  var unplaced = [];
  allNodes.forEach(function(n) {
    if (!positions[n.id]) unplaced.push(n);
  });
  var maxY = 0;
  for (var id in positions) { if (positions[id].y > maxY) maxY = positions[id].y; }
  for (var i = 0; i < unplaced.length; i++) {
    positions[unplaced[i].id] = {x: (maxDepth + 1) * colW, y: maxY + (i + 1) * Math.max(rowH, 10)};
  }

  // Center X
  var totalW = maxDepth * colW;
  var offsetX = -totalW / 2;
  for (var id in positions) { positions[id].x += offsetX; }

  return positions;
}

function computeConcentricPositions() {
  var positions = {};
  if (allNodes.length === 0) return positions;

  // Find root: use rootId, or node with no incoming edges, or first node
  var rootNode = null;
  if (rootId) {
    for (var i = 0; i < allNodes.length; i++) {
      if (allNodes[i].id === rootId) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) {
    var hasInc = {};
    allEdges.forEach(function(e) { hasInc[e.target] = true; });
    for (var i = 0; i < allNodes.length; i++) {
      if (!hasInc[allNodes[i].id]) { rootNode = allNodes[i]; break; }
    }
  }
  if (!rootNode) rootNode = allNodes[0];

  // BFS: compute depth from root
  var depth = {};
  var children = {};
  var visited = {};
  depth[rootNode.id] = 0;
  children[rootNode.id] = [];
  var queue = [rootNode.id];
  var qi = 0;
  while (qi < queue.length) {
    var nid = queue[qi++];
    if (visited[nid]) continue;
    visited[nid] = true;
    if (!children[nid]) children[nid] = [];
    allEdges.forEach(function(e) {
      if (e.source === nid && !visited[e.target]) {
        depth[e.target] = (depth[nid] || 0) + 1;
        children[nid].push(e.target);
        if (!children[e.target]) children[e.target] = [];
        queue.push(e.target);
      }
    });
  }

  // Unvisited nodes: assign to max depth + 1
  var maxD = 0;
  for (var id in depth) { if (depth[id] > maxD) maxD = depth[id]; }
  allNodes.forEach(function(n) {
    if (depth[n.id] === undefined) {
      depth[n.id] = maxD + 1;
      children[n.id] = [];
    }
  });

  // Group by depth
  var layers = {};
  var maxDepth = 0;
  for (var id in depth) {
    var d = depth[id];
    if (!layers[d]) layers[d] = [];
    layers[d].push(id);
    if (d > maxDepth) maxDepth = d;
  }

  // Barycenter reorder within layers (same as layered)
  var parents = {};
  for (var id in depth) parents[id] = [];
  allEdges.forEach(function(e) {
    if (depth[e.source] !== undefined && depth[e.target] !== undefined) {
      parents[e.target].push(e.source);
    }
  });

  for (var pass = 0; pass < 3; pass++) {
    for (var d = 1; d <= maxDepth; d++) {
      var layer = layers[d];
      if (!layer) continue;
      var prevLayer = layers[d - 1];
      if (!prevLayer) continue;
      var prevPos = {};
      for (var i = 0; i < prevLayer.length; i++) prevPos[prevLayer[i]] = i;
      layer.sort(function(a, b) {
        var aSum = 0, aCnt = 0, bSum = 0, bCnt = 0;
        var pa = parents[a] || [];
        for (var pi = 0; pi < pa.length; pi++) {
          if (prevPos[pa[pi]] !== undefined) { aSum += prevPos[pa[pi]]; aCnt++; }
        }
        var pb = parents[b] || [];
        for (var pi = 0; pi < pb.length; pi++) {
          if (prevPos[pb[pi]] !== undefined) { bSum += prevPos[pb[pi]]; bCnt++; }
        }
        var aAvg = aCnt > 0 ? aSum / aCnt : 999999;
        var bAvg = bCnt > 0 ? bSum / bCnt : 999999;
        return aAvg - bAvg;
      });
    }
  }

  // Place on concentric rings
  var ringStep = 150;
  var angleStepBase = 0.3;
  for (var d = 0; d <= maxDepth; d++) {
    var layer = layers[d] || [];
    if (d === 0) {
      positions[layer[0]] = {x: 0, y: 0};
      continue;
    }
    var count = layer.length;
    var r = d * ringStep;
    var angleStep = count > 1 ? (2 * Math.PI / count) : 0;
    var startAngle = -Math.PI / 2;
    for (var i = 0; i < count; i++) {
      var angle = startAngle + i * angleStep;
      positions[layer[i]] = {x: r * Math.cos(angle), y: r * Math.sin(angle)};
    }
  }

  return positions;
}

function applyPositions(nodes, positions) {
  return nodes.map(function(n) {
    var p = positions[n.id];
    return Object.assign({}, n, {x: p.x, y: p.y, fixed: false});
  });
}

function buildNodesForLayout(nodes) {
  return nodes.map(function(n) {
    return Object.assign({}, n, {fixed: false});
  });
}

function getOption(nodes, edges) {
  var opt = {
    backgroundColor: '#0f1117',
    legend: {
      data: categories.map(function(c) { return c.name; }),
      textStyle: {color: '#a6adc8'},
      top: 8, right: 12,
    },
    tooltip: {
      trigger: 'item',
      formatter: function(params) {
        if (params.dataType !== 'node') return '';
        var d = params.data;
        var lines = [
          '<b>' + (d.id || '') + '</b>',
          'Sheet: ' + (d.sheet || ''),
          '深度: ' + (d.depth !== undefined ? d.depth : ''),
        ];
        if (d.value_old !== null && d.value_old !== undefined)
          lines.push('旧値: ' + d.value_old);
        if (d.value_new !== null && d.value_new !== undefined)
          lines.push('新値: ' + d.value_new);
        if (d.formula) lines.push('公式: ' + d.formula.substring(0, 60));
        if (d.indicator_name) lines.push('指标: ' + d.indicator_name);
        return lines.join('<br>');
      },
    },
    series: [{
      type: 'graph',
      layout: (currentLayout === 'radial' || currentLayout === 'tree' || currentLayout === 'mindmap' || currentLayout === 'layered' || currentLayout === 'concentric') ? 'none' : currentLayout,
      data: nodes,
      links: edges,
      categories: categories,
      roam: true,
      draggable: true,
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 7],
      emphasis: {focus: 'adjacency', lineStyle: {width: 3}},
      animationDurationUpdate: 300,
      animationEasingUpdate: 'cubicInOut',
      label: {
        show: currentLayout === 'tree' || currentLayout === 'mindmap',
        position: currentLayout === 'tree' ? 'bottom' : 'right',
        fontSize: 9,
        color: '#cdd6f4',
      },
      lineStyle: {
        curveness: (currentLayout === 'tree' || currentLayout === 'mindmap' || currentLayout === 'concentric') ? 0.2 : 0,
      },
    }],
  };

  var s = opt.series[0];
  if (currentLayout === 'force') {
    s.force = {
      repulsion: 120,
      gravity: 0.1,
      edgeLength: [60, 200],
      friction: 0.9,
      layoutAnimation: true,
    };
  } else if (currentLayout === 'circular') {
    s.circular = {
      rotateLabel: true,
    };
  } else if (currentLayout === 'layered') {
    // Dense DAG: shrink nodes, thin edges, label on hover only, zoom range wider
    var maxSym = 0, minSym = Infinity;
    for (var si = 0; si < nodes.length; si++) {
      if (nodes[si].symbolSize > maxSym) maxSym = nodes[si].symbolSize;
      if (nodes[si].symbolSize < minSym) minSym = nodes[si].symbolSize;
    }
    var sc = maxSym > 12 ? 0.35 : 1;
    for (var si = 0; si < nodes.length; si++) {
      nodes[si] = Object.assign({}, nodes[si], {symbolSize: Math.max(4, Math.round(nodes[si].symbolSize * sc))});
    }
    s.lineStyle = {width: 0.6, curveness: 0.15, opacity: 0.3};
    s.emphasis = {focus: 'adjacency', lineStyle: {width: 2.5}};
    s.label = {show: true, position: 'right', fontSize: 8, color: '#cdd6f4', offset: [4, 0]};
    s.scaleLimit = {min: 0.1, max: 20};
    s.zoom = 0.6;
  } else if (currentLayout === 'concentric') {
    s.lineStyle = {width: 1, curveness: 0.2, opacity: 0.6};
    s.emphasis = {focus: 'adjacency', lineStyle: {width: 3}};
    s.label = {show: true, position: 'right', fontSize: 8, color: '#cdd6f4'};
    s.scaleLimit = {min: 0.2, max: 10};
  }
  // radial uses layout:'none' with pre-computed x/y

  return opt;
}

function showLabelThreshold(nodes, threshold) {
  // Show labels for visible nodes in large graphs to avoid clutter
  return nodes.map(function(n) {
    return Object.assign({}, n, {
      label: {show: n.symbolSize >= 15},
    });
  });
}

// Initial render
var initNodes = allNodes;
if (currentLayout === 'radial') {
  var radialPos = computeRadialPositions();
  initNodes = applyPositions(allNodes, radialPos);
} else if (currentLayout === 'tree') {
  var treePos = computeTreePositions();
  initNodes = applyPositions(allNodes, treePos);
} else if (currentLayout === 'mindmap') {
  var mindPos = computeMindmapPositions();
  initNodes = applyPositions(allNodes, mindPos);
} else if (currentLayout === 'layered') {
  var layeredPos = computeLayeredPositions();
  initNodes = applyPositions(allNodes, layeredPos);
} else if (currentLayout === 'concentric') {
  var concentricPos = computeConcentricPositions();
  initNodes = applyPositions(allNodes, concentricPos);
}

chart.setOption(getOption(buildNodesForLayout(initNodes), allEdges));

document.getElementById('stats').textContent =
  '节点: ' + stats.total_nodes + ' | 边: ' + stats.total_edges;

// Auto-freeze force layout after 4 seconds
if (currentLayout === 'force') {
  freezeTimer = setTimeout(function() {
    autoFreeze();
  }, 4000);
}

function autoFreeze() {
  if (frozen || currentLayout !== 'force') return;
  // Stop force animation while keeping current positions and layout type
  chart.setOption({
    series: [{
      force: {
        repulsion: 120,
        gravity: 0.1,
        edgeLength: [60, 200],
        friction: 0.9,
        layoutAnimation: false,
      },
    }],
  });
  frozen = true;
  document.getElementById('btn-freeze').innerHTML = '&#9654; 解冻';
}

document.getElementById('btn-freeze').addEventListener('click', function() {
  if (freezeTimer) { clearTimeout(freezeTimer); freezeTimer = null; }
  if (frozen) {
    frozen = false;
    chart.setOption({
      series: [{
        force: {
          layoutAnimation: true,
        },
      }],
    });
    this.innerHTML = '&#9646;&#9646; 冻结';
    freezeTimer = setTimeout(autoFreeze, 4000);
  } else {
    autoFreeze();
  }
});

document.getElementById('sel-layout').addEventListener('change', function() {
  currentLayout = this.value;
  frozen = false;
  if (freezeTimer) { clearTimeout(freezeTimer); freezeTimer = null; }
  document.getElementById('btn-freeze').innerHTML = '&#9646;&#9646; 冻结';

  var newNodes = allNodes;
  if (currentLayout === 'radial') {
    var radialPos = computeRadialPositions();
    newNodes = applyPositions(allNodes, radialPos);
  } else if (currentLayout === 'tree') {
    var treePos = computeTreePositions();
    newNodes = applyPositions(allNodes, treePos);
  } else if (currentLayout === 'mindmap') {
    var mindPos = computeMindmapPositions();
    newNodes = applyPositions(allNodes, mindPos);
  } else if (currentLayout === 'layered') {
    var layeredPos = computeLayeredPositions();
    newNodes = applyPositions(allNodes, layeredPos);
  } else if (currentLayout === 'concentric') {
    var concentricPos = computeConcentricPositions();
    newNodes = applyPositions(allNodes, concentricPos);
  }
  chart.setOption(getOption(
    (currentLayout === 'radial' || currentLayout === 'tree' || currentLayout === 'mindmap' || currentLayout === 'layered' || currentLayout === 'concentric') ? newNodes : buildNodesForLayout(allNodes),
    allEdges
  ), true);

  if (currentLayout === 'force') {
    freezeTimer = setTimeout(autoFreeze, 4000);
  }
});

document.getElementById('btn-fs').addEventListener('click', function() {
  var wrap = document.getElementById('wrap');
  if (!document.fullscreenElement) {
    wrap.requestFullscreen && wrap.requestFullscreen();
  } else {
    document.exitFullscreen && document.exitFullscreen();
  }
});
document.addEventListener('fullscreenchange', function() {
  setTimeout(function() { chart.resize(); }, 100);
});

window.addEventListener('resize', function() { chart.resize(); });
</script>
</body>
</html>"""

    return part1 + graph_json + part2
