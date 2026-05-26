"""BFS-based propagation graph builder for ECharts visualization."""
from __future__ import annotations
from collections import deque
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.snapshot import SnapshotDiff

# Category indices (must match echarts_template.py categories list)
_CAT_ROOT = 0
_CAT_CHANGED = 1
_CAT_DOWNSTREAM = 2
_CAT_INDICATOR = 3

_SYMBOL_SIZES = {_CAT_ROOT: 28, _CAT_CHANGED: 18, _CAT_DOWNSTREAM: 10, _CAT_INDICATOR: 22}


def build_propagation_data(
    graph: FinancialGraph,
    diff: SnapshotDiff,
    root_cell_id: str,
    max_depth: int = 10,
    max_nodes: int = 500,
) -> dict:
    """BFS from root_cell_id through downstream dependents.

    Edge convention: A -> B means "A depends on B".
    Downstream of B = predecessors of B (cells whose formulas reference B).

    Returns a dict ready for JSON serialization and ECharts consumption.
    """
    changed_set: set[str] = {c["id"] for c in diff.changed_cells}
    old_new: dict[str, tuple] = {c["id"]: (c["old"], c["new"]) for c in diff.changed_cells}

    # BFS: track depth per node
    depth_map: dict[str, int] = {root_cell_id: 0}
    queue: deque[str] = deque([root_cell_id])
    visited_cells: list[str] = [root_cell_id]  # ordered by discovery
    truncated = False

    while queue:
        node = queue.popleft()
        current_depth = depth_map[node]
        if current_depth >= max_depth:
            continue
        for pred in graph.cell_graph.predecessors(node):
            if pred in depth_map:
                continue
            depth_map[pred] = current_depth + 1
            visited_cells.append(pred)
            queue.append(pred)
            if len(visited_cells) >= max_nodes:
                truncated = True
                queue.clear()
                break

    # Build depth_levels index
    depth_levels: dict[int, list[str]] = {}
    for cid in visited_cells:
        d = depth_map[cid]
        depth_levels.setdefault(d, []).append(cid)

    # Collect indicator nodes for visited cells
    ind_depth: dict[str, int] = {}  # indicator_id -> min depth of its cells
    for cid in visited_cells:
        cell = graph.cells.get(cid)
        if cell and cell.indicator_id:
            iid = cell.indicator_id
            ind_depth[iid] = min(ind_depth.get(iid, 9999), depth_map[cid])

    # Build nodes list
    nodes: list[dict] = []
    node_ids: set[str] = set()

    for cid in visited_cells:
        cell = graph.cells.get(cid)
        is_root = cid == root_cell_id
        is_changed = cid in changed_set
        if is_root:
            cat = _CAT_ROOT
        elif is_changed:
            cat = _CAT_CHANGED
        else:
            cat = _CAT_DOWNSTREAM

        old_v, new_v = old_new.get(cid, (None, None))
        ind_name = None
        if cell and cell.indicator_id:
            ind = graph.indicators.get(cell.indicator_id)
            ind_name = ind.name if ind else None

        # Short label: strip sheet prefix
        parts = cid.split("_", 1)
        label = parts[1] if len(parts) == 2 else cid

        nodes.append({
            "id": cid,
            "name": label,
            "category": cat,
            "depth": depth_map[cid],
            "sheet": cell.sheet if cell else "",
            "value_old": old_v,
            "value_new": new_v,
            "formula": cell.formula_raw if cell else None,
            "indicator_name": ind_name,
            "symbolSize": _SYMBOL_SIZES[cat],
        })
        node_ids.add(cid)

    for iid, d in ind_depth.items():
        ind = graph.indicators.get(iid)
        if ind is None:
            continue
        nodes.append({
            "id": iid,
            "name": ind.name[:20] if ind.name else iid[-20:],
            "category": _CAT_INDICATOR,
            "depth": d,
            "sheet": ind.sheet,
            "value_old": None,
            "value_new": None,
            "formula": None,
            "indicator_name": ind.name,
            "unit": ind.unit,
            "display_value": ind.display_value,
            "summary_value": str(ind.summary_value) if ind.summary_value is not None else None,
            "category_str": ind.category,
            "subcategory": ind.subcategory,
            "symbolSize": _SYMBOL_SIZES[_CAT_INDICATOR],
        })
        node_ids.add(iid)
        depth_levels.setdefault(d, []).append(iid)

    # Build edges (only between nodes in our set)
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    for cid in visited_cells:
        for pred in graph.cell_graph.predecessors(cid):
            if pred in node_ids:
                key = (cid, pred)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({"source": cid, "target": pred})

    # Cell -> Indicator edges
    for cid in visited_cells:
        cell = graph.cells.get(cid)
        if cell and cell.indicator_id and cell.indicator_id in node_ids:
            key = (cid, cell.indicator_id)
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"source": cid, "target": cell.indicator_id})

    categories = [
        {"name": "起点", "itemStyle": {"color": "#EF5350"}},
        {"name": "直接变化", "itemStyle": {"color": "#FFA726"}},
        {"name": "下游传播", "itemStyle": {"color": "#78909C"}},
        {"name": "指标", "itemStyle": {"color": "#42A5F5"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "depth_levels": {str(k): v for k, v in depth_levels.items()},
        "max_depth": max(depth_map.values()) if depth_map else 0,
        "root_id": root_cell_id,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "truncated": truncated,
        },
    }
