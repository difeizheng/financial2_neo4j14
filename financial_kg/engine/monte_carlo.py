"""Monte Carlo simulation: probability distribution-based risk analysis.

Random sampling from parameter distributions → multiple simulations →
probability distribution of output metrics (IRR, NPV, DSCR).

Usage:
    result = run_monte_carlo(
        graph=graph,
        param_cells=[("cell_id", "电价", "normal", {"mean": 0, "std": 0.1})],
        iterations=1000,
    )
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Literal, Any
import numpy as np

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.derived_metrics import compute_derived_metrics, DerivedMetrics


DISTRIBUTION_TYPES = Literal["normal", "uniform", "triangular", "lognormal"]


@dataclass
class DistributionConfig:
    """Probability distribution configuration for a parameter."""
    type: DISTRIBUTION_TYPES
    params: dict[str, float]  # {"mean": 0, "std": 0.1} or {"min": -0.2, "max": 0.2}


@dataclass(frozen=True)
class SimulationResult:
    """One Monte Carlo simulation iteration result."""
    iteration: int
    param_changes: dict[str, float]  # {cell_id: actual_change_ratio}
    metrics: DerivedMetrics


@dataclass
class MonteCarloResult:
    """Complete Monte Carlo simulation result."""
    base_metrics: DerivedMetrics
    iterations: int
    simulations: list[SimulationResult] = field(default_factory=list)
    statistics: dict[str, dict[str, float]] = field(default_factory=dict)
    probability_table: list[dict] = field(default_factory=list)
    confidence_intervals: dict[str, tuple[float, float]] = field(default_factory=dict)


def run_monte_carlo(
    graph: FinancialGraph,
    param_cells: list[tuple[str, str, DistributionConfig]],  # [(cell_id, name, dist_config), ...]
    iterations: int = 1000,
    seed: int | None = None,
) -> MonteCarloResult:
    """Run Monte Carlo simulation on specified parameters.

    Args:
        graph: FinancialGraph (will be cloned for each iteration).
        param_cells: List of (cell_id, display_name, DistributionConfig) tuples.
        iterations: Number of simulation iterations (default 1000).
        seed: Random seed for reproducibility (optional).

    Returns:
        MonteCarloResult with all simulation results and statistics.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    base_metrics = compute_derived_metrics(graph)
    simulations: list[SimulationResult] = []

    # Pre-compute original values
    original_values: dict[str, float] = {}
    for cell_id, param_name, dist_config in param_cells:
        cell = graph.cells.get(cell_id)
        if cell is None:
            continue
        val = cell.value
        if not isinstance(val, (int, float)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        if val == 0:
            continue
        original_values[cell_id] = val

    # Run iterations
    for i in range(iterations):
        # Sample change ratios for each parameter
        changes: dict[str, float] = {}
        for cell_id, param_name, dist_config in param_cells:
            if cell_id not in original_values:
                continue
            ratio = _sample_distribution(dist_config)
            changes[cell_id] = ratio

        # Clone graph and apply changes
        work_graph = _clone_graph(graph)

        for cell_id, ratio in changes.items():
            cell = work_graph.cells.get(cell_id)
            if cell:
                original = original_values[cell_id]
                cell.value = original * (1 + ratio)

        # Recalculate
        recalc_input = {cid: work_graph.cells[cid].value for cid in changes}
        result = recalculate(work_graph, recalc_input)

        # Compute metrics
        iter_metrics = compute_derived_metrics(work_graph)

        simulations.append(SimulationResult(
            iteration=i + 1,
            param_changes=changes,
            metrics=iter_metrics,
        ))

    # Compute statistics
    statistics = _compute_statistics(simulations)
    probability_table = _build_probability_table(base_metrics, simulations)
    confidence_intervals = _compute_confidence_intervals(simulations, confidence_level=0.95)

    return MonteCarloResult(
        base_metrics=base_metrics,
        iterations=iterations,
        simulations=simulations,
        statistics=statistics,
        probability_table=probability_table,
        confidence_intervals=confidence_intervals,
    )


def _sample_distribution(config: DistributionConfig) -> float:
    """Sample a random value from the specified distribution."""
    if config.type == "normal":
        mean = config.params.get("mean", 0)
        std = config.params.get("std", 0.1)
        return float(np.random.normal(mean, std))

    elif config.type == "uniform":
        min_val = config.params.get("min", -0.1)
        max_val = config.params.get("max", 0.1)
        return float(np.random.uniform(min_val, max_val))

    elif config.type == "triangular":
        min_val = config.params.get("min", -0.1)
        max_val = config.params.get("max", 0.1)
        mode = config.params.get("mode", 0)
        return float(np.random.triangular(min_val, mode, max_val))

    elif config.type == "lognormal":
        mean = config.params.get("mean", 0)
        std = config.params.get("std", 0.1)
        # Lognormal for positive changes only
        return float(np.random.lognormal(mean, std)) - 1  # Centered around 0

    return 0.0


def _clone_graph(graph: FinancialGraph) -> FinancialGraph:
    """Shallow copy FinancialGraph."""
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


def _compute_statistics(simulations: list[SimulationResult]) -> dict[str, dict[str, float]]:
    """Compute statistics for each metric."""
    METRICS = ["irr_after_tax", "npv_after_tax", "payback_period", "dscr_avg", "dscr_min"]

    stats: dict[str, dict[str, float]] = {}

    for metric_key in METRICS:
        values = [getattr(s.metrics, metric_key, None) for s in simulations]
        values = [v for v in values if v is not None]

        if not values:
            continue

        arr = np.array(values)
        stats[metric_key] = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "median": float(np.median(arr)),
            "p5": float(np.percentile(arr, 5)),
            "p25": float(np.percentile(arr, 25)),
            "p75": float(np.percentile(arr, 75)),
            "p95": float(np.percentile(arr, 95)),
        }

    return stats


def _build_probability_table(
    base: DerivedMetrics,
    simulations: list[SimulationResult],
) -> list[dict]:
    """Build probability table for key thresholds."""
    irr_values = [s.metrics.irr_after_tax for s in simulations if s.metrics.irr_after_tax is not None]

    if not irr_values:
        return []

    # Key thresholds for IRR
    thresholds = [0.04, 0.06, 0.08, 0.10]  # 4%, 6%, 8%, 10%

    rows: list[dict] = []
    for thresh in thresholds:
        prob_above = sum(1 for v in irr_values if v >= thresh) / len(irr_values) * 100
        prob_below = 100 - prob_above
        rows.append({
            "阈值": f"IRR ≥ {thresh * 100:.0f}%",
            "达标概率": f"{prob_above:.1f}%",
            "未达标概率": f"{prob_below:.1f}%",
        })

    return rows


def _compute_confidence_intervals(
    simulations: list[SimulationResult],
    confidence_level: float = 0.95,
) -> dict[str, tuple[float, float]]:
    """Compute confidence intervals for metrics."""
    METRICS = ["irr_after_tax", "npv_after_tax", "payback_period"]

    intervals: dict[str, tuple[float, float]] = {}

    for metric_key in METRICS:
        values = [getattr(s.metrics, metric_key, None) for s in simulations]
        values = [v for v in values if v is not None]

        if len(values) < 30:
            continue

        arr = np.array(values)
        mean = np.mean(arr)
        std = np.std(arr)
        n = len(values)

        # z-score for confidence level
        z = 1.96 if confidence_level == 0.95 else 2.576 if confidence_level == 0.99 else 1.645

        margin = z * std / np.sqrt(n)
        intervals[metric_key] = (float(mean - margin), float(mean + margin))

    return intervals


# Preset distribution configurations
PRESET_DISTRIBUTIONS = {
    "电价_保守": DistributionConfig(type="triangular", params={"min": -0.15, "mode": -0.05, "max": 0.05}),
    "电价_中性": DistributionConfig(type="normal", params={"mean": 0, "std": 0.08}),
    "电价_乐观": DistributionConfig(type="triangular", params={"min": -0.05, "mode": 0.05, "max": 0.15}),
    "利率_保守": DistributionConfig(type="triangular", params={"min": -0.05, "mode": 0.02, "max": 0.15}),
    "利率_中性": DistributionConfig(type="normal", params={"mean": 0, "std": 0.05}),
    "投资_保守": DistributionConfig(type="triangular", params={"min": -0.05, "mode": 0.05, "max": 0.20}),
    "投资_中性": DistributionConfig(type="normal", params={"mean": 0, "std": 0.10}),
}