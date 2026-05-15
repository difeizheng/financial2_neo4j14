"""ECharts HTML template for scenario comparison bar charts."""
from __future__ import annotations

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"


def render_compare_html(
    metrics_json: str,
    height: str = "600px",
    echarts_cdn: str = _ECHARTS_CDN,
) -> str:
    """Return a complete HTML string embedding ECharts comparison chart.

    metrics_json: JSON string with {metrics: [{name, values: [{scenario, value}]}, ...]}
    """
    part1 = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #1a1d2e; font-family: 'Segoe UI', sans-serif; padding: 16px; }}
  #chart {{ width: 100%; height: {height}; }}
  .legend-info {{ color: #8b8fa3; font-size: 12px; text-align: center; margin-top: 8px; }}
</style>
<script src="{echarts_cdn}"></script>
</head>
<body>
<div id="chart"></div>
<p class="legend-info">绿色 = 正向变化，红色 = 负向变化，灰色 = 无变化</p>
<script>
var chart = echarts.init(document.getElementById('chart'), null, {{renderer: 'canvas'}});
var data = """
    part2 = """;

var metricNames = data.metrics.map(function(m) { return m.name; });
var scenarioNames = data.scenarios || [];

var colorPalette = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4'];

function getBarColor(val, baseVal) {
  if (baseVal == null || val == null) return '#8b8fa3';
  var diff = val - baseVal;
  if (Math.abs(diff) < 1e-9) return '#8b8fa3';
  var ratio = baseVal !== 0 ? diff / Math.abs(baseVal) : diff;
  if (ratio > 0) return '#91cc75';
  return '#ee6666';
}

var series = scenarioNames.map(function(scenarioName, si) {
  var colors = data.metrics.map(function(m) {
    var item = m.values.find(function(v) { return v.scenario === scenarioName; });
    var baseItem = m.values.find(function(v) { return v.scenario === '基准' || v.isBaseline; });
    return getBarColor(item ? item.value : null, baseItem ? baseItem.value : null);
  });
  var chartData = data.metrics.map(function(m, mi) {
    var item = m.values.find(function(v) { return v.scenario === scenarioName; });
    return item ? item.value : null;
  });
  return {
    name: scenarioName,
    type: 'bar',
    data: chartData,
    itemStyle: { color: function(params) { return colors[params.dataIndex]; } },
    emphasis: { focus: 'series' }
  };
});

var option = {
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: function(params) {
      var res = '<b>' + params[0].name + '</b><br/>';
      params.forEach(function(p) {
        var diff = null;
        var baseVal = null;
        var baseSeries = series.find(function(s) { return s.name === '基准'; });
        if (baseSeries) { baseVal = baseSeries.data[params[0].dataIndex]; }
        if (baseVal != null && p.value != null) { diff = p.value - baseVal; }
        var diffStr = diff !== null ? ' (<span style="color:' + (diff >= 0 ? '#91cc75' : '#ee6666') + '">' + (diff >= 0 ? '+' : '') + diff.toFixed(2) + '</span>)' : '';
        res += p.marker + p.seriesName + ': <b>' + (p.value !== null ? p.value.toFixed(2) : '—') + '</b>' + diffStr + '<br/>';
      });
      return res;
    }
  },
  legend: {
    data: scenarioNames,
    textStyle: { color: '#cdd6f4' },
    top: 0
  },
  grid: { left: '3%', right: '4%', bottom: '18%', top: '12%', containLabel: true },
  xAxis: {
    type: 'category',
    data: metricNames,
    axisLabel: {
      color: '#cdd6f4',
      rotate: 30,
      interval: 0,
      fontSize: 11,
      formatter: function(val) {
        return val.length > 10 ? val.substring(0, 10) + '...' : val;
      }
    },
    axisLine: { lineStyle: { color: '#45475a' } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#cdd6f4' },
    splitLine: { lineStyle: { color: '#2a3050', type: 'dashed' } },
    axisLine: { lineStyle: { color: '#45475a' } }
  },
  dataZoom: [
    { type: 'slider', bottom: 10, height: 20, textStyle: { color: '#cdd6f4' } },
    { type: 'inside' }
  ],
  series: series
};

chart.setOption(option);
window.addEventListener('resize', function() { chart.resize(); });
</script>
</body>
</html>"""
    return part1 + metrics_json + part2
