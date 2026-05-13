"""Pre-computed derived financial metrics for snapshot/Q&A consumption.

Computes IRR, NPV, payback period, DSCR series, and other aggregate metrics
that require multi-indicator aggregation rather than simple fuzzy matching.

All metrics are computed from the FinancialGraph cell values at snapshot time
and serialized into the snapshot JSON under a "derived_metrics" key.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from financial_kg.models.graph import FinancialGraph
from financial_kg.models.indicator import Indicator


@dataclass(frozen=True)
class DerivedMetrics:
    """Aggregate financial metrics computed from graph cell values."""
    irr_after_tax: float | None = None
    irr_before_tax: float | None = None
    npv_after_tax: float | None = None
    npv_before_tax: float | None = None
    payback_period: float | None = None
    dscr_series: dict[str, float] = field(default_factory=dict)
    dscr_avg: float | None = None
    dscr_min: float | None = None
    annual_net_cashflow: float | None = None
    total_investment_static: float | None = None
    total_investment_dynamic: float | None = None
    total_revenue: float | None = None
    total_cost: float | None = None
    total_tax: float | None = None
    loan_repayment_period: float | None = None
    icrr: dict[str, float] = field(default_factory=dict)  # sensitivity: {scenario: irr}


# ── Pattern matching helpers ─────────────────────────────────────────────────

# Keywords used to locate relevant indicators/cells by name
_IRR_KEYWORDS = [
    "全投资内部收益率", "税后内部收益率", "全部投资内部收益率",
    "税前内部收益率", "所得税前内部收益率",
    "资本金内部收益率",
    "财务内部收益率",
]
_NPV_KEYWORDS = [
    "财务净现值", "净现值", "税后净现值", "税前净现值",
    "全部投资净现值",
]
_PAYBACK_KEYWORDS = [
    "投资回收期", "全部投资回收期", "税后投资回收期",
    "静态投资回收期",
]
_DSCR_KEYWORDS = [
    "偿债备付率", "DSCR",
]
_INVESTMENT_STATIC_KEYWORDS = ["静态总投资", "工程静态投资"]
_INVESTMENT_DYNAMIC_KEYWORDS = ["动态总投资", "工程动态投资", "总投资"]
_NET_CASHFLOW_KEYWORDS = [
    "净现金流量", "净现金流", "累计盈余资金",
]
_LOAN_KEYWORDS = [
    "借款偿还期", "贷款偿还期", "还款期",
]
_DISCOUNT_RATE_KEYWORDS = [
    "基准收益率", "折现率", "贴现率", "基准折现率",
]


def _match_indicator(indicators: dict[str, Indicator], keywords: list[str]) -> Indicator | None:
    """Find first indicator whose name contains any keyword."""
    for ind in indicators.values():
        name = ind.name or ""
        if any(kw in name for kw in keywords):
            return ind
    return None


def _match_cells_by_keyword(graph: FinancialGraph, keywords: list[str]) -> list[tuple[str, Any]]:
    """Return (cell_id, value) pairs for cells whose indicator name matches keywords."""
    results: list[tuple[str, Any]] = []
    for cid, cell in graph.cells.items():
        if cell.indicator_id:
            ind = graph.indicators.get(cell.indicator_id)
            if ind and any(kw in (ind.name or "") for kw in keywords):
                results.append((cid, cell.value))
    return results


def _extract_numeric(val: Any) -> float | None:
    """Safely convert any value to float."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_year(label: str) -> int | None:
    """Extract 4-digit year from a period label like '2024', '2024年', '第2024年'."""
    m = re.search(r"(\d{4})", str(label))
    return int(m.group(1)) if m else None


# ── IRR / NPV computation ────────────────────────────────────────────────────

def _compute_irr(cash_flows: list[float]) -> float | None:
    """Compute IRR using Newton-Raphson iteration.

    Args:
        cash_flows: list of cash flows in chronological order, first is typically negative investment.

    Returns:
        IRR as decimal (e.g. 0.0311 = 3.11%), or None if no convergence.
    """
    if len(cash_flows) < 2:
        return None

    # Newton-Raphson: find r where NPV(r) = sum(CF_t / (1+r)^t) = 0
    rate = 0.05  # initial guess
    for _ in range(200):
        npv = 0.0
        d_npv = 0.0
        valid = True
        for t, cf in enumerate(cash_flows):
            denom = (1 + rate) ** t
            if denom == 0 or abs(denom) > 1e300:
                valid = False
                break
            npv += cf / denom
            if t > 0:
                d_npv -= t * cf / ((1 + rate) ** (t + 1))
        if not valid or abs(d_npv) < 1e-20:
            return None
        new_rate = rate - npv / d_npv
        if abs(new_rate - rate) < 1e-10:
            return new_rate
        rate = new_rate
        # Clamp to prevent divergence
        if rate < -0.99:
            rate = -0.99
        elif rate > 10:
            rate = 10
    return None


def _compute_npv(cash_flows: list[float], discount_rate: float) -> float:
    """Compute NPV at given discount rate."""
    return sum(cf / (1 + discount_rate) ** t for t, cf in enumerate(cash_flows))


def _compute_payback_period(cash_flows: list[float]) -> float | None:
    """Compute payback period: years until cumulative cash flow turns positive.

    Uses linear interpolation for fractional year.
    """
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        prev_cumulative = cumulative
        cumulative += cf
        if cumulative >= 0 and prev_cumulative < 0:
            # Interpolate: fraction of year to recover the remaining negative
            fraction = abs(prev_cumulative) / cf if cf != 0 else 0
            return t - 1 + fraction
    return None


# ── Time-series cash flow extraction ────────────────────────────────────────

def _extract_cash_flow_series(
    graph: FinancialGraph,
    keywords: list[str],
) -> list[tuple[int, float]] | None:
    """Extract chronological (year, value) pairs from indicators matching keywords.

    Returns sorted list of (year, value) or None if not enough data.
    """
    # Strategy 1: use indicator time_series
    ind = _match_indicator(graph.indicators, keywords)
    if ind and ind.time_series:
        pairs: list[tuple[int, float]] = []
        for label, val in ind.time_series.items():
            year = _parse_year(label)
            v = _extract_numeric(val)
            if year is not None and v is not None:
                pairs.append((year, v))
        if len(pairs) >= 2:
            pairs.sort()
            return pairs

    # Strategy 2: scan cells with matching indicator, grouped by time_period
    cell_matches = _match_cells_by_keyword(graph, keywords)
    if cell_matches:
        # Try to use table time_period_labels
        period_values: dict[str, float] = {}
        for cid, val in cell_matches:
            cell = graph.cells.get(cid)
            if cell and cell.table_id:
                tbl = graph.tables.get(cell.table_id)
                if tbl and cell.col in tbl.col_roles and tbl.col_roles[cell.col] == "time_series":
                    period = tbl.time_period_labels.get(cell.col, "")
                    v = _extract_numeric(val)
                    if period and v is not None:
                        period_values[period] = v
        if period_values:
            pairs = []
            for label, val in period_values.items():
                year = _parse_year(label)
                if year is not None:
                    pairs.append((year, val))
            if len(pairs) >= 2:
                pairs.sort()
                return pairs

    return None


# ── DSCR series extraction ──────────────────────────────────────────────────

def _extract_dscr_series(graph: FinancialGraph) -> dict[str, float]:
    """Extract annual DSCR values from indicators/cells."""
    result: dict[str, float] = {}

    # Strategy 1: indicator time_series
    ind = _match_indicator(graph.indicators, _DSCR_KEYWORDS)
    if ind and ind.time_series:
        for label, val in ind.time_series.items():
            v = _extract_numeric(val)
            if v is not None:
                result[str(label)] = v
        if result:
            return result

    # Strategy 2: cells
    cell_matches = _match_cells_by_keyword(graph, _DSCR_KEYWORDS)
    if cell_matches:
        for cid, val in cell_matches:
            cell = graph.cells.get(cid)
            if cell and cell.table_id:
                tbl = graph.tables.get(cell.table_id)
                if tbl and cell.col in tbl.col_roles and tbl.col_roles[cell.col] == "time_series":
                    period = tbl.time_period_labels.get(cell.col, str(cell.col))
                    v = _extract_numeric(val)
                    if v is not None:
                        result[period] = v

    return result


# ── Main computation ────────────────────────────────────────────────────────

def compute_derived_metrics(graph: FinancialGraph) -> DerivedMetrics:
    """Compute all derived financial metrics from the current graph state.

    This is intended to be called at snapshot creation time, so metrics
    reflect the exact cell values at that moment.
    """
    metrics: dict[str, Any] = {}

    # ── 1. Cash flow series for IRR/NPV/payback ─────────────────────────────
    cf_pairs = _extract_cash_flow_series(
        graph,
        ["项目投资现金流量", "全部投资现金流量", "资本金现金流量", "净现金流量"],
    )

    # Fallback: look for any indicator with "现金流量" that has time_series
    if not cf_pairs:
        for ind in graph.indicators.values():
            if "现金流量" in (ind.name or "") and ind.time_series:
                pairs: list[tuple[int, float]] = []
                for label, val in ind.time_series.items():
                    year = _parse_year(label)
                    v = _extract_numeric(val)
                    if year is not None and v is not None:
                        pairs.append((year, v))
                if len(pairs) >= 2:
                    pairs.sort()
                    cf_pairs = pairs
                    break

    if cf_pairs:
        cash_flows = [v for _, v in cf_pairs]

        irr = _compute_irr(cash_flows)
        if irr is not None:
            metrics["irr_after_tax"] = irr

        # NPV — need discount rate
        disc_ind = _match_indicator(graph.indicators, _DISCOUNT_RATE_KEYWORDS)
        disc_rate = _extract_numeric(disc_ind.summary_value) if disc_ind else None
        if disc_rate is None:
            disc_rate = 0.08  # default 8% benchmark rate
        # Convert percentage to decimal
        if disc_rate and disc_rate > 1:
            disc_rate /= 100

        npv = _compute_npv(cash_flows, disc_rate)
        metrics["npv_after_tax"] = npv

        payback = _compute_payback_period(cash_flows)
        if payback is not None:
            metrics["payback_period"] = payback

    # ── 2. DSCR series ──────────────────────────────────────────────────────
    dscr_series = _extract_dscr_series(graph)
    if dscr_series:
        metrics["dscr_series"] = dscr_series
        vals = [v for v in dscr_series.values() if v is not None]
        if vals:
            metrics["dscr_avg"] = sum(vals) / len(vals)
            metrics["dscr_min"] = min(vals)

    # ── 3. Summary values from keyword-matched indicators ───────────────────
    for key, kw_list in [
        ("total_investment_static", _INVESTMENT_STATIC_KEYWORDS),
        ("total_investment_dynamic", _INVESTMENT_DYNAMIC_KEYWORDS),
        ("loan_repayment_period", _LOAN_KEYWORDS),
    ]:
        ind = _match_indicator(graph.indicators, kw_list)
        if ind:
            v = _extract_numeric(ind.summary_value)
            if v is not None:
                metrics[key] = v

    # ── 4. Aggregate totals ────────────────────────────────────────────────
    for key, kw_list in [
        ("total_revenue", ["营业收入", "总收入", "售电收入", "电费收入"]),
        ("total_cost", ["总成本", "经营成本", "综合购电成本"]),
        ("total_tax", ["所得税", "税金总额", "税费合计"]),
    ]:
        matches = _match_cells_by_keyword(graph, kw_list)
        if matches:
            total = sum(v for _, v in matches if _extract_numeric(v) is not None)
            if total != 0:
                metrics[key] = total

    # ── 5. Annual net cashflow (average) ───────────────────────────────────
    nc_matches = _match_cells_by_keyword(graph, _NET_CASHFLOW_KEYWORDS)
    if nc_matches:
        nc_vals = [v for _, v in nc_matches if _extract_numeric(v) is not None]
        if nc_vals:
            metrics["annual_net_cashflow"] = sum(nc_vals) / len(nc_vals)

    # ── 6. Before-tax IRR/NPV (if separate indicators exist) ───────────────
    cf_before_tax = _extract_cash_flow_series(
        graph,
        ["项目投资现金流量(税前)", "税前现金流量"],
    )
    if cf_before_tax:
        cf_bt = [v for _, v in cf_before_tax]
        irr_bt = _compute_irr(cf_bt)
        if irr_bt is not None:
            metrics["irr_before_tax"] = irr_bt
        disc_rate_bt = disc_rate if disc_rate else 0.08
        metrics["npv_before_tax"] = _compute_npv(cf_bt, disc_rate_bt)

    return DerivedMetrics(**metrics)


# ── Serialization helpers ────────────────────────────────────────────────────

def serialize_metrics(metrics: DerivedMetrics) -> dict[str, Any]:
    """Convert DerivedMetrics to JSON-serializable dict."""
    result: dict[str, Any] = {}
    for f_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                    "npv_before_tax", "payback_period", "dscr_avg", "dscr_min",
                    "annual_net_cashflow", "total_investment_static",
                    "total_investment_dynamic", "total_revenue", "total_cost",
                    "total_tax", "loan_repayment_period"):
        val = getattr(metrics, f_name, None)
        if val is not None:
            result[f_name] = val
    if metrics.dscr_series:
        result["dscr_series"] = metrics.dscr_series
    if metrics.icrr:
        result["icrr"] = metrics.icrr
    return result


def deserialize_metrics(data: dict[str, Any]) -> DerivedMetrics:
    """Reconstruct DerivedMetrics from JSON dict."""
    kwargs: dict[str, Any] = {}
    for f_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                    "npv_before_tax", "payback_period", "dscr_avg", "dscr_min",
                    "annual_net_cashflow", "total_investment_static",
                    "total_investment_dynamic", "total_revenue", "total_cost",
                    "total_tax", "loan_repayment_period"):
        if f_name in data:
            kwargs[f_name] = data[f_name]
    if "dscr_series" in data:
        kwargs["dscr_series"] = data["dscr_series"]
    if "icrr" in data:
        kwargs["icrr"] = data["icrr"]
    return DerivedMetrics(**kwargs)
