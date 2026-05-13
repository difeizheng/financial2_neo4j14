"""Incremental recalculation engine.

Given a set of changed cells (with new values), propagates changes through
the dependency DAG and updates the graph in-place.  Also syncs the Indicator
layer (summary_value, time_series).

When circular dependencies exist, iterative evaluation converges within the
strongly connected component (max 100 iterations, tolerance 1e-9).

Performance design
------------------
Original code had three overlapping full-scan loops over 20 K+ affected cells,
causing 11-hour runtimes at 23 ms/cell.  This rewrite:

1. Does NOT call clear_formula_cache() — compiled formulas are reused across
   sessions (see evaluator.py).

2. Single topological pass for acyclic cells (no repeated passes).

3. SCC convergence uses true dirty-set propagation: only cells whose inputs
   changed are re-evaluated each iteration.  The outer "range(10)" loop and
   the separate "final convergence pass" are replaced by a single unified
   alternating loop that terminates as soon as nothing changes.

4. After SCC convergence, non-cyclic dependents are re-evaluated exactly once
   (not inside the convergence loop).

Expected runtime for 20 K cells × 23 ms: ~8 min for one full pass vs 11 hrs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.dependency import downstream_cells
from financial_kg.engine.evaluator import evaluate_cell


@dataclass
class CellChange:
    cell_id: str
    old_value: Any
    new_value: Any
    formula: str | None = None


@dataclass
class RecalcResult:
    changed_cells: list[CellChange] = field(default_factory=list)
    error_cells: list[str] = field(default_factory=list)
    scc_iterations: int = 0

    @property
    def affected_count(self) -> int:
        return len(self.changed_cells)


def recalculate(
    graph: FinancialGraph,
    updates: dict[str, Any],
    max_iter: int = 100,
    tol: float = 1e-9,
) -> RecalcResult:
    """Apply updates and propagate through the dependency graph.

    Args:
        graph:     The FinancialGraph to mutate in-place.
        updates:   Mapping of cell_id → new value for the seed cells.
        max_iter:  Maximum SCC iteration rounds.
        tol:       Convergence tolerance for SCC iterations.

    Returns:
        RecalcResult with all cells that changed value.
    """
    result = RecalcResult()

    # NOTE: clear_formula_cache() deliberately removed.  Compiled formulas are
    # static; clearing the cache on every call was the main performance killer.

    # ── 1. Apply seed changes ─────────────────────────────────────────────────
    for cell_id, new_val in updates.items():
        cell = graph.cells.get(cell_id)
        if cell is None:
            continue
        old_val = cell.value
        cell.value = new_val
        if old_val != new_val:
            result.changed_cells.append(
                CellChange(cell_id, old_val, new_val, cell.formula_raw)
            )

    # ── 2. Find downstream cells (topological order) ──────────────────────────
    affected: list[str] = downstream_cells(graph, updates.keys())
    if not affected:
        _sync_indicators(graph, result.changed_cells)
        return result

    affected_set = set(affected)

    # ── 3. Partition into cyclic vs acyclic cells ─────────────────────────────
    g = graph.cell_graph
    subgraph = g.subgraph(affected_set | set(updates.keys()))

    cyclic_cells: set[str] = set()
    sccs_to_converge: list[list[str]] = []

    for scc in nx.strongly_connected_components(subgraph):
        members_in_affected = scc & affected_set
        if len(members_in_affected) > 1:
            cyclic_cells.update(members_in_affected)
            sccs_to_converge.append(sorted(members_in_affected))

    acyclic_affected = [c for c in affected if c not in cyclic_cells]

    # ── 4. Single-pass evaluation of acyclic cells ────────────────────────────
    for cell_id in acyclic_affected:
        cell = graph.cells.get(cell_id)
        if cell is None or not cell.formula_raw:
            continue

        old_val = cell.value
        new_val = evaluate_cell(cell_id, graph)

        if new_val is None:
            result.error_cells.append(cell_id)
            continue

        cell.value = new_val
        if old_val != new_val:
            result.changed_cells.append(
                CellChange(cell_id, old_val, new_val, cell.formula_raw)
            )

    # ── 5. SCC convergence (only when cycles exist) ───────────────────────────
    if cyclic_cells:
        result.scc_iterations = _converge_sccs(
            graph=graph,
            g=g,
            cyclic_cells=cyclic_cells,
            acyclic_affected=acyclic_affected,
            result=result,
            max_iter=max_iter,
            tol=tol,
        )

    # ── 6. Sync Indicator layer ───────────────────────────────────────────────
    _sync_indicators(graph, result.changed_cells)
    return result


def _converge_sccs(
    graph: FinancialGraph,
    g: nx.DiGraph,
    cyclic_cells: set[str],
    acyclic_affected: list[str],
    result: RecalcResult,
    max_iter: int,
    tol: float,
) -> int:
    """Iteratively converge cyclic SCCs, then re-evaluate non-cyclic dependents.

    Uses a single unified alternating loop instead of the original three
    separate loop bodies (first convergence, outer range(10), final pass).

    Returns the total number of SCC iterations performed.
    """
    sorted_cyclic = sorted(cyclic_cells)
    total_scc_iters = 0

    # Outer loop: alternate between SCC convergence and non-cyclic re-eval.
    # Terminates when neither changes anything.
    for _outer in range(max_iter):
        # ── 5a. Converge the SCC with dirty tracking ──────────────────────────
        dirty: set[str] = set(cyclic_cells)  # start fully dirty

        iter_count = 0
        for iter_count in range(1, max_iter + 1):
            if not dirty:
                break

            max_delta = 0.0
            next_dirty: set[str] = set()

            for cell_id in sorted_cyclic:
                if cell_id not in dirty:
                    continue

                cell = graph.cells.get(cell_id)
                if cell is None or not cell.formula_raw:
                    continue

                old_val = cell.value
                new_val = evaluate_cell(cell_id, graph)
                if new_val is None:
                    continue

                cell.value = new_val
                try:
                    delta = abs(float(new_val) - float(old_val))
                except (TypeError, ValueError):
                    delta = 1.0 if new_val != old_val else 0.0
                max_delta = max(max_delta, delta)

                if delta > tol:
                    for succ in g.successors(cell_id):
                        if succ in cyclic_cells:
                            next_dirty.add(succ)

            dirty = next_dirty
            if max_delta <= tol:
                break

        total_scc_iters += iter_count

        # ── 5b. Re-evaluate non-cyclic cells that depend on converged SCCs ────
        # Do this exactly once per outer iteration; track whether anything changed.
        nc_changed = False
        for cell_id in acyclic_affected:
            cell = graph.cells.get(cell_id)
            if cell is None or not cell.formula_raw:
                continue

            old_val = cell.value
            new_val = evaluate_cell(cell_id, graph)
            if new_val is None:
                continue

            cell.value = new_val
            if old_val != new_val:
                nc_changed = True
                result.changed_cells.append(
                    CellChange(cell_id, old_val, new_val, cell.formula_raw)
                )

        # If neither the SCC nor the non-cyclic cells changed, we're done.
        if not nc_changed and max_delta <= tol:  # type: ignore[possibly-undefined]
            break

    return total_scc_iters


def _sync_indicators(graph: FinancialGraph, changes: list[CellChange]) -> None:
    """Update Indicator summary_value and time_series for affected indicators."""
    dirty_indicators: set[str] = set()
    for change in changes:
        cell = graph.cells.get(change.cell_id)
        if cell and cell.indicator_id:
            dirty_indicators.add(cell.indicator_id)

    for ind_id in dirty_indicators:
        ind = graph.indicators.get(ind_id)
        if ind is None:
            continue

        new_ts: dict[str, Any] = {}
        summary_val = None

        for cell_id in ind.cell_ids:
            cell = graph.cells.get(cell_id)
            if cell is None:
                continue
            tbl = graph.tables.get(cell.table_id) if cell.table_id else None
            if tbl and cell.col in tbl.col_roles:
                role = tbl.col_roles[cell.col]
                if role == "time_series":
                    period = tbl.time_period_labels.get(cell.col, cell.col)
                    new_ts[period] = cell.value
                elif role in ("total", "name"):
                    if isinstance(cell.value, (int, float)):
                        summary_val = cell.value

        if new_ts:
            ind.time_series = new_ts
        if summary_val is not None:
            ind.summary_value = summary_val
