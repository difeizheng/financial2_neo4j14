"""ECharts time series chart generator for QA page."""
from __future__ import annotations

import json

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"

_COLORS = [
    "#89b4fa", "#a6e3a1", "#fab387", "#f38ba8",
    "#cba6f7", "#94e2d5", "#f9e2af", "#74c7ec",
]


def render_time_series_html(
    series_data: list[dict],
    title: str = "",
    height: str = "350px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Render an ECharts line chart for indicator time series."""
    all_periods: set[str] = set()
    for s in series_data:
        all_periods.update(str(k) for k in s["values"].keys())
    periods = sorted(all_periods)

    lines_json = json.dumps(periods, ensure_ascii=False)

    series_list: list[dict] = []
    for i, s in enumerate(series_data):
        vals = s["values"]
        data = []
        for p in periods:
            raw = vals.get(p)
            if raw is None:
                data.append(None)
            elif isinstance(raw, (int, float)):
                data.append(raw)
            else:
                try:
                    data.append(float(raw))
                except (ValueError, TypeError):
                    data.append(None)
        if all(d is None for d in data):
            continue
        color = s.get("color") or _COLORS[i % len(_COLORS)]
        series_list.append({
            "name": s["name"], "type": "line", "data": data,
            "smooth": True, "symbol": "circle", "symbolSize": 6,
            "lineStyle": {"width": 2.5, "color": color},
            "itemStyle": {"color": color},
            "areaStyle": {"color": color, "opacity": 0.08},
        })

    if not series_list:
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body><div style="color:#a6adc8;padding:40px;text-align:center;">无有效时间序列数据</div></body>
</html>"""

    opt = {
        "title": {"text": title, "textStyle": {"color": "#cdd6f4", "fontSize": 13}, "left": "center"},
        "tooltip": {"trigger": "axis", "backgroundColor": "#1e1e2e", "borderColor": "#313244", "textStyle": {"color": "#cdd6f4"}},
        "legend": {"data": [s["name"] for s in series_data], "textStyle": {"color": "#a6adc8"}, "top": 8},
        "grid": {"left": 60, "right": 30, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": periods, "axisLabel": {"color": "#a6adc8", "rotate": 30}, "axisLine": {"lineStyle": {"color": "#313244"}}},
        "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#313244", "type": "dashed"}}, "axisLabel": {"color": "#a6adc8"}},
        "series": series_list,
    }
    opt_json = json.dumps(opt, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>* {{ margin: 0; padding: 0; box-sizing: border-box; }} body {{ background: #181825; height: {height}; }} #chart {{ width: 100%; height: {height}; }}</style>
</head><body><div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>var chart=echarts.init(document.getElementById('chart'),'dark',{{renderer:'canvas'}});chart.setOption({opt_json});window.addEventListener('resize',function(){{chart.resize();}});</script>
</body></html>"""


def render_bar_chart_html(
    labels: list[str], values: list[float],
    title: str = "", height: str = "300px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Render an ECharts bar chart for comparison."""
    data_json = json.dumps(values, ensure_ascii=False)
    labels_json = json.dumps(labels, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>* {{ margin: 0; padding: 0; box-sizing: border-box; }} body {{ background: #181825; height: {height}; }} #chart {{ width: 100%; height: {height}; }}</style>
</head><body><div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>var chart=echarts.init(document.getElementById('chart'),'dark',{{renderer:'canvas'}});
chart.setOption({{title:{{text:{json.dumps(title,ensure_ascii=False)},textStyle:{{color:'#cdd6f4',fontSize:13}},left:'center'}},tooltip:{{trigger:'axis',backgroundColor:'#1e1e2e',borderColor:'#313244',textStyle:{{color:'#cdd6f4'}}}},grid:{{left:60,right:30,top:50,bottom:40}},xAxis:{{type:'category',data:{labels_json},axisLabel:{{color:'#a6adc8',rotate:30}},axisLine:{{lineStyle:{{color:'#313244'}}}}}},yAxis:{{type:'value',splitLine:{{lineStyle:{{color:'#313244',type:'dashed'}}}},axisLabel:{{color:'#a6adc8'}}}},series:[{{type:'bar',data:{data_json},itemStyle:{{color:'#89b4fa'}},barWidth:'60%'}}]}});
window.addEventListener('resize',function(){{chart.resize();}});</script>
</body></html>"""


def render_pie_chart_html(
    labels: list[str], values: list[int | float],
    title: str = "", height: str = "300px",
    echarts_cdn: str = _ECHARTS_CDN,
    chart_type: str = "pie",
) -> str:
    """Render an ECharts pie/doughnut chart for distribution data."""
    radius = '["40%", "70%"]' if chart_type == "doughnut" else '"70%"'

    data_items = []
    for i, (label, value) in enumerate(zip(labels, values)):
        color = _COLORS[i % len(_COLORS)]
        data_items.append({"value": value, "name": label, "itemStyle": {"color": color}})

    opt = {
        "title": {"text": title, "textStyle": {"color": "#cdd6f4", "fontSize": 13}, "left": "center"},
        "tooltip": {"trigger": "item", "backgroundColor": "#1e1e2e", "borderColor": "#313244", "textStyle": {"color": "#cdd6f4"}, "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "right": 10, "top": "center", "textStyle": {"color": "#a6adc8"}, "type": "scroll"},
        "series": [{
            "type": "pie", "radius": radius, "center": ["40%", "50%"],
            "avoidLabelOverlap": True, "data": data_items,
            "label": {"show": True, "color": "#a6adc8", "formatter": "{b}\n{d}%"},
            "labelLine": {"show": True, "lineStyle": {"color": "#585b70"}},
        }],
    }
    opt_json = json.dumps(opt, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>* {{ margin: 0; padding: 0; box-sizing: border-box; }} body {{ background: #181825; height: {height}; }} #chart {{ width: 100%; height: {height}; }}</style>
</head><body><div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>var chart=echarts.init(document.getElementById('chart'),'dark',{{renderer:'canvas'}});chart.setOption({opt_json});window.addEventListener('resize',function(){{chart.resize();}});</script>
</body></html>"""


def render_waterfall_html(
    metrics: list[dict], title: str = "指标变化瀑布图",
    height: str = "400px", echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Render ECharts waterfall chart showing metric before-after impact."""
    valid = [m for m in metrics if m.get("before") is not None and m.get("after") is not None]
    if not valid:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无有效指标数据</p>"

    labels = [m["name"] for m in valid]
    data_items = []
    for m in valid:
        delta = m.get("delta") or (m["after"] - m["before"])
        unit = m.get("unit", "")
        color = "#a6e3a1" if delta >= 0 else "#f38ba8"
        pos = "top" if delta >= 0 else "bottom"
        sign = "+" if delta >= 0 else ""
        if unit == "%":
            fmt = f"{sign}{delta * 100:.2f}pp"
        elif unit == "年":
            fmt = f"{sign}{delta:.2f}年"
        else:
            fmt = f"{sign}{delta:,.2f}"
        data_items.append({
            "value": delta, "itemStyle": {"color": color},
            "label": {"show": True, "position": pos, "formatter": fmt},
        })

    opt = {
        "title": {"text": title, "textStyle": {"color": "#cdd6f4", "fontSize": 13}, "left": "center"},
        "tooltip": {"trigger": "axis", "backgroundColor": "#1e1e2e", "borderColor": "#313244", "textStyle": {"color": "#cdd6f4"}, "formatter": "{b}: {c}"},
        "grid": {"left": 80, "right": 30, "top": 50, "bottom": 60},
        "xAxis": {"type": "category", "data": labels, "axisLabel": {"color": "#a6adc8", "rotate": 30, "interval": 0, "fontSize": 11}, "axisLine": {"lineStyle": {"color": "#313244"}}},
        "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#313244", "type": "dashed"}}, "axisLabel": {"color": "#a6adc8"}},
        "series": [{"type": "bar", "data": data_items, "barWidth": "50%"}],
    }
    opt_json = json.dumps(opt, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>* {{ margin: 0; padding: 0; box-sizing: border-box; }} body {{ background: #181825; height: {height}; }} #chart {{ width: 100%; height: {height}; }}</style>
</head><body><div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>var chart=echarts.init(document.getElementById('chart'),'dark',{{renderer:'canvas'}});chart.setOption({opt_json});window.addEventListener('resize',function(){{chart.resize();}});</script>
</body></html>"""


def render_breakdown_donut_html(
    breakdown: dict, title: str = "",
    height: str = "350px", echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Render doughnut chart for cost/revenue breakdown."""
    parts = breakdown.get("parts", [])
    if not parts:
        return "<p style='color:#a6adc8;padding:40px;text-align:center;'>无组成数据</p>"

    main = breakdown.get("main", {})
    main_name = main.get("name", "指标")
    main_value = main.get("value", 0)

    data_items = []
    for i, p in enumerate(parts):
        color = _COLORS[i % len(_COLORS)]
        data_items.append({"value": p["value"], "name": p["name"], "itemStyle": {"color": color}})

    opt = {
        "title": {"text": title, "subtext": f"{main_name}: {main_value:.2f}%", "textStyle": {"color": "#cdd6f4", "fontSize": 13}, "left": "center"},
        "tooltip": {"trigger": "item", "backgroundColor": "#1e1e2e", "borderColor": "#313244", "textStyle": {"color": "#cdd6f4"}, "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "right": 10, "top": "center", "textStyle": {"color": "#a6adc8"}},
        "series": [{
            "type": "pie", "radius": ["40%", "70%"], "center": ["40%", "50%"],
            "avoidLabelOverlap": True, "data": data_items,
            "label": {"show": True, "color": "#a6adc8", "formatter": "{b}\n{d}%"},
            "labelLine": {"show": True, "lineStyle": {"color": "#585b70"}},
        }],
    }
    opt_json = json.dumps(opt, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>* {{ margin: 0; padding: 0; box-sizing: border-box; }} body {{ background: #181825; height: {height}; }} #chart {{ width: 100%; height: {height}; }}</style>
</head><body><div id="chart"></div>
<script src="{echarts_cdn}"></script>
<script>var chart=echarts.init(document.getElementById('chart'),'dark',{{renderer:'canvas'}});chart.setOption({opt_json});window.addEventListener('resize',function(){{chart.resize();}});</script>
</body></html>"""
