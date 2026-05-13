"""Snapshot management: create, save, load, and diff graph snapshots."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from financial_kg.models.graph import FinancialGraph


# ── Snapshot data structures ─────────────────────────────────────────────────

@dataclass
class Snapshot:
    task_id: str
    name: str
    created_at: str
    filepath: str
    # cell_id -> value (serialized)
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotDiff:
    snapshot_a: str   # name
    snapshot_b: str   # name
    changed_cells: list[dict]          # [{id, old, new}]
    affected_indicators: list[dict]    # [{id, name, old_summary, new_summary}]
    summary: dict                      # {total_changed, sheets_affected, ...}


# ── Create / save / load ─────────────────────────────────────────────────────

def create_snapshot(
    graph: FinancialGraph,
    task_id: str,
    name: str,
    snapshots_dir: str = "snapshots",
) -> Snapshot:
    """Serialize all cell values to a JSON snapshot file."""
    os.makedirs(os.path.join(snapshots_dir, task_id), exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{name}.json"
    filepath = os.path.join(snapshots_dir, task_id, filename)

    values = {cell_id: _serialize_value(cell.value) for cell_id, cell in graph.cells.items()}

    payload = {
        "task_id": task_id,
        "name": name,
        "created_at": datetime.now().isoformat(),
        "cell_count": len(values),
        "values": values,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    return Snapshot(
        task_id=task_id,
        name=name,
        created_at=payload["created_at"],
        filepath=filepath,
        values=values,
    )


def load_snapshot(filepath: str) -> Snapshot:
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return Snapshot(
        task_id=data["task_id"],
        name=data["name"],
        created_at=data["created_at"],
        filepath=filepath,
        values=data["values"],
    )


# ── Diff ─────────────────────────────────────────────────────────────────────

def diff_snapshots(
    snap_a: Snapshot,
    snap_b: Snapshot,
    graph: FinancialGraph,
) -> SnapshotDiff:
    """Compare two snapshots and return a structured diff."""
    changed_cells: list[dict] = []
    sheets_affected: set[str] = set()

    all_ids = set(snap_a.values) | set(snap_b.values)
    for cell_id in all_ids:
        old_val = snap_a.values.get(cell_id)
        new_val = snap_b.values.get(cell_id)
        if not _values_equal(old_val, new_val):
            cell = graph.cells.get(cell_id)
            magnitude = _compute_magnitude(old_val, new_val)
            direction = _compute_direction(old_val, new_val)
            indicator_name = ""
            if cell and cell.indicator_id:
                ind = graph.indicators.get(cell.indicator_id)
                if ind:
                    indicator_name = ind.name
            changed_cells.append({
                "id": cell_id,
                "sheet": cell.sheet if cell else "",
                "old": old_val,
                "new": new_val,
                "formula": cell.formula_raw if cell else None,
                "change_magnitude": magnitude,
                "direction": direction,
                "indicator_name": indicator_name,
            })
            if cell:
                sheets_affected.add(cell.sheet)

    # Aggregate indicator-level changes
    ind_changes: dict[str, dict] = {}
    for entry in changed_cells:
        cell = graph.cells.get(entry["id"])
        if cell and cell.indicator_id:
            ind_id = cell.indicator_id
            if ind_id not in ind_changes:
                ind = graph.indicators.get(ind_id)
                ind_changes[ind_id] = {
                    "id": ind_id,
                    "name": ind.name if ind else ind_id,
                    "sheet": ind.sheet if ind else "",
                    "old_summary": None,
                    "new_summary": None,
                    "changed_cell_count": 0,
                }
            ind_changes[ind_id]["changed_cell_count"] += 1

    # Fill old/new summary values from snapshots
    for ind_id, entry in ind_changes.items():
        ind = graph.indicators.get(ind_id)
        if ind is None:
            continue
        # Use the value cell (first numeric cell in indicator) as proxy
        for cid in ind.cell_ids:
            old_v = snap_a.values.get(cid)
            new_v = snap_b.values.get(cid)
            if isinstance(old_v, (int, float)) or isinstance(new_v, (int, float)):
                entry["old_summary"] = old_v
                entry["new_summary"] = new_v
                break

    summary = {
        "total_changed_cells": len(changed_cells),
        "total_changed_indicators": len(ind_changes),
        "sheets_affected": sorted(sheets_affected),
    }

    return SnapshotDiff(
        snapshot_a=snap_a.name,
        snapshot_b=snap_b.name,
        changed_cells=changed_cells,
        affected_indicators=list(ind_changes.values()),
        summary=summary,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

FLOAT_REL_TOL = 1e-9   # Relative tolerance for floating-point comparison
FLOAT_ABS_TOL = 1e-9   # Absolute tolerance (covers floating-point noise ~1e-11)


def _serialize_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    return str(val)


def _compute_magnitude(old_val: Any, new_val: Any) -> float:
    """变化幅度: 数值取绝对差, 字符串/None 取 0。"""
    if old_val is None or new_val is None:
        try:
            return abs(float(new_val if new_val is not None else old_val))
        except (TypeError, ValueError):
            return 0.0
    try:
        return abs(float(new_val) - float(old_val))
    except (TypeError, ValueError):
        return 0.0


def _compute_direction(old_val: Any, new_val: Any) -> str:
    """变化方向: increase / decrease。"""
    if old_val is None or new_val is None:
        return "increase"
    try:
        return "increase" if float(new_val) >= float(old_val) else "decrease"
    except (TypeError, ValueError):
        return "unchanged"


def _values_equal(old_val: Any, new_val: Any) -> bool:
    """Compare values with floating-point tolerance (both relative and absolute)."""
    if old_val is None and new_val is None:
        return True
    if old_val is None or new_val is None:
        return False
    if isinstance(old_val, str) and isinstance(new_val, str):
        return old_val == new_val
    if isinstance(old_val, bool) and isinstance(new_val, bool):
        return old_val == new_val
    # Numeric comparison with tolerance (matches math.isclose behavior)
    try:
        old_f = float(old_val)
        new_f = float(new_val)
        if old_f == new_f:
            return True
        # Absolute tolerance: critical for values near zero
        # Example: 0 vs -3.6e-11 should be equal (abs_tol covers this)
        abs_diff = abs(old_f - new_f)
        if abs_diff <= FLOAT_ABS_TOL:
            return True
        # Relative tolerance: for larger values
        max_val = max(abs(old_f), abs(new_f))
        if max_val == 0:
            return True  # Both zero (already covered by abs_tol)
        rel_diff = abs_diff / max_val
        return rel_diff <= FLOAT_REL_TOL
    except (TypeError, ValueError):
        return old_val == new_val
