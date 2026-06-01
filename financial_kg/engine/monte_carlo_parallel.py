"""Parallel Monte Carlo simulation using concurrent.futures.

Uses ProcessPoolExecutor with initializer to load graph once per worker,
then execute iterations in parallel.

Performance:
- 4 workers → ~125 minutes for 100 iterations (vs 500 minutes serial)
- 8 workers → ~62 minutes for 100 iterations

Memory safety:
- Workers use shallow cell copies (not deep graph clone)
- Explicit GC after each iteration
- Memory monitoring (Windows/Linux)
- Formula cache NOT cleared (safe: cell_id-based, no accumulation)

Usage:
    result = run_monte_carlo_parallel(
        graph=graph,
        param_cells=param_configs,
        iterations=100,
        workers=4,
        cells_path="/path/to/cells.json",
    )
"""
from __future__ import annotations

import gc
import json
import multiprocessing
import multiprocessing.context
import os
import platform
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from financial_kg.engine.derived_metrics import compute_derived_metrics, DerivedMetrics
from financial_kg.engine.monte_carlo import DistributionConfig, _sample_distribution


# Force 'spawn' context on Windows for memory safety (avoid fork issues)
if platform.system() == "Windows":
    mp_ctx = multiprocessing.get_context("spawn")
else:
    # Linux/Unix can use fork (more efficient)
    mp_ctx = multiprocessing.get_context("fork")


@dataclass(frozen=True)
class ParallelMonteCarloResult:
    """Result from parallel Monte Carlo simulation."""
    base_metrics: DerivedMetrics
    iterations: int
    workers: int
    irr_values: list[float] = field(default_factory=list)  # Store raw IRR values for histogram
    npv_values: list[float] = field(default_factory=list)  # Optional: NPV values
    statistics: dict[str, dict[str, float]] = field(default_factory=dict)
    probability_table: list[dict] = field(default_factory=dict)


# Global graph loaded by initializer (shared within worker process)
_worker_graph = None


def _load_graph_from_json(cells_path: str) -> Any:
    """Load graph from cells JSON file for worker initialization."""
    # Import here to avoid circular imports
    from financial_kg.storage.json_store import load_graph
    return load_graph(cells_path)


def _worker_init(cells_path: str):
    """Initializer for each worker process.

    Loads graph once per worker and stores in module global.
    """
    global _worker_graph
    _worker_graph = _load_graph_from_json(cells_path)

    # Log worker memory usage
    if platform.system() == "Windows":
        import psutil
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / 1024 / 1024
        print(f"  Worker {os.getpid()} loaded: {mem_mb:.1f} MB")
    else:
        # Linux/Unix
        import resource
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(f"  Worker {os.getpid()} loaded: {mem_kb / 1024:.1f} MB")


def run_monte_carlo_parallel(
    graph: Any,  # FinancialGraph (not pickled)
    param_cells: list[tuple[str, str, DistributionConfig]],
    iterations: int = 100,
    workers: int = 4,
    cells_path: str = "",
    seed: int | None = None,
) -> ParallelMonteCarloResult:
    """Run Monte Carlo simulation in parallel using ProcessPoolExecutor.

    Args:
        graph: FinancialGraph (used for base_metrics only, not pickled).
        param_cells: List of (cell_id, param_name, DistributionConfig).
        iterations: Number of simulation iterations.
        workers: Number of parallel processes (default 4).
        cells_path: Path to cells JSON file for worker initialization.
        seed: Random seed for reproducibility.

    Returns:
        ParallelMonteCarloResult with statistics and probability table.

    Raises:
        MemoryError: If system memory insufficient.
    """
    # Memory safety check
    try:
        import psutil
        available_gb = psutil.virtual_memory().available / 1024**3
        estimated_gb = workers * 0.3  # ~300MB per worker estimate
        if available_gb < estimated_gb * 2:  # Need 2x safety margin
            raise MemoryError(
                f"Available memory {available_gb:.1f}GB insufficient "
                f"for {workers} workers (need {estimated_gb * 2:.1f}GB)"
            )
        print(f"Memory check passed: {available_gb:.1f}GB available, estimated {estimated_gb:.1f}GB")
    except ImportError:
        print("Warning: psutil not installed, skipping memory check")

    # Clamp workers to reasonable max (prevent memory exhaustion)
    max_workers = min(multiprocessing.cpu_count(), 8)  # Max 8 workers
    if workers > max_workers:
        print(f"Warning: workers={workers} clamped to {max_workers} (memory safety)")
        workers = max_workers

    if seed is not None:
        np.random.seed(seed)

    from financial_kg.engine.derived_metrics import compute_derived_metrics
    base_metrics = compute_derived_metrics(graph)

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

    # Generate all iteration changes upfront
    all_changes: list[dict[str, float]] = []
    for i in range(iterations):
        changes: dict[str, float] = {}
        for cell_id, param_name, dist_config in param_cells:
            if cell_id not in original_values:
                continue
            ratio = _sample_distribution(dist_config)
            changes[cell_id] = ratio
        all_changes.append(changes)

    # Build worker tasks
    tasks: list[tuple] = []
    for i, changes in enumerate(all_changes):
        tasks.append((changes, original_values, i))

    # Run in parallel
    print(f"Running {iterations} iterations across {workers} workers...")
    results: list[dict[str, Any]] = [None] * iterations

    try:
        # Use mp_ctx for platform-specific spawning
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(cells_path,),
            mp_context=mp_ctx,
        ) as executor:
            futures = {
                executor.submit(_run_iteration_worker, changes, original_values, idx): idx
                for idx, changes in enumerate(all_changes)
            }

            completed = 0
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=600)  # 10min timeout per iteration
                    idx = futures[future]
                    results[idx] = result
                    completed += 1
                    if completed % 10 == 0 or completed == iterations:
                        print(f"  Progress: {completed}/{iterations}")
                except Exception as e:
                    idx = futures[future]
                    print(f"  WARNING: iteration {idx} failed: {e}")
                    results[idx] = None
    except KeyboardInterrupt:
        print("  Interrupted by user, cleaning up...")
        # Executor will clean up workers on exit
        raise
    except Exception as e:
        print(f"  ERROR: parallel execution failed: {e}")
        raise

    # Cleanup worker global graph (hint to GC, though process will exit)
    global _worker_graph
    _worker_graph = None

    # Aggregate results
    irr_values = [r["irr"] for r in results if r is not None and r["irr"] is not None]
    npv_values = [r["npv"] for r in results if r is not None and r["npv"] is not None]

    # Compute statistics
    statistics = _compute_statistics_from_values(irr_values, npv_values)
    probability_table = _build_probability_table_from_values(irr_values)

    return ParallelMonteCarloResult(
        base_metrics=base_metrics,
        iterations=iterations,
        workers=workers,
        irr_values=irr_values,  # Store raw values for visualization
        npv_values=npv_values,
        statistics=statistics,
        probability_table=probability_table,
    )


def _run_iteration_worker(
    changes: dict[str, float],
    original_values: dict[str, float],
    idx: int,
) -> dict[str, Any]:
    """Worker function for one iteration.

    Uses globally loaded graph from initializer.

    Args:
        changes: {cell_id: change_ratio}
        original_values: {cell_id: original_value}
        idx: iteration index

    Returns:
        dict with irr, npv, payback, or None values on error
    """
    global _worker_graph

    try:
        # Shallow copy cells dict only (cells are modified in-place by recalculate)
        import copy
        work_cells = {}
        for cid, cell in _worker_graph.cells.items():
            cell_copy = copy.copy(cell)
            cell_copy.dependencies = list(cell.dependencies)
            cell_copy.dependents = list(cell.dependents)
            work_cells[cid] = cell_copy

        # Create minimal working graph (no full cell_graph copy)
        from financial_kg.models.graph import FinancialGraph
        work_graph = FinancialGraph(source_file=_worker_graph.source_file)
        work_graph.cells = work_cells
        work_graph.indicators = dict(_worker_graph.indicators)
        work_graph.tables = dict(_worker_graph.tables)
        # Only copy cell_graph structure (nodes/edges), not deep copy
        work_graph.cell_graph = _worker_graph.cell_graph.__class__()
        work_graph.cell_graph.add_nodes_from(_worker_graph.cell_graph.nodes())
        work_graph.cell_graph.add_edges_from(_worker_graph.cell_graph.edges())

        # Apply changes to the graph copy
        for cell_id, ratio in changes.items():
            cell = work_graph.cells.get(cell_id)
            if cell:
                original = original_values.get(cell_id, 0)
                cell.value = original * (1 + ratio)

        # Recalculate
        from financial_kg.engine.recalculator import recalculate
        recalc_input = {cid: work_graph.cells[cid].value for cid in changes}
        recalculate(work_graph, recalc_input)

        # Compute metrics
        metrics = compute_derived_metrics(work_graph)

        # Explicit cleanup to help GC
        del work_cells
        del work_graph
        del recalc_input
        gc.collect()

        return {
            "iteration": idx,
            "irr": metrics.irr_after_tax,
            "npv": metrics.npv_after_tax,
            "payback": metrics.payback_period,
        }

    except Exception as e:
        # Log error but don't crash worker (return None values)
        print(f"Worker iteration {idx} error: {e}")
        gc.collect()  # Cleanup on error too
        return {
            "iteration": idx,
            "irr": None,
            "npv": None,
            "payback": None,
            "error": str(e),
        }


def _compute_statistics_from_values(
    irr_values: list[float],
    npv_values: list[float],
) -> dict[str, dict[str, float]]:
    """Compute statistics from value lists."""
    stats: dict[str, dict[str, float]] = {}

    if irr_values:
        arr = np.array(irr_values)
        stats["irr_after_tax"] = {
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

    if npv_values:
        arr = np.array(npv_values)
        stats["npv_after_tax"] = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "median": float(np.median(arr)),
            "p5": float(np.percentile(arr, 5)),
            "p95": float(np.percentile(arr, 95)),
        }

    return stats


def _build_probability_table_from_values(irr_values: list[float]) -> list[dict]:
    """Build probability table for IRR thresholds."""
    if not irr_values:
        return []

    thresholds = [0.04, 0.06, 0.08, 0.10]
    rows: list[dict] = []

    for thresh in thresholds:
        prob_above = sum(1 for v in irr_values if v >= thresh) / len(irr_values) * 100
        rows.append({
            "阈值": f"IRR ≥ {thresh * 100:.0f}%",
            "达标概率": f"{prob_above:.1f}%",
            "未达标概率": f"{100 - prob_above:.1f}%",
        })

    return rows