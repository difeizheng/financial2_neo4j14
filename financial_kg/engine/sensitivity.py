"""Sensitivity analysis: parameter perturbation → recalc → IRR comparison.

Generates sensitivity tables showing how key metrics (IRR, NPV, DSCR) change
when input parameters are perturbed by ±5% / ±10%.

Usage from Streamlit page:
    result = run_sensitivity(graph, param_cells=["X_Y_Z"], perturbations=[0.1])
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.snapshot import create_snapshot
from financial_kg.engine.derived_metrics import (
    compute_derived_metrics,
    serialize_metrics,
    deserialize_metrics,
    DerivedMetrics,
)


@dataclass(frozen=True)
class SensitivityScenario:
    """One sensitivity scenario result."""
    name: str            # e.g. "售电电价 +10%"
    param_name: str      # perturbed parameter display name
    param_cell_id: str   # the cell that was perturbed
    perturbation: float  # e.g. 0.1 = +10%
    original_value: float
    perturbed_value: float
    metrics: DerivedMetrics
    snapshot_name: str   # name of the created snapshot


@dataclass
class SensitivityResult:
    """Complete sensitivity analysis result."""
    base_metrics: DerivedMetrics
    scenarios: list[SensitivityScenario] = field(default_factory=list)
    summary_table: list[dict] = field(default_factory=list)


def run_sensitivity(
    graph: FinancialGraph,
    param_cells: list[tuple[str, str]],  # [(cell_id, display_name), ...]
    perturbations: list[float] | None = None,
    task_id: str = "",
    output_dir: str = "",
    snapshots_dir: str = "",
) -> SensitivityResult:
    """Run sensitivity analysis on specified parameters.

    Args:
        graph: The FinancialGraph (will be deepcopied for each scenario).
        param_cells: List of (cell_id, display_name) tuples for parameters to perturb.
        perturbations: List of perturbation ratios (e.g. [0.05, 0.1, -0.05, -0.1]).
            Defaults to [-0.1, -0.05, 0.05, 0.1].
        task_id: Task ID for snapshot creation.
        output_dir: Output directory for snapshot files.
        snapshots_dir: Snapshots directory root.

    Returns:
        SensitivityResult with base metrics and all scenarios.
    """
    if perturbations is None:
        perturbations = [-0.1, -0.05, 0.05, 0.1]

    base_metrics = compute_derived_metrics(graph)
    scenarios: list[SensitivityScenario] = []

    for cell_id, param_name in param_cells:
        cell = graph.cells.get(cell_id)
        if cell is None:
            continue

        original_val = cell.value
        if not isinstance(original_val, (int, float)):
            try:
                original_val = float(original_val)
            except (TypeError, ValueError):
                continue
        if original_val == 0:
            continue

        for pct in perturbations:
            scenario_name = f"{param_name} {pct:+.0%}"
            perturbed_val = original_val * (1 + pct)

            # Deep copy graph and apply perturbation
            work_graph = _clone_graph(graph)
            perturbed_cell = work_graph.cells.get(cell_id)
            if perturbed_cell is None:
                continue
            perturbed_cell.value = perturbed_val

            # Recalculate
            result = recalculate(work_graph, {cell_id: perturbed_val})
            if not result.changed_cells:
                continue

            # Compute derived metrics for perturbed scenario
            perturbed_metrics = compute_derived_metrics(work_graph)

            # Create snapshot if directories provided
            snap_name = f"sensitivity_{scenario_name.replace(' ', '_').replace('+', 'plus').replace('-', 'neg')}"
            if task_id and output_dir:
                snap_dir = snapshots_dir or output_dir
                create_snapshot(work_graph, task_id, snap_name, snap_dir)

            scenarios.append(SensitivityScenario(
                name=scenario_name,
                param_name=param_name,
                param_cell_id=cell_id,
                perturbation=pct,
                original_value=original_val,
                perturbed_value=perturbed_val,
                metrics=perturbed_metrics,
                snapshot_name=snap_name,
            ))

    # Build summary table
    summary_table = _build_summary_table(base_metrics, scenarios)

    return SensitivityResult(
        base_metrics=base_metrics,
        scenarios=scenarios,
        summary_table=summary_table,
    )


def _clone_graph(graph: FinancialGraph) -> FinancialGraph:
    """Create a shallow copy of FinancialGraph — sufficient since sensitivity
    only mutates cell.value which is a primitive type."""
    import copy
    # Shallow copy the container, deep copy cells dict values
    clone = FinancialGraph(source_file=graph.source_file)
    # Deep copy cells (values are primitives, but Cell objects need copying)
    clone.cells = {}
    for cid, cell in graph.cells.items():
        cell_copy = copy.copy(cell)
        cell_copy.dependencies = list(cell.dependencies)
        cell_copy.dependents = list(cell.dependents)
        clone.cells[cid] = cell_copy
    # Shallow copy indicators (we only read, don't mutate)
    clone.indicators = dict(graph.indicators)
    clone.tables = dict(graph.tables)
    # Copy cell_graph (NetworkX DiGraph) — use copy method
    clone.cell_graph = graph.cell_graph.copy()
    return clone


def _build_summary_table(
    base: DerivedMetrics,
    scenarios: list[SensitivityScenario],
) -> list[dict]:
    """Build a summary table comparing base vs scenario metrics."""
    rows: list[dict] = []

    # Group scenarios by parameter
    by_param: dict[str, list[SensitivityScenario]] = {}
    for s in scenarios:
        by_param.setdefault(s.param_name, []).append(s)

    for param_name, param_scenarios in by_param.items():
        row: dict[str, Any] = {"参数": param_name}

        for s in sorted(param_scenarios, key=lambda x: x.perturbation):
            label = f"{s.perturbation:+.0%}"
            if s.metrics.irr_after_tax is not None and base.irr_after_tax is not None:
                irr_delta = s.metrics.irr_after_tax - base.irr_after_tax
                row[label] = f"{s.metrics.irr_after_tax * 100:.2f}% ({irr_delta:+.2f}pp)"
            else:
                row[label] = "—"

        rows.append(row)

    return rows


def _build_spider_table(
    base: DerivedMetrics,
    scenarios: list[SensitivityScenario],
    metric_key: str = "irr_after_tax",
) -> list[dict]:
    """Build a spider/sensitivity table for a specific metric.

    Each row is a parameter, columns are perturbation levels,
    values are the resulting metric (IRR by default).
    """
    rows: list[dict] = []

    by_param: dict[str, list[SensitivityScenario]] = {}
    for s in scenarios:
        by_param.setdefault(s.param_name, []).append(s)

    base_val = getattr(base, metric_key, None)
    if base_val is None:
        return rows

    for param_name, param_scenarios in by_param.items():
        row: dict[str, Any] = {"参数": param_name}

        for s in sorted(param_scenarios, key=lambda x: x.perturbation):
            label = f"{s.perturbation:+.0%}"
            val = getattr(s.metrics, metric_key, None)
            if val is not None:
                delta = val - base_val
                row[label] = round(val * 100, 2) if "irr" in metric_key else round(val, 2)
                row[f"{label}_delta"] = round(delta * 100, 2) if "irr" in metric_key else round(delta, 2)
            else:
                row[label] = None

        rows.append(row)

    return rows
