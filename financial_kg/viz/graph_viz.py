"""pyvis-based interactive graph visualization."""
from __future__ import annotations
import os
import tempfile
from typing import Optional

from financial_kg.models.graph import FinancialGraph

try:
    from pyvis.network import Network
    _PYVIS_AVAILABLE = True
except ImportError:
    _PYVIS_AVAILABLE = False


# Node/edge color palette
_COLORS = {
    "cell_formula": "#9E9E9E",
    "cell_value": "#BDBDBD",
    "indicator": "#42A5F5",
    "table": "#FFA726",
    "edge_depends": "#CFD8DC",
    "edge_calculates": "#42A5F5",
    "edge_feeds": "#FFA726",
}


def build_indicator_graph(
    graph: FinancialGraph,
    sheet_filter: Optional[str] = None,
    max_nodes: int = 500,
    output_path: Optional[str] = None,
) -> str:
    """Build a pyvis HTML showing Indicator + Table layers.

    Returns the path to the generated HTML file.
    """
    if not _PYVIS_AVAILABLE:
        raise ImportError("pyvis is not installed. Run: pip install pyvis")

    net = Network(height="700px", width="100%", directed=True, notebook=False)
    net.set_options("""
    {
      "physics": {"stabilization": {"iterations": 100}},
      "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}},
      "interaction": {"hover": true, "navigationButtons": true}
    }
    """)

    added_inds: set[str] = set()
    added_tables: set[str] = set()
    node_count = 0

    # Add Table nodes
    for tbl_id, tbl in graph.tables.items():
        if sheet_filter and tbl.sheet != sheet_filter:
            continue
        if node_count >= max_nodes:
            break
        net.add_node(
            tbl_id,
            label=tbl.name[:20],
            title=f"[Table] {tbl.name}\nSheet: {tbl.sheet}\nType: {tbl.table_type}",
            color=_COLORS["table"],
            shape="box",
            size=25,
        )
        added_tables.add(tbl_id)
        node_count += 1

    # Add Indicator nodes
    for ind_id, ind in graph.indicators.items():
        if sheet_filter and ind.sheet != sheet_filter:
            continue
        if node_count >= max_nodes:
            break
        label = ind.name[:18] if ind.name else ind_id[-20:]
        val_str = ind.display_value if ind.display_value is not None else (
            f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float) else str(ind.summary_value or "")
        )
        unit_str = f" {ind.unit}" if ind.unit else ""
        net.add_node(
            ind_id,
            label=label,
            title=f"[Indicator] {ind.name}\nSheet: {ind.sheet}\nValue: {val_str}{unit_str}\nCategory: {ind.category or ''}",
            color=_COLORS["indicator"],
            shape="ellipse",
            size=15,
        )
        added_inds.add(ind_id)
        node_count += 1

    # CALCULATES_FROM edges (Indicator → Indicator)
    for ind_id, ind in graph.indicators.items():
        if ind_id not in added_inds:
            continue
        for dep_id in ind.depends_on_indicators:
            if dep_id in added_inds:
                net.add_edge(ind_id, dep_id, color=_COLORS["edge_calculates"], width=1.5)

    # FEEDS_INTO edges (Table → Table)
    for tbl_id, tbl in graph.tables.items():
        if tbl_id not in added_tables:
            continue
        for target_id in tbl.feeds_into:
            if target_id in added_tables:
                net.add_edge(tbl_id, target_id, color=_COLORS["edge_feeds"], width=2)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".html", prefix="kg_viz_")
        os.close(fd)

    net.save_graph(output_path)
    return output_path


def build_cell_subgraph(
    graph: FinancialGraph,
    root_cell_id: str,
    depth: int = 3,
    output_path: Optional[str] = None,
) -> str:
    """Build a pyvis HTML showing the dependency subgraph around a single cell."""
    if not _PYVIS_AVAILABLE:
        raise ImportError("pyvis is not installed.")

    import networkx as nx

    g = graph.cell_graph
    if root_cell_id not in g:
        raise ValueError(f"Cell {root_cell_id!r} not in graph")

    # BFS up to `depth` hops in both directions
    neighbors: set[str] = {root_cell_id}
    frontier = {root_cell_id}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            next_frontier.update(g.predecessors(n))
            next_frontier.update(g.successors(n))
        next_frontier -= neighbors
        neighbors |= next_frontier
        frontier = next_frontier

    subg = g.subgraph(neighbors)
    net = Network(height="600px", width="100%", directed=True, notebook=False)

    for node in subg.nodes:
        cell = graph.cells.get(node)
        is_root = node == root_cell_id
        color = "#EF5350" if is_root else (_COLORS["cell_formula"] if (cell and cell.formula_raw) else _COLORS["cell_value"])
        label = node.split("_", 1)[-1] if "_" in node else node
        title = f"{node}\nValue: {cell.value if cell else '?'}\nFormula: {cell.formula_raw or '' if cell else ''}"
        net.add_node(node, label=label, title=title, color=color, size=20 if is_root else 12)

    for src, dst in subg.edges:
        net.add_edge(src, dst, color=_COLORS["edge_depends"])

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".html", prefix="kg_cell_")
        os.close(fd)

    net.save_graph(output_path)
    return output_path


def build_table_graph(
    graph: FinancialGraph,
    sheet_filter: str,
    output_path: Optional[str] = None,
) -> str:
    """Build a pyvis HTML showing Table nodes and FEEDS_INTO edges for a sheet."""
    if not _PYVIS_AVAILABLE:
        raise ImportError("pyvis is not installed.")

    net = Network(height="600px", width="100%", directed=True, notebook=False)
    net.set_options("""
    {
      "physics": {"stabilization": {"iterations": 100}},
      "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}},
      "interaction": {"hover": true, "navigationButtons": true}
    }
    """)

    in_sheet: set[str] = set()
    for tbl_id, tbl in graph.tables.items():
        if tbl.sheet == sheet_filter:
            in_sheet.add(tbl_id)
            net.add_node(
                tbl_id,
                label=tbl.name[:20],
                title=f"[Table] {tbl.name}\nType: {tbl.table_type}\nIndicators: {len(tbl.indicator_ids)}",
                color=_COLORS["table"],
                shape="box",
                size=25,
            )

    ghost_added: set[str] = set()
    for tbl_id in in_sheet:
        tbl = graph.tables[tbl_id]
        for target_id in tbl.feeds_into:
            if target_id not in in_sheet and target_id not in ghost_added:
                target = graph.tables.get(target_id)
                label = target.name[:20] if target else target_id[-20:]
                sheet_label = f"\nSheet: {target.sheet}" if target else ""
                net.add_node(
                    target_id,
                    label=label,
                    title=f"[外部 Table]{sheet_label}",
                    color="#BDBDBD",
                    shape="box",
                    size=15,
                )
                ghost_added.add(target_id)
            net.add_edge(tbl_id, target_id, color=_COLORS["edge_feeds"], width=2)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".html", prefix="kg_tbl_")
        os.close(fd)

    net.save_graph(output_path)
    return output_path


def build_indicator_subgraph(
    graph: FinancialGraph,
    table_id: str,
    output_path: Optional[str] = None,
) -> str:
    """Build a pyvis HTML showing Indicator nodes for a table + CALCULATES_FROM edges."""
    if not _PYVIS_AVAILABLE:
        raise ImportError("pyvis is not installed.")

    net = Network(height="600px", width="100%", directed=True, notebook=False)
    net.set_options("""
    {
      "physics": {"stabilization": {"iterations": 100}},
      "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}},
      "interaction": {"hover": true, "navigationButtons": true}
    }
    """)

    tbl = graph.tables.get(table_id)
    in_table: set[str] = set(tbl.indicator_ids) if tbl else set()

    for ind_id in in_table:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        val_str = ind.display_value if ind.display_value is not None else (
            f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
            else str(ind.summary_value or "")
        )
        net.add_node(
            ind_id,
            label=(ind.name[:18] if ind.name else ind_id[-20:]),
            title=f"[Indicator] {ind.name}\nValue: {val_str}\nCategory: {ind.category or ''}",
            color=_COLORS["indicator"],
            shape="ellipse",
            size=15,
        )

    ghost_added: set[str] = set()
    for ind_id in in_table:
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue
        for dep_id in ind.depends_on_indicators:
            if dep_id not in in_table and dep_id not in ghost_added:
                dep = graph.indicators.get(dep_id)
                label = dep.name[:18] if dep and dep.name else dep_id[-20:]
                net.add_node(
                    dep_id,
                    label=label,
                    title="[外部 Indicator]",
                    color="#BDBDBD",
                    shape="ellipse",
                    size=10,
                )
                ghost_added.add(dep_id)
            net.add_edge(ind_id, dep_id, color=_COLORS["edge_calculates"], width=1.5)

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".html", prefix="kg_ind_")
        os.close(fd)

    net.save_graph(output_path)
    return output_path


def build_indicator_cell_graph(
    graph: FinancialGraph,
    indicator_id: str,
    output_path: Optional[str] = None,
) -> str:
    """Build a pyvis HTML showing Cell nodes for an indicator + DEPENDS_ON edges."""
    if not _PYVIS_AVAILABLE:
        raise ImportError("pyvis is not installed.")

    net = Network(height="600px", width="100%", directed=True, notebook=False)
    net.set_options("""
    {
      "physics": {"stabilization": {"iterations": 100}},
      "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}},
      "interaction": {"hover": true, "navigationButtons": true}
    }
    """)

    ind = graph.indicators.get(indicator_id)
    in_indicator: set[str] = set(ind.cell_ids) if ind else set()

    for cell_id in in_indicator:
        cell = graph.cells.get(cell_id)
        if not cell:
            continue
        color = _COLORS["cell_formula"] if cell.formula_raw else _COLORS["cell_value"]
        label = cell_id.split("_", 1)[-1] if "_" in cell_id else cell_id
        net.add_node(
            cell_id,
            label=label,
            title=f"{cell_id}\nValue: {cell.value}\nFormula: {cell.formula_raw or '无'}",
            color=color,
            size=15,
        )

    ghost_added: set[str] = set()
    for cell_id in in_indicator:
        cell = graph.cells.get(cell_id)
        if not cell:
            continue
        for dep_id in cell.dependencies:
            if dep_id not in in_indicator and dep_id not in ghost_added:
                dep_cell = graph.cells.get(dep_id)
                label = dep_id.split("_", 1)[-1] if "_" in dep_id else dep_id
                net.add_node(
                    dep_id,
                    label=label,
                    title=f"[外部 Cell]\nValue: {dep_cell.value if dep_cell else '?'}",
                    color="#BDBDBD",
                    size=10,
                )
                ghost_added.add(dep_id)
            net.add_edge(cell_id, dep_id, color=_COLORS["edge_depends"])

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".html", prefix="kg_icell_")
        os.close(fd)

    net.save_graph(output_path)
    return output_path
