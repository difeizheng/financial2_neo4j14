"""Break-even analysis: binary search for critical parameter value where
a target metric crosses a user-defined threshold.

Usage:
    result = find_break_even(graph, cell_id, "irr_after_tax", 0.08)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.derived_metrics import compute_derived_metrics


@dataclass(frozen=True)
class BreakEvenResult:
    """Result of a break-even search."""
    param_name: str
    param_cell_id: str
    original_value: float
    metric_key: str
    metric_label: str
    threshold: float
    break_even_value: float | None
    break_even_pct: float | None  # % change from original
    found: bool
    iterations: int
    metric_at_break_even: float | None
    direction: str  # "decrease" or "increase" — which direction triggers break-even


def find_break_even(
    graph: FinancialGraph,
    cell_id: str,
    metric_key: str,
    threshold: float,
    max_iterations: int = 50,
    tolerance: float = 1e-6,
    perturb_pct: float = 50,
) -> BreakEvenResult:
    """Binary search for the break-even point.

    Args:
        graph: FinancialGraph (will be cloned per iteration).
        cell_id: Parameter cell to perturb.
        metric_key: Metric attribute name (e.g. "irr_after_tax").
        threshold: Target value the metric must reach.
        max_iterations: Max binary search iterations.
        tolerance: Acceptable distance from threshold.
        perturb_pct: Maximum perturbation range in percent (default 50).

    Returns:
        BreakEvenResult with findings.
    """
    # ── Defensive deep clone: never mutate the caller's graph ──────────────────────
    graph = graph.deep_clone()

    cell = graph.cells.get(cell_id)
    if cell is None:
        return BreakEvenResult(
            param_name="", param_cell_id=cell_id, original_value=0,
            metric_key=metric_key, metric_label="", threshold=threshold,
            break_even_value=None, break_even_pct=None, found=False,
            iterations=0, metric_at_break_even=None, direction="",
        )

    original_val = float(cell.value) if cell.value else 0
    if original_val == 0:
        return BreakEvenResult(
            param_name="", param_cell_id=cell_id, original_value=0,
            metric_key=metric_key, metric_label="", threshold=threshold,
            break_even_value=None, break_even_pct=None, found=False,
            iterations=0, metric_at_break_even=None, direction="",
        )

    base_metrics = compute_derived_metrics(graph)
    base_metric = getattr(base_metrics, metric_key, None)

    if base_metric is None:
        return BreakEvenResult(
            param_name="", param_cell_id=cell_id, original_value=original_val,
            metric_key=metric_key, metric_label="", threshold=threshold,
            break_even_value=None, break_even_pct=None, found=False,
            iterations=0, metric_at_break_even=None, direction="",
        )

    # Determine search direction
    # If base_metric > threshold, we need to decrease the metric
    # If base_metric < threshold, we need to increase the metric
    need_decrease = base_metric > threshold

    # Verify break-even exists by testing ±perturb_pct% perturbation
    factor = perturb_pct / 100.0
    test_val_low = original_val * (1 - factor)
    test_val_high = original_val * (1 + factor)

    metric_at_low = _eval_metric_at(graph, cell_id, test_val_low, metric_key)
    metric_at_high = _eval_metric_at(graph, cell_id, test_val_high, metric_key)

    # Check if threshold is reachable
    if need_decrease:
        if metric_at_high is not None and metric_at_high < threshold:
            # Threshold between original and +50%
            lo, hi = original_val, test_val_high
            direction = "increase"
        elif metric_at_low is not None and metric_at_low < threshold:
            lo, hi = test_val_low, original_val
            direction = "decrease"
        else:
            return BreakEvenResult(
                param_name="", param_cell_id=cell_id, original_value=original_val,
                metric_key=metric_key, metric_label="", threshold=threshold,
                break_even_value=None, break_even_pct=None, found=False,
                iterations=0, metric_at_break_even=None,
                direction="decrease" if need_decrease else "increase",
            )
    else:
        if metric_at_high is not None and metric_at_high > threshold:
            lo, hi = original_val, test_val_high
            direction = "increase"
        elif metric_at_low is not None and metric_at_low > threshold:
            lo, hi = test_val_low, original_val
            direction = "decrease"
        else:
            return BreakEvenResult(
                param_name="", param_cell_id=cell_id, original_value=original_val,
                metric_key=metric_key, metric_label="", threshold=threshold,
                break_even_value=None, break_even_pct=None, found=False,
                iterations=0, metric_at_break_even=None,
                direction="decrease" if need_decrease else "increase",
            )

    # Binary search
    metric_at_be = None
    for i in range(max_iterations):
        mid = (lo + hi) / 2
        m = _eval_metric_at(graph, cell_id, mid, metric_key)
        if m is None:
            break
        metric_at_be = m

        if abs(m - threshold) < tolerance:
            be_pct = (mid - original_val) / original_val
            return BreakEvenResult(
                param_name="", param_cell_id=cell_id, original_value=original_val,
                metric_key=metric_key, metric_label="", threshold=threshold,
                break_even_value=mid, break_even_pct=be_pct, found=True,
                iterations=i + 1, metric_at_break_even=m, direction=direction,
            )

        # Determine which half to keep
        if need_decrease:
            if m > threshold:
                lo = mid
            else:
                hi = mid
        else:
            if m < threshold:
                lo = mid
            else:
                hi = mid

    be_pct = (mid - original_val) / original_val if mid else None
    return BreakEvenResult(
        param_name="", param_cell_id=cell_id, original_value=original_val,
        metric_key=metric_key, metric_label="", threshold=threshold,
        break_even_value=mid, break_even_pct=be_pct,
        found=(metric_at_be is not None and abs(metric_at_be - threshold) < tolerance * 100),
        iterations=max_iterations, metric_at_break_even=metric_at_be,
        direction=direction,
    )


def _eval_metric_at(
    graph: FinancialGraph, cell_id: str, value: float, metric_key: str,
) -> float | None:
    """Clone graph, set cell value, recalculate, return metric."""
    work = graph.deep_clone()
    c = work.cells.get(cell_id)
    if c is None:
        return None
    c.value = value
    result = recalculate(work, {cell_id: value})
    # Always compute metrics even if no cascading changes — the parameter
    # itself changed, and metrics might depend on it via non-formula paths.
    metrics = compute_derived_metrics(work)
    return getattr(metrics, metric_key, None)


def _clone_graph(graph: FinancialGraph) -> FinancialGraph:
    """Create a copy of FinancialGraph for mutation."""
    clone = FinancialGraph(source_file=graph.source_file)
    clone.cells = {}
    for cid, cell in graph.cells.items():
        cell_copy = copy.copy(cell)
        cell_copy.dependencies = list(cell.dependencies)
        cell_copy.dependents = list(cell.dependents)
        clone.cells[cid] = cell_copy
    clone.indicators = dict(graph.indicators)
    clone.tables = dict(graph.tables)
    clone.cell_graph = graph.cell_graph.copy()
    return clone
