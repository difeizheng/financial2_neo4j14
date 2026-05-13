"""ECharts-based data builders for financial knowledge graph visualization."""
from __future__ import annotations

import networkx as nx

from financial_kg.models.graph import FinancialGraph

# Node/edge color palette (matches graph_viz.py)
_COLORS = {
    "cell_formula": "#9E9E9E",
    "cell_value": "#BDBDBD",
    "indicator": "#42A5F5",
    "table": "#FFA726",
    "edge_depends": "#CFD8DC",
    "edge_calculates": "#42A5F5",
    "edge_feeds": "#FFA726",
}


def _make_stats(nodes: int, edges: int, truncated: bool = False) -> dict:
    return {"total_nodes": nodes, "total_edges": edges, "truncated": truncated}


def build_indicator_graph_data(
    graph: FinancialGraph,
    sheet_filter: str | None = None,
    max_nodes: int = 500,
) -> dict:
    """Build Indicator + Table layer data for ECharts."""
    nodes: list[dict] = []
    edges: list[dict] = []
    node_count = 0
    added_inds: set[str] = set()
    added_tables: set[str] = set()

    # Table nodes
    for tbl_id, tbl in graph.tables.items():
        if sheet_filter and tbl.sheet != sheet_filter:
            continue
        if node_count >= max_nodes:
            break
        nodes.append({
            "id": tbl_id,
            "name": tbl.name[:20],
            "category": 0,  # table
            "depth": 0,
            "sheet": tbl.sheet,
            "symbolSize": 25,
        })
        added_tables.add(tbl_id)
        node_count += 1

    # Indicator nodes
    for ind_id, ind in graph.indicators.items():
        if sheet_filter and ind.sheet != sheet_filter:
            continue
        if node_count >= max_nodes:
            break
        nodes.append({
            "id": ind_id,
            "name": ind.name[:18] if ind.name else ind_id[-20:],
            "category": 1,  # indicator
            "depth": 1,
            "sheet": ind.sheet,
            "symbolSize": 15,
        })
        added_inds.add(ind_id)
        node_count += 1

    # CALCULATES_FROM edges
    for ind_id in added_inds:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        for dep_id in ind.depends_on_indicators:
            if dep_id in added_inds:
                edges.append({"source": ind_id, "target": dep_id, "category": 1})

    # FEEDS_INTO edges
    for tbl_id in added_tables:
        tbl = graph.tables.get(tbl_id)
        if not tbl:
            continue
        for target_id in tbl.feeds_into:
            if target_id in added_tables:
                edges.append({"source": tbl_id, "target": target_id, "category": 0})

    categories = [
        {"name": "Table", "itemStyle": {"color": _COLORS["table"]}},
        {"name": "Indicator", "itemStyle": {"color": _COLORS["indicator"]}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "root_id": "",
        "stats": _make_stats(len(nodes), len(edges)),
    }


def build_cell_subgraph_data(
    graph: FinancialGraph,
    root_cell_id: str,
    depth: int = 3,
    max_nodes: int = 1000,
) -> dict:
    """Build cell dependency subgraph data for ECharts."""
    g = graph.cell_graph
    if root_cell_id not in g:
        raise ValueError(f"Cell {root_cell_id!r} not in graph")

    # BFS
    neighbors: set[str] = {root_cell_id}
    frontier = {root_cell_id}
    depth_map: dict[str, int] = {root_cell_id: 0}
    for d in range(1, depth + 1):
        next_frontier: set[str] = set()
        for n in frontier:
            next_frontier.update(g.predecessors(n))
            next_frontier.update(g.successors(n))
        new_nodes = next_frontier - neighbors
        for nn in new_nodes:
            depth_map[nn] = d
        neighbors |= new_nodes
        frontier = next_frontier
        if len(neighbors) >= max_nodes:
            break

    nodes: list[dict] = []
    edges: list[dict] = []

    for cid in sorted(neighbors, key=lambda c: depth_map.get(c, 0)):
        cell = graph.cells.get(cid)
        is_root = cid == root_cell_id
        has_formula = cell and cell.formula_raw
        cat = 0 if is_root else (1 if has_formula else 2)
        label = cid.split("_", 1)[-1] if "_" in cid else cid
        nodes.append({
            "id": cid,
            "name": label,
            "category": cat,
            "depth": depth_map.get(cid, 0),
            "sheet": cell.sheet if cell else "",
            "symbolSize": 20 if is_root else 12,
        })

    for src in neighbors:
        for dst in g.successors(src):
            if dst in neighbors:
                edges.append({"source": src, "target": dst, "category": 0})

    categories = [
        {"name": "根节点", "itemStyle": {"color": "#EF5350"}},
        {"name": "有公式", "itemStyle": {"color": _COLORS["cell_formula"]}},
        {"name": "纯数值", "itemStyle": {"color": _COLORS["cell_value"]}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "root_id": root_cell_id,
        "stats": _make_stats(len(nodes), len(edges)),
    }


def build_table_graph_data(
    graph: FinancialGraph,
    sheet_filter: str,
    max_nodes: int = 500,
) -> dict:
    """Build Table layer data for ECharts."""
    nodes: list[dict] = []
    edges: list[dict] = []
    in_sheet: set[str] = set()
    ghost_added: set[str] = set()

    for tbl_id, tbl in graph.tables.items():
        if tbl.sheet == sheet_filter:
            nodes.append({
                "id": tbl_id,
                "name": tbl.name[:20],
                "category": 0,  # in-sheet table
                "depth": 0,
                "sheet": tbl.sheet,
                "symbolSize": 25,
            })
            in_sheet.add(tbl_id)

    # External tables
    for tbl_id in in_sheet:
        tbl = graph.tables.get(tbl_id)
        if not tbl:
            continue
        for target_id in tbl.feeds_into:
            if target_id not in in_sheet and target_id not in ghost_added:
                target = graph.tables.get(target_id)
                label = target.name[:20] if target else target_id[-20:]
                nodes.append({
                    "id": target_id,
                    "name": label,
                    "category": 1,  # external table
                    "depth": 1,
                    "sheet": target.sheet if target else "",
                    "symbolSize": 15,
                })
                ghost_added.add(target_id)
            edges.append({"source": tbl_id, "target": target_id, "category": 0})

    categories = [
        {"name": "表内 Table", "itemStyle": {"color": _COLORS["table"]}},
        {"name": "外部 Table", "itemStyle": {"color": "#BDBDBD"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "root_id": "",
        "stats": _make_stats(len(nodes), len(edges)),
    }


def build_indicator_subgraph_data(
    graph: FinancialGraph,
    table_id: str,
    max_nodes: int = 1000,
) -> dict:
    """Build Indicator layer data for a specific table."""
    nodes: list[dict] = []
    edges: list[dict] = []
    tbl = graph.tables.get(table_id)
    in_table: set[str] = set(tbl.indicator_ids) if tbl else set()
    ghost_added: set[str] = set()

    for ind_id in in_table:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        nodes.append({
            "id": ind_id,
            "name": ind.name[:18] if ind.name else ind_id[-20:],
            "category": 0,  # in-table indicator
            "depth": 0,
            "sheet": ind.sheet,
            "symbolSize": 15,
        })

    for ind_id in in_table:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        for dep_id in ind.depends_on_indicators:
            if dep_id not in in_table and dep_id not in ghost_added:
                dep = graph.indicators.get(dep_id)
                label = dep.name[:18] if dep and dep.name else dep_id[-20:]
                nodes.append({
                    "id": dep_id,
                    "name": label,
                    "category": 1,  # external indicator
                    "depth": 1,
                    "sheet": dep.sheet if dep else "",
                    "symbolSize": 10,
                })
                ghost_added.add(dep_id)
            edges.append({"source": ind_id, "target": dep_id, "category": 0})

    categories = [
        {"name": "表内 Indicator", "itemStyle": {"color": _COLORS["indicator"]}},
        {"name": "外部 Indicator", "itemStyle": {"color": "#BDBDBD"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "root_id": "",
        "stats": _make_stats(len(nodes), len(edges)),
    }


def build_indicator_cell_graph_data(
    graph: FinancialGraph,
    indicator_id: str,
    max_nodes: int = 1000,
) -> dict:
    """Build Cell layer data for a specific indicator."""
    nodes: list[dict] = []
    edges: list[dict] = []
    ind = graph.indicators.get(indicator_id)
    in_indicator: set[str] = set(ind.cell_ids) if ind else set()
    ghost_added: set[str] = set()

    for cell_id in in_indicator:
        cell = graph.cells.get(cell_id)
        if not cell:
            continue
        cat = 1 if cell.formula_raw else 2
        label = cell_id.split("_", 1)[-1] if "_" in cell_id else cell_id
        nodes.append({
            "id": cell_id,
            "name": label,
            "category": cat,
            "depth": 0,
            "sheet": cell.sheet,
            "symbolSize": 15,
        })

    for cell_id in in_indicator:
        cell = graph.cells.get(cell_id)
        if not cell:
            continue
        for dep_id in cell.dependencies:
            if dep_id not in in_indicator and dep_id not in ghost_added:
                dep_cell = graph.cells.get(dep_id)
                label = dep_id.split("_", 1)[-1] if "_" in dep_id else dep_id
                nodes.append({
                    "id": dep_id,
                    "name": label,
                    "category": 3,  # external cell
                    "depth": 1,
                    "sheet": dep_cell.sheet if dep_cell else "",
                    "symbolSize": 10,
                })
                ghost_added.add(dep_id)
            edges.append({"source": cell_id, "target": dep_id, "category": 0})

    categories = [
        {"name": "依赖边", "itemStyle": {"color": _COLORS["edge_depends"]}},
        {"name": "有公式", "itemStyle": {"color": _COLORS["cell_formula"]}},
        {"name": "纯数值", "itemStyle": {"color": _COLORS["cell_value"]}},
        {"name": "外部 Cell", "itemStyle": {"color": "#BDBDBD"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "root_id": "",
        "stats": _make_stats(len(nodes), len(edges)),
    }
