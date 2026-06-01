"""Parallel scenario analysis using concurrent.futures.

Uses ProcessPoolExecutor for executing multiple scenarios in parallel.
Each scenario involves multi-variable simultaneous change.

Performance:
- For 3 standard scenarios: marginal benefit
- For 5+ custom scenarios: meaningful speedup

Memory safety: Same pattern as monte_carlo_parallel.

Usage:
    result = run_scenario_analysis_parallel(
        graph=graph,
        param_cells=[("cell_id", "电价", "revenue"), ...],
        scenario_ratios={"悲观": {...}, "基准": {}, "乐观": {...}},
        workers=4,
        cells_path="/path/to/cells.json",
    )
"""
from __future__ import annotations

import gc
import multiprocessing
import multiprocessing.context
import os
import platform
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from financial_kg.engine.derived_metrics import compute_derived_metrics, DerivedMetrics
from financial_kg.engine.scenario_analysis import ScenarioResult


# Force 'spawn' context on Windows
if platform.system() == "Windows":
    mp_ctx = multiprocessing.get_context("spawn")
else:
    mp_ctx = multiprocessing.get_context("fork")


@dataclass(frozen=True)
class ParallelScenarioResult:
    """Result from parallel scenario analysis."""
    base_metrics: DerivedMetrics
    workers: int
    scenarios: list[ScenarioResult] = field(default_factory=list)
    comparison_table: list[dict] = field(default_factory=list)
    delta_table: list[dict] = field(default_factory=list)


# Global graph for worker
_worker_graph = None


def _load_graph_from_json(cells_path: str) -> Any:
    """Load graph from cells JSON file."""
    from financial_kg.storage.json_store import load_graph
    return load_graph(cells_path)


def _worker_init(cells_path: str):
    """Initializer for each worker process."""
    global _worker_graph
    _worker_graph = _load_graph_from_json(cells_path)


def run_scenario_analysis_parallel(
    graph: Any,
    param_cells: list[tuple[str, str, str]],  # [(cell_id, name, classification), ...]
    scenario_ratios: dict[str, dict[str, float]],  # {"悲观": {cell_id: ratio}, ...}
    workers: int = 4,
    cells_path: str = "",
) -> ParallelScenarioResult:
    """Run scenario analysis in parallel.

    Args:
        graph: FinancialGraph (used for base_metrics only).
        param_cells: List of (cell_id, display_name, classification).
        scenario_ratios: Dict of {scenario_name: {cell_id: ratio}}.
        workers: Number of parallel processes.
        cells_path: Path to cells JSON file.

    Returns:
        ParallelScenarioResult with all scenarios.
    """
    # Memory safety check
    try:
        import psutil
        available_gb = psutil.virtual_memory().available / 1024**3
        estimated_per_worker_gb = 0.4  # 400MB

        if available_gb < workers * estimated_per_worker_gb * 1.5:
            safe_workers = int(available_gb / estimated_per_worker_gb / 1.5)
            if safe_workers > 0:
                workers = min(workers, safe_workers)
                print(f"Adjusted workers to {workers} (memory: {available_gb:.1f}GB)")
    except ImportError:
        pass

    # Clamp workers
    max_workers = min(multiprocessing.cpu_count(), 8)
    if workers > max_workers:
        workers = max_workers

    base_metrics = compute_derived_metrics(graph)

    # Pre-compute original values
    original_values: dict[str, float] = {}
    for cell_id, param_name, classification in param_cells:
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

    # Build tasks (one per scenario)
    scenario_names = list(scenario_ratios.keys())
    tasks: list[tuple] = []

    for scenario_name in scenario_names:
        ratios = scenario_ratios.get(scenario_name, {})
        if not ratios and scenario_name == "基准":
            # Base scenario: no changes
            continue
        tasks.append((scenario_name, ratios, original_values))

    if not tasks:
        # Only base scenario
        return ParallelScenarioResult(
            base_metrics=base_metrics,
            workers=workers,
            scenarios=[
                ScenarioResult(
                    name="基准",
                    param_changes={},
                    metrics=base_metrics,
                    changed_cells=0,
                )
            ],
            comparison_table=[],
            delta_table=[],
        )

    # Run in parallel
    results: list[dict] = [None] * len(tasks)

    try:
        with ProcessPoolExecutor(
            max_workers=min(workers, len(tasks)),
            initializer=_worker_init,
            initargs=(cells_path,),
            mp_context=mp_ctx,
        ) as executor:
            futures = {
                executor.submit(
                    _run_scenario_worker,
                    scenario_name,
                    ratios,
                    original_values,
                    idx,
                ): idx
                for idx, (scenario_name, ratios, original_values) in enumerate(tasks)
            }

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result(timeout=600)
                except Exception as e:
                    print(f"  WARNING: scenario {idx} failed: {e}")
                    results[idx] = None

    except KeyboardInterrupt:
        print("  Interrupted, cancelling pending scenarios...")
        raise
    except Exception as e:
        print(f"  ERROR: parallel execution failed: {e}")
        raise

    # Cleanup note: workers release memory when ProcessPoolExecutor context exits.

    # Build scenarios
    scenarios: list[ScenarioResult] = []

    # Add base scenario first
    scenarios.append(ScenarioResult(
        name="基准",
        param_changes={},
        metrics=base_metrics,
        changed_cells=0,
    ))

    # Add other scenarios
    for r in results:
        if r is None:
            continue
        scenarios.append(ScenarioResult(
            name=r["name"],
            param_changes=r["param_changes"],
            metrics=r["metrics"],
            changed_cells=r["changed_cells"],
        ))

    # Build tables
    comparison_table = _build_comparison_table(base_metrics, scenarios)
    delta_table = _build_delta_table(base_metrics, scenarios)

    return ParallelScenarioResult(
        base_metrics=base_metrics,
        workers=workers,
        scenarios=scenarios,
        comparison_table=comparison_table,
        delta_table=delta_table,
    )


def _run_scenario_worker(
    scenario_name: str,
    ratios: dict[str, float],
    original_values: dict[str, float],
    idx: int,
) -> dict[str, Any]:
    """Worker function for one scenario."""
    global _worker_graph

    try:
        import copy
        work_cells = {}
        for cid, cell in _worker_graph.cells.items():
            cell_copy = copy.copy(cell)
            cell_copy.dependencies = list(cell.dependencies)
            cell_copy.dependents = list(cell.dependents)
            work_cells[cid] = cell_copy

        from financial_kg.models.graph import FinancialGraph
        work_graph = FinancialGraph(source_file=_worker_graph.source_file)
        work_graph.cells = work_cells
        work_graph.indicators = dict(_worker_graph.indicators)
        work_graph.tables = dict(_worker_graph.tables)
        work_graph.cell_graph = _worker_graph.cell_graph.__class__()
        work_graph.cell_graph.add_nodes_from(_worker_graph.cell_graph.nodes())
        work_graph.cell_graph.add_edges_from(_worker_graph.cell_graph.edges())

        # Apply all param changes
        changes: dict[str, float] = {}
        for cell_id, ratio in ratios.items():
            original = original_values.get(cell_id)
            if original is None:
                continue
            perturbed = original * (1 + ratio)
            cell = work_graph.cells.get(cell_id)
            if cell:
                cell.value = perturbed
                changes[cell_id] = ratio

        # Recalculate
        from financial_kg.engine.recalculator import recalculate
        recalc_input = {cid: work_graph.cells[cid].value for cid in changes}
        result = recalculate(work_graph, recalc_input)

        # Compute metrics
        metrics = compute_derived_metrics(work_graph)

        # Cleanup ALL local references (prevent memory accumulation)
        del result
        del recalc_input
        del work_cells
        del work_graph
        del changes
        gc.collect()

        return {
            "idx": idx,
            "name": scenario_name,
            "param_changes": changes,
            "metrics": metrics,
            "changed_cells": len(result.changed_cells) if result else 0,
        }

    except Exception as e:
        print(f"Worker scenario {idx} error: {e}")
        gc.collect()
        return {
            "idx": idx,
            "name": scenario_name,
            "param_changes": {},
            "metrics": DerivedMetrics(),
            "changed_cells": 0,
            "error": str(e),
        }


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
    """Build delta from base."""
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