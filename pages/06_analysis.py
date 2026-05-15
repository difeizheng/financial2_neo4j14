"""Page 6: Financial analysis — sensitivity, history."""
from __future__ import annotations

import json
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
        })
    return rows


all_params = _build_params(task.id, task.output_dir)
all_param_names = sorted(set(r["name"] for r in all_params if r["name"]))
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
    param_options = {f"{r['name']} ({r['sheet']}) = {r['value']:,.2f}": (r["cell_id"], r["name"]) for r in filtered}
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

        # Display results
        result_key = f"sens_result_{task.id}"
        result: SensitivityResult | None = st.session_state.get(result_key)

        if result:
            st.divider()
            st.subheader("分析结果")

            # Base metrics summary
            st.caption(f"基准: IRR={result.base_metrics.irr_after_tax * 100:.2f}%, "
                       f"NPV={result.base_metrics.npv_after_tax:,.0f}, "
                       f"回收期={result.base_metrics.payback_period or 0:.2f}年, "
                       f"DSCR均值={result.base_metrics.dscr_avg or 0:.2f}")

            # Charts
            chart_row = st.columns(2)
            with chart_row[0]:
                tornado_html = render_tornado_html(result)
                if tornado_html:
                    st.components.v1.html(tornado_html, height=450, scrolling=False)
                else:
                    st.info("无可视化数据")

            with chart_row[1]:
                spider_html = render_spider_chart(result)
                if spider_html:
                    st.components.v1.html(spider_html, height=450, scrolling=False)

            # Summary table
            st.divider()
            st.subheader("汇总数据")

            if result.summary_table:
                st.dataframe(result.summary_table, use_container_width=True, hide_index=True)
            else:
                st.info("无汇总数据")

            # Detailed scenario table
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

    st.dataframe(hist_rows, use_container_width=True, hide_index=True, height=400)

    # Detail / delete
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

        # Reconstruct result for visualization
        base_m = h["base_metrics"]
        scenarios = h["scenarios"]

        # Show summary as table
        summary_data = []
        by_param: dict[str, dict] = {}
        for s in scenarios:
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
            st.subheader("IRR 敏感性汇总表")
            st.dataframe(summary_data, use_container_width=True, hide_index=True)
