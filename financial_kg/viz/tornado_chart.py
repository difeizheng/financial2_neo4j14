"""Tornado chart for sensitivity analysis — ECharts horizontal bar chart."""
from __future__ import annotations

import json

from financial_kg.engine.sensitivity import SensitivityResult


def render_tornado_html(
    result: SensitivityResult,
    metric_key: str = "irr_after_tax",
    metric_label: str = "税后IRR",
) -> str:
    """Generate ECharts tornado/sensitivity chart HTML.

    Bars sorted by impact magnitude (largest at center).
    Left side = negative perturbation, right side = positive.
    """
    base_val = getattr(result.base_metrics, metric_key, None)
    if base_val is None:
        return ""

    multiplier = 100 if "irr" in metric_key else 1
    unit = "%" if "irr" in metric_key else ""

    # Group by parameter, compute max impact for sorting
    by_param: dict[str, dict] = {}
    for s in result.scenarios:
        s_val = getattr(s.metrics, metric_key, None)
        if s_val is None:
            continue
        by_param.setdefault(s.param_name, {})[s.perturbation] = s_val

    params_sorted = sorted(
        by_param.keys(),
        key=lambda p: max(
            abs(by_param[p].get(k, base_val) - base_val) for k in by_param[p]
        ),
        reverse=True,
    )
    if not params_sorted:
        return ""

    # Build paired bars: left=negative, right=positive
    neg_values: list = []
    pos_values: list = []
    param_labels: list = []

    for p in params_sorted:
        param_labels.append(p)
        neg_scenarios = sorted(
            [k for k in by_param[p] if k < 0]
        )
        pos_scenarios = sorted(
            [k for k in by_param[p] if k > 0]
        )
        # Take closest-to-zero perturbation for each direction
        neg_pct = neg_scenarios[-1] if neg_scenarios else None
        pos_pct = pos_scenarios[0] if pos_scenarios else None

        neg_val = by_param[p].get(neg_pct) if neg_pct else None
        pos_val = by_param[p].get(pos_pct) if pos_pct else None

        neg_delta = (neg_val - base_val) * multiplier if neg_val is not None else 0
        pos_delta = (pos_val - base_val) * multiplier if pos_val is not None else 0

        neg_values.append(round(neg_delta, 2))
        pos_values.append(round(pos_delta, 2))

    option = {
        "title": {"text": f"{metric_label} 敏感性分析 — 龙卷风图", "left": "center", "textStyle": {"fontSize": 15}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                    "formatter": "{b}<br/>{a}: {c}{d}"},
        "legend": {"data": ["负向扰动", "正向扰动"], "top": 30},
        "grid": {"top": 80, "bottom": 40, "left": 70, "right": 70},
        "xAxis": {"type": "value", "name": f"变化量({unit})", "splitLine": {"lineStyle": {"type": "dashed", "color": "#ddd"}}},
        "yAxis": {"type": "category", "data": list(reversed(param_labels)),
                  "axisLabel": {"fontSize": 11, "interval": 0, "overflow": "truncate", "width": 120},
                  "axisTick": {"show": False}},
        "series": [
            {
                "name": "正向扰动", "type": "bar", "stack": "right",
                "data": list(reversed(pos_values)),
                "itemStyle": {"color": "#ef4444"},
                "label": {"show": True, "position": "right", "formatter": f"{{c}}{unit}", "fontSize": 10},
            },
            {
                "name": "负向扰动", "type": "bar", "stack": "left",
                "data": list(reversed(neg_values)),
                "itemStyle": {"color": "#3b82f6"},
                "label": {"show": True, "position": "left", "formatter": f"{{c}}{unit}", "fontSize": 10},
            },
        ],
    }

    return _wrap_echarts(option)


def render_spider_chart(
    result: SensitivityResult,
    metric_key: str = "irr_after_tax",
    metric_label: str = "税后IRR",
) -> str:
    """Generate ECharts line (spider) chart for sensitivity analysis.

    X-axis = parameters, Y-axis = metric value, one line per perturbation level.
    """
    base_val = getattr(result.base_metrics, metric_key, None)
    if base_val is None:
        return ""

    multiplier = 100 if "irr" in metric_key else 1

    by_param: dict[str, dict[float, float]] = {}
    for s in result.scenarios:
        s_val = getattr(s.metrics, metric_key, None)
        if s_val is None:
            continue
        by_param.setdefault(s.param_name, {})[s.perturbation] = s_val

    params = list(by_param.keys())
    if not params:
        return ""

    all_pcts = sorted({pct for p in by_param.values() for pct in p})
    base_series = [round(base_val * multiplier, 2)] * len(params)

    series_data: list[dict] = [{
        "name": "基准", "type": "line", "data": base_series,
        "lineStyle": {"type": "dashed", "color": "#888", "width": 2},
        "itemStyle": {"color": "#888"}, "symbol": "circle", "symbolSize": 6,
    }]

    colors = {"-10%": "#1e40af", "-5%": "#3b82f6", "+5%": "#f97316", "+10%": "#dc2626"}
    for pct in all_pcts:
        label = f"{pct:+.0%}"
        line_data = []
        color = colors.get(label, "#6366f1")
        for p in params:
            val = by_param[p].get(pct)
            if val is not None:
                line_data.append(round(val * multiplier, 2))
            else:
                line_data.append(None)
        series_data.append({
            "name": label, "type": "line", "data": line_data,
            "lineStyle": {"width": 2}, "itemStyle": {"color": color},
            "symbol": "diamond", "symbolSize": 7,
        })

    option = {
        "title": {"text": f"{metric_label} 敏感性分析 — 蛛网图", "left": "center", "textStyle": {"fontSize": 15}},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": [s["name"] for s in series_data], "bottom": 0},
        "grid": {"top": 50, "bottom": 60, "left": 70, "right": 30},
        "xAxis": {"type": "category", "data": params, "axisLabel": {"rotate": 30, "interval": 0, "fontSize": 11, "overflow": "truncate", "width": 100}},
        "yAxis": {"type": "value", "name": metric_label, "splitLine": {"lineStyle": {"type": "dashed"}}},
        "series": series_data,
    }

    return _wrap_echarts(option)


def _wrap_echarts(option: dict) -> str:
    """Wrap ECharts option dict into HTML."""
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js\"></script>"
        "<style>body{margin:0;font-family:-apple-system,sans-serif;}#chart{width:100%;height:400px;}</style>"
        "</head><body><div id=\"chart\"></div>"
        "<script>var chart=echarts.init(document.getElementById('chart'));"
        "var option=" + json.dumps(option, ensure_ascii=False) + ";"
        "chart.setOption(option);window.addEventListener('resize',function(){chart.resize();});"
        "</script></body></html>"
    )
