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
from financial_kg.engine.derived_metrics import DerivedMetrics, deserialize_metrics

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

# Session state for A/B selection (default: B=newest, A=second-newest)
_key_a = f"cmp_a_{task.id}"
_key_b = f"cmp_b_{task.id}"
if _key_a not in st.session_state:
    st.session_state[_key_a] = snap_labels[1] if len(snap_labels) >= 2 else snap_labels[0]
if _key_b not in st.session_state:
    st.session_state[_key_b] = snap_labels[0]  # newest (created_at DESC order)

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
    st.session_state["snap_a_derived"] = snap_a.derived_metrics
    st.session_state["snap_b_derived"] = snap_b.derived_metrics
    st.session_state["snap_a_name"] = rec_a.name
    st.session_state["snap_b_name"] = rec_b.name
    st.session_state["recalc_excel_path"] = recalc_excel_path
    st.session_state.pop("prop_html", None)

# ══════════════════════════════════════════════════════════════════════════════
# Fragment: tab area — isolated rerun so tab state survives widget interactions
# ══════════════════════════════════════════════════════════════════════════════

@st.fragment
def _render_compare_tabs(graph, task_id: str, task_output_dir: str, task_filename: str):
    diff = st.session_state.get("diff")
    _has_diff = diff is not None and st.session_state.get("diff_task_id") == task_id

    snap_a_name = st.session_state.get("snap_a_name", "A")
    snap_b_name = st.session_state.get("snap_b_name", "B")
    snap_b_values = st.session_state.get("snap_b_values", {})
    snap_a_values = st.session_state.get("snap_a_values", {})

    # ── Excel target file choice ──
    if _has_diff:
        _orig_path = find_original_excel(task_id, task_output_dir)
        _recalc_path = st.session_state.get("recalc_excel_path")
        _target_opts = {}
        if _orig_path and os.path.exists(_orig_path):
            _target_opts["原始文件"] = _orig_path
        if _recalc_path and os.path.exists(_recalc_path):
            _target_opts[f"场景文件（{snap_b_name}）"] = _recalc_path

        if len(_target_opts) > 1:
            _target_label = st.radio(
                "Excel 定位目标", list(_target_opts.keys()), horizontal=True, key="excel_target",
            )
            st.session_state["excel_locate_path"] = _target_opts[_target_label]
        elif len(_target_opts) == 1:
            st.session_state["excel_locate_path"] = list(_target_opts.values())[0]
        else:
            st.session_state.pop("excel_locate_path", None)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_metrics, tab_matrix, tab_detail, tab_prop, tab_export = st.tabs([
        "关键指标对比",
        "变化矩阵",
        "变化明细",
        "传播图",
        "导出",
    ])

    if not _has_diff:
        with tab_metrics:
            st.info("选择两个快照后点击「执行对比」显示差异")
        return

    # ── Helper: Excel locate via win32com ──────────────────────────────────────────

    def _cell_to_ref(cid: str) -> str:
        """Convert cell ID to Excel reference (e.g. '参数输入表!I250')."""
        parts = cid.rsplit("_", 2)
        if len(parts) != 3:
            return cid
        sheet, row, col = parts
        ref = f"{col}{row}"
        if sheet:
            ref = f"{sheet}!{ref}"
        return ref

    def _do_excel_locate(excel_path: str, ref: str) -> None:
        """Open Excel/WPS and navigate to the specified cell reference."""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                if "!" in ref:
                    sheet_name, addr = ref.split("!", 1)
                else:
                    sheet_name, addr = None, ref
                addr = addr.replace("$", "")
                abs_path = os.path.abspath(excel_path)

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

                try:
                    xl.Iteration = True
                    xl.MaxIterations = 1000
                    xl.MaxChange = 1e-6
                except Exception:
                    pass
                try:
                    wb.EnableIteration = True
                except Exception:
                    pass
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
                    rng.Interior.Color = 0xFFFF00
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

    def _do_excel_locate_row(
        excel_path: str, sheet_name: str, row_num: int, cell_ids: list[str],
    ) -> None:
        """Highlight the entire indicator row in Excel."""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                # Extract all columns from cell_ids
                cols = set()
                for cid in cell_ids:
                    parts = cid.rsplit("_", 2)
                    if len(parts) == 3:
                        cols.add(parts[2])

                if not cols:
                    st.warning("无法解析列范围")
                    return

                # Sort by column index (A=1, Z=26, AA=27, etc.) not alphabetically
                try:
                    from openpyxl.utils import column_index_from_string
                except ImportError:
                    st.warning("无法导入 openpyxl.utils.column_index_from_string，列排序可能不准")
                    column_index_from_string = lambda c: c  # fallback: alphabetical sort

                sorted_cols = sorted(cols, key=lambda c: column_index_from_string(c))
                min_col = sorted_cols[0]
                max_col = sorted_cols[-1]
                range_addr = f"{min_col}{row_num}:{max_col}{row_num}"

                abs_path = os.path.abspath(excel_path)

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

                try:
                    xl.Iteration = True
                    xl.MaxIterations = 1000
                    xl.MaxChange = 1e-6
                except Exception:
                    pass
                try:
                    wb.EnableIteration = True
                except Exception:
                    pass
                try:
                    wb.RefreshAll()
                    wb.Calculate()
                except Exception:
                    pass

                try:
                    ws = wb.Sheets(sheet_name)
                except Exception:
                    st.warning(f"未找到工作表「{sheet_name}」")
                    ws = wb.ActiveSheet
                ws.Activate()

                rng = ws.Range(range_addr)
                rng.Select()
                rng.Interior.Color = 0xFFFF00
                rng.Font.Bold = True
                st.success(
                    f"已选中 {sheet_name} 第 {row_num} 行（{min_col} → {max_col}），"
                    f"共 {len(cols)} 列，已标记黄色高亮（Ctrl+Z 撤销）"
                )
            finally:
                pythoncom.CoUninitialize()
        except ImportError as e:
            st.error(f"pywin32 未安装或导入失败：{e}")
        except Exception as e:
            st.error(f"打开 Excel 失败：{type(e).__name__}: {e}")


    # ══════════════════════════════════════════════════════════════════════════════
    # Tab 1: Key metrics comparison (方案A: 卡片仪表盘)
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_metrics:
        # ── Helper: read metric directly from snapshot cell values ────────────
        # For IRR / payback, read the value from the indicator's D-column cell
        # in the snapshot (which contains the Excel formula result after recalc).
        # This is authoritative — matches what the user sees in Excel.
        _METRIC_INDICATOR_KEYWORDS: list[tuple[str, tuple[str, ...], str | None]] = [
            # (metric_key, keyword_variants, preferred_table_prefix)
            ("irr_after_tax", ("资本金内部收益率（税后）", "资本金内部收益率(税后)"), None),
            ("irr_before_tax", ("资本金内部收益率（税前）", "资本金内部收益率(税前)"), None),
            ("payback_period", ("投资回收期",), "表6"),
        ]

        def _read_metric_from_snap(
            snap_values: dict, graph: FinancialGraph, metric_key: str,
            keywords: tuple[str, ...], prefer_table: str | None,
        ) -> tuple[float | None, str | None]:
            """Read a metric from snapshot cell values via indicator matching."""
            candidates: list[tuple[float, str]] = []
            for ind_id, ind in graph.indicators.items():
                name = ind.name or ""
                if not any(kw in name for kw in keywords):
                    continue
                for cid in ind.cell_ids:
                    if not cid.endswith("_D"):
                        continue
                    raw = snap_values.get(cid)
                    if raw is None:
                        continue
                    try:
                        val = float(raw)
                    except (ValueError, TypeError):
                        continue
                    # Build source label: "表N-XXX D45"
                    cell = graph.cells.get(cid)
                    parts = ind_id.split("__")
                    prefix = parts[0].replace("IND_", "") if parts else ""
                    segs = prefix.rsplit("_", 1)
                    tbl = segs[0] if len(segs) > 1 else prefix
                    ref = f"{tbl} {cell.col}{cell.row}" if cell else cid
                    candidates.append((val, ref))

            if not candidates:
                return None, None
            if prefer_table:
                for v, s in candidates:
                    if prefer_table in s:
                        return v, s
            return candidates[0][0], candidates[0][1]

        def _get_snapshot_metrics(
            snap_values: dict, snap_derived: dict, graph: FinancialGraph,
        ) -> tuple[DerivedMetrics, dict[str, str]]:
            """Build DerivedMetrics from snapshot, preferring direct cell reads."""
            metrics: dict[str, Any] = {}
            sources: dict[str, str] = {}

            # 1. Read IRR / payback from indicator cells (authoritative)
            for mkey, kws, pref in _METRIC_INDICATOR_KEYWORDS:
                val, source = _read_metric_from_snap(snap_values, graph, mkey, kws, pref)
                if val is not None:
                    metrics[mkey] = val
                    sources[mkey] = source or ""

            # 2. Read NPV / DSCR from snap_derived (self-computed, no Excel cell)
            if snap_derived:
                dm_old = deserialize_metrics(snap_derived)
                if "npv_after_tax" not in metrics and dm_old.npv_after_tax is not None:
                    metrics["npv_after_tax"] = dm_old.npv_after_tax
                    sources["npv_after_tax"] = "系统自算"
                if dm_old.dscr_avg is not None:
                    metrics["dscr_avg"] = dm_old.dscr_avg
                    sources["dscr_avg"] = "系统自算"
                if dm_old.dscr_min is not None:
                    metrics["dscr_min"] = dm_old.dscr_min
                    sources["dscr_min"] = "系统自算"

            metrics["metric_sources"] = sources
            return DerivedMetrics(**metrics), sources

        # ── Helper: extract cells for a specific indicator keyword ───────────────────
        def _get_cells_for_indicator_keyword(keyword: str, diff, graph) -> list[dict]:
            """Get changed cells that belong to indicators matching keyword."""
            cells = []
            for c in diff.changed_cells:
                ind_name = c.get("indicator_name", "")
                if ind_name and keyword.lower() in ind_name.lower():
                    cells.append(c)
            return cells

        # Compute metrics
        snap_a_derived = st.session_state.get("snap_a_derived", {})
        snap_b_derived = st.session_state.get("snap_b_derived", {})
        metrics_a, sources_a = _get_snapshot_metrics(snap_a_values, snap_a_derived, graph)
        metrics_b, sources_b = _get_snapshot_metrics(snap_b_values, snap_b_derived, graph)

        # ═══════════════════════════════════════════════════════════════════════════
        # 变化概要卡片（快照A/B相对于基准的参数修改）
        # ═══════════════════════════════════════════════════════════════════════════
        def _get_user_param_edits(snapshot_values: dict, graph) -> list[dict]:
            """找出快照相对于原始基准的参数输入修改（用户手动编辑的常量单元格）。"""
            edits = []
            for cid, snap_val in snapshot_values.items():
                cell = graph.cells.get(cid)
                if not cell:
                    continue
                # 只关注参数输入表的常量单元格（非公式，用户可编辑）
                if ("参数" in cell.sheet or "输入" in cell.sheet) and not cell.formula_raw:
                    orig_val = cell.value
                    # 检查是否有变化（数值对比）
                    try:
                        orig_num = float(orig_val) if orig_val is not None else None
                        snap_num = float(snap_val) if snap_val is not None else None
                        if orig_num is not None and snap_num is not None:
                            if abs(orig_num - snap_num) > 1e-9:
                                # 找indicator名称
                                ind_name = ""
                                for ind_id, ind in graph.indicators.items():
                                    if cid in ind.cell_ids:
                                        ind_name = ind.name or ""
                                        break
                                edits.append({
                                    "cell_id": cid,
                                    "sheet": cell.sheet,
                                    "indicator": ind_name[:20] if ind_name else cid[:20],
                                    "old": orig_num,
                                    "new": snap_num,
                                    "change": snap_num - orig_num,
                                })
                    except (ValueError, TypeError):
                        # 非数值，跳过或记录字符串变化
                        if str(orig_val) != str(snap_val):
                            edits.append({
                                "cell_id": cid,
                                "sheet": cell.sheet,
                                "indicator": cid[:20],
                                "old": orig_val,
                                "new": snap_val,
                                "change": None,
                            })
            # 按变化量排序
            edits.sort(key=lambda x: abs(x["change"]) if x["change"] is not None else 0, reverse=True)
            return edits

        # 找出快照A和B相对于基准的修改
        edits_a = _get_user_param_edits(snap_a_values, graph)
        edits_b = _get_user_param_edits(snap_b_values, graph)

        # 找出A和B之间的参数差异（共同修改了哪些，各自独有修改了哪些）
        param_ids_a = {e["cell_id"] for e in edits_a}
        param_ids_b = {e["cell_id"] for e in edits_b}

        common_edits = param_ids_a & param_ids_b  # 两快照都修改了
        only_a_edits = param_ids_a - param_ids_b  # 只有A修改了
        only_b_edits = param_ids_b - param_ids_a  # 只有B修改了

        # 生成概要文字
        def _format_edits_summary(edits: list[dict], label: str) -> str:
            if not edits:
                return f"{label}：无参数修改"
            top3 = edits[:3]
            parts = []
            for e in top3:
                if e["change"] is not None:
                    pct = (e["change"] / abs(e["old"]) * 100) if e["old"] != 0 else None
                    if pct and abs(pct) < 100:
                        parts.append(f"{e['indicator']} {e['change']:+.2f} ({pct:+.0f}%)")
                    else:
                        parts.append(f"{e['indicator']} {e['change']:+.2f}")
                else:
                    parts.append(f"{e['indicator']} 改变")
            return f"{label}：{', '.join(parts)}{'...' if len(edits)>3 else ''}"

        summary_a = _format_edits_summary(edits_a, snap_a_name)
        summary_b = _format_edits_summary(edits_b, snap_b_name)

        # 展示两列对比
        st.markdown(f"""
        <div style="background: linear-gradient(90deg, #f8f9fa 0%, #fff 100%);
                    border: 1px solid #ddd;
                    padding: 16px;
                    margin: 8px 0;
                    border-radius: 8px;">
            <div style="font-size: 1.1em; font-weight: 600; margin-bottom: 12px; color: #1565C0;">
                📝 参数修改概要（相对于原始基准）
            </div>
            <div style="display: flex; gap: 20px;">
                <div style="flex: 1; padding: 8px; background: #e3f2fd; border-radius: 4px;">
                    <div style="font-weight: 500; color: #1976D2;">{snap_a_name}</div>
                    <div style="font-size: 0.9em; margin-top: 4px;">{summary_a}</div>
                    <div style="font-size: 0.8em; color: #666; margin-top: 2px;">
                        共修改 {len(edits_a)} 个参数
                    </div>
                </div>
                <div style="flex: 1; padding: 8px; background: #fff3e0; border-radius: 4px;">
                    <div style="font-weight: 500; color: #F57C00;">{snap_b_name}</div>
                    <div style="font-size: 0.9em; margin-top: 4px;">{summary_b}</div>
                    <div style="font-size: 0.8em; color: #666; margin-top: 2px;">
                        共修改 {len(edits_b)} 个参数
                    </div>
                </div>
            </div>
            {f'''<div style="margin-top: 10px; font-size: 0.85em; color: #555;">
                共同修改 {len(common_edits)} 个参数，
                {snap_a_name}独有 {len(only_a_edits)} 个，
                {snap_b_name}独有 {len(only_b_edits)} 个
            </div>''' if common_edits or only_a_edits or only_b_edits else ''}
        </div>
        """, unsafe_allow_html=True)

        # ═══════════════════════════════════════════════════════════════════════════
        # 区1: 变化概览（紧凑一行）
        # ═══════════════════════════════════════════════════════════════════════════
        st.subheader("📊 变化概览")
        ov_cols = st.columns(6)
        ov_cols[0].metric("变化单元格", diff.summary["total_changed_cells"])
        ov_cols[1].metric("受影响Indicator", diff.summary["total_changed_indicators"])
        ov_cols[2].metric("涉及Sheet", len(diff.summary["sheets_affected"]))
        if diff.changed_cells:
            summary = compute_change_summary(diff, graph)
            ov_cols[3].metric("总增加", f"{summary['total_increase']:,.0f}")
            ov_cols[4].metric("总减少", f"{summary['total_decrease']:,.0f}")
            ov_cols[5].metric("最大变化", f"{summary['max_magnitude']:,.0f}")
        else:
            for i in range(3, 6):
                ov_cols[i].metric("—", "—")

        st.divider()

        # ═══════════════════════════════════════════════════════════════════════════
        # 区2: 关键财务指标卡片（大卡片 + st.metric + 展开详情）
        # ═══════════════════════════════════════════════════════════════════════════
        st.subheader("💰 关键财务指标对比")

        metric_defs = [
            ("📈 税后IRR", "irr_after_tax", "{:.2f}%", 100, False, "irr"),  # 增加=好事
            ("💵 财务净现值", "npv_after_tax", "{:,.0f}元", 1, False, "npv"),  # 增加=好事
            ("⏱️ 投资回收期", "payback_period", "{:.2f}年", 1, True, "回收期"),  # 减少=好事
            ("🛡️ DSCR均值", "dscr_avg", "{:.2f}", 1, False, "dscr"),  # 增加=好事
            ("🛡️ DSCR最低值", "dscr_min", "{:.2f}", 1, False, "dscr"),  # 增加=好事
        ]

        card_cols = st.columns(min(len(metric_defs), 5))

        for i, (label, attr, fmt, multiplier, lower_better, keyword) in enumerate(metric_defs):
            val_a = getattr(metrics_a, attr, None)
            val_b = getattr(metrics_b, attr, None)

            with card_cols[i]:
                # 大卡片容器
                with st.container(border=True):
                    st.markdown(f"**{label}**")

                    # 计算delta值（格式化显示）
                    delta_str = None
                    if val_a is not None and val_b is not None:
                        delta_raw = val_b - val_a
                        if "irr" in attr:
                            delta_str = f"{delta_raw*100:+.2f}%"
                        elif "npv" in attr:
                            delta_str = f"{delta_raw:+,.0f}元"
                        elif "payback" in attr:
                            delta_str = f"{delta_raw:+.2f}年"
                        else:
                            delta_str = f"{delta_raw:+.2f}"

                    if val_b is not None:
                        st.metric(
                            snap_b_name,
                            value=fmt.format(val_b * multiplier),
                            delta=delta_str,
                            delta_color="inverse" if lower_better else "normal",
                        )
                    else:
                        st.metric(snap_b_name, value="—", delta=None)

                    if val_a is not None:
                        st.caption(f"{snap_a_name}: {fmt.format(val_a * multiplier)}")

                    # 来源标注：显示数据来自哪个表/单元格
                    source_b = sources_b.get(attr, "")
                    if source_b:
                        st.caption(f"📎 {source_b}")

                    # 展开详情：显示涉及单元格
                    cells_for_this = _get_cells_for_indicator_keyword(keyword, diff, graph)
                    with st.expander(f"涉及单元格 ({len(cells_for_this)}个)", expanded=False):
                        if cells_for_this:
                            import pandas as pd
                            detail_rows = [
                                {"Cell ID": c["id"][:25], "Sheet": c.get("sheet", ""), "旧值": c.get("old", ""), "新值": c.get("new", "")}
                                for c in cells_for_this[:20]  # 最多显示20个
                            ]
                            st.dataframe(pd.DataFrame(detail_rows), hide_index=True, use_container_width=True)
                            if len(cells_for_this) > 20:
                                st.caption(f"显示前20个，共{len(cells_for_this)}个")
                        else:
                            st.caption("未找到涉及该指标的变化单元格")

        st.divider()

        # ═══════════════════════════════════════════════════════════════════════════
        # 区3: 变更分布（Sheet排行 + 变化瀑布图）
        # ═══════════════════════════════════════════════════════════════════════════
        if diff.changed_cells:
            summary = compute_change_summary(diff, graph)

            st.subheader("📋 变更分布")
            dist_cols = st.columns([2, 3])

            with dist_cols[0]:
                st.markdown("**Sheet 变更排行**")
                if summary["sheets_ranking"]:
                    import pandas as pd
                    sheet_df = pd.DataFrame([
                        {"Sheet": s, "变化数": c, "占比": f"{c/diff.summary['total_changed_cells']*100:.1f}%"}
                        for s, c in summary["sheets_ranking"][:10]
                    ])
                    sheet_event = st.dataframe(
                        sheet_df,
                        hide_index=True,
                        use_container_width=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="sheet_rank_select",
                    )
                    # 点击定位到Sheet（在Excel中）
                    sheet_sel_rows = sheet_event.selection.get("rows", [])
                    if sheet_sel_rows:
                        sel_sheet = summary["sheets_ranking"][sheet_sel_rows[0]][0]
                        st.caption(f"已选中: {sel_sheet}")
                        _sheet_excel_path = st.session_state.get("excel_locate_path")
                        if _sheet_excel_path and st.button("📍 定位到Sheet", key="sheet_locate_btn"):
                            try:
                                import win32com.client
                                import pythoncom
                                pythoncom.CoInitialize()
                                try:
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
                                                pass
                                    if xl:
                                        xl.Visible = True
                                        abs_path = os.path.abspath(_sheet_excel_path)
                                        wb = None
                                        for i in range(1, xl.Workbooks.Count + 1):
                                            try:
                                                b = xl.Workbooks(i)
                                                if os.path.abspath(b.FullName) == abs_path:
                                                    wb = b
                                                    break
                                            except Exception:
                                                pass
                                        if wb is None:
                                            wb = xl.Workbooks.Open(abs_path)
                                        ws = wb.Sheets(sel_sheet)
                                        ws.Activate()
                                        st.success(f"已激活Sheet: {sel_sheet}")
                                finally:
                                    pythoncom.CoUninitialize()
                            except Exception as e:
                                st.error(f"定位失败: {e}")

            with dist_cols[1]:
                st.markdown("**变化量瀑布图**")
                # 瀑布图数据：按Sheet聚合变化量（正负分开）
                waterfall_data = []
                for s, c in summary["sheets_ranking"][:10]:
                    sheet_cells = [x for x in diff.changed_cells if x.get("sheet") == s]
                    increase = sum(abs(x.get("change_magnitude", 0)) for x in sheet_cells if x.get("direction") == "increase")
                    decrease = sum(abs(x.get("change_magnitude", 0)) for x in sheet_cells if x.get("direction") == "decrease")
                    waterfall_data.append({"Sheet": s, "增加": increase, "减少": -decrease})

                if waterfall_data:
                    import pandas as pd
                    wf_df = pd.DataFrame(waterfall_data)
                    # 简化瀑布图：用bar_chart横向堆叠
                    st.bar_chart(wf_df.set_index("Sheet"), horizontal=True)
                    st.caption("绿色=增加量，红色=减少量（绝对值）")

        st.divider()

        # ═══════════════════════════════════════════════════════════════════════════
        # 区4: Top受影响Indicator表格（可点击定位Excel）
        # ═══════════════════════════════════════════════════════════════════════════
        if diff.changed_cells:
            summary = compute_change_summary(diff, graph)
            st.subheader("🔥 Top 10 受影响Indicator")

            if summary["top_indicators"]:
                import pandas as pd
                top_ind_df = pd.DataFrame([
                    {"Indicator": n, "变化数": c, "操作": "📍"}
                    for n, c in summary["top_indicators"][:10]
                ])

                top_event = st.dataframe(
                    top_ind_df,
                    hide_index=True,
                    use_container_width=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="top_ind_select",
                    column_config={
                        "Indicator": st.column_config.TextColumn("Indicator", width="large"),
                        "变化数": st.column_config.NumberColumn("变化数", width="small"),
                    },
                )

                top_sel_rows = top_event.selection.get("rows", [])
                if top_sel_rows:
                    sel_ind_name = summary["top_indicators"][top_sel_rows[0]][0]
                    st.caption(f"已选中: {sel_ind_name}")

                    # 找到该indicator的row/sheet信息
                    for ind_id, ind in graph.indicators.items():
                        if ind.name == sel_ind_name:
                            _ind_row = ind.row if hasattr(ind, "row") else None
                            _ind_sheet = ind.sheet if hasattr(ind, "sheet") else None
                            if _ind_row and _ind_sheet:
                                st.info(f"位于: {_ind_sheet} 第{_ind_row}行")
                                _ind_excel_path = st.session_state.get("excel_locate_path")
                                if _ind_excel_path and st.button("📍 定位Excel", key="top_ind_locate_btn"):
                                    # 定位到indicator行
                                    row_cell_ids = [
                                        cid for cid, cell in graph.cells.items()
                                        if cell.sheet == _ind_sheet and cell.row == _ind_row
                                    ]
                                    _do_excel_locate_row(_ind_excel_path, _ind_sheet, _ind_row, row_cell_ids)
                            break

    # ══════════════════════════════════════════════════════════════════════════════
    # Tab 2: Change matrix
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_matrix:
        st.subheader("变化矩阵")

        # Build matrix data: rows = indicators, columns = old/new/delta
        matrix_data = []
        mat_sheet_set: set[str] = set()
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

            # Row number from indicator.row
            row_num = ind.row if hasattr(ind, "row") and ind.row is not None else None

            # Collect sheets for this indicator's changed cells
            ind_sheets = {c.get("sheet", "") for c in changed_cells_for_ind if c.get("sheet")}
            mat_sheet_set |= ind_sheets

            matrix_data.append({
                "Indicator": ind.name or ind_id,
                f"{snap_a_name}": old_summary,
                f"{snap_b_name}": new_summary,
                "变化": (vb - va) if (va is not None and vb is not None) else None,
                "变化单元格数": len(changed_cells_for_ind),
                "_row_num": row_num,
                "_ind_id": ind_id,
                "_sheet": ind.sheet,
                "_cell_ids": [c["id"] for c in changed_cells_for_ind],
            })

        if matrix_data:
            # Filter bar
            mat_all_sheets = sorted(mat_sheet_set)
            mat_col1, mat_col2 = st.columns([1, 2])
            with mat_col1:
                st.caption("按 Sheet 筛选")
                mat_selected_sheets = st.multiselect(
                    "Sheet", mat_all_sheets, default=[],
                    key="matrix_sheets", label_visibility="collapsed",
                )
            with mat_col2:
                st.caption("按 Indicator 名称搜索")
                mat_search = st.text_input(
                    "搜索", placeholder="输入关键词筛选",
                    key="matrix_search", label_visibility="collapsed",
                )

            filtered_mat = matrix_data
            if mat_selected_sheets:
                filtered_mat = [
                    r for r in filtered_mat
                    if any(
                        c.get("sheet") in mat_selected_sheets
                        for c in diff.changed_cells
                        if c.get("indicator_name") == (r["Indicator"])
                    )
                ]
            if mat_search:
                kw = mat_search.lower()
                filtered_mat = [r for r in filtered_mat if kw in r["Indicator"].lower()]

            if filtered_mat:
                import pandas as pd

                mat_rows = []
                for r in filtered_mat:
                    mat_rows.append({
                        "行号": r["_row_num"] if r["_row_num"] is not None else "",
                        "Indicator": r["Indicator"],
                        f"{snap_a_name}": r[f"{snap_a_name}"],
                        f"{snap_b_name}": r[f"{snap_b_name}"],
                        "变化": r["变化"],
                        "变化单元格数": r["变化单元格数"],
                    })
                mat_df = pd.DataFrame(mat_rows)

                mat_event = st.dataframe(
                    mat_df,
                    use_container_width=True,
                    hide_index=True,
                    height=600,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="matrix_table_select",
                    column_config={
                        "行号": st.column_config.NumberColumn("行号", width="small"),
                        "Indicator": st.column_config.TextColumn("Indicator", width="medium"),
                        f"{snap_a_name}": st.column_config.NumberColumn(snap_a_name, width="small"),
                        f"{snap_b_name}": st.column_config.NumberColumn(snap_b_name, width="small"),
                        "变化": st.column_config.NumberColumn("变化", width="small"),
                        "变化单元格数": st.column_config.NumberColumn("变化单元格数", width="small"),
                    },
                )
                st.caption(f"显示 {len(filtered_mat)} / {len(matrix_data)} 行")

                # Excel locate on selected row — highlight entire indicator row
                mat_selected_rows = mat_event.selection.get("rows", [])
                mat_last = st.session_state.get("matrix_last_locate_id", "")
                if mat_selected_rows:
                    idx = mat_selected_rows[0]
                    if idx < len(filtered_mat):
                        sel = filtered_mat[idx]

                        if sel["_ind_id"] != mat_last:
                            st.session_state["matrix_last_locate_id"] = sel["_ind_id"]
                            _mat_excel_path = st.session_state.get("excel_locate_path")
                            if _mat_excel_path and sel["_row_num"] is not None:
                                # Get ALL cells in this row from the graph (all columns across all tables)
                                row_cell_ids = [
                                    cid for cid, cell in graph.cells.items()
                                    if cell.sheet == sel["_sheet"] and cell.row == sel["_row_num"]
                                ]
                                _do_excel_locate_row(
                                    _mat_excel_path,
                                    sel["_sheet"],
                                    sel["_row_num"],
                                    row_cell_ids,
                                )

                            st.caption(
                                f"已选中：{sel['Indicator']} — "
                                f"{len(sel['_cell_ids'])} 个变化单元格"
                            )
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
            import pandas as pd

            rows = []
            for c in filtered:
                rows.append({
                    "Cell ID": c["id"],
                    "Sheet": c.get("sheet", ""),
                    "旧值": c.get("old", ""),
                    "新值": c.get("new", ""),
                    "变化量": round(c.get("change_magnitude", 0), 6),
                    "方向": "↑" if c.get("direction") == "increase" else "↓",
                    "公式": c.get("formula", "") or "",
                    "Indicator": c.get("indicator_name", "") or "",
                })
            detail_df = pd.DataFrame(rows)

            event = st.dataframe(
                detail_df,
                use_container_width=True,
                hide_index=True,
                height=600,
                on_select="rerun",
                selection_mode="single-row",
                key="detail_table_select",
                column_config={
                    "Cell ID": st.column_config.TextColumn("Cell ID", width="medium"),
                    "公式": st.column_config.TextColumn("公式", width="large"),
                    "Indicator": st.column_config.TextColumn("Indicator", width="medium"),
                },
            )

            # Excel locate on selected row — click a row to locate directly
            selected_rows = event.selection.get("rows", [])
            _last_locate_id = st.session_state.get("detail_last_locate_id", "")
            if selected_rows:
                idx = selected_rows[0]
                if idx < len(filtered):
                    selected_cell = filtered[idx]
                    cell_id = selected_cell["id"]
                    ref = _cell_to_ref(cell_id)

                    # Remember for propagation button
                    st.session_state["detail_selected_id"] = cell_id
                    st.session_state["detail_selected_ref"] = ref

                    # Auto Excel locate on new selection
                    if cell_id != _last_locate_id:
                        st.session_state["detail_last_locate_id"] = cell_id
                        _detail_excel_path = st.session_state.get("excel_locate_path")
                        if _detail_excel_path:
                            _do_excel_locate(_detail_excel_path, ref)

            # Propagation graph button for selected cell
            _sel_id = st.session_state.get("detail_selected_id")
            _sel_ref = st.session_state.get("detail_selected_ref")
            if _sel_id:
                st.divider()
                pc1, pc2 = st.columns([4, 1])
                with pc1:
                    st.markdown(f"已选中：**{_sel_ref}**")
                with pc2:
                    if st.button("查看传播图", key="detail_prop_btn", use_container_width=True):
                        with st.spinner("构建传播图..."):
                            data = build_propagation_data(graph, diff, _sel_id, 8, 500)
                            html = render_propagation_html(
                                json.dumps(data, ensure_ascii=False, default=str)
                            )
                        st.session_state["prop_html"] = html
                        st.session_state["prop_root"] = _sel_id
                        st.session_state["prop_truncated"] = data["stats"]["truncated"]
                        st.session_state["prop_nodes"] = data["stats"]["total_nodes"]
                        st.success(f"传播图已生成，请点击上方「传播图」标签页查看")

    # ══════════════════════════════════════════════════════════════════════════════
    # Tab 4: Propagation graph
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_prop:
        if not diff.changed_cells:
            st.info("无变化单元格，无法生成传播图")
        else:
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
                _prop_excel_path = st.session_state.get("excel_locate_path")
                if _prop_excel_path:
                    _loc_ref = st.text_input(
                        "Excel 定位引用",
                        placeholder="如：参数输入表!I250（在传播图中点击节点可复制引用）",
                        key="prop_excel_locate_ref",
                    )
                    if st.button("在 Excel 中定位", key="prop_excel_locate_btn", disabled=not _loc_ref):
                        _do_excel_locate(_prop_excel_path, _loc_ref.strip())
                else:
                    st.caption("未找到 Excel 文件，Excel 定位功能不可用")

    # ══════════════════════════════════════════════════════════════════════════════
    # Tab 4: Export
    # ══════════════════════════════════════════════════════════════════════════════
    with tab_export:
        original_path = find_original_excel(task_id, task_output_dir)
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
            out_path = os.path.join(task_output_dir, f"{task_id}{suffix}.xlsx")

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
                    file_name=f"{task_filename.rsplit('.', 1)[0]}{suffix}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_export{suffix}",
                )

        st.caption(f"公式单元格: {len(formula_ids):,} 个，常量单元格: {len(graph.cells) - len(formula_ids):,} 个")

        st.divider()
        st.subheader("导出差异报告")

        if st.button("导出差异报告", type="secondary", use_container_width=True):
            report_path = os.path.join(task_output_dir, f"{task_id}_diff_report.xlsx")
            with st.spinner("生成中..."):
                export_diff_report_excel(diff, graph, report_path)

            with open(report_path, "rb") as f:
                st.download_button(
                    "下载差异报告",
                    data=f,
                    file_name=f"{task_filename.rsplit('.', 1)[0]}_diff_report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_diff_report",
                )

    # ── Invoke fragment ──────────────────────────────────────────────────────────

_render_compare_tabs(graph, task.id, task.output_dir, task.filename)