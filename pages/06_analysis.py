"""Page 6: Financial analysis — sensitivity, history, comparison."""
from __future__ import annotations

import os
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.sensitivity import run_sensitivity, SensitivityResult
from financial_kg.engine.derived_metrics import DerivedMetrics
from financial_kg.viz.tornado_chart import render_tornado_html, render_spider_chart

st.set_page_config(layout="wide")
st.title("📈 分析模块")

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
tab_sensitivity, tab_history = st.tabs(["敏感性分析", "历史记录"])


# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Sensitivity Analysis
# ══════════════════════════════════════════════════════════════════════════════
with tab_sensitivity:
    st.subheader("参数选择")

    # Filter bar
    col_f1, col_f2, col_f3 = st.columns([2, 2, 3])
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
            if st.button("运行敏感性分析", type="primary", use_container_width=True, disabled=not selected_keys):
                param_cells = [param_options[k] for k in selected_keys]

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

        if result:
            st.divider()
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
# Tab 2: Sensitivity History
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    history = db.list_sensitivity(task.id)

    if not history:
        st.info("暂无敏感性分析历史")
        st.stop()

    st.subheader("历史分析记录")

    # History table
    hist_rows = []
    for h in history:
        params_str = ", ".join(p["name"] for p in h["params"][:5])
        if len(h["params"]) > 5:
            params_str += f" ... +{len(h['params']) - 5}"
        base = h["base_metrics"]
        irr = base.get("irr_after_tax")
        hist_rows.append({
            "ID": h["id"],
            "运行时间": h["created_at"][:19],
            "参数": params_str,
            "扰动": ", ".join(f"{p:+.0%}" for p in h["perturbations"]),
            "基准IRR": f"{irr * 100:.2f}%" if irr else "—",
            "场景数": len(h["scenarios"]),
        })

    st.dataframe(hist_rows, use_container_width=True, hide_index=True, height=300)

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

    else:
        # Single record view
        st.divider()
        hist_ids = {f"[{h['id']}] {h['run_name']} ({h['created_at'][:19]})": h for h in history}
        selected_hist = st.selectbox("查看历史详情", list(hist_ids.keys()), label_visibility="collapsed")

        if selected_hist:
            h = hist_ids[selected_hist]

            detail_row = st.columns([8, 1])
            with detail_row[1]:
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
