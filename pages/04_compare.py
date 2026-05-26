"""Page 4: Snapshot comparison — card selector, derived metrics, change matrix."""
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
    build_heatmap_data,
    render_heatmap_html,
    export_diff_report_excel,
)
from financial_kg.engine.excel_export import export_modified_excel, find_original_excel
from financial_kg.engine.derived_metrics import DerivedMetrics

st.set_page_config(layout="wide")
st.title("📊 快照对比")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("任务", list(task_options.keys()), label_visibility="collapsed")
task = task_options[selected_label]


@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load(task.id, task.output_dir)

# ── Snapshot selection (card-style) ───────────────────────────────────────────
snaps = db.list_snapshots(task.id)

if len(snaps) < 2:
    st.info("该任务快照不足 2 个，请先在「参数工作台」页面创建快照。")
    st.stop()

snap_map = {f"{s.name} ({s.created_at[:19]})": s for s in snaps}
snap_labels = list(snap_map.keys())

# Session state for A/B selection
_key_a = f"cmp_a_{task.id}"
_key_b = f"cmp_b_{task.id}"
if _key_a not in st.session_state:
    st.session_state[_key_a] = snap_labels[0]
if _key_b not in st.session_state:
    st.session_state[_key_b] = snap_labels[-1]

# Card-style selector
st.subheader("选择对比快照")
card_cols = st.columns([4, 1, 4, 1])

with card_cols[0]:
    label_a = st.selectbox("← 基准快照 A", snap_labels, index=snap_labels.index(st.session_state[_key_a]))
    st.session_state[_key_a] = label_a

with card_cols[1]:
    st.write("")  # spacer
    st.write("")
    st.write("")
    if st.button("⇄ 交换", use_container_width=True, key="swap_snapshots"):
        tmp = st.session_state[_key_a]
        st.session_state[_key_a] = st.session_state[_key_b]
        st.session_state[_key_b] = tmp
        st.rerun()

with card_cols[2]:
    label_b = st.selectbox("对比快照 B →", snap_labels, index=snap_labels.index(st.session_state[_key_b]))
    st.session_state[_key_b] = label_b

# Quick actions
quick_row = st.columns([2, 2, 10])
with quick_row[0]:
    if st.button("📌 锁定最新为基准", use_container_width=True, key="lock_latest_a"):
        st.session_state[_key_a] = snap_labels[-2] if len(snap_labels) >= 2 else snap_labels[-1]
        st.session_state[_key_b] = snap_labels[-1]
        st.rerun()
with quick_row[1]:
    if st.button("📌 锁定最旧为基准", use_container_width=True, key="lock_oldest_a"):
        st.session_state[_key_a] = snap_labels[0]
        st.session_state[_key_b] = snap_labels[-1]
        st.rerun()

st.divider()

# ── Execute diff ──────────────────────────────────────────────────────────────
if st.button("执行对比", type="primary", use_container_width=True):
    rec_a = snap_map[label_a]
    rec_b = snap_map[label_b]

    if rec_a.id == rec_b.id:
        st.warning("请选择两个不同的快照")
        st.stop()

    original_path = find_original_excel(task.id, task.output_dir)
    formula_cell_ids = {cid for cid, cell in graph.cells.items() if cell.formula_raw}

    with st.spinner("对比并生成重算 Excel..."):
        snap_a = load_snapshot(rec_a.filepath)
        snap_b = load_snapshot(rec_b.filepath)
        diff = diff_snapshots(snap_a, snap_b, graph)

        # Auto-generate recalculated Excel from original + snap_b values
        recalc_excel_path = None
        if original_path:
            safe_name = rec_b.name.replace("/", "_").replace("\\", "_").replace(":", "_")
            recalc_excel_path = os.path.join(task.output_dir, f"{task.id}_recalc_{safe_name}.xlsx")
            try:
                export_modified_excel(original_path, snap_b.values, recalc_excel_path, formula_cell_ids=formula_cell_ids)
            except Exception:
                recalc_excel_path = None

    st.session_state["diff"] = diff
    st.session_state["diff_task_id"] = task.id
    st.session_state["snap_a_values"] = snap_a.values
    st.session_state["snap_b_values"] = snap_b.values
    st.session_state["snap_a_name"] = rec_a.name
    st.session_state["snap_b_name"] = rec_b.name
    st.session_state["recalc_excel_path"] = recalc_excel_path
    st.session_state.pop("prop_html", None)

# ── Show diff results ─────────────────────────────────────────────────────────
diff = st.session_state.get("diff")
if diff is None or st.session_state.get("diff_task_id") != task.id:
    st.info("选择两个快照后点击「执行对比」显示差异")
    st.stop()

snap_a_name = st.session_state.get("snap_a_name", "A")
snap_b_name = st.session_state.get("snap_b_name", "B")
snap_b_values = st.session_state.get("snap_b_values", {})
snap_a_values = st.session_state.get("snap_a_values", {})

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_metrics, tab_matrix, tab_detail, tab_prop, tab_export = st.tabs([
    "关键指标对比",
    "变化矩阵",
    "变化明细",
    "传播图",
    "导出",
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Key metrics comparison
# ══════════════════════════════════════════════════════════════════════════════
with tab_metrics:
    # Summary metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("变化单元格数", diff.summary["total_changed_cells"])
    c2.metric("受影响 Indicator 数", diff.summary["total_changed_indicators"])
    c3.metric("涉及 Sheet 数", len(diff.summary["sheets_affected"]))

    if diff.summary["sheets_affected"]:
        st.caption("涉及 Sheet：" + "、".join(diff.summary["sheets_affected"]))

    st.divider()

    # Derived metrics comparison
    st.subheader(f"{snap_a_name} vs {snap_b_name} — 关键财务指标")

    def _compute_metrics_for_snapshot(snapshot_values, graph):
        """Compute derived metrics for a snapshot."""
        # Apply snapshot values to graph cells temporarily
        original_values = {}
        for cid, val in snapshot_values.items():
            cell = graph.cells.get(cid)
            if cell:
                original_values[cid] = cell.value
                cell.value = val

        # Collect metrics from graph indicators
        irr = None
        npv = None
        payback = None
        dscr_avg = None
        dscr_min = None

        for ind_id, ind in graph.indicators.items():
            if ind.name:
                name_lower = ind.name.lower()
                val = ind.summary_value
                if val is not None:
                    try:
                        float_val = float(val)
                    except (ValueError, TypeError):
                        continue
                    if any(k in name_lower for k in ["irr", "内部收益率"]):
                        if irr is None:
                            irr = float_val
                    elif any(k in name_lower for k in ["净现值", "npv"]):
                        if npv is None:
                            npv = float_val
                    elif any(k in name_lower for k in ["回收期", "payback"]):
                        if payback is None:
                            payback = float_val
                    elif "dscr" in name_lower:
                        if dscr_avg is None:
                            dscr_avg = float_val
                        if dscr_min is None or float_val < dscr_min:
                            dscr_min = float_val

        # Restore original values
        for cid, val in original_values.items():
            cell = graph.cells.get(cid)
            if cell:
                cell.value = val

        return DerivedMetrics(
            irr_after_tax=irr,
            npv_after_tax=npv,
            payback_period=payback,
            dscr_avg=dscr_avg,
            dscr_min=dscr_min,
        )

    # Compute metrics for both snapshots
    metrics_a = _compute_metrics_for_snapshot(snap_a_values, graph)
    metrics_b = _compute_metrics_for_snapshot(snap_b_values, graph)

    # Display comparison cards
    metric_defs = [
        ("税后IRR", "irr_after_tax", "{:.2f}%", 100, True),
        ("财务净现值", "npv_after_tax", "{:,.0f}", 1, False),
        ("投资回收期", "payback_period", "{:.2f}年", 1, True),
        ("DSCR均值", "dscr_avg", "{:.2f}", 1, False),
        ("DSCR最低值", "dscr_min", "{:.2f}", 1, False),
    ]

    n_cols = min(len(metric_defs), 5)
    metric_cols = st.columns(n_cols)

    for i, (label, attr, fmt, multiplier, lower_is_better) in enumerate(metric_defs):
        val_a = getattr(metrics_a, attr, None)
        val_b = getattr(metrics_b, attr, None)

        with metric_cols[i % n_cols]:
            row_a, row_b = st.columns(2)
            with row_a:
                st.caption(snap_a_name)
                if val_a is not None:
                    st.markdown(f"**{fmt.format(val_a * multiplier)}**")
                else:
                    st.caption("—")
            with row_b:
                st.caption(snap_b_name)
                if val_b is not None:
                    st.markdown(f"**{fmt.format(val_b * multiplier)}**")
                else:
                    st.caption("—")

            # Delta
            if val_a is not None and val_b is not None:
                delta = val_b - val_a
                pct = (delta / abs(val_a) * 100) if val_a != 0 else None
                delta_str = f"{delta:+.2f}" if multiplier == 1 else f"{delta * multiplier:+.2f}"
                if pct is not None:
                    delta_str += f" ({pct:+.1f}%)"
                # Color: green = positive change, red = negative
                if "irr" in attr or "npv" in attr or "dscr" in attr:
                    good = delta > 0
                elif "payback" in attr:
                    good = delta < 0  # shorter payback is better
                else:
                    good = True

                color = "green" if good else "red"
                st.markdown(f"<span style='color:{color};font-weight:bold;font-size:0.9em'>变化: {delta_str}</span>", unsafe_allow_html=True)
            else:
                st.caption("无法计算变化")

    st.divider()

    # Change summary
    if diff.changed_cells:
        summary = compute_change_summary(diff, graph)

        st.subheader("变更摘要")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("总增加量", f"{summary['total_increase']:,.2f}")
        r2.metric("总减少量", f"{summary['total_decrease']:,.2f}")
        r3.metric("最大变化单元格", summary["max_magnitude_cell"][:30])
        r4.metric("最大变化幅度", f"{summary['max_magnitude']:,.2f}")

        # Sheet + Indicator ranking side by side
        rank_a, rank_b = st.columns(2)
        with rank_a:
            if summary["sheets_ranking"]:
                st.caption("Sheet 变更排行")
                sheet_rows = [{"Sheet": s, "变化数": c} for s, c in summary["sheets_ranking"]]
                st.bar_chart(sheet_rows, x="Sheet", y="变化数", horizontal=True)
        with rank_b:
            if summary["top_indicators"]:
                st.caption("Top 10 受影响 Indicator")
                ind_rows = [{"Indicator": n, "变化单元格数": c} for n, c in summary["top_indicators"]]
                st.bar_chart(ind_rows, x="Indicator", y="变化单元格数", horizontal=True)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: Change matrix
# ══════════════════════════════════════════════════════════════════════════════
with tab_matrix:
    st.subheader("变化矩阵")

    # Build matrix data: rows = indicators, columns = cells with old/new/delta
    matrix_data = []
    for ind_entry in diff.affected_indicators:
        ind_id = ind_entry.get("id") if isinstance(ind_entry, dict) else ind_entry
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue

        # Find cells for this indicator that changed
        changed_cells_for_ind = [
            c for c in diff.changed_cells
            if c.get("indicator_name") == (ind.name or "")
        ]

        if not changed_cells_for_ind:
            continue

        old_summary = ind_entry.get("old_summary") if isinstance(ind_entry, dict) else None
        new_summary = ind_entry.get("new_summary") if isinstance(ind_entry, dict) else None

        def _safe_float(v):
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        va = _safe_float(old_summary)
        vb = _safe_float(new_summary)
        matrix_data.append({
            "Indicator": ind.name or ind_id,
            f"{snap_a_name}": old_summary,
            f"{snap_b_name}": new_summary,
            "变化": (vb - va) if (va is not None and vb is not None) else None,
            "变化单元格数": len(changed_cells_for_ind),
        })

    if matrix_data:
        # Filter bar
        mat_search = st.text_input("搜索 Indicator", placeholder="输入 Indicator 名称筛选", label_visibility="collapsed")

        filtered_mat = matrix_data
        if mat_search:
            kw = mat_search.lower()
            filtered_mat = [r for r in filtered_mat if kw in r["Indicator"].lower()]

        if filtered_mat:
            st.dataframe(
                filtered_mat,
                use_container_width=True,
                hide_index=True,
                height=600,
                column_config={
                    "Indicator": st.column_config.TextColumn("Indicator", width="medium"),
                    f"{snap_a_name}": st.column_config.NumberColumn(snap_a_name, width="small"),
                    f"{snap_b_name}": st.column_config.NumberColumn(snap_b_name, width="small"),
                    "变化": st.column_config.NumberColumn("变化", width="small"),
                    "变化单元格数": st.column_config.NumberColumn("变化单元格数", width="small"),
                },
            )
            st.caption(f"显示 {len(filtered_mat)} / {len(matrix_data)} 行")
        else:
            st.info("无匹配数据")
    else:
        st.info("无受影响的时间序列数据")

    # Heatmap sub-tab
    st.divider()
    st.subheader("单元格热力图")

    all_sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})
    if all_sheets:
        sheet_tabs = st.tabs(all_sheets)
        for si, sheet_name in enumerate(all_sheets):
            with sheet_tabs[si]:
                hdata = build_heatmap_data(graph, diff, sheet_name=sheet_name)
                if hdata:
                    html = render_heatmap_html(hdata, sheet_name=sheet_name)
                    components.html(html, height=520, scrolling=False)
                else:
                    st.info(f"{sheet_name} 无变化单元格")
    else:
        st.info("无变化单元格")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Propagation graph
# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Change detail (per-cell)
# ══════════════════════════════════════════════════════════════════════════════
with tab_detail:
    all_sheets = sorted({c.get("sheet", "") for c in diff.changed_cells if c.get("sheet")})

    st.caption(f"共 {len(diff.changed_cells)} 条变化")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        selected_sheets = st.multiselect("按 Sheet 筛选", all_sheets, default=[], key="detail_sheets")
    with col_f2:
        search_kw = st.text_input("搜索", placeholder="Cell ID / Sheet / Indicator / 值", key="detail_search")

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
        # Build HTML table with clickable "定位" links
        def _cell_to_ref(cid):
            parts = cid.rsplit("_", 2)
            if len(parts) != 3:
                return cid
            sheet, row, col = parts
            ref = f"{col}{row}"
            if sheet:
                ref = f"{sheet}!{ref}"
            return ref

        html_rows = ""
        for c in filtered:
            ref = _cell_to_ref(c["id"])
            old_v = c.get("old", "")
            new_v = c.get("new", "")
            chg = round(c.get("change_magnitude", 0), 6)
            direction = "↑" if c.get("direction") == "increase" else "↓"
            ind_name = c.get("indicator_name", "") or ""
            formula = c.get("formula", "") or ""
            # Escape for HTML
            ind_name = ind_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            formula = formula.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html_rows += (
                f'<tr>'
                f'<td style="font-size:11px;font-family:monospace">{c["id"]}</td>'
                f'<td style="font-size:11px">{c.get("sheet", "")}</td>'
                f'<td style="font-size:11px;text-align:right">{old_v}</td>'
                f'<td style="font-size:11px;text-align:right">{new_v}</td>'
                f'<td style="font-size:11px;text-align:right">{chg}</td>'
                f'<td style="font-size:11px">{direction}</td>'
                f'<td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{formula}</td>'
                f'<td style="font-size:11px">{ind_name}</td>'
                f'<td style="font-size:11px"><button class="loc-btn" onclick="locRef(this.getAttribute(\'data-ref\'))" data-ref="{ref}">定位</button></td>'
                f'</tr>\n'
            )

        table_html = f"""
        <style>
        .detail-tbl {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
        .detail-tbl th {{ background: #1e1e2e; color: #89b4fa; padding: 6px 8px; text-align: left; position: sticky; top: 0; }}
        .detail-tbl td {{ padding: 4px 8px; border-bottom: 1px solid #2a3050; }}
        .detail-tbl tr:hover {{ background: rgba(137,180,250,0.05); }}
        .loc-btn {{ background: #45475a; color: #cdd6f4; border: none; padding: 2px 10px; border-radius: 3px; font-size: 10px; cursor: pointer; }}
        .loc-btn:hover {{ background: #89b4fa; color: #1e1e2e; }}
        </style>
        <table class="detail-tbl">
        <thead>
        <tr>
        <th>Cell ID</th><th>Sheet</th><th>旧值</th><th>新值</th><th>变化量</th><th>方向</th><th>公式</th><th>Indicator</th><th>定位</th>
        </tr>
        </thead>
        <tbody>
        {html_rows}
        </tbody>
        </table>
        <script>
        function locRef(ref) {{
          var input = document.querySelector('input[aria-label="Excel 定位引用"]');
          if (!input) input = document.querySelector('input[placeholder*="Excel定位"]');
          if (input) {{
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(input, ref);
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.scrollIntoView({{behavior: 'smooth', block: 'center'}});
          }}
        }}
        </script>
        """
        st.markdown(table_html, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: Propagation graph
# ══════════════════════════════════════════════════════════════════════════════
with tab_prop:
    if not diff.changed_cells:
        st.info("无变化单元格，无法生成传播图")
        st.stop()

    # Default: show top 5 cells by magnitude
    sorted_by_mag = sorted(diff.changed_cells, key=lambda c: c.get("change_magnitude", 0), reverse=True)
    top5 = sorted_by_mag[:5]

    st.caption("默认显示变化最大的 5 个单元格作为传播起点，也可搜索自定义起点")

    # Quick picks
    quick_cells = st.columns(min(len(top5), 5))
    for i, c in enumerate(top5):
        with quick_cells[i]:
            cid_short = c["id"][:20] + ("…" if len(c["id"]) > 20 else "")
            mag = c.get("change_magnitude", 0)
            if st.button(f"{cid_short}\nΔ={mag:.2f}", use_container_width=True, key=f"qc_{c['id']}"):
                st.session_state["prop_root"] = c["id"]

    # Search
    cell_search = st.text_input("搜索传播起点", placeholder="输入 Cell ID、Sheet 名或值...", label_visibility="collapsed")
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

    root_id = st.session_state.get("prop_root", top5[0]["id"] if top5 else diff.changed_cells[0]["id"])

    if cell_search and candidates:
        cell_options = {
            f"{c['id']}  ({c['sheet']})  {c['old']} → {c['new']}": c["id"]
            for c in candidates[:500]
        }
        if cell_options:
            selected_label_prop = st.selectbox("选择传播起点", list(cell_options.keys()))
            root_id = cell_options[selected_label_prop]

    max_depth = st.slider("传播深度", 1, 15, 8)
    max_nodes = st.slider("最大节点数", 100, 2000, 500, 100)

    if st.button("生成传播图", type="primary"):
        with st.spinner("构建传播图..."):
            data = build_propagation_data(graph, diff, root_id, max_depth, max_nodes)
            html = render_propagation_html(json.dumps(data, ensure_ascii=False, default=str))
        st.session_state["prop_html"] = html
        st.session_state["prop_truncated"] = data["stats"]["truncated"]
        st.session_state["prop_nodes"] = data["stats"]["total_nodes"]

    if "prop_html" in st.session_state:
        if st.session_state.get("prop_truncated"):
            st.warning(f"图谱已截断至 {st.session_state['prop_nodes']} 个节点")
        components.html(st.session_state["prop_html"], height=780, scrolling=False)

        # ── Excel locate section ──
        st.divider()
        _recalc_path = st.session_state.get("recalc_excel_path")
        _orig_path = find_original_excel(task.id, task.output_dir)
        _excel_path = None

        if _recalc_path and os.path.exists(_recalc_path):
            _excel_path = _recalc_path
            snap_b_name = st.session_state.get("snap_b_name", "B")
            st.caption(f"重算文件：{_excel_path}（对应快照「{snap_b_name}」）")
        elif _orig_path and os.path.exists(_orig_path):
            _excel_path = _orig_path
            st.caption(f"原始文件：{_excel_path}（无重算文件，使用原始数据）")

        if _excel_path:
            _loc_ref = st.text_input(
                "Excel 定位引用",
                placeholder="如：参数输入表!I250（在传播图中点击节点可复制引用）",
                key="prop_excel_locate_ref",
            )
            if st.button("在 Excel 中定位", key="prop_excel_locate_btn", disabled=not _loc_ref):
                try:
                    import win32com.client
                    import pythoncom
                    pythoncom.CoInitialize()
                    try:
                        ref = _loc_ref.strip()
                        if "!" in ref:
                            sheet_name, addr = ref.split("!", 1)
                        else:
                            sheet_name, addr = None, ref
                        addr = addr.replace("$", "")
                        abs_path = os.path.abspath(_excel_path)

                        # Try WPS first, then Office Excel
                        xl = None
                        for prog_id in ["ket.Application", "Excel.Application"]:
                            try:
                                xl = win32com.client.GetActiveObject(prog_id)
                                break
                            except Exception:
                                pass

                        if xl is None:
                            for prog_id in ["ket.Application", "Excel.Application"]:
                                try:
                                    xl = win32com.client.Dispatch(prog_id)
                                    break
                                except Exception:
                                    continue

                        if xl is None:
                            raise RuntimeError("未找到 WPS 或 Office Excel")

                        xl.Visible = True

                        # Find workbook by full name using index-based iteration
                        wb = None
                        count = xl.Workbooks.Count
                        for i in range(1, count + 1):
                            try:
                                b = xl.Workbooks(i)
                                if os.path.abspath(b.FullName) == abs_path:
                                    wb = b
                                    break
                            except Exception:
                                continue

                        if wb is None:
                            wb = xl.Workbooks.Open(abs_path)

                        # Enable iterative calculation (set AFTER workbook open for WPS compatibility)
                        try:
                            # App-level (Office Excel)
                            xl.Iteration = True
                            xl.MaxIterations = 1000
                            xl.MaxChange = 1e-6
                        except Exception:
                            pass
                        try:
                            # Workbook-level (WPS)
                            wb.EnableIteration = True
                        except Exception:
                            pass
                        # Recalculate to resolve circular refs
                        try:
                            wb.RefreshAll()
                            wb.Calculate()
                        except Exception:
                            pass

                        if sheet_name:
                            try:
                                ws = wb.Sheets(sheet_name)
                            except Exception:
                                st.warning(f"未找到工作表「{sheet_name}」，已打开文件但无法定位")
                                ws = wb.ActiveSheet
                        else:
                            ws = wb.ActiveSheet
                        ws.Activate()
                        try:
                            rng = ws.Range(addr)
                            rng.Select()
                            # Highlight for visibility in large sheets
                            rng.Interior.Color = 0xFFFF00  # yellow
                            rng.Font.Bold = True
                            st.success(f"已定位到 {ref}，已标记黄色高亮（Ctrl+Z 撤销）")
                        except Exception:
                            st.warning(f"无法定位到 {addr}，已打开文件并激活工作表")
                    finally:
                        pythoncom.CoUninitialize()
                except ImportError:
                    st.error("需要安装 pywin32：pip install pywin32")
                except Exception as e:
                    st.error(f"打开 Excel 失败：{e}")
        else:
            st.caption("未找到原始 Excel 文件，Excel 定位功能不可用")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: Export
# ══════════════════════════════════════════════════════════════════════════════
with tab_export:
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
    formula_ids = {cid for cid, cell in graph.cells.items() if cell.formula_raw}

    st.subheader("导出修改后 Excel")

    # Toggle for formula preservation
    keep_formulas = st.toggle("保留公式", value=False, help="开启后公式单元格保留原公式，只覆盖常量值")

    if st.button("导出 Excel", type="primary", disabled=not can_export, use_container_width=True):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_orig:
            if uploaded_original:
                uploaded_original.seek(0)
                tmp_orig.write(uploaded_original.read())
            else:
                with open(original_path, "rb") as f:
                    tmp_orig.write(f.read())
            tmp_orig_path = tmp_orig.name

        suffix = "_with_formulas" if keep_formulas else "_modified"
        out_path = os.path.join(task.output_dir, f"{task.id}{suffix}.xlsx")

        with st.spinner("导出中..."):
            if keep_formulas:
                export_modified_excel(tmp_orig_path, snap_b_values, out_path, formula_cell_ids=formula_ids)
            else:
                export_modified_excel(tmp_orig_path, snap_b_values, out_path)
            os.unlink(tmp_orig_path)

        with open(out_path, "rb") as f:
            st.download_button(
                f"下载 {suffix}.xlsx",
                data=f,
                file_name=f"{task.filename.rsplit('.', 1)[0]}{suffix}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_export{suffix}",
            )

    st.caption(f"公式单元格: {len(formula_ids):,} 个，常量单元格: {len(graph.cells) - len(formula_ids):,} 个")

    st.divider()
    st.subheader("导出差异报告")

    if st.button("导出差异报告", type="secondary", use_container_width=True):
        report_path = os.path.join(task.output_dir, f"{task.id}_diff_report.xlsx")
        with st.spinner("生成中..."):
            export_diff_report_excel(diff, graph, report_path)

        with open(report_path, "rb") as f:
            st.download_button(
                "下载差异报告",
                data=f,
                file_name=f"{task.filename.rsplit('.', 1)[0]}_diff_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_diff_report",
            )
