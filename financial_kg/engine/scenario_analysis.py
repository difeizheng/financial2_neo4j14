"""Scenario analysis: preset pessimistic/base/optimistic scenarios.

Multi-variable simultaneous change with predefined ratios based on
variable classification (revenue/cost/investment).

Usage:
    result = run_scenario_analysis(
        graph=graph,
        param_cells=[("cell_id", "电价", "revenue"), ("cell_id", "投资", "investment")],
        preset="standard",  # or "custom"
    )
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.derived_metrics import compute_derived_metrics, DerivedMetrics


# Variable classification determines pessimistic/optimistic direction
VAR_CLASSIFICATION = {
    "revenue": {
        "悲观": -0.10,  # Revenue drops in pessimistic
        "基准": 0.00,
        "乐观": +0.10,
    },
    "cost": {
        "悲观": +0.10,  # Cost rises in pessimistic
        "基准": 0.00,
        "乐观": -0.10,
    },
    "investment": {
        "悲观": +0.15,  # Investment overruns in pessimistic
        "基准": 0.00,
        "乐观": -0.10,
    },
}

DEFAULT_CLASSIFICATION = "revenue"


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario result (pessimistic/base/optimistic)."""
    name: str  # "悲观" / "基准" / "乐观"
    param_changes: dict[str, float]  # {cell_id: change_ratio}
    metrics: DerivedMetrics
    changed_cells: int


@dataclass
class ScenarioAnalysisResult:
    """Complete scenario analysis result."""
    base_metrics: DerivedMetrics
    scenarios: list[ScenarioResult] = field(default_factory=list)
    comparison_table: list[dict] = field(default_factory=list)
    delta_table: list[dict] = field(default_factory=list)


def run_scenario_analysis(
    graph: FinancialGraph,
    param_cells: list[tuple[str, str, str]],  # [(cell_id, display_name, classification), ...]
    preset: Literal["standard", "custom"] = "standard",
    custom_ratios: dict[str, dict[str, float]] | None = None,
) -> ScenarioAnalysisResult:
    """Run scenario analysis on multiple parameters.

    Args:
        graph: FinancialGraph (will be cloned for each scenario).
        param_cells: List of (cell_id, display_name, classification) tuples.
            classification: "revenue" | "cost" | "investment"
        preset: "standard" for default -10%/0/+10% ratios,
                "custom" for user-defined ratios.
        custom_ratios: {"悲观": {cell_id: ratio}, "基准": {}, "乐观": {...}}
            Only used when preset="custom".

    Returns:
        ScenarioAnalysisResult with base metrics and 3 scenarios.
    """
    base_metrics = compute_derived_metrics(graph)
    scenarios: list[ScenarioResult] = []

    # Determine scenario definitions
    scenario_names = ["悲观", "基准", "乐观"]

    if preset == "custom" and custom_ratios:
        # User-defined ratios
        scenario_ratios = custom_ratios
    else:
        # Standard preset based on variable classification
        scenario_ratios = {}
        for scenario_name in scenario_names:
            scenario_ratios[scenario_name] = {}
            for cell_id, param_name, classification in param_cells:
                ratios = VAR_CLASSIFICATION.get(classification, VAR_CLASSIFICATION[DEFAULT_CLASSIFICATION])
                scenario_ratios[scenario_name][cell_id] = ratios[scenario_name]

    # Run each scenario
    for scenario_name in scenario_names:
        ratios_for_scenario = scenario_ratios.get(scenario_name, {})

        if not ratios_for_scenario and scenario_name == "基准":
            # Base scenario: no changes
            scenarios.append(ScenarioResult(
                name=scenario_name,
                param_changes={},
                metrics=base_metrics,
                changed_cells=0,
            ))
            continue

        # Clone graph and apply changes
        work_graph = _clone_graph(graph)

        changes: dict[str, float] = {}
        for cell_id, ratio in ratios_for_scenario.items():
            cell = work_graph.cells.get(cell_id)
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

            new_val = original_val * (1 + ratio)
            cell.value = new_val
            changes[cell_id] = ratio

        # Recalculate
        recalc_input = {cid: work_graph.cells[cid].value for cid in changes}
        result = recalculate(work_graph, recalc_input)

        # Compute metrics
        scenario_metrics = compute_derived_metrics(work_graph)

        scenarios.append(ScenarioResult(
            name=scenario_name,
            param_changes=changes,
            metrics=scenario_metrics,
            changed_cells=len(result.changed_cells) if result else 0,
        ))

    # Build comparison tables
    comparison_table = _build_comparison_table(base_metrics, scenarios)
    delta_table = _build_delta_table(base_metrics, scenarios)

    return ScenarioAnalysisResult(
        base_metrics=base_metrics,
        scenarios=scenarios,
        comparison_table=comparison_table,
        delta_table=delta_table,
    )


def _clone_graph(graph: FinancialGraph) -> FinancialGraph:
    """Shallow copy FinancialGraph (mutates cell.value only)."""
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


def _build_comparison_table(
    base: DerivedMetrics,
    scenarios: list[ScenarioResult],
) -> list[dict]:
    """Build metrics comparison across scenarios."""
    METRICS = [
        ("irr_after_tax", "税后IRR", 100, "%"),
        ("npv_after_tax", "财务净现值", 1, ""),
        ("payback_period", "投资回收期", 1, "年"),
        ("dscr_avg", "DSCR均值", 1, ""),
        ("dscr_min", "DSCR最低值", 1, ""),
    ]

    rows = []
    for key, label, mult, unit in METRICS:
        row = {"指标": label}
        for s in scenarios:
            val = getattr(s.metrics, key, None)
            if val is not None:
                row[s.name] = f"{val * mult:.2f}{unit}"
            else:
                row[s.name] = "—"
        rows.append(row)

    return rows


def _build_delta_table(
    base: DerivedMetrics,
    scenarios: list[ScenarioResult],
) -> list[dict]:
    """Build delta from base for pessimistic/optimistic."""
    METRICS = [
        ("irr_after_tax", "税后IRR", 100, "%"),
        ("npv_after_tax", "财务净现值", 1, ""),
        ("payback_period", "投资回收期", 1, "年"),
        ("dscr_avg", "DSCR均值", 1, ""),
        ("dscr_min", "DSCR最低值", 1, ""),
    ]

    rows = []
    for key, label, mult, unit in METRICS:
        base_val = getattr(base, key, None)
        if base_val is None:
            continue

        row = {"指标": label, "基准": f"{base_val * mult:.2f}{unit}"}

        for s in scenarios:
            if s.name == "基准":
                continue
            val = getattr(s.metrics, key, None)
            if val is not None:
                delta = (val - base_val) * mult
                row[f"{s.name}差异"] = f"{delta:+.2f}{unit}"
            else:
                row[f"{s.name}差异"] = "—"

        rows.append(row)

    return rows


def classify_parameter(param_name: str) -> str:
    """Auto-classify parameter based on name keywords."""
    name_lower = param_name.lower()

    # Revenue keywords
    if any(kw in name_lower for kw in ["电价", "收入", "售电", "发电量", "收益", "营业", "revenue", "price", "sales"]):
        return "revenue"

    # Cost keywords
    if any(kw in name_lower for kw in ["成本", "费用", "运营", "维护", "人工", "材料", "cost", "expense", "opex"]):
        return "cost"

    # Investment keywords
    if any(kw in name_lower for kw in ["投资", "建设", "资本", "capex", "investment", "capital", "建设投资"]):
        return "investment"

    return DEFAULT_CLASSIFICATION