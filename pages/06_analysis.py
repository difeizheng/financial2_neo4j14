"""Page 6: Financial analysis — sensitivity, history, comparison."""
from __future__ import annotations

import json
import numpy as np
import os
import sys
import time
from typing import Any

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.sensitivity import run_sensitivity, SensitivityResult, SensitivityScenario
from financial_kg.engine.sensitivity_parallel import run_sensitivity_parallel, ParallelSensitivityResult
from financial_kg.engine.derived_metrics import DerivedMetrics
from financial_kg.engine.break_even import find_break_even, BreakEvenResult
from financial_kg.engine.scenario_analysis import run_scenario_analysis, ScenarioAnalysisResult, classify_parameter
from financial_kg.engine.scenario_analysis_parallel import run_scenario_analysis_parallel, ParallelScenarioResult
from financial_kg.viz.tornado_chart import render_tornado_html, render_spider_chart

st.set_page_config(layout="wide")
st.title("📈 分析模块")

# ── Helper: rebuild SensitivityResult from stored JSON ────────────────────────
def _rebuild_result_from_history(h: dict) -> SensitivityResult:
    """Reconstruct a SensitivityResult from SQLite-stored JSON data."""
    base = h["base_metrics"]
    base_metrics = DerivedMetrics(
        irr_after_tax=base.get("irr_after_tax"),
        irr_before_tax=base.get("irr_before_tax"),
        npv_after_tax=base.get("npv_after_tax"),
        npv_before_tax=base.get("npv_before_tax"),
        payback_period=base.get("payback_period"),
        dscr_avg=base.get("dscr_avg"),
        dscr_min=base.get("dscr_min"),
    )
    scenarios: list[SensitivityScenario] = []
    for s in h["scenarios"]:
        m = s["metrics"]
        scenario_metrics = DerivedMetrics(
            irr_after_tax=m.get("irr_after_tax"),
            irr_before_tax=m.get("irr_before_tax"),
            npv_after_tax=m.get("npv_after_tax"),
            npv_before_tax=m.get("npv_before_tax"),
            payback_period=m.get("payback_period"),
            dscr_avg=m.get("dscr_avg"),
            dscr_min=m.get("dscr_min"),
        )
        scenarios.append(SensitivityScenario(
            name=s["name"],
            param_name=s["param_name"],
            param_cell_id=s["param_cell_id"],
            perturbation=s["perturbation"],
            original_value=s["original_value"],
            perturbed_value=s["perturbed_value"],
            metrics=scenario_metrics,
            snapshot_name=s.get("snapshot_name", ""),
        ))
    summary_table = _build_summary_table_from_history(base_metrics, scenarios)
    return SensitivityResult(
        base_metrics=base_metrics,
        scenarios=scenarios,
        summary_table=summary_table,
    )


def _build_summary_table_from_history(
    base: DerivedMetrics,
    scenarios: list[SensitivityScenario],
) -> list[dict]:
    """Build summary table from rebuilt scenarios (matches sensitivity module)."""
    rows: list[dict] = []
    by_param: dict[str, list[SensitivityScenario]] = {}
    for s in scenarios:
        by_param.setdefault(s.param_name, []).append(s)
    for param_name, param_scenarios in by_param.items():
        row: dict[str, Any] = {"参数": param_name}
        for s in sorted(param_scenarios, key=lambda x: x.perturbation):
            label = f"{s.perturbation:+.0%}"
            if s.metrics.irr_after_tax is not None and base.irr_after_tax is not None:
                irr_delta = s.metrics.irr_after_tax - base.irr_after_tax
                row[label] = f"{s.metrics.irr_after_tax * 100:.2f}% ({irr_delta:+.2f}pp)"
            else:
                row[label] = "—"
        rows.append(row)
    return rows


# ── DB + task selection ─────────────────────────────────────────────────────
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

# ── Metric definitions ───────────────────────────────────────────────────────
METRICS = {
    "税后IRR": ("irr_after_tax", 100, "%", True, "越低越好"),
    "财务净现值": ("npv_after_tax", 1, "", False, "越高越好"),
    "投资回收期": ("payback_period", 1, "年", True, "越低越好"),
    "DSCR均值": ("dscr_avg", 1, "", False, "越高越好"),
    "DSCR最低值": ("dscr_min", 1, "", False, "越高越好"),
}

# ── Build parameter list ─────────────────────────────────────────────────────
@st.cache_data(show_spinner="构建参数列表...")
def _build_params(task_id: str, output_dir: str):
    g = load_graph(os.path.join(output_dir, f"{task_id}_cells.json"))
    rows = []
    for cid, cell in g.cells.items():
        ind_name = ""
        ind_category = ""
        if cell.indicator_id and cell.indicator_id in g.indicators:
            ind = g.indicators[cell.indicator_id]
            ind_name = ind.name or ""
            ind_category = ind.category or ""
        val = cell.value
        try:
            float(val)
        except (TypeError, ValueError):
            continue
        if val == 0:
            continue
        rows.append({
            "cell_id": cid,
            "name": ind_name,
            "category": ind_category,
            "sheet": cell.sheet or "",
            "value": float(val),
            "row": cell.row,
            "col": cell.col,
        })
    return rows


all_params = _build_params(task.id, task.output_dir)
all_categories = sorted(set(r["category"] for r in all_params if r["category"]))

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_sensitivity, tab_break_even, tab_scenario, tab_monte_carlo, tab_history, tab_export = st.tabs(["敏感性分析", "盈亏平衡", "情景分析", "蒙特卡罗", "历史记录", "导出报告"])


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Sensitivity Analysis
# ══════════════════════════════════════════════════════════════════════════════
with tab_sensitivity:
    st.subheader("参数选择")

    # ── Mode selector ───────────────────────────────────────────────
    sens_mode = st.radio(
        "计算模式",
        ["串行模式（单进程）", "并行模式（多进程）"],
        horizontal=True,
        key="sens_mode",
        help="并行模式适合20+场景，4进程约8x加速",
    )

    is_parallel_sens = sens_mode == "并行模式（多进程）"

    if is_parallel_sens:
        st.info("🚀 并行模式：多进程并行执行，适合大量参数×扰动组合")
        sens_workers = st.slider(
            "进程数量",
            min_value=1,
            max_value=8,
            value=4,
            step=1,
            help="建议设置为CPU核心数，最多8进程（防止内存耗尽）",
            key="sens_workers_slider",
        )
        st.caption(f"预计内存峰值：{sens_workers * 300:.0f}MB")
    else:
        st.caption("串行模式：逐个执行场景")

    # Filter bar
    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 3, 1])
    with col_f1:
        cat_filter = st.multiselect("按类别筛选", all_categories, default=[], key="sens_cat")
    with col_f2:
        search_kw = st.text_input("搜索参数", placeholder="Indicator 名称 / Sheet", key="sens_search")
    with col_f3:
        perturbations = st.multiselect(
            "扰动幅度",
            options=[-0.15, -0.1, -0.05, 0.05, 0.1, 0.15],
            default=[-0.1, -0.05, 0.05, 0.1],
            key="sens_perturbations",
        )
    with col_f4:
        if perturbations:
            st.caption(f"{len(perturbations)}档扰动")

    # Filtered params
    filtered = all_params
    if cat_filter:
        filtered = [r for r in filtered if r["category"] in cat_filter]
    if search_kw:
        kw = search_kw.lower()
        filtered = [r for r in filtered if kw in r["name"].lower() or kw in r["sheet"].lower()]

    # Parameter multiselect
    param_options = {f"{r['name']} | {r['sheet']} 第{r['row']}行 {r['col']}列 = {r['value']:,.2f}": (r["cell_id"], r["name"]) for r in filtered}
    if not param_options:
        st.info("无匹配的参数")
    else:
        # Session state for selection
        _key_sel = f"sens_sel_{task.id}"
        if _key_sel not in st.session_state:
            st.session_state[_key_sel] = []

        selected_keys = st.multiselect(
            "选择分析参数（建议 3-8 个）",
            list(param_options.keys()),
            default=st.session_state[_key_sel],
            label_visibility="collapsed",
            key="sens_multiselect",
        )

        if selected_keys:
            st.session_state[_key_sel] = selected_keys

        # Run button
        run_row = st.columns([2, 6])
        with run_row[0]:
            # Show scenario count estimate
            if selected_keys:
                scenario_count = len(selected_keys) * len(perturbations)
                st.caption(f"预估场景数：{scenario_count}")
                if scenario_count > 20 and not is_parallel_sens:
                    st.warning("建议使用并行模式加速")

            if st.button("运行敏感性分析", type="primary", use_container_width=True, disabled=not selected_keys):
                param_cells = [param_options[k] for k in selected_keys]

                if is_parallel_sens:
                    # Parallel mode
                    from financial_kg.engine.sensitivity_parallel import run_sensitivity_parallel

                    cells_path = os.path.join(task.output_dir, f"{task.id}_cells.json")

                    with st.spinner(f"并行分析中（{len(param_cells)} 参数 × {len(perturbations)} 扰动，{sens_workers} 进程）..."):
                        parallel_result = run_sensitivity_parallel(
                            graph=graph,
                            param_cells=param_cells,
                            perturbations=perturbations,
                            workers=sens_workers,
                            cells_path=cells_path,
                        )

                    # Convert ParallelSensitivityResult to SensitivityResult for compatibility
                    result = SensitivityResult(
                        base_metrics=parallel_result.base_metrics,
                        scenarios=parallel_result.scenarios,
                        summary_table=parallel_result.summary_table,
                    )
                    st.session_state[f"sens_result_{task.id}"] = result
                    st.session_state[f"sens_workers_{task.id}"] = parallel_result.workers

                    # Save to SQLite (same structure as serial)
                    params_list = [{"cell_id": c, "name": n} for c, n in param_cells]
                    scenarios_data = []
                    for s in result.scenarios:
                        metrics_dict = {}
                        for field_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                                           "npv_before_tax", "payback_period", "dscr_avg", "dscr_min"):
                            val = getattr(s.metrics, field_name, None)
                            if val is not None:
                                metrics_dict[field_name] = val
                        scenarios_data.append({
                            "name": s.name,
                            "param_name": s.param_name,
                            "param_cell_id": s.param_cell_id,
                            "perturbation": s.perturbation,
                            "original_value": s.original_value,
                            "perturbed_value": s.perturbed_value,
                            "metrics": metrics_dict,
                            "snapshot_name": s.snapshot_name,
                        })
                    base_dict = {}
                    for field_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                                       "npv_before_tax", "payback_period", "dscr_avg", "dscr_min"):
                        val = getattr(result.base_metrics, field_name, None)
                        if val is not None:
                            base_dict[field_name] = val

                    db.save_sensitivity(
                        task_id=task.id,
                        run_name=f"分析_并行_{sens_workers}进程_{len(param_cells)}参数_{time.strftime('%H%M')}",
                        params=params_list,
                        perturbations=perturbations,
                        base_metrics=base_dict,
                        scenarios=scenarios_data,
                    )
                    st.toast(f"并行分析完成（{parallel_result.total_scenarios}场景×{parallel_result.workers}进程）并已保存")

                else:
                    # Serial mode (original)
                    with st.spinner(f"分析中（{len(param_cells)} 参数 × {len(perturbations)} 扰动）..."):
                        result = run_sensitivity(
                            graph=graph,
                            param_cells=param_cells,
                            perturbations=perturbations,
                            task_id="",  # Don't create snapshots
                        )

                    st.session_state[f"sens_result_{task.id}"] = result

                    # Save to SQLite
                    params_list = [{"cell_id": c, "name": n} for c, n in param_cells]
                    scenarios_data = []
                    for s in result.scenarios:
                        metrics_dict = {}
                        for field_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                                           "npv_before_tax", "payback_period", "dscr_avg", "dscr_min"):
                            val = getattr(s.metrics, field_name, None)
                            if val is not None:
                                metrics_dict[field_name] = val
                        scenarios_data.append({
                            "name": s.name,
                            "param_name": s.param_name,
                            "param_cell_id": s.param_cell_id,
                            "perturbation": s.perturbation,
                            "original_value": s.original_value,
                            "perturbed_value": s.perturbed_value,
                            "metrics": metrics_dict,
                            "snapshot_name": s.snapshot_name,
                        })
                    base_dict = {}
                    for field_name in ("irr_after_tax", "irr_before_tax", "npv_after_tax",
                                       "npv_before_tax", "payback_period", "dscr_avg", "dscr_min"):
                        val = getattr(result.base_metrics, field_name, None)
                        if val is not None:
                            base_dict[field_name] = val

                    db.save_sensitivity(
                        task_id=task.id,
                        run_name=f"分析_{len(param_cells)}参数_{time.strftime('%H%M')}",
                        params=params_list,
                        perturbations=perturbations,
                        base_metrics=base_dict,
                        scenarios=scenarios_data,
                    )
                    st.toast("分析完成并已保存")
                st.rerun()

        # ── Display results ──────────────────────────────────────────────
        result_key = f"sens_result_{task.id}"
        result: SensitivityResult | None = st.session_state.get(result_key)

        # Check if loaded from history
        loaded_hist_keys = [k for k in st.session_state.keys() if k.startswith(f"hist_loaded_") and st.session_state[k]]
        is_from_history = len(loaded_hist_keys) > 0
        last_loaded_id = loaded_hist_keys[-1].replace(f"hist_loaded_", "") if loaded_hist_keys else None

        if result:
            st.divider()

            # History-loaded banner
            if is_from_history and last_loaded_id:
                c_banner1, c_banner2, c_banner3 = st.columns([5, 1, 1])
                with c_banner1:
                    st.success(f"已加载历史记录 #{last_loaded_id} — 龙卷风图/蛛网图/排名/汇总均可查看")
                with c_banner3:
                    if st.button("清除分析结果", key="clear_sens_result", use_container_width=True):
                        st.session_state.pop(result_key, None)
                        for k in loaded_hist_keys:
                            st.session_state.pop(k, None)
                        st.rerun()
            else:
                # New-run banner with clear option
                c_clear1, c_clear2 = st.columns([5, 1])
                with c_clear2:
                    if st.button("清除结果", key="clear_sens_result_fresh", use_container_width=True):
                        st.session_state.pop(result_key, None)
                        st.rerun()

            st.subheader("分析结果")

            # Base metrics summary
            bm = result.base_metrics
            st.caption(
                f"基准: IRR={bm.irr_after_tax * 100:.2f}%, "
                f"NPV={bm.npv_after_tax:,.0f}, "
                f"回收期={bm.payback_period or 0:.2f}年, "
                f"DSCR均值={bm.dscr_avg or 0:.2f}"
            )

            # Metric selector
            col_m1, col_m2 = st.columns([3, 3])
            with col_m1:
                selected_metric_label = st.selectbox(
                    "查看指标",
                    list(METRICS.keys()),
                    index=0,
                    key="metric_selector",
                )
            with col_m2:
                chart_type = st.selectbox(
                    "图表类型",
                    ["龙卷风图", "蛛网图"],
                    key="chart_selector",
                )

            metric_key, multiplier, unit, lower_is_better, note = METRICS[selected_metric_label]

            # Render chart
            if chart_type == "龙卷风图":
                html = render_tornado_html(result, metric_key, selected_metric_label)
            else:
                html = render_spider_chart(result, metric_key, selected_metric_label)

            if html:
                st.components.v1.html(html, height=450, scrolling=False)
            else:
                st.info("无可视化数据")

            # ── Sensitivity ranking table ─────────────────────────────────
            st.divider()
            st.subheader("敏感度排名")

            # Compute sensitivity per parameter: max delta from base
            base_val = getattr(result.base_metrics, metric_key, None)
            if base_val is not None:
                by_param: dict[str, dict] = {}
                for s in result.scenarios:
                    s_val = getattr(s.metrics, metric_key, None)
                    if s_val is not None:
                        by_param.setdefault(s.param_name, {})[s.perturbation] = s_val

                sensitivity_rows = []
                for pname, perts in by_param.items():
                    max_delta = max(
                        abs(v - base_val) for v in perts.values()
                    )
                    # Find which perturbation causes max impact
                    max_pert = max(perts.keys(), key=lambda p: abs(perts[p] - base_val))
                    pct_at_max = perts[max_pert]
                    sensitivity_rows.append({
                        "排名": 0,
                        "参数": pname,
                        "基准值": f"{base_val * multiplier:.2f}{unit}",
                        "最大变化后值": f"{pct_at_max * multiplier:.2f}{unit}",
                        "最大偏差": f"{abs(pct_at_max - base_val) * multiplier:.2f}{unit}",
                        "触发扰动": f"{max_pert:+.0%}",
                    })

                sensitivity_rows.sort(key=lambda r: float(r["最大偏差"].replace(unit, "")), reverse=True)
                for i, r in enumerate(sensitivity_rows):
                    r["排名"] = i + 1

                st.dataframe(
                    sensitivity_rows,
                    use_container_width=True,
                    hide_index=True,
                    height=300,
                    column_config={
                        "排名": st.column_config.NumberColumn("排名", width="small"),
                        "参数": st.column_config.TextColumn("参数", width="medium"),
                        "基准值": st.column_config.TextColumn("基准值", width="small"),
                        "最大变化后值": st.column_config.TextColumn("最大变化后值", width="small"),
                        "最大偏差": st.column_config.TextColumn("最大偏差", width="small"),
                        "触发扰动": st.column_config.TextColumn("触发扰动", width="small"),
                    },
                )

            # ── Multi-metric summary ──────────────────────────────────────
            st.divider()
            st.subheader("多指标敏感性汇总")

            mm_rows = []
            for metric_label, (mkey, mmult, munit, mlower, mnote) in METRICS.items():
                bv = getattr(result.base_metrics, mkey, None)
                if bv is None:
                    continue
                row = {"指标": metric_label, "基准值": f"{bv * mmult:.2f}{munit}"}
                for s in sorted(result.scenarios, key=lambda x: (x.param_name, x.perturbation)):
                    sv = getattr(s.metrics, mkey, None)
                    if sv is not None:
                        delta = (sv - bv) * mmult
                        key = f"{s.param_name[:6]}… {s.perturbation:+.0%}"
                        row[key] = f"{sv * mmult:.2f}{munit}"
                mm_rows.append(row)

            if mm_rows:
                st.dataframe(mm_rows, use_container_width=True, hide_index=True, height=250)

            # ── Detailed scenario table ───────────────────────────────────
            st.divider()
            st.subheader("详细场景")

            if result.scenarios:
                detail_rows = []
                for s in result.scenarios:
                    detail_rows.append({
                        "参数": s.param_name,
                        "扰动": f"{s.perturbation:+.0%}",
                        "原值": s.original_value,
                        "扰动后值": s.perturbed_value,
                        "IRR": (getattr(s.metrics, "irr_after_tax", None) or 0) * 100,
                        "NPV": getattr(s.metrics, "npv_after_tax", None) or 0,
                        "回收期": getattr(s.metrics, "payback_period", None),
                        "DSCR均值": getattr(s.metrics, "dscr_avg", None),
                    })
                st.dataframe(detail_rows, use_container_width=True, hide_index=True, height=400)

            # ── Conclusion summary ────────────────────────────────────────
            st.divider()
            st.subheader("分析结论")

            if base_val is not None and by_param:
                # Top 3 sensitive parameters
                ranked = sorted(
                    [(p, max(abs(v - base_val) for v in vs)) for p, vs in by_param.items()],
                    key=lambda x: x[1],
                    reverse=True,
                )
                top3 = ranked[:3]

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.info(f"最敏感参数: **{top3[0][0]}** (偏差 ±{top3[0][1] * multiplier:.2f}{unit})")
                with c2:
                    if len(top3) > 1:
                        st.info(f"次敏感参数: **{top3[1][0]}** (偏差 ±{top3[1][1] * multiplier:.2f}{unit})")
                with c3:
                    if len(top3) > 2:
                        st.info(f"第三敏感: **{top3[2][0]}** (偏差 ±{top3[2][1] * multiplier:.2f}{unit})")

                # Risk assessment
                max_impact_pct = (top3[0][1] / abs(base_val) * 100) if base_val != 0 else 0
                if max_impact_pct > 20:
                    risk_level = "高风险"
                    risk_color = "#dc2626"
                elif max_impact_pct > 10:
                    risk_level = "中风险"
                    risk_color = "#f59e0b"
                else:
                    risk_level = "低风险"
                    risk_color = "#16a34a"

                st.markdown(
                    f"<div style='padding:12px;background:#f8fafc;border-left:4px solid {risk_color};"
                    f"border-radius:4px;margin-top:12px'>"
                    f"<strong>风险等级:</strong> <span style='color:{risk_color};font-weight:bold'>{risk_level}</span> "
                    f"(最大偏差占基准值 {max_impact_pct:.1f}%)<br/>"
                    f"<strong>结论:</strong> {selected_metric_label} 对 "
                    f"{', '.join(p for p, _ in top3)} 最敏感。"
                    f"建议重点关注这些参数的实际取值范围，"
                    f"以评估项目{selected_metric_label}的稳定性和可靠性。"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: Break-even Analysis
# ══════════════════════════════════════════════════════════════════════════════
with tab_break_even:
    st.subheader("盈亏平衡分析")
    st.caption("搜索参数需要变化多少，关键指标才会触达设定的阈值")

    # ── Mode selector ───────────────────────────────────────
    be_mode = st.radio(
        "计算模式",
        ["并行模式（多进程）", "串行模式（单进程）"],
        horizontal=True,
        key="be_mode",
        help="并行模式利用下游范围重算+多候选点并行测试，约2分钟完成；串行模式约6小时",
    )

    is_parallel_be = be_mode == "并行模式（多进程）"

    if is_parallel_be:
        st.info("🚀 并行模式：下游范围重算（5秒/次）+ 多候选点并行测试，预计2分钟完成")
        be_workers = st.slider(
            "进程数量",
            min_value=1,
            max_value=8,
            value=4,
            step=1,
            help="建议设置为CPU核心数，最多8进程",
            key="be_workers_slider",
        )
        be_candidates = st.slider(
            "每轮候选点数",
            min_value=2,
            max_value=5,
            value=3,
            step=1,
            help="每轮并行测试的候选点数量，3个为最优（收敛快+并行效率高）",
            key="be_candidates_slider",
        )
        st.caption(f"预计轮数: ~17轮 (每轮{be_candidates}个候选点×{be_workers}进程)")
    else:
        st.warning("⚠️ 串行模式：每次迭代完整重算（8分钟），50次迭代约6小时")

    # Configuration
    col_be1, col_be2, col_be3 = st.columns(3)
    with col_be1:
        be_metric_label = st.selectbox(
            "目标指标",
            list(METRICS.keys()),
            key="be_metric",
        )
    with col_be2:
        be_threshold = st.number_input(
            "阈值",
            value=0.08 if "IRR" in be_metric_label else 0.0,
            step=0.01,
            format="%.4f",
            key="be_threshold",
        )
    with col_be3:
        be_perturb_pct = st.slider(
            "最大扰动范围",
            min_value=10,
            max_value=80,
            value=50,
            step=5,
            key="be_perturb",
        )

    be_metric_key, be_mult, be_unit, _, _ = METRICS[be_metric_label]

    # ── Filter: only show params with downstream impact ────────────────────────
    @st.cache_data(show_spinner="筛选有影响的参数...")
    def _filter_params_with_impact(task_id: str, output_dir: str, all_params: list[dict]) -> list[dict]:
        """Only return parameters that have downstream cells (affect other cells).

        Optimization: Use graph.predecessors() directly (O(1)) instead of full BFS (O(n)).
        """
        from financial_kg.storage.json_store import load_graph
        import os

        cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
        g = load_graph(cells_path)

        filtered = []
        for p in all_params:
            cid = p["cell_id"]
            if g.cell_graph.has_node(cid):
                # Fast check: predecessors = cells that depend on this cell
                pred_count = g.cell_graph.in_degree(cid)
                if pred_count > 0:
                    p["downstream_count"] = pred_count
                    filtered.append(p)

        return filtered

    be_params_with_impact = _filter_params_with_impact(task.id, task.output_dir, all_params)

    if not be_params_with_impact:
        st.warning("未找到有下游影响的参数。图谱依赖关系可能不完整。")
    else:
        st.caption(f"已筛选 {len(be_params_with_impact)} 个有影响的参数（下游cells > 0）")

        # Parameter selector (sorted by impact)
        be_params_with_impact.sort(key=lambda x: x.get("downstream_count", 0), reverse=True)
        be_param_options = {
            f"{r['name']} ({r.get('downstream_count', 0)} cells影响) | {r['sheet']} 第{r['row']}行 {r['col']}列 = {r['value']:,.2f}": (r["cell_id"], r["name"])
            for r in be_params_with_impact
        }
        be_param_labels = list(be_param_options.keys())
        selected_be_param = st.selectbox("参数", be_param_labels, key="be_param_sel")

        # Show downstream count
        selected_info = be_param_options[selected_be_param]
        st.caption(f"该参数修改会影响约 {selected_be_param.split('(')[1].split()[0]} 个下游单元格")

    # Run
    if st.button("搜索盈亏平衡点", type="primary", use_container_width=True):
        cell_id, param_name = be_param_options[selected_be_param]

        if is_parallel_be:
            from financial_kg.engine.break_even_parallel import find_break_even_parallel, ParallelBreakEvenResult

            cells_path = os.path.join(task.output_dir, f"{task.id}_cells.json")

            progress_bar = st.progress(0, text=f"并行搜索中...")

            with st.spinner(f"并行搜索盈亏平衡点（{be_workers}进程，每轮{be_candidates}候选点）..."):
                be_result = find_break_even_parallel(
                    graph=graph,
                    cell_id=cell_id,
                    metric_key=be_metric_key,
                    threshold=be_threshold,
                    max_rounds=20,
                    candidates_per_round=be_candidates,
                    workers=be_workers,
                    cells_path=cells_path,
                    metric_label=be_metric_label,
                    perturb_pct=be_perturb_pct,
                )

            progress_bar.empty()

            # Convert to compatible result format for display
            be_result_display = BreakEvenResult(
                param_name=param_name,
                param_cell_id=be_result.param_cell_id,
                original_value=be_result.original_value,
                metric_key=be_result.metric_key,
                metric_label=be_result.metric_label,
                threshold=be_result.threshold,
                break_even_value=be_result.break_even_value,
                break_even_pct=be_result.break_even_pct,
                found=be_result.found,
                iterations=be_result.total_evaluations,  # Use total evaluations
                metric_at_break_even=be_result.metric_at_break_even,
                direction=be_result.direction,
            )
            st.session_state[f"be_result_{task.id}"] = be_result_display
            st.session_state[f"be_parallel_info_{task.id}"] = {
                "workers": be_result.workers,
                "rounds": be_result.rounds,
                "elapsed_seconds": be_result.elapsed_seconds,
            }
            st.toast(f"并行搜索完成：{be_result.rounds}轮×{be_workers}进程，耗时{be_result.elapsed_seconds:.1f}秒")

        else:
            # Serial mode (original)
            with st.spinner("二分搜索中（预计6小时）..."):
                be_result = find_break_even(
                    graph=graph,
                    cell_id=cell_id,
                    metric_key=be_metric_key,
                    threshold=be_threshold,
                    max_iterations=50,
                    perturb_pct=be_perturb_pct,
                )
                be_result_display = BreakEvenResult(
                    param_name=param_name,
                    param_cell_id=be_result.param_cell_id,
                    original_value=be_result.original_value,
                    metric_key=be_result.metric_key,
                    metric_label=be_metric_label,
                    threshold=be_threshold,
                    break_even_value=be_result.break_even_value,
                    break_even_pct=be_result.break_even_pct,
                    found=be_result.found,
                    iterations=be_result.iterations,
                    metric_at_break_even=be_result.metric_at_break_even,
                    direction=be_result.direction,
                )
            st.session_state[f"be_result_{task.id}"] = be_result_display

    # Display result
    be_key = f"be_result_{task.id}"
    be: BreakEvenResult | None = st.session_state.get(be_key)
    be_parallel_info = st.session_state.get(f"be_parallel_info_{task.id}")

    if be:
        st.divider()
        # Always show current base metric value for context
        from financial_kg.engine.derived_metrics import compute_derived_metrics
        _be_base_m = compute_derived_metrics(graph)
        _be_base_val = getattr(_be_base_m, be.metric_key, None)

        if not be.found:
            if _be_base_val is not None:
                _be_val_str = f"{_be_base_val * be_mult:.2f}{be_unit}"
                _be_gap = be.threshold - _be_base_val
                _be_dir_hint = "提高" if _be_gap > 0 else "降低"
                _be_gap_str = f"{abs(_be_gap) * be_mult:.2f}{be_unit}"
                _be_diag = (
                    f"当前 **{be_metric_label}** 基准值为 **{_be_val_str}**，"
                    f"需要**{_be_dir_hint} {_be_gap_str}**才能达到阈值 "
                    f"{be_threshold * be_mult:.2f}{be_unit}。\n\n"
                )
            else:
                _be_diag = f"当前 **{be_metric_label}** 基准值无法计算。\n\n"
            st.warning(
                f"在 ±{be_perturb_pct}% 范围内未找到 {be_metric_label}={be_threshold * be_mult:.2f}{be_unit} 的盈亏平衡点。\n\n"
                f"{_be_diag}"
                f"💡 建议：①增大扰动范围（如 ±80%） ②更换影响更大的参数 ③降低阈值"
            )
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("参数原值", f"{be.original_value:,.2f}")
            c2.metric("盈亏平衡值", f"{be.break_even_value:,.2f}" if be.break_even_value else "—")
            c3.metric("需要变化", f"{be.break_even_pct:+.1%}" if be.break_even_pct else "—")
            if be_parallel_info:
                c4.metric("搜索耗时", f"{be_parallel_info['elapsed_seconds']:.1f}秒")
            else:
                c4.metric("搜索次数", str(be.iterations))

            # Parallel mode additional info
            if be_parallel_info:
                st.caption(
                    f"并行模式：{be_parallel_info['rounds']}轮搜索 × {be_parallel_info['workers']}进程 "
                    f"共测试{be.iterations}个候选值"
                )

            # Base metric value
            from financial_kg.engine.derived_metrics import compute_derived_metrics
            base_m = compute_derived_metrics(graph)
            base_val = getattr(base_m, be.metric_key, None)

            st.info(
                f"**{be_metric_label}** 当前值: {base_val * be_mult:.2f}{be_unit}，"
                f"阈值: {be_threshold * be_mult:.2f}{be_unit}\n\n"
                f"当参数值变为 **{be.break_even_value:,.2f}**（变化 **{be.break_even_pct:+.1%}**）时，"
                f"{be_metric_label} 刚好触达阈值。"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Scenario Analysis (Pessimistic/Base/Optimistic)
# ══════════════════════════════════════════════════════════════════════════════
with tab_scenario:
    st.subheader("情景分析")
    st.caption("多变量同时变动，对比悲观/基准/乐观三种情景")

    # ── Parallel execution option ───────────────────────────────────────
    scn_parallel_col1, scn_parallel_col2 = st.columns([3, 2])
    with scn_parallel_col1:
        scn_parallel = st.toggle(
            "并行执行",
            value=False,  # Default off for 3 scenarios
            key="scn_parallel_toggle",
            help="标准3情景模式收益有限，自定义5+情景建议开启",
        )
    with scn_parallel_col2:
        if scn_parallel:
            scn_workers = st.slider(
                "进程数",
                min_value=1,
                max_value=8,
                value=4,
                step=1,
                key="scn_workers_slider",
            )
        else:
            scn_workers = 1

    # Mode selector
    scenario_mode = st.radio(
        "情景模式",
        ["标准情景（悲观/基准/乐观）", "自定义情景"],
        horizontal=True,
        key="scn_mode",
    )

    # ── Parameter selection ───────────────────────────────────────────────
    st.divider()
    st.subheader("选择分析变量")

    col_f1, col_f2 = st.columns([2, 3])
    with col_f1:
        scn_cat_filter = st.multiselect("按类别筛选", all_categories, default=[], key="scn_cat")
    with col_f2:
        scn_search_kw = st.text_input("搜索参数", placeholder="Indicator 名称 / Sheet", key="scn_search")

    scn_filtered = all_params
    if scn_cat_filter:
        scn_filtered = [r for r in scn_filtered if r["category"] in scn_cat_filter]
    if scn_search_kw:
        kw = scn_search_kw.lower()
        scn_filtered = [r for r in scn_filtered if kw in r["name"].lower() or kw in r["sheet"].lower()]

    # Parameter multiselect
    scn_param_options = {
        f"{r['name']} | {r['sheet']} 第{r['row']}行 {r['col']}列 = {r['value']:,.2f}": r
        for r in scn_filtered
    }

    if not scn_param_options:
        st.info("无匹配的参数")
    else:
        selected_scn_keys = st.multiselect(
            "选择变量（建议 2-5 个）",
            list(scn_param_options.keys()),
            default=st.session_state.get(f"scn_sel_{task.id}", []),
            key="scn_multiselect",
        )
        st.session_state[f"scn_sel_{task.id}"] = selected_scn_keys

        # Variable count hint
        if len(selected_scn_keys) == 1:
            st.info("已选择单变量。建议选择多个变量进行情景分析。")
        elif len(selected_scn_keys) > 5:
            st.warning(f"已选择 {len(selected_scn_keys)} 个变量。建议控制在 5 个以内。")

        # ── Variable classification ───────────────────────────────────────────
        if selected_scn_keys and scenario_mode == "标准情景（悲观/基准/乐观）":
            st.divider()
            st.subheader("变量分类设置")
            st.caption("分类决定悲观/乐观方向：收入类悲观=-10%，成本类悲观=+10%，投资类悲观=+15%")

            classifications = []
            for key in selected_scn_keys:
                r = scn_param_options[key]
                # Auto-classify
                auto_class = classify_parameter(r["name"])
                default_idx = {"revenue": 0, "cost": 1, "investment": 2}.get(auto_class, 0)

                col_c1, col_c2, col_c3 = st.columns([4, 2, 1])
                with col_c1:
                    st.caption(f"{r['name']}")
                with col_c2:
                    cls = st.selectbox(
                        "分类",
                        ["收入类", "成本类", "投资类"],
                        index=default_idx,
                        key=f"cls_{r['cell_id']}",
                        label_visibility="collapsed",
                    )
                with col_c3:
                    cls_map = {"收入类": "revenue", "成本类": "cost", "投资类": "investment"}
                    classifications.append((r["cell_id"], r["name"], cls_map[cls]))

            # ── Run scenario analysis ───────────────────────────────────────────
            st.divider()
            if st.button("运行情景分析", type="primary", use_container_width=True, disabled=not selected_scn_keys):
                cells_path = os.path.join(task.output_dir, f"{task.id}_cells.json")

                if scn_parallel:
                    # Parallel mode
                    from financial_kg.engine.scenario_analysis_parallel import run_scenario_analysis_parallel
                    from financial_kg.engine.scenario_analysis import VAR_CLASSIFICATION, DEFAULT_CLASSIFICATION

                    # Build scenario_ratios
                    scenario_names = ["悲观", "基准", "乐观"]
                    scenario_ratios = {}
                    for scenario_name in scenario_names:
                        scenario_ratios[scenario_name] = {}
                        for cell_id, param_name, classification in classifications:
                            ratios = VAR_CLASSIFICATION.get(classification, VAR_CLASSIFICATION[DEFAULT_CLASSIFICATION])
                            scenario_ratios[scenario_name][cell_id] = ratios[scenario_name]

                    with st.spinner(f"并行分析悲观/基准/乐观三种情景（{scn_workers} 进程）..."):
                        parallel_scn_result = run_scenario_analysis_parallel(
                            graph=graph,
                            param_cells=classifications,
                            scenario_ratios=scenario_ratios,
                            workers=scn_workers,
                            cells_path=cells_path,
                        )

                    # Convert to ScenarioAnalysisResult for compatibility
                    scn_result = ScenarioAnalysisResult(
                        base_metrics=parallel_scn_result.base_metrics,
                        scenarios=parallel_scn_result.scenarios,
                        comparison_table=parallel_scn_result.comparison_table,
                        delta_table=parallel_scn_result.delta_table,
                    )
                    st.session_state[f"scn_result_{task.id}"] = scn_result

                    # Save to database
                    params_data = [{"cell_id": c, "name": n, "classification": cls} for c, n, cls in classifications]
                    base_metrics_data = {
                        k: v for k in ["irr_after_tax", "npv_after_tax", "payback_period", "dscr_avg", "dscr_min"]
                        if (v := getattr(scn_result.base_metrics, k, None)) is not None
                    }
                    scenarios_data = []
                    for s in scn_result.scenarios:
                        scenarios_data.append({
                            "name": s.name,
                            "param_changes": s.param_changes,
                            "metrics": {k: v for k in ["irr_after_tax", "npv_after_tax", "payback_period"]
                                        if (v := getattr(s.metrics, k, None)) is not None},
                            "changed_cells": s.changed_cells,
                        })
                    db.save_scenario(
                        task_id=task.id,
                        run_name=f"情景分析_并行_{scn_workers}进程_{len(classifications)}参数_{time.strftime('%H%M')}",
                        params=params_data,
                        base_metrics=base_metrics_data,
                        scenarios=scenarios_data,
                        comparison_table=scn_result.comparison_table,
                        delta_table=scn_result.delta_table,
                    )
                    st.toast(f"并行情景分析完成（{scn_workers}进程）并已保存")

                else:
                    # Serial mode (original)
                    with st.spinner("分析悲观/基准/乐观三种情景..."):
                        scn_result = run_scenario_analysis(
                            graph=graph,
                            param_cells=classifications,
                            preset="standard",
                        )

                    st.session_state[f"scn_result_{task.id}"] = scn_result

                    # Save to database
                    params_data = [{"cell_id": c, "name": n, "classification": cls} for c, n, cls in classifications]
                    base_metrics_data = {
                        k: v for k in ["irr_after_tax", "npv_after_tax", "payback_period", "dscr_avg", "dscr_min"]
                        if (v := getattr(scn_result.base_metrics, k, None)) is not None
                    }
                    scenarios_data = []
                    for s in scn_result.scenarios:
                        scenarios_data.append({
                            "name": s.name,
                            "param_changes": s.param_changes,
                            "metrics": {k: v for k in ["irr_after_tax", "npv_after_tax", "payback_period"]
                                        if (v := getattr(s.metrics, k, None)) is not None},
                            "changed_cells": s.changed_cells,
                        })
                    db.save_scenario(
                        task_id=task.id,
                        run_name=f"情景分析_{len(classifications)}参数_{time.strftime('%H%M')}",
                        params=params_data,
                        base_metrics=base_metrics_data,
                        scenarios=scenarios_data,
                        comparison_table=scn_result.comparison_table,
                        delta_table=scn_result.delta_table,
                    )
                    st.toast("情景分析完成并已保存")
                st.rerun()

        # ── Custom scenario mode ───────────────────────────────────────────────
        elif selected_scn_keys and scenario_mode == "自定义情景":
            st.divider()
            st.subheader("自定义各情景参数变动")

            custom_scenario_names = st.text_input(
                "情景名称（逗号分隔）",
                value="悲观,基准,乐观",
                key="custom_scn_names",
            )
            scn_name_list = [n.strip() for n in custom_scenario_names.split(",") if n.strip()]

            # Build ratio inputs for each scenario
            custom_ratios: dict[str, dict[str, float]] = {name: {} for name in scn_name_list}

            for key in selected_scn_keys:
                r = scn_param_options[key]
                col_r1, col_r2, col_r3 = st.columns([3, 2, 1])
                with col_r1:
                    st.caption(f"{r['name']}")
                with col_r2:
                    # Ratio input for each scenario
                    ratios_input = {}
                    for i, scn_name in enumerate(scn_name_list):
                        ratio_val = st.number_input(
                            f"{scn_name}变动%",
                            value=-0.10 if i == 0 else 0.0 if i == 1 else 0.10,
                            step=0.01,
                            format="%.2f",
                            key=f"ratio_{r['cell_id']}_{i}",
                            label_visibility="collapsed",
                        )
                        custom_ratios[scn_name][r["cell_id"]] = ratio_val

            if st.button("运行自定义情景分析", type="primary", use_container_width=True):
                from financial_kg.engine.scenario_analysis import run_scenario_analysis
                cells_path = os.path.join(task.output_dir, f"{task.id}_cells.json")

                if scn_parallel and len(scn_name_list) > 3:
                    # Parallel mode for custom scenarios
                    from financial_kg.engine.scenario_analysis_parallel import run_scenario_analysis_parallel

                    with st.spinner(f"并行分析 {len(scn_name_list)} 种情景（{scn_workers} 进程）..."):
                        scn_result = run_scenario_analysis_parallel(
                            graph=graph,
                            param_cells=[(r["cell_id"], r["name"], "revenue") for key in selected_scn_keys for r in [scn_param_options[key]]],
                            scenario_ratios=custom_ratios,
                            workers=scn_workers,
                            cells_path=cells_path,
                        )

                    # Convert to ScenarioAnalysisResult
                    scn_result = ScenarioAnalysisResult(
                        base_metrics=scn_result.base_metrics,
                        scenarios=scn_result.scenarios,
                        comparison_table=scn_result.comparison_table,
                        delta_table=scn_result.delta_table,
                    )
                    st.toast(f"并行自定义情景分析完成（{scn_workers}进程）")

                else:
                    # Serial mode
                    with st.spinner(f"分析 {len(scn_name_list)} 种情景..."):
                        scn_result = run_scenario_analysis(
                            graph=graph,
                            param_cells=[(r["cell_id"], r["name"], "revenue") for key in selected_scn_keys for r in [scn_param_options[key]]],
                            preset="custom",
                            custom_ratios=custom_ratios,
                        )
                    st.toast("自定义情景分析完成")

                st.session_state[f"scn_result_{task.id}"] = scn_result
                st.rerun()

    # ── Display results ───────────────────────────────────────────────────────
    scn_result_key = f"scn_result_{task.id}"
    scn_result: ScenarioAnalysisResult | None = st.session_state.get(scn_result_key)

    if scn_result:
        st.divider()
        st.subheader("情景分析结果")

        # Clear button
        if st.button("清除结果", type="secondary", key="clear_scn_result"):
            st.session_state.pop(scn_result_key, None)
            st.rerun()

        # Base metrics
        bm = scn_result.base_metrics
        st.caption(
            f"基准: IRR={bm.irr_after_tax * 100:.2f}%, "
            f"NPV={bm.npv_after_tax:,.0f}, "
            f"回收期={bm.payback_period or 0:.2f}年"
        )

        # Comparison table
        st.subheader("指标对比")
        st.dataframe(scn_result.comparison_table, use_container_width=True, hide_index=True, height=200)

        # Delta table
        st.subheader("与基准差异")
        st.dataframe(scn_result.delta_table, use_container_width=True, hide_index=True, height=200)

        # Visual comparison chart
        st.divider()
        st.subheader("对比图表")

        chart_metric = st.selectbox(
            "选择指标",
            list(METRICS.keys()),
            key="scn_chart_metric",
        )
        metric_key, multiplier, unit, _, _ = METRICS[chart_metric]

        # Build ECharts bar chart
        scenario_names = [s.name for s in scn_result.scenarios]
        values = [getattr(s.metrics, metric_key, 0) * multiplier for s in scn_result.scenarios]

        import json
        chart_option = {
            "title": {"text": f"{chart_metric} 情景对比", "left": "center", "textStyle": {"fontSize": 14}},
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": scenario_names},
            "yAxis": {"type": "value", "name": chart_metric},
            "series": [{
                "name": chart_metric,
                "type": "bar",
                "data": values,
                "itemStyle": {"color": ["#ef4444", "#6b7280", "#16a34a"]},
                "label": {"show": True, "position": "top", "formatter": "{c}"},
            }],
        }
        chart_html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<script src='https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js'></script>"
            "<style>body{margin:0;font-family:sans-serif;}#chart{width:100%;height:280px;}</style>"
            "</head><body><div id='chart'></div>"
            "<script>var chart=echarts.init(document.getElementById('chart'));"
            "var option=" + json.dumps(chart_option, ensure_ascii=False) + ";"
            "chart.setOption(option);window.addEventListener('resize',function(){chart.resize();});"
            "</script></body></html>"
        )
        st.components.v1.html(chart_html, height=300, scrolling=False)

        # ── Risk assessment ───────────────────────────────────────────────────
        st.divider()
        st.subheader("风险评估")

        pessimistic = next((s for s in scn_result.scenarios if s.name == "悲观"), None)
        if pessimistic:
            base_irr = bm.irr_after_tax or 0
            pessimistic_irr = pessimistic.metrics.irr_after_tax or 0
            irr_delta = pessimistic_irr - base_irr

            if pessimistic_irr < 0.06:  # IRR < 6%
                risk_level = "高风险"
                risk_color = "#dc2626"
                risk_note = "悲观情景IRR低于行业基准收益率"
            elif abs(irr_delta) > base_irr * 0.3:  # Delta > 30%
                risk_level = "中风险"
                risk_color = "#f59e0b"
                risk_note = "悲观情景IRR波动幅度较大"
            else:
                risk_level = "低风险"
                risk_color = "#16a34a"
                risk_note = "项目抗风险能力较强"

            st.markdown(
                f"<div style='padding:12px;background:#f8fafc;border-left:4px solid {risk_color};"
                f"border-radius:4px;'>"
                f"<strong>风险等级:</strong> <span style='color:{risk_color};font-weight:bold'>{risk_level}</span><br/>"
                f"<strong>悲观情景IRR:</strong> {pessimistic_irr * 100:.2f}% "
                f"<span style='color:{risk_color}'>({irr_delta * 100:+.2f}pp)</span><br/>"
                f"<strong>评估:</strong> {risk_note}"
                f"</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: Monte Carlo Simulation
# ══════════════════════════════════════════════════════════════════════════════
with tab_monte_carlo:
    st.subheader("蒙特卡罗模拟")
    st.caption("基于概率分布的随机抽样模拟，得到输出指标的概率分布和置信区间")

    # ── Mode selector ───────────────────────────────────────────────
    mc_mode = st.radio(
        "计算模式",
        ["快速模式（敏感性系数近似）", "并行精确模式（多进程）", "精确模式（单进程）"],
        horizontal=True,
        key="mc_mode",
        help="快速:1秒/1000次; 并行:4进程125分钟/100次; 精确:单进程500分钟/100次",
    )

    is_fast_mode = mc_mode == "快速模式（敏感性系数近似）"
    is_parallel_mode = mc_mode == "并行精确模式（多进程）"

    if is_fast_mode:
        st.info("⚡ 快速模式：敏感性系数近似，1000次仅需1秒，准确度约97%")
        iterations = 1000  # Default for fast mode
    elif is_parallel_mode:
        st.info("🚀 并行模式：多进程并行执行")
        # Workers configuration
        workers = st.slider(
            "进程数量",
            min_value=1,
            max_value=8,  # Reduced from 16 to 8 for memory safety
            value=4,
            step=1,
            help="建议设置为CPU核心数，最多8进程（防止内存耗尽）",
        )
        # Iterations slider (moved here for scope)
        iterations = st.slider(
            "模拟次数",
            min_value=100,
            max_value=5000,
            value=1000,
            step=100,
            help="次数越多结果越精确，但耗时更长",
        )
        st.warning(
            f"⚠️ 并行模式内存安全：已限制最多8进程。"
            f"当前配置：{workers}进程 × {iterations}次，"
            f"预计内存峰值：{workers * 300:.0f}MB"
        )
    else:
        st.warning(
            f"⚠️ 精确模式：单进程执行，100次约需500分钟"
        )
        iterations = 100  # Default for precise mode

    # ── Parameter selection ───────────────────────────────────────────────
    st.divider()
    st.subheader("选择分析变量")

    col_mc1, col_mc2 = st.columns([2, 3])
    with col_mc1:
        mc_cat_filter = st.multiselect("按类别筛选", all_categories, default=[], key="mc_cat")
    with col_mc2:
        mc_search_kw = st.text_input("搜索参数", placeholder="Indicator 名称 / Sheet", key="mc_search")

    mc_filtered = all_params
    if mc_cat_filter:
        mc_filtered = [r for r in mc_filtered if r["category"] in mc_cat_filter]
    if mc_search_kw:
        kw = mc_search_kw.lower()
        mc_filtered = [r for r in mc_filtered if kw in r["name"].lower() or kw in r["sheet"].lower()]

    mc_param_options = {
        f"{r['name']} | {r['sheet']} 第{r['row']}行 {r['col']}列 = {r['value']:,.2f}": r
        for r in mc_filtered
    }

    if not mc_param_options:
        st.info("无匹配的参数")
    else:
        selected_mc_keys = st.multiselect(
            "选择变量（建议 2-4 个）",
            list(mc_param_options.keys()),
            default=st.session_state.get(f"mc_sel_{task.id}", []),
            key="mc_multiselect",
        )
        st.session_state[f"mc_sel_{task.id}"] = selected_mc_keys

        # ── Distribution configuration ───────────────────────────────────────────
        if selected_mc_keys:
            st.divider()
            st.subheader("设置概率分布")

            # Iterations slider (only for non-parallel modes)
            if not is_parallel_mode:
                iterations = st.slider(
                    "模拟次数",
                    min_value=100,
                    max_value=5000,
                    value=1000,
                    step=100,
                    help="次数越多结果越精确，但耗时更长",
                )

            # Show estimated time for parallel mode
            if is_parallel_mode:
                st.caption(f"预计耗时: {iterations * 5 / workers:.1f} 分钟")

            dist_configs = []
            for key in selected_mc_keys:
                r = mc_param_options[key]

                col_d1, col_d2, col_d3 = st.columns([3, 2, 2])
                with col_d1:
                    st.caption(f"{r['name']}")
                with col_d2:
                    dist_type = st.selectbox(
                        "分布类型",
                        ["正态分布", "均匀分布", "三角分布"],
                        key=f"mc_dist_type_{r['cell_id']}",
                        label_visibility="collapsed",
                    )
                with col_d3:
                    if dist_type == "正态分布":
                        std_val = st.number_input(
                            "标准差",
                            value=0.10,
                            step=0.01,
                            format="%.2f",
                            key=f"mc_std_{r['cell_id']}",
                            help="变动范围约为 ±3σ，如σ=0.1则约±30%",
                        )
                        from financial_kg.engine.monte_carlo import DistributionConfig
                        dist_configs.append((
                            r["cell_id"],
                            r["name"],
                            DistributionConfig(type="normal", params={"mean": 0, "std": std_val}),
                        ))
                    elif dist_type == "均匀分布":
                        min_val = st.number_input("最小", value=-0.15, step=0.01, format="%.2f", key=f"mc_min_{r['cell_id']}")
                        max_val = st.number_input("最大", value=0.15, step=0.01, format="%.2f", key=f"mc_max_{r['cell_id']}")
                        dist_configs.append((
                            r["cell_id"],
                            r["name"],
                            DistributionConfig(type="uniform", params={"min": min_val, "max": max_val}),
                        ))
                    elif dist_type == "三角分布":
                        min_val = st.number_input("最小", value=-0.15, step=0.01, format="%.2f", key=f"mc_tri_min_{r['cell_id']}")
                        mode_val = st.number_input("峰值", value=0.0, step=0.01, format="%.2f", key=f"mc_tri_mode_{r['cell_id']}")
                        max_val = st.number_input("最大", value=0.15, step="%.2f", key=f"mc_tri_max_{r['cell_id']}")
                        dist_configs.append((
                            r["cell_id"],
                            r["name"],
                            DistributionConfig(type="triangular", params={"min": min_val, "mode": mode_val, "max": max_val}),
                        ))

            # ── Run simulation ───────────────────────────────────────────────────
            st.divider()
            if st.button("运行蒙特卡罗模拟", type="primary", use_container_width=True):
                if is_fast_mode:
                    # Fast mode: use sensitivity coefficients
                    from financial_kg.engine.monte_carlo_fast import run_monte_carlo_fast, DEFAULT_ELASTICITIES
                    from financial_kg.engine.monte_carlo import DistributionConfig
                    from financial_kg.engine.derived_metrics import compute_derived_metrics

                    base_metrics = compute_derived_metrics(graph)
                    base_irr = base_metrics.irr_after_tax or 0.068

                    # Build param distributions for fast mode
                    fast_dists = []
                    for key in selected_mc_keys:
                        r = mc_param_options[key]
                        param_name = r["name"].split()[0] if r["name"] else "电价"
                        # Use default distribution if not configured
                        dist_type_idx = 0  # Default normal
                        std_val = 0.08
                        fast_dists.append((param_name, DistributionConfig(type="normal", params={"mean": 0, "std": std_val})))

                    with st.spinner(f"快速模拟 {iterations} 次..."):
                        mc_result = run_monte_carlo_fast(
                            base_irr=base_irr,
                            elasticities=DEFAULT_ELASTICITIES,
                            param_distributions=fast_dists if fast_dists else None,
                            iterations=iterations,
                            seed=42,
                        )

                    st.session_state[f"mc_result_{task.id}"] = mc_result
                    st.session_state[f"mc_mode_{task.id}"] = "fast"

                    # Save to database
                    params_data = [{"name": name, "distribution": {"type": dc.type, "params": dc.params}}
                                   for name, dc in fast_dists]
                    # Extract IRR values for storage
                    irr_values_to_save = mc_result.irr_values if hasattr(mc_result, 'irr_values') else []
                    db.save_monte_carlo(
                        task_id=task.id,
                        run_name=f"蒙特卡罗_快速_{iterations}次_{time.strftime('%H%M')}",
                        mode="fast",
                        iterations=iterations,
                        params=params_data,
                        base_irr=base_irr,
                        statistics=mc_result.statistics,
                        probability_table=mc_result.probability_table,
                        irr_values=irr_values_to_save,  # Store IRR values
                    )

                    st.toast(f"快速模式完成 {iterations} 次模拟并已保存")
                    st.rerun()

                elif is_parallel_mode:
                    # Parallel mode: multiprocessing
                    from financial_kg.engine.monte_carlo_parallel import run_monte_carlo_parallel
                    from financial_kg.engine.monte_carlo import DistributionConfig

                    # Build distribution configs
                    parallel_dists = []
                    for key in selected_mc_keys:
                        r = mc_param_options[key]
                        cell_id = r["cell_id"]
                        param_name = r["name"]
                        # Get distribution config from UI (reuse existing inputs)
                        dist_type = st.session_state.get(f"mc_dist_type_{cell_id}", "正态分布")
                        if dist_type == "正态分布":
                            std_val = st.session_state.get(f"mc_std_{cell_id}", 0.08)
                            dc = DistributionConfig(type="normal", params={"mean": 0, "std": std_val})
                        elif dist_type == "均匀分布":
                            min_val = st.session_state.get(f"mc_min_{cell_id}", -0.15)
                            max_val = st.session_state.get(f"mc_max_{cell_id}", 0.15)
                            dc = DistributionConfig(type="uniform", params={"min": min_val, "max": max_val})
                        else:
                            dc = DistributionConfig(type="normal", params={"mean": 0, "std": 0.08})
                        parallel_dists.append((cell_id, param_name, dc))

                    progress_bar = st.progress(0, text=f"预克隆图谱 (0/{workers})...")

                    cells_path = os.path.join(task.output_dir, f"{task.id}_cells.json")

                    with st.spinner(f"并行执行 {iterations} 次（{workers} 进程）..."):
                        mc_result = run_monte_carlo_parallel(
                            graph=graph,
                            param_cells=parallel_dists,
                            iterations=iterations,
                            workers=workers,
                            cells_path=cells_path,
                            seed=42,
                        )

                    progress_bar.empty()
                    st.session_state[f"mc_result_{task.id}"] = mc_result
                    st.session_state[f"mc_mode_{task.id}"] = "parallel"

                    # Save to database
                    params_data = [{"cell_id": c, "name": n, "distribution": {"type": dc.type, "params": dc.params}}
                                   for c, n, dc in parallel_dists]
                    # Extract IRR values for storage
                    irr_values_to_save = mc_result.irr_values if hasattr(mc_result, 'irr_values') else []
                    npv_values_to_save = mc_result.npv_values if hasattr(mc_result, 'npv_values') else []
                    db.save_monte_carlo(
                        task_id=task.id,
                        run_name=f"蒙特卡罗_并行_{iterations}次_{workers}进程_{time.strftime('%H%M')}",
                        mode="parallel",
                        iterations=iterations,
                        params=params_data,
                        base_irr=mc_result.base_metrics.irr_after_tax or 0.068,
                        statistics=mc_result.statistics.get("irr_after_tax", {}),
                        probability_table=mc_result.probability_table,
                        irr_values=irr_values_to_save,  # Store IRR values for visualization
                    )

                    st.toast(f"并行模式完成 {iterations} 次×{workers}进程并已保存")
                    st.rerun()

                else:
                    # Precise mode: single-process full recalculation
                    from financial_kg.engine.monte_carlo import run_monte_carlo

                    progress_bar = st.progress(0, text=f"模拟中 (0/{iterations})...")

                    with st.spinner(f"执行 {iterations} 次模拟（预计 {iterations * 5 // 60}-{iterations * 10 // 60} 分钟）..."):
                        mc_result = run_monte_carlo(
                            graph=graph,
                            param_cells=dist_configs,
                            iterations=iterations,
                            seed=42,
                        )

                    progress_bar.empty()
                    st.session_state[f"mc_result_{task.id}"] = mc_result
                    st.session_state[f"mc_mode_{task.id}"] = "precise"

                    # Save to database
                    params_data = [{"name": name, "distribution": {"type": dc.type, "params": dc.params}}
                                   for name, dc in dist_configs]
                    # Extract IRR values from simulations
                    irr_values_to_save = [s.metrics.irr_after_tax for s in mc_result.simulations if s.metrics.irr_after_tax]
                    db.save_monte_carlo(
                        task_id=task.id,
                        run_name=f"蒙特卡罗_精确_{iterations}次_{time.strftime('%H%M')}",
                        mode="precise",
                        iterations=iterations,
                        params=params_data,
                        base_irr=mc_result.base_metrics.irr_after_tax or 0.068,
                        statistics=mc_result.statistics.get("irr_after_tax", {}),
                        probability_table=mc_result.probability_table,
                        irr_values=irr_values_to_save,  # Store IRR values
                    )

                    st.toast(f"精确模式完成 {iterations} 次模拟并已保存")
                    st.rerun()

    # ── Display results ───────────────────────────────────────────────────────
    mc_result_key = f"mc_result_{task.id}"
    mc_result = st.session_state.get(mc_result_key)
    mc_mode_type = st.session_state.get(f"mc_mode_{task.id}", "fast")

    if mc_result:
        st.divider()
        st.subheader("模拟结果")

        if st.button("清除结果", type="secondary", key="clear_mc_result"):
            st.session_state.pop(mc_result_key, None)
            st.session_state.pop(f"mc_mode_{task.id}", None)
            st.session_state.pop(f"mc_params_{task.id}", None)  # Clear params info
            st.rerun()

        # ── Show parameters analyzed ───────────────────────────────────────
        params_info = st.session_state.get(f"mc_params_{task.id}", [])
        if params_info:
            st.subheader("分析参数")
            params_rows = []
            for p in params_info:
                dist_type = p.get("distribution", {}).get("type", "normal")
                dist_params = p.get("distribution", {}).get("params", {})
                if dist_type == "normal":
                    dist_str = f"正态(σ={dist_params.get('std', 0)*100:.1f}%)"
                elif dist_type == "uniform":
                    dist_str = f"均匀({dist_params.get('min', 0)*100:+.1f}%~{dist_params.get('max', 0)*100:+.1f}%)"
                elif dist_type == "triangular":
                    dist_str = f"三角({dist_params.get('min', 0)*100:+.1f}%~{dist_params.get('max', 0)*100:+.1f}%)"
                else:
                    dist_str = dist_type
                params_rows.append({
                    "参数": p.get("name", "未知"),
                    "分布": dist_str,
                    "Cell ID": p.get("cell_id", "—"),
                })
            st.dataframe(params_rows, use_container_width=True, hide_index=True, height=min(200, len(params_rows)*35+40))
            st.caption(f"模拟次数: {mc_result.iterations if hasattr(mc_result, 'iterations') else '未知'}")

        # Mode indicator
        if hasattr(mc_result, 'base_irr'):
            # FastMonteCarloResult (fast or parallel mode history)
            st.caption("⚡ 快速模式结果（敏感性系数近似）")
            base_irr = mc_result.base_irr
            st.caption(f"基准IRR: {base_irr * 100:.2f}%")
        elif hasattr(mc_result, 'base_metrics'):
            # MonteCarloResult or ParallelMonteCarloResult (precise mode)
            bm = mc_result.base_metrics
            st.caption(f"基准: IRR={bm.irr_after_tax * 100:.2f}%, NPV={bm.npv_after_tax:,.0f}")
        else:
            # Fallback
            st.caption("蒙特卡罗模拟结果")

        # ── Statistics table ───────────────────────────────────────────────────
        st.subheader("统计指标")

        stats = mc_result.statistics if hasattr(mc_result, 'statistics') else {}

        if stats:
            stats_rows = [{
                "指标": "IRR",
                "均值": f"{stats.get('mean', 0) * 100:.2f}%",
                "标准差": f"{stats.get('std', 0) * 100:.2f}%",
                "最小值": f"{stats.get('min', 0) * 100:.2f}%",
                "最大值": f"{stats.get('max', 0) * 100:.2f}%",
                "中位数": f"{stats.get('median', 0) * 100:.2f}%",
                "5%分位": f"{stats.get('p5', 0) * 100:.2f}%",
                "95%分位": f"{stats.get('p95', 0) * 100:.2f}%",
            }]
            st.dataframe(stats_rows, use_container_width=True, hide_index=True, height=200)

        # ── Probability table ───────────────────────────────────────────────────
        st.subheader("IRR达标概率")

        prob_table = mc_result.probability_table if hasattr(mc_result, 'probability_table') else []
        if prob_table:
            st.dataframe(prob_table, use_container_width=True, hide_index=True, height=200)

        # ── Distribution histogram ──────────────────────────────────────────────
        st.divider()
        st.subheader("IRR概率分布")

        # Get IRR values based on mode
        if mc_mode_type == "fast":
            irr_values = mc_result.irr_values if hasattr(mc_result, 'irr_values') else []
            irr_values = [v * 100 for v in irr_values]  # Convert to percentage
        elif mc_mode_type == "parallel":
            # Parallel mode now stores irr_values
            irr_values = mc_result.irr_values if hasattr(mc_result, 'irr_values') else []
            irr_values = [v * 100 for v in irr_values]  # Convert to percentage
        else:
            # Precise mode: has simulations attribute
            if hasattr(mc_result, 'simulations'):
                irr_values = [s.metrics.irr_after_tax * 100 for s in mc_result.simulations if s.metrics.irr_after_tax]
            elif hasattr(mc_result, 'irr_values'):
                # Fallback: use irr_values if available
                irr_values = [v * 100 for v in mc_result.irr_values]
            else:
                irr_values = []

        if irr_values:
            import json

            min_irr = min(irr_values)
            max_irr = max(irr_values)
            bins = 20
            bin_width = (max_irr - min_irr) / bins if max_irr > min_irr else 1

            counts = []
            bin_labels = []
            for i in range(bins):
                bin_start = min_irr + i * bin_width
                bin_end = bin_start + bin_width
                count = sum(1 for v in irr_values if bin_start <= v < bin_end)
                counts.append(count)
                bin_labels.append(f"{bin_start:.1f}")

            chart_option = {
                "title": {"text": "IRR概率分布", "left": "center", "textStyle": {"fontSize": 14}},
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": bin_labels, "name": "IRR (%)"},
                "yAxis": {"type": "value", "name": "频数"},
                "series": [{
                    "name": "频数",
                    "type": "bar",
                    "data": counts,
                    "itemStyle": {"color": "#3b82f6"},
                }],
            }

            chart_html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<script src='https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js'></script>"
                "<style>body{margin:0;font-family:sans-serif;}#chart{width:100%;height:280px;}</style>"
                "</head><body><div id='chart'></div>"
                "<script>var chart=echarts.init(document.getElementById('chart'));"
                "var option=" + json.dumps(chart_option, ensure_ascii=False) + ";"
                "chart.setOption(option);window.addEventListener('resize',function(){chart.resize();});"
                "</script></body></html>"
            )
            st.components.v1.html(chart_html, height=300, scrolling=False)

        # ── Risk assessment ───────────────────────────────────────────────────────
        st.divider()
        st.subheader("风险评估")

        # Get stats from correct structure
        if hasattr(mc_result, 'statistics'):
            stats_dict = mc_result.statistics
            if isinstance(stats_dict, dict) and "irr_after_tax" in stats_dict:
                stats = stats_dict["irr_after_tax"]
            else:
                stats = stats_dict
        else:
            stats = {}

        if stats:
            mean_irr = stats.get("mean", 0.068)
            std_irr = stats.get("std", 0.01)
            p5_irr = stats.get("p5", 0.05)

            # ── Cumulative probability chart ─────────────────────────────────────
            st.subheader("累积概率曲线")
            st.caption("显示IRR低于某个阈值的累积概率，帮助评估风险")

            # Build cumulative probability data
            if irr_values:
                sorted_irr = sorted(irr_values)
                cum_prob = []
                thresholds = np.linspace(min(irr_values), max(irr_values), 50)
                for thresh in thresholds:
                    prob_below = sum(1 for v in irr_values if v <= thresh) / len(irr_values)
                    cum_prob.append(prob_below * 100)

                cum_chart_option = {
                    "title": {"text": "IRR累积概率分布", "left": "center", "textStyle": {"fontSize": 14}},
                    "tooltip": {"trigger": "axis", "formatter": "IRR ≤ {b}%: {c}%概率"},
                    "xAxis": {"type": "value", "name": "IRR (%)", "min": min(irr_values), "max": max(irr_values)},
                    "yAxis": {"type": "value", "name": "累积概率 (%)", "min": 0, "max": 100},
                    "series": [{
                        "name": "累积概率",
                        "type": "line",
                        "data": [[t, p] for t, p in zip(thresholds, cum_prob)],
                        "smooth": True,
                        "markLine": {
                            "data": [
                                {"xAxis": 6.0, "name": "行业基准6%", "label": {"formatter": "基准6%"}},
                                {"yAxis": 50, "name": "中位数", "label": {"formatter": "50%概率"}},
                            ],
                            "lineStyle": {"color": "#ef4444", "type": "dashed"},
                        },
                    }],
                }

                cum_chart_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<script src='https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js'></script>"
                    "<style>body{margin:0;font-family:sans-serif;}#chart{width:100%;height:320px;}</style>"
                    "</head><body><div id='chart'></div>"
                    "<script>var chart=echarts.init(document.getElementById('chart'));"
                    "var option=" + json.dumps(cum_chart_option, ensure_ascii=False) + ";"
                    "chart.setOption(option);window.addEventListener('resize',function(){chart.resize();});"
                    "</script></body></html>"
                )
                st.components.v1.html(cum_chart_html, height=350, scrolling=False)

            # ── Risk criteria ───────────────────────────────────────────────────────
            if p5_irr < 0.04:  # 5th percentile below 4%
                risk_level = "极高风险"
                risk_color = "#991b1b"
                risk_note = "5%分位IRR低于4%，极端情况下项目可能亏损"
            elif p5_irr < 0.06:  # 5th percentile below 6%
                risk_level = "高风险"
                risk_color = "#dc2626"
                risk_note = "5%分位IRR低于行业基准6%，有较高概率不达标"
            elif std_irr > 0.02:  # High volatility
                risk_level = "中风险"
                risk_color = "#f59e0b"
                risk_note = f"IRR波动性较大（σ={std_irr * 100:.2f}%），不确定性高"
            else:
                risk_level = "低风险"
                risk_color = "#16a34a"
                risk_note = "IRR分布集中，波动性小，项目收益稳定"

            st.markdown(
                f"<div style='padding:12px;background:#f8fafc;border-left:4px solid {risk_color};"
                f"border-radius:4px;'>"
                f"<strong>风险等级:</strong> <span style='color:{risk_color};font-weight:bold'>{risk_level}</span><br/>"
                f"<strong>均值IRR:</strong> {mean_irr * 100:.2f}% (σ={std_irr * 100:.2f}%)<br/>"
                f"<strong>5%分位IRR:</strong> {p5_irr * 100:.2f}% (极端情况)<br/>"
                f"<strong>评估:</strong> {risk_note}"
                f"</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 5: Export Report
# ══════════════════════════════════════════════════════════════════════════════
with tab_export:
    st.subheader("导出分析报告")

    result_key = f"sens_result_{task.id}"
    has_result = result_key in st.session_state
    _key_scn_list = f"scn_list_{task.id}"
    scn_list = st.session_state.get(_key_scn_list, [])
    be_key = f"be_result_{task.id}"
    be_result = st.session_state.get(be_key)

    if not has_result and not scn_list and not be_result:
        st.info("请先在「敏感性分析」「场景构建」或「盈亏平衡」中运行分析，然后回来导出。")
    else:
        st.caption(f"已包含：{'敏感性分析 ' if has_result else ''}{'场景对比 ' if scn_list else ''}{'盈亏平衡' if be_result else ''}")

        if st.button("生成报告", type="primary", use_container_width=True):
            from financial_kg.engine.report_export import export_financial_report
            import tempfile as _tf

            sens = st.session_state.get(result_key)

            with _tf.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                export_financial_report(
                    graph=graph,
                    output_path=tmp.name,
                    project_name=task.filename,
                    sensitivity_result=sens,
                    scenarios=scn_list,
                    break_even_results=[
                        {
                            "param_name": be_result.param_name,
                            "metric_label": be_result.metric_label,
                            "threshold": be_result.threshold,
                            "found": be_result.found,
                            "break_even_value": be_result.break_even_value,
                            "break_even_pct": be_result.break_even_pct,
                        }
                    ] if be_result else None,
                )
                with open(tmp.name, "rb") as f:
                    st.download_button(
                        "下载 Word 报告",
                        data=f,
                        file_name=f"{task.filename}_分析报告.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="dl_report",
                    )
                os.unlink(tmp.name)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: Sensitivity History
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("历史记录")

    # Sub-tabs for different analysis types
    hist_sens, hist_scenario, hist_mc = st.tabs(["敏感性分析", "情景分析", "蒙特卡罗"])

    # ── Sensitivity History ───────────────────────────────────────────────────
    with hist_sens:
        history = db.list_sensitivity(task.id)

        if not history:
            st.info("暂无敏感性分析历史")
        else:
            st.caption(f"共 {len(history)} 条记录")

            # History table with "load to analysis" button
            for h in history:
                params_str = ", ".join(p["name"] for p in h["params"][:5])
                if len(h["params"]) > 5:
                    params_str += f" ... +{len(h['params']) - 5}"
                base = h["base_metrics"]
                irr = base.get("irr_after_tax")

                col_t1, col_t2, col_t3, col_t4, col_t5, col_t6, col_t7 = st.columns([1, 3, 3, 2, 2, 2, 1])
                with col_t1:
                    st.caption(f"#{h['id']}")
                with col_t2:
                    st.caption(h["created_at"][:19])
                with col_t3:
                    st.caption(params_str)
                with col_t4:
                    st.caption(", ".join(f"{p:+.0%}" for p in h["perturbations"]))
                with col_t5:
                    st.caption(f"{irr * 100:.2f}%" if irr else "—")
                with col_t6:
                    st.caption(str(len(h["scenarios"])))
                with col_t7:
                    if st.button("📊 加载", key=f"load_hist_{h['id']}", use_container_width=True, help="加载到敏感性分析Tab查看图表"):
                        rebuilt = _rebuild_result_from_history(h)
                        st.session_state[f"sens_result_{task.id}"] = rebuilt
                        st.session_state[f"hist_loaded_{h['id']}"] = True
                        st.toast(f"已加载记录 #{h['id']}，正在切换到分析视图...")
                        st.rerun()

            st.divider()

            # Indicator when a history record is loaded
            loaded_hint = [k for k in st.session_state.keys() if k.startswith(f"hist_loaded_") and st.session_state[k]]
            if loaded_hint:
                last_loaded = loaded_hint[-1]
                loaded_id = last_loaded.replace(f"hist_loaded_", "")
                st.success(f"记录 #{loaded_id} 已加载到「敏感性分析」Tab，可查看龙卷风图/蛛网图等完整分析。")

            # Comparison mode toggle
            comparison_mode = st.toggle("对比模式", key="hist_compare_mode")

            if comparison_mode:
                st.divider()
                st.subheader("选择两条历史记录进行对比")

                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    hist_ids_a = {f"[{h['id']}] {h['run_name']} ({h['created_at'][:19]})": h for h in history}
                    sel_a = st.selectbox("记录 A", list(hist_ids_a.keys()), key="hist_sel_a")
                with col_h2:
                    hist_ids_b = {f"[{h['id']}] {h['run_name']} ({h['created_at'][:19]})": h for h in history}
                    sel_b = st.selectbox("记录 B", list(hist_ids_b.keys()), key="hist_sel_b")

                if sel_a and sel_b:
                    h_a = hist_ids_a[sel_a]
                    h_b = hist_ids_b[sel_b]

                    # Comparison table
                    st.divider()
                    st.subheader(f"{h_a['run_name']} vs {h_b['run_name']}")

                    # Base metrics comparison
                    base_a = h_a["base_metrics"]
                    base_b = h_b["base_metrics"]

                    compare_rows = []
                    for label, (mkey, mmult, munit, _, _) in METRICS.items():
                        va = base_a.get(mkey)
                        vb = base_b.get(mkey)
                        if va is None and vb is None:
                            continue
                        delta = (vb - va) * mmult if (va is not None and vb is not None) else None
                        compare_rows.append({
                            "指标": label,
                            "记录 A 基准": f"{va * mmult:.2f}{munit}" if va is not None else "—",
                            "记录 B 基准": f"{vb * mmult:.2f}{munit}" if vb is not None else "—",
                            "差异": f"{delta:+.2f}{munit}" if delta is not None else "—",
                        })

                    st.dataframe(compare_rows, use_container_width=True, hide_index=True)

                    # Parameter impact comparison
                    st.divider()
                    st.subheader("参数影响对比")

                    # Build IRR summary for both
                    def _build_hist_summary(hist):
                        by_p: dict[str, dict] = {}
                        for s in hist["scenarios"]:
                            by_p.setdefault(s["param_name"], {})[s["perturbation"]] = s["metrics"]
                        return by_p

                    pa = _build_hist_summary(h_a)
                    pb = _build_hist_summary(h_b)

                    all_params_hist = sorted(set(list(pa.keys()) + list(pb.keys())))
                    all_perts = sorted(set(
                        list(p for perts in pa.values() for p in perts)
                        + list(p for perts in pb.values() for p in perts)
                    ))

                    compare_param_rows = []
                    for pname in all_params_hist:
                        row = {"参数": pname}
                        base_irr_a = h_a["base_metrics"].get("irr_after_tax")
                        base_irr_b = h_b["base_metrics"].get("irr_after_tax")
                        for pct in all_perts:
                            val_a = pa.get(pname, {}).get(pct, {})
                            val_b = pb.get(pname, {}).get(pct, {})
                            irr_a = val_a.get("irr_after_tax") if isinstance(val_a, dict) else None
                            irr_b = val_b.get("irr_after_tax") if isinstance(val_b, dict) else None
                            if irr_a is not None or irr_b is not None:
                                a_str = f"{irr_a * 100:.2f}%" if irr_a is not None else "—"
                                b_str = f"{irr_b * 100:.2f}%" if irr_b is not None else "—"
                                row[f"{pct:+.0%}"] = f"A: {a_str} / B: {b_str}"
                        compare_param_rows.append(row)

                    if compare_param_rows:
                        st.dataframe(compare_param_rows, use_container_width=True, hide_index=True)
                st.dataframe(compare_param_rows, use_container_width=True, hide_index=True)

            else:
                # Single record view
                st.divider()
                hist_ids = {f"[{h['id']}] {h['run_name']} ({h['created_at'][:19]})": h for h in history}
                selected_hist = st.selectbox("查看历史详情", list(hist_ids.keys()), label_visibility="collapsed")

            if selected_hist:
                h = hist_ids[selected_hist]

                detail_row = st.columns([6, 2, 1])
                with detail_row[1]:
                    if st.button("📊 加载到分析", type="primary", use_container_width=True, key=f"load_detail_{h['id']}"):
                        rebuilt = _rebuild_result_from_history(h)
                        st.session_state[f"sens_result_{task.id}"] = rebuilt
                        st.session_state[f"hist_loaded_{h['id']}"] = True
                        st.toast(f"已加载记录 #{h['id']} 到分析视图")
                        st.rerun()
                with detail_row[2]:
                    if st.button("🗑️ 删除", type="secondary", use_container_width=True, key=f"del_hist_{h['id']}"):
                        db.delete_sensitivity(h["id"])
                        st.toast("已删除")
                        st.rerun()

                st.caption(f"运行时间: {h['created_at'][:19]} | "
                           f"参数: {', '.join(p['name'] for p in h['params'])} | "
                           f"扰动: {', '.join(f'{p:+.0%}' for p in h['perturbations'])}")

                # Multi-metric base comparison
                st.subheader("基准指标")
                base_m = h["base_metrics"]
                base_rows = []
                for label, (mkey, mmult, munit, _, _) in METRICS.items():
                    val = base_m.get(mkey)
                    if val is not None:
                        base_rows.append({"指标": label, "基准值": f"{val * mmult:.2f}{munit}"})
                if base_rows:
                    st.dataframe(base_rows, use_container_width=True, hide_index=True, height=200)
    
                # IRR summary
                st.subheader("IRR 敏感性汇总表")
                summary_data = []
                by_param: dict[str, dict] = {}
                for s in h["scenarios"]:
                    by_param.setdefault(s["param_name"], {})[s["perturbation"]] = s["metrics"]
    
                for pname, perts in by_param.items():
                    row = {"参数": pname}
                    for pct, metrics in sorted(perts.items()):
                        label = f"{pct:+.0%}"
                        irr = metrics.get("irr_after_tax")
                        if irr is not None:
                            base_irr = base_m.get("irr_after_tax")
                            delta = (irr - base_irr) * 100 if base_irr is not None else 0
                            row[label] = f"{irr * 100:.2f}% ({delta:+.2f}pp)"
                        else:
                            row[label] = "—"
                    summary_data.append(row)
    
                if summary_data:
                    st.dataframe(summary_data, use_container_width=True, hide_index=True)
    
                # ── Inline chart preview ──────────────────────────────────────
                st.divider()
                st.subheader("快速图表预览")
    
                rebuilt = _rebuild_result_from_history(h)
                col_c1, col_c2 = st.columns([3, 3])
                with col_c1:
                    hist_chart_metric = st.selectbox(
                        "指标",
                        list(METRICS.keys()),
                        index=0,
                        key="hist_chart_metric",
                    )
                with col_c2:
                    hist_chart_type = st.selectbox(
                        "图表",
                        ["龙卷风图", "蛛网图"],
                        key="hist_chart_type",
                    )
    
                h_metric_key, h_mult, h_unit, _, _ = METRICS[hist_chart_metric]
                if hist_chart_type == "龙卷风图":
                    html = render_tornado_html(rebuilt, h_metric_key, hist_chart_metric)
                else:
                    html = render_spider_chart(rebuilt, h_metric_key, hist_chart_metric)
    
                if html:
                    st.components.v1.html(html, height=450, scrolling=False)
                else:
                    st.info("无可视化数据")
    
                # ── Sensitivity ranking (inline) ──────────────────────────────
                st.divider()
                st.subheader("敏感度排名")
    
                h_base_val = getattr(rebuilt.base_metrics, h_metric_key, None)
                if h_base_val is not None:
                    h_by_param: dict[str, dict] = {}
                    for s in rebuilt.scenarios:
                        s_val = getattr(s.metrics, h_metric_key, None)
                        if s_val is not None:
                            h_by_param.setdefault(s.param_name, {})[s.perturbation] = s_val
    
                    h_rank_rows = []
                    for pname, perts in h_by_param.items():
                        max_delta = max(abs(v - h_base_val) for v in perts.values())
                        max_pert = max(perts.keys(), key=lambda p: abs(perts[p] - h_base_val))
                        pct_at_max = perts[max_pert]
                        h_rank_rows.append({
                            "参数": pname,
                            "基准值": f"{h_base_val * h_mult:.2f}{h_unit}",
                            "最大变化后值": f"{pct_at_max * h_mult:.2f}{h_unit}",
                            "最大偏差": f"{abs(pct_at_max - h_base_val) * h_mult:.2f}{h_unit}",
                            "触发扰动": f"{max_pert:+.0%}",
                        })
    
                    h_rank_rows.sort(key=lambda r: float(r["最大偏差"].replace(h_unit, "")), reverse=True)
                    for i, r in enumerate(h_rank_rows):
                        r["排名"] = i + 1
    
                    st.dataframe(
                        h_rank_rows,
                        use_container_width=True,
                        hide_index=True,
                        height=min(300, len(h_rank_rows) * 35 + 40),
                        column_config={
                            "排名": st.column_config.NumberColumn("排名", width="small"),
                        },
                    )
    
    # ── Scenario History ───────────────────────────────────────────────────
    with hist_scenario:
        scenario_history = db.list_scenario(task.id)

        if not scenario_history:
            st.info("暂无情景分析历史")
        else:
            st.caption(f"共 {len(scenario_history)} 条记录")

            for h in scenario_history:
                params_str = ", ".join(p.get("name", "")[:15] for p in h["params"][:3])
                base = h["base_metrics"]
                irr = base.get("irr_after_tax")

                col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns([1, 3, 2, 2, 1])
                with col_s1:
                    st.caption(f"#{h['id']}")
                with col_s2:
                    st.caption(h["created_at"][:19])
                with col_s3:
                    st.caption(params_str)
                with col_s4:
                    st.caption(f"基准IRR={irr * 100:.2f}%" if irr else "—")
                with col_s5:
                    if st.button("📊", key=f"load_scn_hist_{h['id']}", use_container_width=True, help="加载到情景分析Tab"):
                        from financial_kg.engine.scenario_analysis import ScenarioAnalysisResult, ScenarioResult
                        base_metrics = DerivedMetrics(
                            irr_after_tax=base.get("irr_after_tax"),
                            npv_after_tax=base.get("npv_after_tax"),
                            payback_period=base.get("payback_period"),
                        )
                        scenarios = []
                        for s in h["scenarios"]:
                            scenarios.append(ScenarioResult(
                                name=s["name"],
                                param_changes=s.get("param_changes", {}),
                                metrics=DerivedMetrics(
                                    irr_after_tax=s.get("metrics", {}).get("irr_after_tax"),
                                    npv_after_tax=s.get("metrics", {}).get("npv_after_tax"),
                                ),
                                changed_cells=s.get("changed_cells", 0),
                            ))
                        rebuilt = ScenarioAnalysisResult(
                            base_metrics=base_metrics,
                            scenarios=scenarios,
                            comparison_table=h["comparison_table"],
                            delta_table=h["delta_table"],
                        )
                        st.session_state[f"scn_result_{task.id}"] = rebuilt
                        st.toast(f"已加载情景分析 #{h['id']}")
                        st.rerun()

    # ── Monte Carlo History ───────────────────────────────────────────────────
    with hist_mc:
        mc_history = db.list_monte_carlo(task.id)

        if not mc_history:
            st.info("暂无蒙特卡罗历史")
        else:
            st.caption(f"共 {len(mc_history)} 条记录")

            for h in mc_history:
                stats = h["statistics"]
                mean_irr = stats.get("mean", 0)
                params_info = h.get("params", [])
                params_str = ", ".join(p.get("name", "未知")[:10] for p in params_info[:3])
                if len(params_info) > 3:
                    params_str += f" +{len(params_info)-3}"

                col_m1, col_m2, col_m3, col_m4, col_m5, col_m6, col_m7 = st.columns([1, 2, 2, 2, 2, 2, 1])
                with col_m1:
                    st.caption(f"#{h['id']}")
                with col_m2:
                    st.caption(h["created_at"][:19])
                with col_m3:
                    mode_label = "⚡快" if h["mode"] == "fast" else ("🚀并" if h["mode"] == "parallel" else "🎯精")
                    st.caption(f"{mode_label} {h['iterations']}次")
                with col_m4:
                    st.caption(params_str if params_str else "—")
                with col_m5:
                    st.caption(f"均值IRR={mean_irr * 100:.2f}%")
                with col_m6:
                    std_irr = stats.get("std", 0)
                    st.caption(f"σ={std_irr * 100:.2f}%")
                with col_m7:
                    if st.button("📊", key=f"load_mc_hist_{h['id']}", use_container_width=True, help="加载到蒙特卡罗Tab"):
                        from financial_kg.engine.monte_carlo_fast import FastMonteCarloResult
                        # Load IRR values from database
                        irr_values_loaded = h.get("irr_values", [])
                        params_loaded = h.get("params", [])
                        rebuilt = FastMonteCarloResult(
                            base_irr=h["base_irr"],
                            iterations=h["iterations"],
                            irr_values=irr_values_loaded,  # Restore IRR values for charts
                            statistics=stats,
                            probability_table=h["probability_table"],
                        )
                        st.session_state[f"mc_result_{task.id}"] = rebuilt
                        st.session_state[f"mc_mode_{task.id}"] = h["mode"]
                        st.session_state[f"mc_params_{task.id}"] = params_loaded  # Store params info
                        st.toast(f"已加载蒙特卡罗 #{h['id']}")
                        st.rerun()
