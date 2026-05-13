"""Infer CALCULATES_FROM (Indicator→Indicator) and FEEDS_INTO (Table→Table) relationships.

These are derived from the Cell-layer DEPENDS_ON edges already in the graph.
"""
from __future__ import annotations

from ..models.graph import FinancialGraph


def infer_relationships(graph: FinancialGraph) -> None:
    """Populate indicator.depends_on_indicators and table.feeds_into/fed_by.

    Modifies graph in-place.
    """
    _infer_indicator_relationships(graph)
    _infer_table_relationships(graph)


def _infer_indicator_relationships(graph: FinancialGraph) -> None:
    """For each DEPENDS_ON edge (cell A -> cell B), if A and B belong to
    different indicators, add a CALCULATES_FROM edge between those indicators.
    """
    for from_id, to_id in graph.cell_graph.edges():
        from_cell = graph.cells.get(from_id)
        to_cell = graph.cells.get(to_id)
        if not from_cell or not to_cell:
            continue

        from_ind_id = from_cell.indicator_id
        to_ind_id = to_cell.indicator_id
        if not from_ind_id or not to_ind_id or from_ind_id == to_ind_id:
            continue

        from_ind = graph.indicators.get(from_ind_id)
        to_ind = graph.indicators.get(to_ind_id)
        if not from_ind or not to_ind:
            continue

        if to_ind_id not in from_ind.depends_on_indicators:
            from_ind.depends_on_indicators.append(to_ind_id)
        if from_ind_id not in to_ind.depended_by_indicators:
            to_ind.depended_by_indicators.append(from_ind_id)


def _infer_table_relationships(graph: FinancialGraph) -> None:
    """For each DEPENDS_ON edge where cells belong to different tables,
    add a FEEDS_INTO edge between those tables.
    """
    seen: set[tuple[str, str]] = set()

    for from_id, to_id in graph.cell_graph.edges():
        from_cell = graph.cells.get(from_id)
        to_cell = graph.cells.get(to_id)
        if not from_cell or not to_cell:
            continue

        from_tbl_id = from_cell.table_id
        to_tbl_id = to_cell.table_id
        if not from_tbl_id or not to_tbl_id or from_tbl_id == to_tbl_id:
            continue

        pair = (to_tbl_id, from_tbl_id)  # to_table FEEDS_INTO from_table
        if pair in seen:
            continue
        seen.add(pair)

        to_tbl = graph.tables.get(to_tbl_id)
        from_tbl = graph.tables.get(from_tbl_id)
        if not to_tbl or not from_tbl:
            continue

        if from_tbl_id not in to_tbl.feeds_into:
            to_tbl.feeds_into.append(from_tbl_id)
        if to_tbl_id not in from_tbl.fed_by:
            from_tbl.fed_by.append(to_tbl_id)
