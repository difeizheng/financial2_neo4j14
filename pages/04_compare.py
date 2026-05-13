"""Page 4: Snapshot comparison — enhanced with tabs, heatmap, export."""
from __future__ import annotations

import json
import os
import sys
import tempfile

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.snapshot import load_snapshot, diff_snapshots
from financial_kg.viz.propagation_graph import build_propagation_data
from financial_kg.viz.echarts_template import render_propagation_html
from financial_kg.viz.compare_viz import (
    compute_change_summary,
    build_indicator_change_chart,
    render_indicator_chart_html,
    build_heatmap_data,
    render_heatmap_html,
    export_diff_report_excel,
)
from financial_kg.engine.excel_export import export_modified_excel, find_original_excel

st.title("快照对比")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]

@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)

graph = _load(task.id, task.output_dir)

# ── Quick compare latest two ──────────────────────────────────────────────────
snaps = db.list_snapshots(task.id)

if "locked_baseline" not in st.session_state:
    st.session_state["locked_baseline"] = None

if "locked_baseline_task" not in st.session_state:
    st.session_state["locked_baseline_task"] = None

# If task changed and baseline was locked to a different task, reset lock
if st.session_state["locked_baseline_task"] != task.id:
    st.session_state["locked_baseline"] = None
    st.session_state["locked_baseline_task"] = None

# ── Snapshot Selection ────────────────────────────────────────────────────────
if len(snaps) < 2:
    st.info("该任务快照不足 2 个，请先在「参数重算」页面创建快照。")
    st.stop()

snap_options = {f"{s.name} ({s.created_at[:19]})": s for s in snaps}
snap_labels = list(snap_options.keys())
oldest_label = snap_labels[0]
latest_label = snap_labels[-1]

# Initialize A/B selection in session_state on first render per task
_sel_key_a = f"_sel_label_a_{task.id}"
_sel_key_b = f"_sel_label_b_{task.id}"
if _sel_key_a not in st.session_state:
    st.session_state[_sel_key_a] = oldest_label  # default A = oldest (baseline)
if _sel_key_b not in st.session_state:
    st.session_state[_sel_key_b] = latest_label  # default B = latest (comparison)

# ── Button handlers (run BEFORE selectbox render, mutate session_state) ───────
if snaps:
    if st.button("对比最新两个快照", use_container_width=False):
        if len(snaps) >= 2:
            sorted_snaps = sorted(snaps, key=lambda s: s.created_at, reverse=True)
            st.session_state[_sel_key_a] = f"{sorted_snaps[1].name} ({sorted_snaps[1].created_at[:19]})"  # older = baseline
            st.session_state[_sel_key_b] = f"{sorted_snaps[0].name} ({sorted_snaps[0].created_at[:19]})"  # newer = comparison
            st.session_state["_run_auto_diff"] = True
        else:
            st.info("至少需要 2 个快照")

# Swap: exchange session_state values so selectboxes update on re-render
if st.button("⇄", help="交换 A/B"):
    tmp = st.session_state[_sel_key_a]
    st.session_state[_sel_key_a] = st.session_state[_sel_key_b]
    st.session_state[_sel_key_b] = tmp
    st.session_state["_run_swap_diff"] = True

col_lock, col_a, col_swap, col_b = st.columns([1, 5, 1, 5])

with col_lock:
    lock_baseline = st.checkbox("🔒 锁定基准", key="lock_cb")

# Initialize locked baseline
if lock_baseline and st.session_state["locked_baseline"] is None:
    st.session_state["locked_baseline"] = st.session_state[_sel_key_a]
    st.session_state["locked_baseline_task"] = task.id
elif not lock_baseline:
    st.session_state["locked_baseline"] = None
    st.session_state["locked_baseline_task"] = None

with col_a:
    if st.session_state["locked_baseline"]:
        label_a = st.selectbox("快照 A（基准，已锁定）", [st.session_state["locked_baseline"]])
    else:
        label_a = st.selectbox("快照 A（基准）", snap_labels, index=snap_labels.index(st.session_state[_sel_key_a]))

with col_b:
    label_b = st.selectbox("快照 B（对比）", snap_labels, index=snap_labels.index(st.session_state[_sel_key_b]))

# Sync selectbox changes back to session_state
st.session_state[_sel_key_a] = label_a
st.session_state[_sel_key_b] = label_b

# ── Execute diff ──────────────────────────────────────────────────────────────
if st.button("执行对比", type="primary"):
    rec_a = snap_options[label_a]
    rec_b = snap_options[label_b]

    if rec_a.id == rec_b.id:
        st.warning("请选择两个不同的快照")
        st.stop()

    with st.spinner("对比中..."):
        snap_a = load_snapshot(rec_a.filepath)
        snap_b = load_snapshot(rec_b.filepath)
        diff = diff_snapshots(snap_a, snap_b, graph)

    st.session_state["diff"] = diff
    st.session_state["diff_task_id"] = task.id
    st.session_state["snap_b_values"] = snap_b.values
    st.session_state.pop("prop_html", None)

# Auto diff triggered by "latest two" button
if st.session_state.pop("_run_auto_diff", False):
    auto_a = st.session_state.get(_sel_key_a, "")
    auto_b = st.session_state.get(_sel_key_b, "")
    if auto_a and auto_b and auto_a in snap_options and auto_b in snap_options:
        rec_a = snap_options[auto_a]
        rec_b = snap_options[auto_b]
        with st.spinner("对比中..."):
            snap_a = load_snapshot(rec_a.filepath)
            snap_b = load_snapshot(rec_b.filepath)
            diff = diff_snapshots(snap_a, snap_b, graph)
        st.session_state["diff"] = diff
        st.session_state["diff_task_id"] = task.id
        st.session_state["snap_b_values"] = snap_b.values
        st.session_state.pop("prop_html", None)

# Swap diff
if st.session_state.pop("_run_swap_diff", False):
    sw_a = st.session_state.get(_sel_key_a, "")
    sw_b = st.session_state.get(_sel_key_b, "")
    if sw_a and sw_b and sw_a in snap_options and sw_b in snap_options:
        rec_a = snap_options[sw_a]
        rec_b = snap_options[sw_b]
        with st.spinner("对比中..."):
            snap_a = load_snapshot(rec_a.filepath)
            snap_b = load_snapshot(rec_b.filepath)
            diff = diff_snapshots(snap_a, snap_b, graph)
        st.session_state["diff"] = diff
        st.session_state["diff_task_id"] = task.id
        st.session_state["snap_b_values"] = snap_b.values
        st.session_state.pop("prop_html", None)

# ── Show diff results ─────────────────────────────────────────────────────────
diff = st.session_state.get("diff")
if diff is None or st.session_state.get("diff_task_id") != task.id:
    st.stop()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_summary, tab_heatmap, tab_cells, tab_prop, tab_export = st.tabs([
    "汇总分析",
    "热力图",
    "变化明细",
    "传播图",
    "导出",
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: 汇总分析
# ══════════════════════════════════════════════════════════════════════════════
with tab_summary:
    c1, c2, c3 = st.columns(3)
    c1.metric("变化单元格数", diff.summary["total_changed_cells"])
    c2.metric("受影响 Indicator 数", diff.summary["total_changed_indicators"])
    c3.metric("涉及 Sheet 数", len(diff.summary["sheets_affected"]))

    if diff.summary["sheets_affected"]:
        st.write("涉及 Sheet：", "、".join(diff.summary["sheets_affected"]))

    if diff.changed_cells:
        summary = compute_change_summary(diff, graph)

        st.subheader("变更摘要")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("总增加量", f"{summary['total_increase']:,.2f}")
        r2.metric("总减少量", f"{summary['total_decrease']:,.2f}")
        r3.metric("最大变化单元格", summary["max_magnitude_cell"][:30])
        r4.metric("最大变化幅度", f"{summary['max_magnitude']:,.2f}")

        st.subheader("影响分布")
        ic1, ic2 = st.columns(2)
        ic1.metric("关键单元格", summary["critical_count"])
        ic2.metric("普通单元格", summary["normal_count"])

        if summary["sheets_ranking"]:
            st.subheader("Sheet 变更排行")
            sheet_rows = [{"Sheet": s, "变化数": c} for s, c in summary["sheets_ranking"]]
            st.bar_chart(sheet_rows, x="Sheet", y="变化数", horizontal=True)

        if summary["top_indicators"]:
            st.subheader("Top 10 受影响 Indicator")
            ind_rows = [{"Indicator": n, "变化单元格数": c} for n, c in summary["top_indicators"]]
            st.bar_chart(ind_rows, x="Indicator", y="变化单元格数", horizontal=True)

        # Indicator chart
        if st.button("查看指标变化图表", key="show_ind_chart"):
            chart_data = build_indicator_change_chart(diff, graph)
            html = render_indicator_chart_html(chart_data)
            components.html(html, height=520, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: 热力图
# ══════════════════════════════════════════════════════════════════════════════
with tab_heatmap:
    view_mode = st.radio("视图模式", ["指标聚合图", "单元格热力图"], horizontal=True)

    if view_mode == "指标聚合图":
        chart_data = build_indicator_change_chart(diff, graph)
        if chart_data["items"]:
            html = render_indicator_chart_html(chart_data)
            components.html(html, height=520, scrolling=False)
        else:
            st.info("无关联 indicator 的变化单元格")

    else:
        sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})
        selected_sheet = st.selectbox("选择 Sheet", sheets)
        if selected_sheet:
            hdata = build_heatmap_data(graph, diff, sheet_name=selected_sheet)
            html = render_heatmap_html(hdata, sheet_name=selected_sheet)
            components.html(html, height=620, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: 变化明细
# ══════════════════════════════════════════════════════════════════════════════
with tab_cells:
    all_sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})

    st.caption(f"共 {len(diff.changed_cells)} 条变化")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        selected_sheets = st.multiselect("按 Sheet 筛选", all_sheets, default=[])
    with col_f2:
        search_kw = st.text_input("搜索", placeholder="Cell ID / Sheet / Indicator / 值")

    filtered = diff.changed_cells
    if selected_sheets:
        filtered = [c for c in filtered if c.get("sheet") in selected_sheets]
    if search_kw:
        kw = search_kw.lower()
        filtered = [
            c for c in filtered
            if kw in c["id"].lower()
            or kw in c.get("sheet", "").lower()
            or kw in c.get("indicator_name", "").lower()
            or kw in str(c.get("old", "")).lower()
            or kw in str(c.get("new", "")).lower()
        ]

    if not filtered:
        st.info("无匹配的变化单元格")
    else:
        rows = [
            {
                "Cell ID": c["id"],
                "Sheet": c.get("sheet", ""),
                "旧值": c.get("old"),
                "新值": c.get("new"),
                "变化量": c.get("change_magnitude", 0),
                "方向": "↑ 增加" if c.get("direction") == "increase" else "↓ 减少",
                "公式": c.get("formula") or "",
                "Indicator": c.get("indicator_name", ""),
            }
            for c in filtered
        ]
        st.dataframe(rows, use_container_width=True, height=500)
        if search_kw or selected_sheets:
            st.caption(f"筛选结果：{len(rows)} / {len(diff.changed_cells)} 条")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: 传播图 (保留现有功能)
# ══════════════════════════════════════════════════════════════════════════════
with tab_prop:
    cell_search = st.text_input("搜索传播起点", placeholder="输入 Cell ID、Sheet 名或值...")
    if cell_search:
        kw = cell_search.lower()
        candidates = [
            c for c in diff.changed_cells
            if kw in c["id"].lower()
            or kw in c.get("sheet", "").lower()
            or kw in str(c.get("old", "")).lower()
            or kw in str(c.get("new", "")).lower()
        ]
    else:
        candidates = diff.changed_cells

    cell_options = {
        f"{c['id']}  ({c['sheet']})  {c['old']} → {c['new']}": c["id"]
        for c in candidates[:500]
    }
    if not cell_options:
        st.warning("无匹配的变化单元格，请调整搜索条件")
        st.stop()
    root_id = cell_options[st.selectbox("选择传播起点", list(cell_options.keys()))]
    if cell_search and len(candidates) > 500:
        st.caption(f"匹配 {len(candidates)} 个，显示前 500 个")

    col_d, col_s = st.columns(2)
    max_depth = col_d.slider("最大传播深度", 1, 15, 8)
    max_nodes = col_s.slider("最大节点数", 100, 2000, 500, 100)

    if st.button("生成传播图"):
        with st.spinner("构建传播图..."):
            data = build_propagation_data(graph, diff, root_id, max_depth, max_nodes)
            html = render_propagation_html(
                json.dumps(data, ensure_ascii=False, default=str)
            )
        st.session_state["prop_html"] = html
        st.session_state["prop_truncated"] = data["stats"]["truncated"]
        st.session_state["prop_nodes"] = data["stats"]["total_nodes"]

    if "prop_html" in st.session_state:
        if st.session_state.get("prop_truncated"):
            st.warning(f"图谱已截断至 {st.session_state['prop_nodes']} 个节点（下游更多）")
        components.html(st.session_state["prop_html"], height=780, scrolling=False)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 5: 导出
# ══════════════════════════════════════════════════════════════════════════════
with tab_export:
    # ── 文件来源 ─────────────────────────────────────────────────────
    original_path = find_original_excel(task.id, task.output_dir)
    has_original = original_path is not None

    if has_original:
        st.success(f"已找到原始文件：{original_path}")
    else:
        st.warning("未自动找到原始 Excel，请手动上传")

    uploaded_original = None
    if not has_original:
        uploaded_original = st.file_uploader("上传原始 Excel 文件", type=["xlsx"], key="compare_original")

    can_export = has_original or uploaded_original

    # 预计算公式单元格集合
    formula_ids = {cid for cid, cell in graph.cells.items() if cell.formula_raw}

    st.subheader("导出修改后 Excel")

    col_export1, col_export2 = st.columns(2)

    with col_export1:
        if st.button("导出全量值", type="primary", use_container_width=True,
                     disabled=not can_export, help="所有单元格覆盖为快照值，公式变为常数"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_orig:
                if uploaded_original:
                    uploaded_original.seek(0)
                    tmp_orig.write(uploaded_original.read())
                else:
                    with open(original_path, "rb") as f:
                        tmp_orig.write(f.read())
                tmp_orig_path = tmp_orig.name

            snap_b_values = st.session_state.get("snap_b_values", {})
            out_path = os.path.join(task.output_dir, f"{task.id}_modified.xlsx")

            with st.spinner("导出中..."):
                export_modified_excel(tmp_orig_path, snap_b_values, out_path)
                os.unlink(tmp_orig_path)

            with open(out_path, "rb") as f:
                st.download_button(
                    "下载 全量值 Excel",
                    data=f,
                    file_name=f"{task.filename.rsplit('.', 1)[0]}_modified.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_all_values",
                )

    with col_export2:
        if st.button("保留公式导出", type="primary", use_container_width=True,
                     disabled=not can_export, help="公式单元格保留原公式，只覆盖常量值"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_orig:
                if uploaded_original:
                    uploaded_original.seek(0)
                    tmp_orig.write(uploaded_original.read())
                else:
                    with open(original_path, "rb") as f:
                        tmp_orig.write(f.read())
                tmp_orig_path = tmp_orig.name

            snap_b_values = st.session_state.get("snap_b_values", {})
            out_path = os.path.join(task.output_dir, f"{task.id}_modified_with_formulas.xlsx")

            with st.spinner("导出中..."):
                export_modified_excel(
                    tmp_orig_path, snap_b_values, out_path,
                    formula_cell_ids=formula_ids,
                )
                os.unlink(tmp_orig_path)

            with open(out_path, "rb") as f:
                st.download_button(
                    "下载 保留公式 Excel",
                    data=f,
                    file_name=f"{task.filename.rsplit('.', 1)[0]}_with_formulas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_with_formulas",
                )

    st.caption(
        f"公式单元格: {len(formula_ids):,} 个，"
        f"常量单元格: {len(graph.cells) - len(formula_ids):,} 个。"
        f"保留公式导出只覆盖 {len(graph.cells) - len(formula_ids):,} 个常量单元格。"
    )

    st.divider()
    st.subheader("导出差异报告")
    st.caption("生成 Excel 格式的差异报告，含汇总、变化明细、受影响 Indicator。")

    if st.button("导出差异报告", type="secondary"):
        report_path = os.path.join(task.output_dir, f"{task.id}_diff_report.xlsx")
        with st.spinner("生成中..."):
            export_diff_report_excel(diff, graph, report_path)

        with open(report_path, "rb") as f:
            st.download_button(
                "下载差异报告",
                data=f,
                file_name=f"{task.filename.rsplit('.', 1)[0]}_diff_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
