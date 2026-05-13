"""Page 2: Interactive graph explorer — hierarchical navigation."""
from __future__ import annotations
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.viz.graph_viz import (
    build_cell_subgraph,
    build_indicator_cell_graph,
    build_indicator_subgraph,
    build_table_graph,
)
from financial_kg.viz.echarts_graph import (
    build_cell_subgraph_data,
    build_indicator_cell_graph_data,
    build_indicator_subgraph_data,
    build_table_graph_data,
    build_indicator_graph_data,
)
from financial_kg.viz.echarts_template import render_graph_html
import json

st.set_page_config(layout="wide")
st.title("🔍 图谱浏览")

# ── Task selector ─────────────────────────────────────────────────────────────
db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务，请先在「上传解析」页面上传 Excel。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]


@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load(task.id, task.output_dir)
stats = graph.stats()

# ── Overview metrics ──────────────────────────────────────────────────────────
m_cols = st.columns(6)
m_cols[0].metric("Sheets", len(stats["sheets"]))
m_cols[1].metric("Tables", stats["total_tables"])
m_cols[2].metric("Indicators", stats["total_indicators"])
m_cols[3].metric("Cells", stats["total_cells"])
m_cols[4].metric("公式 Cells", stats["formula_cells"])
unlinked = stats.get("unlinked_cells", 0)
m_cols[5].metric("未关联 Table", f"{unlinked:,}", delta=f"{unlinked/stats['total_cells']*100:.1f}%" if stats["total_cells"] else "")

st.divider()

# ── Navigation state ──────────────────────────────────────────────────────────
_NAV_KEY = f"nav_{task.id}"
if _NAV_KEY not in st.session_state:
    st.session_state[_NAV_KEY] = {"sheet": None, "table": None, "indicator": None, "cell": None}

nav = st.session_state[_NAV_KEY]

# ── Render engine toggle ──────────────────────────────────────────────────────
_ENGINE_KEY = f"viz_engine_{task.id}"
if _ENGINE_KEY not in st.session_state:
    st.session_state[_ENGINE_KEY] = "echarts"


def _render_html(path: str, height: int = 640) -> None:
    with open(path, encoding="utf-8") as f:
        components.html(f.read(), height=height, scrolling=False)


def _render_echarts(data: dict, height: int = 640, layout: str = "force") -> None:
    html = render_graph_html(json.dumps(data, ensure_ascii=False, default=str), height=f"{height}px", default_layout=layout)
    components.html(html, height=height, scrolling=False)


def _render_graph(pyvis_builder, data_builder, *args, height: int = 640, layout: str = "force", **kwargs):
    engine = st.session_state[_ENGINE_KEY]
    if engine == "pyvis":
        _render_html(pyvis_builder(*args, **kwargs), height=height)
    else:
        _render_echarts(data_builder(*args, **kwargs), height=height, layout=layout)


def _clear_below(level: str):
    """Clear navigation below the given level."""
    if level == "sheet":
        nav.update({"table": None, "indicator": None, "cell": None})
    elif level == "table":
        nav.update({"indicator": None, "cell": None})
    elif level == "indicator":
        nav["cell"] = None


def _navigate_to(level: str, value):
    """Navigate to a specific level and rerun."""
    if level == "sheet":
        nav.update({"sheet": value, "table": None, "indicator": None, "cell": None})
    elif level == "table":
        nav.update({"table": value, "indicator": None, "cell": None})
    elif level == "indicator":
        nav.update({"indicator": value, "cell": None})
    elif level == "cell":
        nav["cell"] = value
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("渲染引擎")
st.session_state[_ENGINE_KEY] = st.sidebar.radio(
    "选择渲染引擎",
    ["pyvis", "echarts"],
    format_func=lambda x: "Pyvis (vis.js)" if x == "pyvis" else "ECharts (可切换布局)",
    index=0 if st.session_state[_ENGINE_KEY] == "pyvis" else 1,
    label_visibility="collapsed",
)

st.sidebar.header("层级导航")
max_nodes = st.sidebar.slider("最大节点数", 50, 2000, 500, 50)

# Search box
st.sidebar.header("搜索")
search_query = st.sidebar.text_input("搜索指标名 / Cell ID / 表名", placeholder="输入关键词...")

if search_query:
    q = search_query.lower()
    # Search indicators
    matched_inds = [ind for ind in graph.indicators.values() if q in ind.name.lower() or q in ind.id.lower()]
    # Search tables
    matched_tbls = [tbl for tbl in graph.tables.values() if q in tbl.name.lower() or q in tbl.id.lower()]
    # Search cells
    matched_cells = [c for c in graph.cells.values() if q in c.id.lower()]

    if matched_inds:
        st.sidebar.caption(f"指标 ({len(matched_inds)})")
        for ind in matched_inds[:20]:
            label = f"{ind.name} ({ind.sheet})"
            if st.sidebar.button(label, key=f"search_ind_{ind.id}", use_container_width=True):
                _navigate_to("indicator", ind.id)
        if len(matched_inds) > 20:
            st.sidebar.caption(f"... 及其他 {len(matched_inds) - 20} 个")

    if matched_tbls:
        st.sidebar.caption(f"表 ({len(matched_tbls)})")
        for tbl in matched_tbls[:20]:
            label = f"{tbl.name} ({tbl.sheet})"
            if st.sidebar.button(label, key=f"search_tbl_{tbl.id}", use_container_width=True):
                _navigate_to("table", tbl.id)
        if len(matched_tbls) > 20:
            st.sidebar.caption(f"... 及其他 {len(matched_tbls) - 20} 个")

    if matched_cells:
        st.sidebar.caption(f"Cell ({len(matched_cells)})")
        for c in matched_cells[:20]:
            short_id = c.id.split("_", 1)[-1] if "_" in c.id else c.id
            if st.sidebar.button(short_id, key=f"search_cell_{c.id}", use_container_width=True):
                _navigate_to("cell", c.id)
        if len(matched_cells) > 20:
            st.sidebar.caption(f"... 及其他 {len(matched_cells) - 20} 个")

    if not matched_inds and not matched_tbls and not matched_cells:
        st.sidebar.info("无匹配结果")

# Sheet selector
sheets = sorted(stats["sheets"])
sheet_opts = ["(选择 Sheet)"] + sheets
sheet_idx = (sheets.index(nav["sheet"]) + 1) if nav["sheet"] in sheets else 0
new_sheet_raw = st.sidebar.selectbox("Sheet", sheet_opts, index=sheet_idx)
new_sheet = None if new_sheet_raw == "(选择 Sheet)" else new_sheet_raw
if new_sheet != nav["sheet"]:
    _navigate_to("sheet", new_sheet)

if nav["sheet"]:
    tables_in_sheet = [t for t in graph.tables.values() if t.sheet == nav["sheet"]]
    tbl_names_map = {t.name[:30]: t.id for t in tables_in_sheet}
    tbl_opts = ["(选择 Table)"] + list(tbl_names_map.keys())
    tbl_idx = (list(tbl_names_map.values()).index(nav["table"]) + 1) if nav["table"] in tbl_names_map.values() else 0
    new_tbl_name = st.sidebar.selectbox("Table", tbl_opts, index=tbl_idx)
    new_tbl = tbl_names_map.get(new_tbl_name) if new_tbl_name != "(选择 Table)" else None
    if new_tbl != nav["table"]:
        _navigate_to("table", new_tbl)

if nav["table"]:
    tbl_obj = graph.tables.get(nav["table"])
    inds_in_table = [graph.indicators[i] for i in (tbl_obj.indicator_ids if tbl_obj else []) if i in graph.indicators]
    ind_names_map = {i.name[:30]: i.id for i in inds_in_table}
    ind_opts = ["(选择 Indicator)"] + list(ind_names_map.keys())
    ind_idx = (list(ind_names_map.values()).index(nav["indicator"]) + 1) if nav["indicator"] in ind_names_map.values() else 0
    new_ind_name = st.sidebar.selectbox("Indicator", ind_opts, index=ind_idx)
    new_ind = ind_names_map.get(new_ind_name) if new_ind_name != "(选择 Indicator)" else None
    if new_ind != nav["indicator"]:
        _navigate_to("indicator", new_ind)

if nav["indicator"]:
    ind_obj = graph.indicators.get(nav["indicator"])
    cells_in_ind = [graph.cells[c] for c in (ind_obj.cell_ids if ind_obj else []) if c in graph.cells]
    cell_ids = [c.id for c in cells_in_ind]
    cell_opts = ["(选择 Cell)"] + cell_ids
    cell_idx = (cell_ids.index(nav["cell"]) + 1) if nav["cell"] in cell_ids else 0
    new_cell = st.sidebar.selectbox("Cell", cell_opts, index=cell_idx)
    if new_cell == "(选择 Cell)":
        new_cell = None
    if new_cell != nav["cell"]:
        _navigate_to("cell", new_cell)

# ── Main area ─────────────────────────────────────────────────────────────────

# Breadcrumb
st.subheader("导航路径")
bc_cols = st.columns(4)
levels = [
    ("Sheet", nav["sheet"]),
    ("Table", nav["table"]),
    ("Indicator", nav["indicator"]),
    ("Cell", nav["cell"]),
]
for i, (level_name, level_val) in enumerate(levels):
    with bc_cols[i]:
        display = level_val if level_val else f"未选择 {level_name}"
        if level_val:
            if st.button(display, key=f"bc_{level_name.lower()}", use_container_width=True):
                if i == 0:
                    _navigate_to("sheet", None)
                elif i == 1:
                    _navigate_to("table", None)
                elif i == 2:
                    _navigate_to("indicator", None)
                elif i == 3:
                    _navigate_to("cell", None)
        else:
            st.caption(display)

st.divider()

# Cell level
if nav["cell"]:
    cell = graph.cells[nav["cell"]]
    st.subheader(f"Cell: {nav['cell']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("值", str(cell.value))
    c2.metric("上游依赖", len(cell.dependencies))
    c3.metric("下游被依赖", len(cell.dependents))
    st.write(f"**公式**: `{cell.formula_raw or '无'}`")
    depth = st.slider("展开深度", 1, 5, 2)
    _render_graph(
        build_cell_subgraph, build_cell_subgraph_data,
        graph, nav["cell"],
        depth=depth,
        layout="layered",
    )

# Indicator level
elif nav["indicator"]:
    ind = graph.indicators[nav["indicator"]]
    st.subheader(f"Indicator: {ind.name}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("分类", ind.category or "—")
    c2.metric("单位", ind.unit or "—")
    val_str = ind.display_value if ind.display_value is not None else str(ind.summary_value or "—")
    c3.metric("汇总值", val_str)
    c4.metric("时间序列点数", len(ind.time_series))
    if ind.formula_readable:
        st.write(f"**公式**: `{ind.formula_readable}`")
    if ind.description:
        st.caption(ind.description)

    ind_obj = graph.indicators.get(nav["indicator"])
    cells_in_ind = [graph.cells[c] for c in (ind_obj.cell_ids if ind_obj else []) if c in graph.cells]
    if cells_in_ind:
        st.subheader(f"Cell 列表（{len(cells_in_ind)} 个）")
        rows = [
            {
                "ID": c.id,
                "值": c.value,
                "公式": c.formula_raw or "",
                "上游依赖": len(c.dependencies),
                "下游被依赖": len(c.dependents),
            }
            for c in cells_in_ind
        ]
        st.dataframe(rows, use_container_width=True)

    _render_graph(
        build_indicator_cell_graph, build_indicator_cell_graph_data,
        graph, nav["indicator"],
        layout="concentric",
    )

# Table level
elif nav["table"]:
    tbl = graph.tables[nav["table"]]
    st.subheader(f"Table: {tbl.name}")
    c1, c2, c3 = st.columns(3)
    c1.metric("类型", tbl.table_type)
    row_range = f"{tbl.data_row_range[0]}–{tbl.data_row_range[-1]}" if tbl.data_row_range else "—"
    c2.metric("行范围", row_range)
    c3.metric("Indicator 数", len(tbl.indicator_ids))

    inds_in_table = [graph.indicators[i] for i in tbl.indicator_ids if i in graph.indicators]
    if inds_in_table:
        st.subheader(f"Indicator 列表（{len(inds_in_table)} 个）")
        rows = []
        for ind in inds_in_table:
            val_str = ind.display_value if ind.display_value is not None else (
                f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
                else str(ind.summary_value or "")
            )
            rows.append({
                "名称": ind.name,
                "分类": ind.category or "",
                "单位": ind.unit or "",
                "汇总值": val_str,
                "公式": ind.formula_readable or "",
                "时间序列点数": len(ind.time_series),
            })
        st.dataframe(rows, use_container_width=True)

    _render_graph(
        build_indicator_subgraph, build_indicator_subgraph_data,
        graph, nav["table"],
        layout="layered",
    )

# Sheet level
elif nav["sheet"]:
    st.subheader(f"Sheet: {nav['sheet']}")
    tables_in_sheet = [t for t in graph.tables.values() if t.sheet == nav["sheet"]]
    unlinked_by_sheet = graph.get_unlinked_cells()
    orphan_cells = len(unlinked_by_sheet.get(nav["sheet"], []))

    if tables_in_sheet:
        st.subheader(f"Table 列表（{len(tables_in_sheet)} 个）")
        rows = []
        for tbl in tables_in_sheet:
            header_rows = sorted(tbl.header_rows)
            if not header_rows:
                header_display = "—"
            elif len(header_rows) == 1:
                header_display = str(header_rows[0])
            else:
                header_display = f"{header_rows[0]}–{header_rows[-1]}"
            ts_cols = len(tbl.time_period_labels)
            rows.append({
                "名称": tbl.name,
                "类型": tbl.table_type,
                "行范围": f"{tbl.data_row_range[0]}–{tbl.data_row_range[1]}" if tbl.data_row_range else "—",
                "表头行": header_display,
                "时间序列列": ts_cols if ts_cols else "—",
                "Indicator": len(tbl.indicator_ids),
                "上游 Table": len(tbl.fed_by),
                "下游 Table": len(tbl.feeds_into),
            })
        st.dataframe(rows, use_container_width=True)

    if orphan_cells > 0:
        st.caption(f"未归属 Cell（无 Indicator）: {orphan_cells} 个")

    _render_graph(
        build_table_graph, build_table_graph_data,
        graph, nav["sheet"],
        layout="concentric",
    )

# Overview (no selection) — full graph visualization
else:
    st.subheader("全量图谱概览")
    st.caption("力导向布局，4 秒后自动冻结。拖拽可微调节点位置。")
    _render_echarts(
        build_indicator_graph_data(graph, max_nodes=max_nodes),
        height=800,
        layout="force",
    )

    # Quick stats
    st.divider()
    st.subheader("按 Sheet 统计")
    sheet_rows = []
    for sheet_name in sorted(stats["sheets"]):
        tbl_count = sum(1 for t in graph.tables.values() if t.sheet == sheet_name)
        ind_count = sum(1 for i in graph.indicators.values() if i.sheet == sheet_name)
        sheet_rows.append({"Sheet": sheet_name, "Table": tbl_count, "Indicator": ind_count})
    st.dataframe(sheet_rows, use_container_width=True)
