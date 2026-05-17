"""What-If Q&A: detect '如果...会怎样' questions, compute parameter perturbation
impact, return structured answer without LLM dependency."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.derived_metrics import compute_derived_metrics, DerivedMetrics


@dataclass(frozen=True)
class MetricDelta:
    name: str
    before: float | None
    after: float | None
    delta: float | None
    delta_pct: float | None
    unit: str


@dataclass
class WhatIfAnswer:
    text: str
    param_name: str
    param_before: float
    param_after: float
    perturbation_pct: float
    metrics: list[MetricDelta]
    confidence: int


# ── Pattern detection ────────────────────────────────────────────────────────

_WHAT_IF_PATTERNS = [
    r"如果(.{1,30})[提升增涨加]加?(.{1,5})%(.{1,20})变[成化]多[少大]?",
    r"假设(.{1,30})[提升增涨加]加?(.{1,5})%(.{1,20})变[成化]多[少大]?",
    r"(.{1,30})[提升增涨加]加?(.{1,5})%后(.{1,20})变[成化]多[少大]?",
    r"如果(.{1,30})[降减缩下]低?(.{1,5})%(.{1,20})变[成化]多[少大]?",
    r"假设(.{1,30})[降减缩下]低?(.{1,5})%(.{1,20})变[成化]多[少大]?",
    r"(.{1,30})[降减缩下]低?(.{1,5})%后(.{1,20})变[成化]多[少大]?",
    r"如果(.{1,30})为(.{1,20})[，,则]?(.{1,20})变[成化]多[少大]?",
    r"假设(.{1,30})为(.{1,20})[，,则]?(.{1,20})变[成化]多[少大]?",
]

_METRIC_NAMES = {
    "irr": ("irr_after_tax", "税后IRR", "%"),
    "内部收益率": ("irr_after_tax", "税后IRR", "%"),
    "irr.*税.*前": ("irr_before_tax", "税前IRR", "%"),
    "净现值.*税.*后": ("npv_after_tax", "税后净现值", ""),
    "净现值.*税.*前": ("npv_before_tax", "税前净现值", ""),
    "回收期": ("payback_period", "投资回收期", "年"),
    "dscr.*均": ("dscr_avg", "DSCR均值", ""),
    "dscr.*最低": ("dscr_min", "DSCR最低值", ""),
    "dscr": ("dscr_avg", "DSCR均值", ""),
    "npv": ("npv_after_tax", "财务净现值", ""),
}


def detect_what_if(question: str) -> dict | None:
    """Detect what-if question pattern. Returns parsed dict or None."""
    for pat in _WHAT_IF_PATTERNS:
        m = re.search(pat, question)
        if not m:
            continue

        param_text = m.group(1).strip()
        target_text = m.group(3).strip()

        # Determine perturbation direction and magnitude
        pct_str = m.group(2).strip()
        try:
            pct_val = float(pct_str)
        except ValueError:
            pct_val = 10  # default

        # Determine direction from pattern keywords
        increase_words = ["提", "升", "增", "涨", "加"]
        is_increase = any(kw in pat for kw in increase_words)
        perturbation = pct_val / 100 if is_increase else -pct_val / 100

        return {
            "param_text": param_text,
            "target_text": target_text,
            "perturbation": perturbation,
        }

    # Pattern: "X为Y" (set to absolute value)
    for pat in _WHAT_IF_PATTERNS[6:]:
        m = re.search(pat, question)
        if not m:
            continue
        param_text = m.group(1).strip()
        abs_value_str = m.group(2).strip()
        target_text = m.group(3).strip() if len(m.groups()) > 3 else ""
        try:
            abs_value = float(abs_value_str.replace(",", ""))
            return {
                "param_text": param_text,
                "target_text": target_text,
                "absolute_value": abs_value,
                "perturbation": None,
            }
        except ValueError:
            pass

    return None


def _find_param_cell(
    graph: FinancialGraph, param_text: str,
) -> tuple[str, str, float] | None:
    """Find parameter cell by name match. Returns (cell_id, display_name, value)."""
    param_text = param_text.lower()
    best_score = 0
    best_result = None

    # Strategy 1: match by indicator name, use indicator's summary_value cell
    for ind_id, ind in graph.indicators.items():
        ind_name = (ind.name or "").lower()
        if not ind_name:
            continue
        score = _fuzzy_match(param_text, ind_name)
        if score < 0.4 or score <= best_score:
            continue

        # Find the summary_value cell (the one matching indicator's summary_value)
        target_val = ind.summary_value
        if target_val is None:
            continue
        try:
            target_val = float(target_val)
        except (TypeError, ValueError):
            continue
        if target_val == 0:
            continue

        # Find the cell that holds this value
        target_cid = None
        for cid in ind.cell_ids:
            cell = graph.cells.get(cid)
            if cell:
                try:
                    cv = float(cell.value)
                except (TypeError, ValueError):
                    continue
                if abs(cv - target_val) < 1e-9:
                    target_cid = cid
                    break

        if target_cid:
            best_score = score
            display = ind_name
            best_result = (target_cid, display, target_val)

    # Fallback: if no indicator match, try table/sheet names
    if best_result is None:
        for cid, cell in graph.cells.items():
            ind_name = ""
            if cell.indicator_id and cell.indicator_id in graph.indicators:
                ind_name = (graph.indicators[cell.indicator_id].name or "").lower()

            names_to_check = [ind_name, cell.sheet or ""]
            if cell.table_id and cell.table_id in graph.tables:
                names_to_check.append(graph.tables[cell.table_id].name.lower())

            max_score = max(_fuzzy_match(param_text, n) for n in names_to_check if n)

            if max_score > best_score and max_score >= 0.4:
                try:
                    val = float(cell.value)
                except (TypeError, ValueError):
                    continue
                if val == 0:
                    continue
                best_score = max_score
                display = ind_name or cell.sheet or cid
                best_result = (cid, display, val)

    return best_result


def _fuzzy_match(query: str, target: str) -> float:
    """Fuzzy match score."""
    if query in target:
        return 0.9 + 0.1 * (len(query) / len(target))
    if target in query:
        return 0.8 + 0.1 * (len(target) / len(query))
    from difflib import SequenceMatcher
    return SequenceMatcher(None, query, target).ratio()


def _resolve_metric_key(target_text: str) -> tuple[str, str, str]:
    """Resolve target metric from question text. Returns (key, label, unit)."""
    target = target_text.lower()
    for pattern, (key, label, unit) in _METRIC_NAMES.items():
        if re.search(pattern, target):
            return key, label, unit
    # Default: IRR
    return "irr_after_tax", "税后IRR", "%"


def answer_what_if(
    graph: FinancialGraph, question: str,
) -> WhatIfAnswer | None:
    """Try to answer a what-if question. Returns WhatIfAnswer or None."""
    parsed = detect_what_if(question)
    if not parsed:
        return None

    param_text = parsed["param_text"]
    target_text = parsed.get("target_text", "")

    # Find parameter cell
    found = _find_param_cell(graph, param_text)
    if not found:
        return None

    cell_id, display_name, original_value = found

    # Compute perturbation or absolute value
    if parsed.get("absolute_value") is not None:
        perturbed_value = parsed["absolute_value"]
        perturbation = (perturbed_value - original_value) / original_value
    else:
        perturbation = parsed["perturbation"]
        perturbed_value = original_value * (1 + perturbation)

    # Clone graph and apply perturbation
    work = _clone_graph(graph)

    # Compute base metrics BEFORE perturbation (clone shares indicator objects
    # with original, so compute now before recalculate mutates them via _sync_indicators).
    base_metrics = compute_derived_metrics(graph)

    c = work.cells.get(cell_id)
    if not c:
        return None
    c.value = perturbed_value

    # Force full model re-evaluation: perturb the cell and let recalculate()
    # propagate through the dependency graph (handles cycles via SCC convergence).
    recalculate(work, {cell_id: perturbed_value})

    # Compute metrics after perturbation
    new_metrics = compute_derived_metrics(work)

    # Build metric deltas
    all_metric_keys = [
        ("irr_after_tax", "税后IRR", "%"),
        ("irr_before_tax", "税前IRR", "%"),
        ("npv_after_tax", "财务净现值", ""),
        ("npv_before_tax", "税前净现值", ""),
        ("payback_period", "投资回收期", "年"),
        ("dscr_avg", "DSCR均值", ""),
        ("dscr_min", "DSCR最低值", ""),
    ]

    metrics = []
    for key, label, unit in all_metric_keys:
        before = getattr(base_metrics, key, None)
        after = getattr(new_metrics, key, None)
        if before is None and after is None:
            continue
        delta = (after - before) if (before is not None and after is not None) else None
        delta_pct = (delta / abs(before) * 100) if (before is not None and before != 0 and delta is not None) else None
        metrics.append(MetricDelta(
            name=label,
            before=before,
            after=after,
            delta=delta,
            delta_pct=delta_pct,
            unit=unit,
        ))

    # Format text
    target_key, target_label, target_unit = _resolve_metric_key(target_text)
    target_metric = next((m for m in metrics if m.name == target_label), None)

    if target_metric and target_metric.after is not None:
        if target_unit == "%":
            after_str = f"{target_metric.after * 100:.2f}%"
            before_str = f"{target_metric.before * 100:.2f}%"
        else:
            after_str = f"{target_metric.after:,.2f}{target_unit}"
            before_str = f"{target_metric.before:,.2f}{target_unit}" if target_metric.before is not None else "—"

        text = (
            f"如果 **{display_name}** {f'变化 {perturbation:+.0%}' if perturbation else f'设为 {perturbed_value:,.2f}'}，"
            f"**{target_label}** 将从 **{before_str}** 变为 **{after_str}**"
        )
    else:
        text = f"**{display_name}** 变化后，参数已更新（{original_value:,.2f} → {perturbed_value:,.2f}）"

    # Confidence: based on param match quality and metric computation success
    confidence = 75 if any(m.after is not None for m in metrics) else 50

    return WhatIfAnswer(
        text=text,
        param_name=display_name,
        param_before=original_value,
        param_after=perturbed_value,
        perturbation_pct=perturbation * 100,
        metrics=metrics,
        confidence=confidence,
    )


def _clone_graph(graph: FinancialGraph) -> FinancialGraph:
    """Create a copy of FinancialGraph for mutation."""
    import copy as _copy
    clone = FinancialGraph(source_file=graph.source_file)
    clone.cells = {}
    for cid, cell in graph.cells.items():
        cell_copy = _copy.copy(cell)
        cell_copy.dependencies = list(cell.dependencies)
        cell_copy.dependents = list(cell.dependents)
        clone.cells[cid] = cell_copy
    clone.indicators = dict(graph.indicators)
    clone.tables = dict(graph.tables)
    clone.cell_graph = graph.cell_graph.copy()
    return clone
