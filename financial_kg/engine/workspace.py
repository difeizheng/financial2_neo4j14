"""Workspace management for parameter editing, scenarios, and modification history."""
from __future__ import annotations
import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate, RecalcResult


def _serialize_value(val: Any) -> Any:
    """Convert potentially non-JSON-serializable values (numpy types, etc.) to primitives."""
    if val is None:
        return None
    # Handle numpy/primitive numeric types
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, str)):
        return val
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return str(val)


@dataclass(frozen=True)
class ModificationRecord:
    """A single parameter change record."""
    id: str
    task_id: str
    scenario: str
    cell_id: str
    indicator_name: str
    sheet: str
    old_value: Any
    new_value: Any
    timestamp: str
    batch_id: str


@dataclass
class Scenario:
    """A named set of parameter overrides."""
    id: str
    task_id: str
    name: str
    created_at: str
    overrides: dict[str, Any] = field(default_factory=dict)
    recalc_result: dict | None = None


@dataclass
class WorkspaceState:
    """Holds all workspace data for a task."""
    task_id: str
    scenarios: dict[str, Scenario] = field(default_factory=dict)
    history: list[ModificationRecord] = field(default_factory=list)
    active_scenario: str = "基准"
    pending_edits: dict[str, Any] = field(default_factory=dict)
    last_recalc_result: dict | None = None
    recalc_max_iter: int = 100
    recalc_tol: float = 1e-9


_WORKSPACES_DIR = Path(__file__).resolve().parent.parent.parent / "workspaces"


def _workspace_path(task_id: str) -> Path:
    return _WORKSPACES_DIR / f"{task_id}.json"


def load_workspace(task_id: str) -> WorkspaceState:
    """Load workspace from JSON, create default if missing."""
    path = _workspace_path(task_id)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        ws = WorkspaceState(task_id=task_id)
        ws.active_scenario = data.get("active_scenario", "基准")
        ws.pending_edits = data.get("pending_edits", {})
        ws.last_recalc_result = data.get("last_recalc_result")
        ws.recalc_max_iter = data.get("recalc_max_iter", 100)
        ws.recalc_tol = data.get("recalc_tol", 1e-9)

        for name, sdata in data.get("scenarios", {}).items():
            ws.scenarios[name] = Scenario(
                id=sdata["id"],
                task_id=sdata["task_id"],
                name=sdata["name"],
                created_at=sdata["created_at"],
                overrides=sdata.get("overrides", {}),
                recalc_result=sdata.get("recalc_result"),
            )

        for rdata in data.get("history", []):
            ws.history.append(ModificationRecord(**rdata))

        return ws

    # Create default workspace with a "基准" scenario
    ws = WorkspaceState(task_id=task_id)
    ws.scenarios["基准"] = Scenario(
        id=str(uuid.uuid4())[:8],
        task_id=task_id,
        name="基准",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_workspace(ws)
    return ws


def save_workspace(ws: WorkspaceState) -> None:
    """Write workspace state to JSON file."""
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "task_id": ws.task_id,
        "active_scenario": ws.active_scenario,
        "pending_edits": ws.pending_edits,
        "last_recalc_result": ws.last_recalc_result,
        "recalc_max_iter": ws.recalc_max_iter,
        "recalc_tol": ws.recalc_tol,
        "scenarios": {
            name: {
                "id": s.id,
                "task_id": s.task_id,
                "name": s.name,
                "created_at": s.created_at,
                "overrides": s.overrides,
                "recalc_result": s.recalc_result,
            }
            for name, s in ws.scenarios.items()
        },
        "history": [
            {
                "id": r.id,
                "task_id": r.task_id,
                "scenario": r.scenario,
                "cell_id": r.cell_id,
                "indicator_name": r.indicator_name,
                "sheet": r.sheet,
                "old_value": _serialize_value(r.old_value),
                "new_value": _serialize_value(r.new_value),
                "timestamp": r.timestamp,
                "batch_id": r.batch_id,
            }
            for r in ws.history
        ],
    }
    _workspace_path(ws.task_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_scenario_overrides(scenario: Scenario) -> dict[str, Any]:
    """Return the cell_id → value dict for applying a scenario."""
    return dict(scenario.overrides)


def record_modifications(
    ws: WorkspaceState,
    graph: FinancialGraph,
    updates: dict[str, Any],
    result: RecalcResult,
    batch_id: str | None = None,
) -> None:
    """Create ModificationRecords for seed changes and append to history."""
    if batch_id is None:
        batch_id = str(uuid.uuid4())[:8]

    now = datetime.now(timezone.utc).isoformat()

    for cell_id, new_val in updates.items():
        cell = graph.cells.get(cell_id)
        if cell is None:
            continue
        old_val = cell.value  # already mutated by recalculate, need to track differently

        # Use the old_value from result.changed_cells if available
        seed_change = next((c for c in result.changed_cells if c.cell_id == cell_id), None)
        if seed_change is not None:
            old_val = seed_change.old_value

        ind_name = ""
        sheet = ""
        if cell:
            if cell.indicator_id and cell.indicator_id in graph.indicators:
                ind_name = graph.indicators[cell.indicator_id].name
            sheet = cell.sheet

        ws.history.append(ModificationRecord(
            id=str(uuid.uuid4())[:8],
            task_id=ws.task_id,
            scenario=ws.active_scenario,
            cell_id=cell_id,
            indicator_name=ind_name,
            sheet=sheet,
            old_value=old_val,
            new_value=new_val,
            timestamp=now,
            batch_id=batch_id,
        ))

    save_workspace(ws)


def rollback_record(ws: WorkspaceState, record_id: str) -> dict[str, Any] | None:
    """Revert a specific record. Returns updates dict for recalculate, or None if not found."""
    record = next((r for r in ws.history if r.id == record_id), None)
    if record is None:
        return None

    # Remove the record from history
    ws.history = [r for r in ws.history if r.id != record_id]

    # Also remove from active scenario overrides if present
    scenario = ws.scenarios.get(ws.active_scenario)
    if scenario and record.cell_id in scenario.overrides:
        del scenario.overrides[record.cell_id]

    # Clear pending edit for this cell
    ws.pending_edits.pop(record.cell_id, None)

    save_workspace(ws)
    return {record.cell_id: record.old_value}


def get_key_metrics(graph: FinancialGraph) -> list[str]:
    """Return list of indicator IDs that are likely key metrics."""
    keywords = ["NPV", "IRR", "净现值", "内部收益率", "投资", "合计", "总计", "利润", "收益", "成本", "收入"]
    matches = []
    for ind_id, ind in graph.indicators.items():
        name = (ind.name or "").lower()
        if any(kw.lower() in name for kw in keywords):
            matches.append(ind_id)
    return matches


def apply_and_recalc(
    graph: FinancialGraph,
    ws: WorkspaceState,
    base_graph: FinancialGraph,
    record_history: bool = True,
) -> RecalcResult:
    """Apply scenario overrides + pending edits to graph and recalculate.

    Args:
        graph: The mutable graph to apply changes to (should be a deepcopy of base).
        ws: Current workspace state.
        base_graph: The original read-only graph (used to get original values for record-keeping).
        record_history: If False, skip appending to history (used for rollbacks).

    Returns:
        RecalcResult from recalculate().
    """
    scenario = ws.scenarios.get(ws.active_scenario)
    updates: dict[str, Any] = {}

    # Start with scenario overrides
    if scenario:
        updates.update(scenario.overrides)

    # Merge pending edits (override scenario values)
    updates.update(ws.pending_edits)

    if not updates:
        return RecalcResult()

    batch_id = str(uuid.uuid4())[:8]

    result = recalculate(graph, updates, max_iter=ws.recalc_max_iter, tol=ws.recalc_tol, profile=True)

    if result.perf:
        import logging
        p = result.perf
        logging.info(
            f"[RECALC PERF] total={p['total_s']:.1f}s | "
            f"affected={p['affected']} | fast_hits={p['fast_hits']}/{p['eval_count']} ({p['fast_pct']}%) | "
            f"downstream={p['downstream_s']:.2f}s | scc_iters={p['scc_iters']} | "
            f"build_plan={p['build_plan_s']:.2f}s | build_input={p['build_input_s']:.2f}s | "
            f"eval_func={p['eval_func_s']:.2f}s | fast_path={p['fast_path_s']:.2f}s"
        )

    # Save overrides to scenario
    if scenario:
        scenario.overrides = dict(updates)
        scenario.recalc_result = {
            "affected_count": result.affected_count,
            "error_count": len(result.error_cells),
        }

    # Record modifications (skip for rollbacks — the record was already removed)
    if record_history:
        record_modifications(ws, graph, updates, result, batch_id)

    ws.last_recalc_result = {
        "affected_count": result.affected_count,
        "error_count": len(result.error_cells),
        "changed_cell_ids": [c.cell_id for c in result.changed_cells[:200]],
        "error_cell_ids": result.error_cells[:50],
    }
    save_workspace(ws)

    return result
