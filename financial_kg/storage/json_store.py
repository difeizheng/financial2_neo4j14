from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Any

from ..models.cell import Cell
from ..models.indicator import Indicator
from ..models.table import Table
from ..models.graph import FinancialGraph


def _default_serializer(obj: Any) -> Any:
    """Handle non-JSON-serializable types."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def save_graph(graph: FinancialGraph, output_dir: str, task_id: str = "") -> dict[str, str]:
    """Save the three-layer graph to JSON files.

    Returns a dict of {layer_name: filepath}.
    """
    os.makedirs(output_dir, exist_ok=True)
    prefix = f"{task_id}_" if task_id else ""

    paths: dict[str, str] = {}

    # ── cells.json ───────────────────────────────────────────────────────────
    cells_path = os.path.join(output_dir, f"{prefix}cells.json")
    cells_data = {
        "metadata": {
            "source_file": graph.source_file,
            "saved_at": datetime.now().isoformat(),
            "stats": graph.stats(),
        },
        "cells": [c.to_dict() for c in graph.cells.values()],
        "dependencies": [
            {"from": u, "to": v}
            for u, v in graph.cell_graph.edges()
        ],
    }
    with open(cells_path, "w", encoding="utf-8") as f:
        json.dump(cells_data, f, ensure_ascii=False, indent=2, default=_default_serializer)
    paths["cells"] = cells_path

    # ── indicators.json ──────────────────────────────────────────────────────
    if graph.indicators:
        ind_path = os.path.join(output_dir, f"{prefix}indicators.json")
        ind_data = {
            "metadata": {"saved_at": datetime.now().isoformat()},
            "indicators": [i.to_dict() for i in graph.indicators.values()],
        }
        with open(ind_path, "w", encoding="utf-8") as f:
            json.dump(ind_data, f, ensure_ascii=False, indent=2, default=_default_serializer)
        paths["indicators"] = ind_path

    # ── tables.json ──────────────────────────────────────────────────────────
    if graph.tables:
        tbl_path = os.path.join(output_dir, f"{prefix}tables.json")
        tbl_data = {
            "metadata": {"saved_at": datetime.now().isoformat()},
            "tables": [t.to_dict() for t in graph.tables.values()],
        }
        with open(tbl_path, "w", encoding="utf-8") as f:
            json.dump(tbl_data, f, ensure_ascii=False, indent=2, default=_default_serializer)
        paths["tables"] = tbl_path

    return paths


def load_graph(cells_path: str) -> FinancialGraph:
    """Load a FinancialGraph from a cells.json file (+ companion indicator/table files)."""
    with open(cells_path, encoding="utf-8") as f:
        data = json.load(f)

    graph = FinancialGraph(source_file=data["metadata"].get("source_file", ""))

    for cd in data["cells"]:
        graph.add_cell(Cell.from_dict(cd))

    for dep in data.get("dependencies", []):
        graph.cell_graph.add_edge(dep["from"], dep["to"])

    # Load companion files if they exist (same prefix, same directory)
    base = cells_path.replace("cells.json", "")
    ind_path = base + "indicators.json"
    tbl_path = base + "tables.json"

    if os.path.exists(ind_path):
        with open(ind_path, encoding="utf-8") as f:
            ind_data = json.load(f)
        for id_ in ind_data.get("indicators", []):
            graph.indicators[id_["id"]] = Indicator.from_dict(id_)

    if os.path.exists(tbl_path):
        with open(tbl_path, encoding="utf-8") as f:
            tbl_data = json.load(f)
        for td in tbl_data.get("tables", []):
            graph.tables[td["id"]] = Table.from_dict(td)

    return graph


def verify_cell_count(graph: FinancialGraph, expected: int) -> dict:
    """Compare parsed cell count against an expected value."""
    actual = len(graph.cells)
    return {
        "expected": expected,
        "actual": actual,
        "match": actual == expected,
        "diff": actual - expected,
    }
