"""Page 3: Parameter workspace — two-column editor + results."""
from __future__ import annotations
import copy
import html
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.engine.snapshot import SnapshotDiff, create_snapshot
from financial_kg.engine.workspace import (
    WorkspaceState,
    load_workspace,
    save_workspace,
    Scenario,
    apply_and_recalc,
    rollback_record,
    get_key_metrics,
)
from financial_kg.viz.propagation_graph import build_propagation_data
from financial_kg.viz.echarts_template import render_propagation_html
from financial_kg.viz.echarts_compare import render_compare_html
from financial_kg.engine.excel_export import export_modified_excel, find_original_excel

st.set_page_config(layout="wide")

# ── Top bar ──────────────────────────────────────────────────────────────────

st.title("⚙️ 参数工作台")

# ── Engine selector ─────────────────────────────────────────────────────────

engine_row = st.columns([3, 8])
with engine_row[0]:
    engine_choice = st.radio(
        "计算引擎",
        ["知识图谱重算引擎", "通用模型引擎"],
        index=0,
        horizontal=True,
        key="engine_select",
    )

USE_GENERIC_ENGINE = engine_choice == "通用模型引擎"

# ── Generic engine path ─────────────────────────────────────────────────────

if USE_GENERIC_ENGINE:
    from dataclasses import replace as _replace
    from financial_model.params import (
        ConstructionParams as _CP,
        OperatingParams as _OP,
        TaxParams as _TP,
        LoanTerms as _LT,
    )
    from financial_model.params.depreciation import AssetCategory as _AC
    from financial_model.analysis.types import ModelConfig as _MC
    from financial_model.engines.orchestrator import AllResults as _AR
    from financial_model.export.excel_exporter import export_excel as _export_excel

    if "gm_config_recalc" not in st.session_state:
        st.session_state.gm_config_recalc = _MC.from_excel_v17()
    if "gm_results_recalc" not in st.session_state:
        st.session_state.gm_results_recalc = None

    _mcfg: _MC = st.session_state.gm_config_recalc

    # ── Left: param editor ──
    gm_left, gm_right = st.columns([3, 2])

    with gm_left:
        gm_tabs = st.tabs(["🏗️ 建设期", "💰 融资", "⚡ 运营", "🏛️ 税务", "📉 折旧"])

        with gm_tabs[0]:
            _c = _mcfg.construction
            _cs = st.date_input("建设期起始", value=_c.construction_start, key="gm_r_start")
            _ce = st.date_input("建设期结束", value=_c.construction_end, key="gm_r_end")
            _oy = st.number_input("运营年限", min_value=5, max_value=80, value=_c.operation_years, key="gm_r_oy")
            try:
                _pc = _CP(construction_start=_cs, construction_end=_ce, operation_years=_oy)
                _ps = _pc.summary()
                st.info(f"建设期 {_ps['construction_months']}月 ({_ps['construction_years']}年) → {_pc.operation_end}")
            except ValueError as e:
                st.error(str(e))

        with gm_tabs[1]:
            _f = _mcfg.financing
            _lt_rate = st.number_input("长期贷款利率", min_value=0.0, max_value=0.20,
                value=_f.long_term_loan.annual_rate, step=0.005, format="%.4f", key="gm_r_lt")
            _lt_term = st.number_input("还款期限(年)", min_value=5, max_value=50,
                value=_f.long_term_loan.repayment_term_years, step=1, key="gm_r_ltterm")
            _st_rate = st.number_input("短期贷款利率", min_value=0.0, max_value=0.20,
                value=_f.short_term_loan_rate, step=0.005, format="%.4f", key="gm_r_st")

        with gm_tabs[2]:
            _o = _mcfg.operating
            _cap = st.number_input("装机容量(MW)", min_value=100.0, value=_o.installed_capacity_mw, step=100.0, key="gm_r_cap")
            _uh = st.number_input("年利用小时(h)", min_value=0.0, value=_o.annual_utilization_hours, step=50.0, key="gm_r_uh")
            _gp = st.number_input("上网电价(元/kWh)", min_value=0.0, value=_o.grid_price, step=0.01, format="%.4f", key="gm_r_gp")
            _pp = st.number_input("抽水电价(元/kWh)", min_value=0.0, value=_o.pump_price, step=0.01, format="%.5f", key="gm_r_pp")

        with gm_tabs[3]:
            _t = _mcfg.tax
            _vat = st.number_input("增值税率", min_value=0.0, max_value=1.0, value=_t.vat_rate, step=0.01, format="%.2f", key="gm_r_vat")
            _itr = st.number_input("所得税率", min_value=0.0, max_value=1.0, value=_t.income_tax_rate, step=0.01, format="%.2f", key="gm_r_itr")

        with gm_tabs[4]:
            _d = _mcfg.depreciation
            _fa_orig = st.number_input("固定资产原值(万元)", min_value=0.0, value=_d.fixed_assets.original_value, step=10000.0, format="%.2f", key="gm_r_fao")
            _fa_life = st.number_input("折旧年限(年)", min_value=5, max_value=50, value=_d.fixed_assets.useful_life, step=1, key="gm_r_fal")

        st.divider()
        if st.button("🚀 运行通用模型", type="primary", use_container_width=True):
            try:
                new_mcfg = _MC(
                    construction=_CP(construction_start=_cs, construction_end=_ce, operation_years=_oy),
                    financing=_replace(
                        _mcfg.financing,
                        long_term_loan=_replace(_mcfg.financing.long_term_loan, annual_rate=_lt_rate, repayment_term_years=_lt_term),
                        short_term_loan_rate=_st_rate,
                    ),
                    operating=_OP(
                        installed_capacity_mw=_cap, annual_utilization_hours=_uh,
                        grid_price=_gp, pump_price=_pp,
                        capacity_price=_o.capacity_price,
                        auxiliary_power_rate=_o.auxiliary_power_rate,
                        production_ratios=_o.production_ratios,
                    ),
                    tax=_TP(
                        vat_rate=_vat, income_tax_rate=_itr,
                        surcharge_rate=_t.surcharge_rate,
                        loss_carryforward_years=_t.loss_carryforward_years,
                        deductible_input_vat=_t.deductible_input_vat,
                        deductible_vat_amort_years=_t.deductible_vat_amort_years,
                    ),
                    depreciation=_replace(
                        _mcfg.depreciation,
                        fixed_assets=_AC("固定资产", _fa_orig, _fa_life, _d.fixed_assets.residual_rate),
                    ),
                    investment=_mcfg.investment,
                    discount_rate=_mcfg.discount_rate,
                )
                st.session_state.gm_config_recalc = new_mcfg
                with st.spinner("运行通用模型 (9个引擎)..."):
                    st.session_state.gm_results_recalc = new_mcfg.to_orchestrator().run()
            except (ValueError, TypeError) as e:
                st.error(f"参数错误: {e}")

    # ── Right: results ──
    with gm_right:
        _res: _AR | None = st.session_state.gm_results_recalc
        if _res is None:
            st.info("👈 设置参数后点击 **运行通用模型**")
        else:
            dm = _res.derived_metrics
            c1 = st.columns(4)
            c1[0].metric("全投资IRR", f"{dm.irr_total:.2%}" if dm.irr_total else "N/A")
            c1[1].metric("NPV(万元)", f"{dm.npv_total:,.0f}" if dm.npv_total else "N/A")
            c1[2].metric("最低DSCR", f"{dm.dscr_min:.2f}" if dm.dscr_min else "N/A")
            c1[3].metric("回收期(年)", f"{dm.payback_static:.1f}" if dm.payback_static else "N/A")

            st.divider()
            report_sel = st.selectbox("查看报表", [
                "投资概算", "资金筹措", "折旧摊销", "成本费用",
                "收入税金", "利润表(全投资)", "利润表(资本金)",
                "现金流(全投资)", "现金流(资本金)", "现金流(财务计划)",
                "资产负债表",
            ], key="gm_r_report")

            _DF_MAP = {
                "投资概算": _res.investment,
                "资金筹措": _res.financing.annual_summary,
                "折旧摊销": _res.depreciation,
                "成本费用": _res.cost,
                "收入税金": _res.revenue,
                "利润表(全投资)": _res.pnl_total.data,
                "利润表(资本金)": _res.pnl_equity.data,
                "现金流(全投资)": _res.cf_total.data,
                "现金流(资本金)": _res.cf_equity.data,
                "现金流(财务计划)": _res.cf_plan.data,
                "资产负债表": _res.balance_sheet.data,
            }
            st.dataframe(_DF_MAP[report_sel], use_container_width=True, height=400)

            # Export
            st.divider()
            if st.button("📥 导出Excel(13Sheet)", key="gm_r_export"):
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                    _export_excel(_res, tmp.name, project_name="抽蓄项目")
                with open(tmp.name, "rb") as f:
                    st.download_button(
                        "⬇️ 下载", data=f.read(),
                        file_name="通用模型_财务报表.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

    # Stop here — don't render the knowledge-graph path
    st.stop()

# ── Knowledge-graph engine path (original) ─────────────────────────────────

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

top_row = st.columns([3, 2, 1, 1])

with top_row[0]:
    task_options = {f"{t.id} — {t.filename}": t for t in tasks}
    selected_label = st.selectbox("任务", list(task_options.keys()), label_visibility="collapsed")
    task = task_options[selected_label]

@st.cache_resource(show_spinner="加载图谱...")
def _load_base(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)

base_graph = _load_base(task.id, task.output_dir)
ws: WorkspaceState = load_workspace(task.id)

# ── Scenario management ───────────────────────────────────────────────────

scenario_names = list(ws.scenarios.keys())

# 场景选择行
scn_row = st.columns([3, 2, 2, 2, 2, 4])
with scn_row[0]:
    selected_scenario = st.selectbox(
        "场景",
        scenario_names,
        index=scenario_names.index(ws.active_scenario) if ws.active_scenario in scenario_names else 0,
        label_visibility="collapsed",
        key="scn_select",
    )
    if selected_scenario != ws.active_scenario:
        ws.active_scenario = selected_scenario
        save_workspace(ws)
        # 切换场景：优先加载已保存的snapshot，避免重复重算
        from financial_kg.storage.task_db import TaskDB as _TaskDB
        from financial_kg.engine.snapshot import load_snapshot as _load_snapshot
        _db = _TaskDB()
        _snaps = _db.list_snapshots(task.id)
        # 找该场景的最新snapshot（名称包含场景名）
        _scene_snaps = [s for s in _snaps if selected_scenario in s.name]
        if _scene_snaps:
            # 最新snapshot（created_at DESC排序）
            _latest_snap = _scene_snaps[0]
            try:
                _snap_data = _load_snapshot(_latest_snap.filepath)
                # 创建working_graph并应用snapshot值
                working_graph_cached = copy.deepcopy(base_graph)
                for cid, val in _snap_data.values.items():
                    cell = working_graph_cached.cells.get(cid)
                    if cell:
                        cell.value = val
                # 同步更新indicator summary_value（用value_cell_id指向的值）
                for ind_id, ind in working_graph_cached.indicators.items():
                    # 优先使用value_cell_id指向的cell值
                    if ind.value_cell_id:
                        cell = working_graph_cached.cells.get(ind.value_cell_id)
                        if cell and cell.value is not None:
                            ind.summary_value = cell.value
                    else:
                        # 无value_cell_id时，尝试从cell_ids中取第一个有效值
                        for cid in ind.cell_ids:
                            cell = working_graph_cached.cells.get(cid)
                            if cell and cell.value is not None:
                                ind.summary_value = cell.value
                                break
                st.session_state[f"wg_{task.id}"] = working_graph_cached
                st.session_state[f"rr_{task.id}"] = None  # 没有新的重算结果，但有cached值
                st.toast(f"已加载场景「{selected_scenario}」的缓存数据", icon="⚡")
            except Exception as e:
                # snapshot加载失败，清除旧结果
                st.session_state.pop(f"rr_{task.id}", None)
                st.session_state.pop(f"wg_{task.id}", None)
                st.toast(f"加载失败，显示基准值", icon="⚠️")
        else:
            # 该场景没有snapshot，检查是否有overrides
            scenario = ws.scenarios.get(selected_scenario)
            if scenario and scenario.overrides:
                # 有overrides但没snapshot，需要预览重算
                working_graph_preview = copy.deepcopy(base_graph)
                preview_result = apply_and_recalc(working_graph_preview, ws, base_graph, record_history=False)
                st.session_state[f"wg_{task.id}"] = working_graph_preview
                st.session_state[f"rr_{task.id}"] = preview_result
                st.toast(f"场景「{selected_scenario}」预览重算完成", icon="🔄")
            else:
                # 无overrides，显示基准
                st.session_state.pop(f"rr_{task.id}", None)
                st.session_state.pop(f"wg_{task.id}", None)
        st.rerun()

with scn_row[1]:
    if st.button("✏️ 重命名", use_container_width=True, key="scn_rename_btn"):
        st.session_state["show_rename"] = True
with scn_row[2]:
    if st.button("📋 复制场景", use_container_width=True, key="scn_copy_btn"):
        st.session_state["show_copy"] = True
with scn_row[3]:
    if st.button("🧹 清空覆盖", use_container_width=True, key="scn_clear_btn"):
        scenario = ws.scenarios.get(ws.active_scenario)
        if scenario and scenario.overrides:
            count = len(scenario.overrides)
            scenario.overrides = {}
            ws.pending_edits = {}
            save_workspace(ws)
            st.toast(f"已清空 {count} 个参数覆盖", icon="🧹")
            st.rerun()
        else:
            st.toast("当前场景无参数覆盖", icon="ℹ️")
with scn_row[4]:
    if len(scenario_names) > 1 and ws.active_scenario != "基准":
        if st.button("🗑 删除", use_container_width=True, key="scn_del_btn"):
            st.session_state["show_delete_confirm"] = True
    elif st.button("🗑 删除", use_container_width=True, key="scn_del_btn", disabled=True):
        pass

# 新场景创建行
new_row = st.columns([3, 2, 10])
with new_row[0]:
    new_name = st.text_input("新场景名称", placeholder="输入名称后点击创建", label_visibility="collapsed", key="scn_new_name")
with new_row[1]:
    if st.button("+ 新建场景", use_container_width=True, key="scn_new"):
        if new_name.strip() and new_name.strip() not in ws.scenarios:
            ws.scenarios[new_name.strip()] = Scenario(
                id=str(uuid.uuid4())[:8],
                task_id=task.id,
                name=new_name.strip(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            ws.active_scenario = new_name.strip()
            save_workspace(ws)
            st.toast(f"已创建场景「{new_name.strip()}」", icon="✅")
            st.rerun()
        elif new_name.strip():
            st.toast("场景已存在", icon="⚠️")

# 重命名对话框
if st.session_state.get("show_rename"):
    with st.expander("重命名场景", expanded=True):
        rename_col_a, rename_col_b, rename_col_c = st.columns([3, 1, 1])
        with rename_col_a:
            new_rename = st.text_input("新名称", value=ws.active_scenario, key="rename_input")
        with rename_col_b:
            if st.button("确认", type="primary", use_container_width=True, key="rename_confirm"):
                if new_rename.strip() and new_rename.strip() != ws.active_scenario and new_rename.strip() not in ws.scenarios:
                    old_name = ws.active_scenario
                    # 重建 scenarios dict 以新 key 保存
                    old_scenario = ws.scenarios.pop(old_name)
                    old_scenario.name = new_rename.strip()
                    ws.scenarios[new_rename.strip()] = old_scenario
                    ws.active_scenario = new_rename.strip()
                    save_workspace(ws)
                    st.session_state["show_rename"] = False
                    st.toast("已重命名", icon="✅")
                    st.rerun()
                elif new_rename.strip() in ws.scenarios:
                    st.toast("名称已存在", icon="⚠️")
        with rename_col_c:
            if st.button("取消", use_container_width=True, key="rename_cancel"):
                st.session_state["show_rename"] = False
                st.rerun()

# 复制场景对话框
if st.session_state.get("show_copy"):
    with st.expander("复制场景", expanded=True):
        copy_col_a, copy_col_b, copy_col_c = st.columns([3, 1, 1])
        with copy_col_a:
            copy_name = st.text_input("新场景名称", value=f"{ws.active_scenario} (副本)", key="copy_input")
        with copy_col_b:
            if st.button("确认复制", type="primary", use_container_width=True, key="copy_confirm"):
                if copy_name.strip() and copy_name.strip() not in ws.scenarios:
                    src = ws.scenarios[ws.active_scenario]
                    ws.scenarios[copy_name.strip()] = Scenario(
                        id=str(uuid.uuid4())[:8],
                        task_id=task.id,
                        name=copy_name.strip(),
                        created_at=datetime.now(timezone.utc).isoformat(),
                        overrides=dict(src.overrides),
                    )
                    ws.active_scenario = copy_name.strip()
                    save_workspace(ws)
                    st.session_state["show_copy"] = False
                    st.toast("已复制场景", icon="✅")
                    st.rerun()
                elif copy_name.strip():
                    st.toast("名称已存在", icon="⚠️")
        with copy_col_c:
            if st.button("取消", use_container_width=True, key="copy_cancel"):
                st.session_state["show_copy"] = False
                st.rerun()

# 删除确认对话框
if st.session_state.get("show_delete_confirm"):
    with st.expander("确认删除", expanded=True):
        del_col_a, del_col_b, del_col_c = st.columns([4, 1, 1])
        with del_col_a:
            st.warning(f"确定要删除场景「{ws.active_scenario}」吗？此操作不可撤销。")
        with del_col_b:
            if st.button("确认删除", type="primary", use_container_width=True, key="del_confirm"):
                if ws.active_scenario != "基准":
                    del ws.scenarios[ws.active_scenario]
                    ws.active_scenario = "基准"
                    save_workspace(ws)
                    st.session_state["show_delete_confirm"] = False
                    st.toast("已删除场景", icon="🗑")
                    st.rerun()
        with del_col_c:
            if st.button("取消", use_container_width=True, key="del_cancel"):
                st.session_state["show_delete_confirm"] = False
                st.rerun()

# ── Export buttons ──────────────────────────────────────────────────────

export_row = st.columns([2, 2, 2, 8])
with export_row[0]:
    if st.button("📥 导出当前场景到 Excel", use_container_width=True, key="export_scenario"):
        original_excel = find_original_excel(task.id, task.output_dir)
        if original_excel:
            # Build snapshot values from scenario overrides + pending edits
            scenario = ws.scenarios.get(ws.active_scenario)
            snapshot_vals = {}
            if scenario:
                snapshot_vals.update(scenario.overrides)
            snapshot_vals.update(ws.pending_edits)
            if snapshot_vals:
                export_dir = os.path.join(task.output_dir, "exports")
                os.makedirs(export_dir, exist_ok=True)
                out_path = os.path.join(export_dir, f"{ws.active_scenario}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx")
                try:
                    export_modified_excel(original_excel, snapshot_vals, out_path)
                    with open(out_path, "rb") as f:
                        st.download_button(
                            label="下载 Excel",
                            data=f,
                            file_name=os.path.basename(out_path),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_{ws.active_scenario}",
                        )
                    st.toast(f"已导出到 {out_path}", icon="✅")
                except Exception as e:
                    st.toast(f"导出失败: {e}", icon="❌")
            else:
                st.toast("当前场景无参数修改", icon="ℹ️")
        else:
            st.toast("未找到原始 Excel 文件", icon="⚠️")

with export_row[1]:
    if st.button("📊 导出场景对比报告", use_container_width=True, key="export_compare"):
        comp_base = st.session_state.get("comp_base", "基准")
        comp_targets = st.session_state.get("comp_targets", [])
        if comp_targets:
            all_scenarios_in_comp = [comp_base] + list(comp_targets)
            rows = []
            comp_key_ids = get_key_metrics(base_graph)
            for ind_id in comp_key_ids:
                ind = base_graph.indicators.get(ind_id)
                if not ind:
                    continue
                row = {"指标": ind.name or ind_id, "基准值": ind.summary_value}
                for scn_name in all_scenarios_in_comp:
                    scn = ws.scenarios.get(scn_name)
                    val = ind.summary_value
                    if scn and scn.overrides:
                        for cid, override_val in scn.overrides.items():
                            cell = base_graph.cells.get(cid)
                            if cell and cell.indicator_id == ind_id:
                                val = float(override_val)
                                break
                    row[scn_name] = val
                    if val is not None and ind.summary_value is not None:
                        try:
                            delta = float(val) - float(ind.summary_value)
                            row[f"{scn_name} 差异"] = f"{delta:+.2f}"
                        except (ValueError, TypeError):
                            row[f"{scn_name} 差异"] = "—"
                rows.append(row)
            if rows:
                df_compare = pd.DataFrame(rows)
                csv = df_compare.to_csv(index=False, encoding="utf-8-sig")
                st.download_button(
                    label="下载对比报告 (CSV)",
                    data=csv,
                    file_name=f"scenario_compare_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key="dl_compare",
                )
                st.toast("对比报告已生成", icon="✅")
        else:
            st.toast("请先在场景对比 tab 选择对比场景", icon="ℹ️")

st.divider()

# ── Main two-column layout ──────────────────────────────────────────────────

editor_col, results_col = st.columns([3, 2])

# ── Shared data ──────────────────────────────────────────────────────────────

def _build_param_cells(graph):
    rows = []
    for cid, cell in graph.cells.items():
        # Skip 参数输入表 C列 and D列
        if cid.startswith("参数输入表_"):
            parts = cid.rsplit("_", 2)
            if len(parts) == 3 and parts[2] in ("C", "D", "G", "H"):
                continue
        ind_name = ""
        ind_category = ""
        if cell.indicator_id and cell.indicator_id in graph.indicators:
            ind = graph.indicators[cell.indicator_id]
            ind_name = ind.name or ""
            ind_category = ind.category or ""
        tbl_name = ""
        if cell.table_id and cell.table_id in graph.tables:
            tbl_name = graph.tables[cell.table_id].name
        rows.append({
            "Cell ID": cid,
            "Indicator 名称": ind_name,
            "类别": ind_category,
            "Table 名称": tbl_name,
            "Sheet": cell.sheet or "",
            "类型": cell.data_type or "number",
            "当前值": cell.value,
        })
    return rows

@st.cache_data(show_spinner="构建参数列表...")
def _cached_param_cells(task_id: str, output_dir: str):
    g = load_graph(os.path.join(output_dir, f"{task_id}_cells.json"))
    return _build_param_cells(g)

all_param_cells = _cached_param_cells(task.id, task.output_dir)
all_sheets = sorted(set(r["Sheet"] for r in all_param_cells if r["Sheet"]))
cell_lookup = {r["Cell ID"]: r for r in all_param_cells}

scenario = ws.scenarios.get(ws.active_scenario)

# ── Left: Editor workspace ──────────────────────────────────────────────────

with editor_col:
    st.subheader("📝 编辑参数")

    # Global filter bar
    filter_a, filter_b, filter_c = st.columns([2, 1, 1])
    with filter_a:
        search_kw = st.text_input("搜索", placeholder="Cell ID / Indicator / Table", label_visibility="collapsed", key="p_search")
    with filter_b:
        selected_sheets = st.multiselect("Sheet", all_sheets, default=[], label_visibility="collapsed", key="p_sheets")
    with filter_c:
        all_types = sorted(set(r["类型"] for r in all_param_cells if r["类型"]))
        selected_types = st.multiselect("类型", all_types, default=["number"], label_visibility="collapsed", key="p_types")

    # Group by category
    category_groups: dict[str, list[dict]] = {}
    for r in all_param_cells:
        if selected_types and r["类型"] not in selected_types:
            continue
        if selected_sheets and r["Sheet"] not in selected_sheets:
            continue
        if search_kw:
            kw = search_kw.lower()
            if kw not in r["Cell ID"].lower() and kw not in r["Indicator 名称"].lower() and kw not in r["Table 名称"].lower():
                continue
        cat = r["类别"] or "未分类"
        category_groups.setdefault(cat, []).append(r)

    # Business-ordered category priority (input → calculation → result)
    _CATEGORY_PRIORITY = [
        "项目基础参数",
        "工程计划",
        "生产技术",
        "工程概算",
        "投资",
        "资金",
        "融资",
        "利率",
        "电价",
        "收入",
        "成本",
        "费用",
        "税金",
        "税收",
        "折旧",
        "摊销",
        "利润",
        "现金流",
        "财务评价指标",
        "评价指标",
    ]

    def _cat_priority(cat: str) -> int:
        for i, kw in enumerate(_CATEGORY_PRIORITY):
            if kw in cat:
                return i
        return len(_CATEGORY_PRIORITY)  # unmatched at end

    sorted_cats = sorted(
        [c for c in category_groups if c != "未分类"],
        key=_cat_priority,
    )
    if "未分类" in category_groups:
        sorted_cats.append("未分类")

    if not sorted_cats:
        st.info("无匹配参数")
    else:
        cat_tabs = st.tabs([f"{c} ({len(category_groups[c])})" for c in sorted_cats])

        pending_key = f"pending_{ws.active_scenario}"
        global_pending: dict[str, Any] = st.session_state.get(pending_key, {})

        for ti, cat in enumerate(sorted_cats):
            with cat_tabs[ti]:
                cat_rows = category_groups[cat]
                if not cat_rows:
                    st.info("当前类别无参数")
                    continue

                df = pd.DataFrame(cat_rows)
                df["场景值"] = df["当前值"].copy()

                # Pre-fill scenario overrides
                if scenario:
                    for idx in df.index:
                        cid = df.at[idx, "Cell ID"]
                        if cid in scenario.overrides:
                            df.at[idx, "场景值"] = scenario.overrides[cid]

                # Pre-fill pending edits
                for idx in df.index:
                    cid = df.at[idx, "Cell ID"]
                    if cid in global_pending:
                        df.at[idx, "场景值"] = global_pending[cid]

                edited_df = st.data_editor(
                    df,
                    column_config={
                        "Cell ID": st.column_config.TextColumn("Cell ID", disabled=True, width="medium"),
                        "Indicator 名称": st.column_config.TextColumn("Indicator", disabled=True),
                        "Table 名称": st.column_config.TextColumn("Table", disabled=True),
                        "Sheet": st.column_config.TextColumn("Sheet", disabled=True, width="small"),
                        "类型": st.column_config.TextColumn("类型", disabled=True, width="small"),
                        "当前值": st.column_config.NumberColumn("当前值", disabled=True, width="small"),
                        "场景值": st.column_config.NumberColumn("场景值", width="small"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key=f"pedit_{ws.active_scenario}_{cat}",
                )

                # Collect per-tab pending edits
                for _, row in edited_df.iterrows():
                    cid = row["Cell ID"]
                    new_val = row["场景值"]
                    old_val = row["当前值"]
                    try:
                        if abs(float(new_val) - float(old_val)) > 1e-9:
                            global_pending[cid] = new_val
                    except (ValueError, TypeError):
                        if new_val != old_val:
                            global_pending[cid] = new_val

        # Save global pending edits
        st.session_state[pending_key] = global_pending
        ws.pending_edits = global_pending

        # ── 统一修改清单 ──────────────────────────────────────────────
        st.divider()
        st.subheader("📋 修改清单")

        if global_pending:
            # 汇总统计
            pending_sheets = set()
            pending_indicators = set()
            pending_rows = []
            for cid, new_val in global_pending.items():
                info = cell_lookup.get(cid, {})
                pending_sheets.add(info.get("Sheet", ""))
                ind_name = info.get("Indicator 名称", cid)
                pending_indicators.add(ind_name)
                old_val = info.get("当前值", None)
                try:
                    delta = float(new_val) - float(old_val) if old_val is not None else None
                    delta_pct = (delta / abs(float(old_val)) * 100) if (old_val is not None and float(old_val) != 0) else None
                except (ValueError, TypeError):
                    delta = None
                    delta_pct = None
                pending_rows.append({
                    "Cell ID": cid,
                    "Indicator": ind_name,
                    "旧值": old_val,
                    "新值": new_val,
                    "变化": delta,
                    "变化%": delta_pct,
                })

            stat_a, stat_b, stat_c = st.columns(3)
            stat_a.metric("已修改参数", len(global_pending))
            stat_b.metric("涉及 Sheet", len(pending_sheets - {""}))
            stat_c.metric("涉及 Indicator", len(pending_indicators))

            # 修改清单表格
            pending_df = pd.DataFrame(pending_rows)
            st.dataframe(
                pending_df,
                use_container_width=True,
                hide_index=True,
                height=min(len(pending_rows) * 35 + 38, 300),
                column_config={
                    "Cell ID": st.column_config.TextColumn("Cell ID", width="medium"),
                    "Indicator": st.column_config.TextColumn("Indicator", width="medium"),
                    "旧值": st.column_config.NumberColumn("旧值", width="small"),
                    "新值": st.column_config.NumberColumn("新值", width="small"),
                    "变化": st.column_config.NumberColumn("变化", width="small"),
                    "变化%": st.column_config.NumberColumn("变化%", format="%.1f%%", width="small"),
                },
            )

            # 单条删除
            st.caption("点击移除单项修改：")
            del_cols = st.columns(min(len(pending_rows), 5))
            for i, row_data in enumerate(pending_rows):
                with del_cols[i % 5]:
                    label = f"↩ {row_data['Cell ID'][:18]}{'…' if len(row_data['Cell ID']) > 18 else ''}"
                    if st.button(label, key=f"pend_del_{row_data['Cell ID']}", use_container_width=True):
                        global_pending.pop(row_data["Cell ID"])
                        st.session_state[pending_key] = global_pending
                        ws.pending_edits = global_pending
                        save_workspace(ws)
                        st.rerun()
        else:
            st.info("暂无待应用的修改")

        # Action buttons
        act_a, act_b, act_c = st.columns([1, 1, 2])
        with act_a:
            if st.button("清空修改", use_container_width=True):
                st.session_state[pending_key] = {}
                ws.pending_edits = {}
                save_workspace(ws)
                st.rerun()
        with act_b:
            if st.button("保存到场景", use_container_width=True):
                if scenario and global_pending:
                    scenario.overrides.update(global_pending)
                    st.session_state[pending_key] = {}
                    ws.pending_edits = {}
                    save_workspace(ws)
                    st.toast(f"已保存 {len(global_pending)} 个修改到「{ws.active_scenario}」", icon="💾")
                    st.rerun()
        with act_c:
            # 影响预览
            if global_pending:
                affected_preview = set()
                for cid in global_pending:
                    affected_preview.add(cid)
                    cell = base_graph.cells.get(cid)
                    if cell:
                        # 查找直接依赖此 cell 的其他 cell（ predecessors = 依赖此节点的）
                        for other_cid in base_graph.cell_graph.predecessors(cid):
                            affected_preview.add(other_cid)
                st.caption(f"预计影响约 {len(affected_preview)} 个单元格")

            apply_clicked = st.button("🔄 应用并重算", type="primary", use_container_width=True)

        if apply_clicked:
            if not global_pending and not (scenario and scenario.overrides):
                st.toast("暂无修改可应用", icon="ℹ️")
            else:
                working_graph = copy.deepcopy(base_graph)

                # Ensure a "before" snapshot exists for comparison
                base_snap_key = f"base_snap_{task.id}_{ws.active_scenario}"
                base_snap_name = st.session_state.get(base_snap_key)
                if not base_snap_name:
                    base_snap_name = f"基准_{ws.active_scenario}"
                    base_snap = create_snapshot(base_graph, task.id, base_snap_name)
                    db.save_snapshot(str(uuid.uuid4())[:8], task.id, base_snap_name, base_snap.filepath)
                    st.session_state[base_snap_key] = base_snap_name
                    st.toast(f"已保存基准快照「{base_snap_name}」", icon="📸")

                with st.spinner("重算中..."):
                    result = apply_and_recalc(working_graph, ws, base_graph)

                # Create "after" snapshot for comparison page
                from datetime import datetime as _dt
                snap_name = f"{ws.active_scenario}_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
                snap = create_snapshot(working_graph, task.id, snap_name)
                db.save_snapshot(str(uuid.uuid4())[:8], task.id, snap_name, snap.filepath)

                st.session_state[f"wg_{task.id}"] = working_graph
                st.session_state[f"rr_{task.id}"] = result
                st.session_state[f"auto_viz_{task.id}"] = True
                iter_info = f"，SCC 迭代 {result.scc_iterations} 次" if result.scc_iterations else ""

                # Show perf info
                if result.perf:
                    p = result.perf
                    st.toast(
                        f"重算完成：{result.affected_count} 个变化{iter_info} | "
                        f"总耗时 {p['total_s']:.1f}s | "
                        f"快速路径命中率 {p['fast_pct']:.0f}% ({p['fast_hits']}/{p['eval_count']})"
                    )
                else:
                    st.toast(f"重算完成：{result.affected_count} 个变化{iter_info}，快照「{snap_name}」已保存（可与「{base_snap_name}」对比）", icon="✅")
                st.rerun()

# ── Right: Results panel ────────────────────────────────────────────────────

recalc_result = st.session_state.get(f"rr_{task.id}")
working_graph = st.session_state.get(f"wg_{task.id}")

with results_col:
    st.subheader("📊 结果面板")

    # ── Perf panel ───────────────────────────────────────────────────────
    if recalc_result and recalc_result.perf:
        p = recalc_result.perf
        with st.expander("⚡ 性能分析", expanded=True):
            cols = st.columns(4)
            cols[0].metric("总耗时", f"{p['total_s']:.1f}s")
            cols[1].metric("受影响cell", str(p['affected']))
            cols[2].metric("快速路径", f"{p['fast_pct']:.0f}%")
            cols[3].metric("SCC迭代", str(p['scc_iters']))
            cols2 = st.columns(4)
            cols2[0].metric("下游发现", f"{p['downstream_s']:.2f}s")
            cols2[1].metric("InputPlan构建", f"{p['build_plan_s']:.2f}s")
            cols2[2].metric("InputMap构建", f"{p['build_input_s']:.2f}s")
            cols2[3].metric("公式评估", f"{p['eval_func_s']:.2f}s")

    r_tabs = st.tabs(["关键指标", "场景对比", "影响链", "修改历史", "重算设置", "批量修改"])

    # ── Tab 1: Key metrics ───────────────────────────────────────────────
    with r_tabs[0]:
        # 始终显示关键指标（基线 + 变化）
        key_metric_ids = get_key_metrics(base_graph)
        all_indicator_ids = list(base_graph.indicators.keys())

        # 用户可选关注指标（从workspace持久化读取，有序list）
        fav_key = f"fav_metrics_{task.id}"
        if fav_key not in st.session_state:
            # 从workspace读取，如果为空则用默认关键指标
            if ws.favorite_metrics:
                st.session_state[fav_key] = list(ws.favorite_metrics)  # 保持顺序
            else:
                st.session_state[fav_key] = list(key_metric_ids[:12])  # 默认前12个

        # Helper: 移动指标位置
        def _move_indicator(idx: int, direction: str):
            """移动指标位置（direction='up'或'down'）。"""
            fav_list = st.session_state[fav_key]
            if direction == "up" and idx > 0:
                fav_list[idx], fav_list[idx-1] = fav_list[idx-1], fav_list[idx]
            elif direction == "down" and idx < len(fav_list) - 1:
                fav_list[idx], fav_list[idx+1] = fav_list[idx+1], fav_list[idx]
            st.session_state[fav_key] = fav_list
            ws.favorite_metrics = fav_list
            save_workspace(ws)

        # ═══════════════════════════════════════════════════════════════════════════
        # 自定义关注指标面板（方案A：分组 + 搜索 + 详情）
        # ═══════════════════════════════════════════════════════════════════════════
        with st.expander("⭐ 自定义关注指标", expanded=False):
            # 顶部控制栏
            ctrl_cols = st.columns([3, 2, 1])
            with ctrl_cols[0]:
                search_kw = st.text_input(
                    "🔍 搜索",
                    placeholder="输入指标名称或Sheet名...",
                    key=f"fav_search_{task.id}",
                    label_visibility="collapsed",
                )
            with ctrl_cols[1]:
                scope_choice = st.radio(
                    "指标范围",
                    ["关键指标", "全部指标"],
                    horizontal=True,
                    key=f"fav_scope_{task.id}",
                    label_visibility="collapsed",
                )
            with ctrl_cols[2]:
                st.caption(f"已选 **{len(st.session_state[fav_key])}** 个")

            # 快捷操作
            quick_cols = st.columns([1, 1, 1, 8])
            with quick_cols[0]:
                if st.button("清空", key=f"fav_clear_{task.id}", use_container_width=True):
                    st.session_state[fav_key] = []
                    ws.favorite_metrics = []
                    save_workspace(ws)
                    st.rerun()
            with quick_cols[1]:
                if st.button("全选关键", key=f"fav_all_key_{task.id}", use_container_width=True):
                    st.session_state[fav_key] = list(key_metric_ids)
                    ws.favorite_metrics = list(key_metric_ids)
                    save_workspace(ws)
                    st.rerun()
            with quick_cols[2]:
                if st.button("全选全部", key=f"fav_all_{task.id}", use_container_width=True):
                    st.session_state[fav_key] = list(all_indicator_ids)
                    ws.favorite_metrics = list(all_indicator_ids)
                    save_workspace(ws)
                    st.rerun()

            # ── Sheet多选过滤 ─────────────────────────────────────────────────────
            # 获取所有Sheet列表
            all_sheets = sorted({ind.sheet or "未知" for ind in base_graph.indicators.values()})
            selected_sheets = st.multiselect(
                "📂 按Sheet筛选",
                all_sheets,
                default=[],  # 默认显示全部（不筛选）
                key=f"fav_sheet_filter_{task.id}",
                help="选择特定Sheet只显示该Sheet的指标，不选则显示全部",
            )

            # 确定要展示的指标范围
            base_ids = key_metric_ids if scope_choice == "关键指标" else all_indicator_ids

            # 搜索过滤
            if search_kw:
                kw = search_kw.lower()
                filtered_ids = [
                    ind_id for ind_id in base_ids
                    if kw in (base_graph.indicators[ind_id].name or "").lower()
                    or kw in (base_graph.indicators[ind_id].sheet or "").lower()
                ]
            else:
                filtered_ids = base_ids

            # Sheet过滤（如果有选择）
            if selected_sheets:
                filtered_ids = [
                    ind_id for ind_id in filtered_ids
                    if base_graph.indicators.get(ind_id) and (base_graph.indicators[ind_id].sheet or "未知") in selected_sheets
                ]

            # 按Sheet分组
            sheet_groups: dict[str, list[str]] = {}
            for ind_id in filtered_ids:
                ind = base_graph.indicators.get(ind_id)
                if ind:
                    sheet = ind.sheet or "未知"
                    if sheet not in sheet_groups:
                        sheet_groups[sheet] = []
                    sheet_groups[sheet].append(ind_id)

            # 展示分组（用container+divider代替嵌套expander）
            st.caption(f"共 {len(filtered_ids)} 个指标，按Sheet分组")

            # 用checkbox控制是否展示全部（折叠逻辑）
            show_all_sheets = st.checkbox("展开全部Sheet", value=False, key=f"fav_show_all_{task.id}")

            # 只展示前N个Sheet或全部
            max_sheets = 100 if show_all_sheets else 5
            sheet_names_sorted = sorted(sheet_groups.keys())

            for sheet_idx, sheet_name in enumerate(sheet_names_sorted[:max_sheets]):
                ind_ids_in_sheet = sheet_groups[sheet_name]

                # Sheet组标题
                st.markdown(f"**📂 {sheet_name}** ({len(ind_ids_in_sheet)}个)")

                # 每Sheet内部用3列checkbox（按行号排序）
                sheet_cols = st.columns(3)
                # 按ind.row排序（Excel行号）
                sorted_ind_ids = sorted(
                    ind_ids_in_sheet,
                    key=lambda x: (base_graph.indicators.get(x).row or 0, base_graph.indicators.get(x).name or "")
                )
                for idx, ind_id in enumerate(sorted_ind_ids):
                    ind = base_graph.indicators.get(ind_id)
                    if not ind:
                        continue

                    # 构建标签：名称 + 行号 + 当前值
                    name = ind.name or ind_id[:20]
                    row_info = f"第{ind.row}行" if ind.row else ""
                    val_info = ""
                    if ind.summary_value is not None:
                        try:
                            v = float(ind.summary_value)
                            if abs(v) > 1e6:
                                val_info = f"{v/1e6:.2f}M"
                            elif abs(v) > 1e3:
                                val_info = f"{v/1e3:.2f}K"
                            else:
                                val_info = f"{v:.2f}"
                        except (ValueError, TypeError):
                            val_info = str(ind.summary_value)[:10]

                    label = f"{name}"
                    detail = f"{row_info} | {val_info}" if row_info or val_info else ""

                    with sheet_cols[idx % 3]:
                        checked = ind_id in st.session_state[fav_key]
                        new_checked = st.checkbox(label, value=checked, key=f"fav_{ind_id}_{task.id}")
                        # 状态变化时更新list + 保存到workspace
                        fav_list = st.session_state[fav_key]
                        if new_checked and ind_id not in fav_list:
                            fav_list.append(ind_id)  # 添加到末尾
                            st.session_state[fav_key] = fav_list
                            ws.favorite_metrics = fav_list
                            save_workspace(ws)
                        elif not new_checked and ind_id in fav_list:
                            fav_list.remove(ind_id)  # 从list移除
                            st.session_state[fav_key] = fav_list
                            ws.favorite_metrics = fav_list
                            save_workspace(ws)
                        if detail:
                            st.caption(detail, unsafe_allow_html=False)

                # Sheet组分隔
                if sheet_idx < len(sheet_names_sorted[:max_sheets]) - 1:
                    st.divider()

            # 如果有更多Sheet未显示
            if len(sheet_names_sorted) > max_sheets:
                st.caption(f"还有 {len(sheet_names_sorted) - max_sheets} 个Sheet未显示，勾选「展开全部Sheet」查看")

        # 检查是否有选择
        fav_list = st.session_state[fav_key]
        if not fav_list:
            st.info("未选择任何关注指标，请在上方勾选")
        else:
            # ── Helper: infer unit from indicator name ─────────────────────────────
            def _infer_unit(ind_name: str) -> str:
                """根据指标名称推断单位。"""
                name_lower = (ind_name or "").lower()
                if any(k in name_lower for k in ["irr", "内部收益率", "收益率"]):
                    return "%"
                elif any(k in name_lower for k in ["npv", "净现值", "现值"]):
                    return "万元"
                elif any(k in name_lower for k in ["回收期", "payback", "年限"]):
                    return "年"
                elif any(k in name_lower for k in ["dscr", "偿债覆盖率", "覆盖率"]):
                    return ""  # 无单位
                elif any(k in name_lower for k in ["收入", "成本", "利润", "投资", "费用", "现金流", "营业", "总额", "合计"]):
                    return "万元"
                elif any(k in name_lower for k in ["电量", "发电量", "用电量"]):
                    return "万kWh"
                elif any(k in name_lower for k in ["装机", "容量", "功率"]):
                    return "MW"
                elif any(k in name_lower for k in ["电价", "价格", "单价"]):
                    return "元/kWh"
                elif any(k in name_lower for k in ["利率", "利率", "率"]):
                    return "%"
                else:
                    return ""

            # ── Helper: format value compactly ─────────────────────────────────────
            def _format_value_compact(val: Any, unit: str) -> str:
                """格式化值（紧凑，大数用K/M）。"""
                if val is None:
                    return "—"
                try:
                    v = float(val)
                    # 根据单位调整显示
                    if unit == "%":
                        return f"{v:.2f}%"
                    elif unit == "年":
                        return f"{v:.2f}年"
                    elif unit in ["万元", "元"]:
                        # 大数用M/K
                        if abs(v) > 1e8:
                            return f"{v/1e8:.2f}亿"
                        elif abs(v) > 1e6:
                            return f"{v/1e6:.2f}M"
                        elif abs(v) > 1e4:
                            return f"{v/1e4:.2f}万"
                        else:
                            return f"{v:.0f}"
                    elif unit == "万kWh":
                        if abs(v) > 1e4:
                            return f"{v/1e4:.2f}亿kWh"
                        else:
                            return f"{v:.0f}万kWh"
                    elif unit == "MW":
                        return f"{v:.0f}MW"
                    elif unit == "元/kWh":
                        return f"{v:.4f}元/kWh"
                    else:
                        if abs(v) > 1e6:
                            return f"{v/1e6:.2f}M"
                        elif abs(v) > 1e3:
                            return f"{v/1e3:.2f}K"
                        else:
                            return f"{v:.2f}"
                except (ValueError, TypeError):
                    return str(val)[:12]

            # ── Helper: build card HTML string ───────────────────────────────────────
            def _build_card_html(idx: int, ind_id: str, task_id: str) -> str:
                """构建单个指标卡片HTML字符串（不渲染，只返回HTML）。"""
                # 从base_graph获取基准值
                ind = base_graph.indicators.get(ind_id)
                if not ind:
                    return ""

                old_val = ind.summary_value  # 基准值（用于对比）
                new_val = old_val

                # 优先使用working_graph的值（snapshot加载或重算后的值）
                if working_graph:
                    w_ind = working_graph.indicators.get(ind_id)
                    if w_ind:
                        new_val = w_ind.summary_value

                delta = None
                delta_pct = None
                if old_val is not None and new_val is not None:
                    try:
                        delta = float(new_val) - float(old_val)
                        if abs(delta) > 1e-9:
                            delta_pct = (delta / abs(float(old_val)) * 100) if old_val != 0 else None
                        else:
                            delta = None
                    except (ValueError, TypeError):
                        pass

                unit = _infer_unit(ind.name or "")
                label_raw = f"{ind.name or ind_id[:20]} ({unit})" if unit else (ind.name or ind_id[:20])
                label_with_unit = html.escape(label_raw)

                val_str = _format_value_compact(new_val, unit)
                val_escaped = html.escape(val_str)

                delta_str = ""
                if delta is not None:
                    delta_fmt = _format_value_compact(delta, unit)
                    if delta_pct is not None and abs(delta_pct) < 100:
                        delta_str = f"{delta_fmt} ({delta_pct:+.1f}%)"
                    else:
                        delta_str = delta_fmt
                delta_escaped = html.escape(delta_str) if delta_str else ""

                name_lower = (ind.name or "").lower()
                is_good = False
                if delta is not None:
                    if any(k in name_lower for k in ["irr", "npv", "dscr", "收入", "利润"]):
                        is_good = delta > 0
                    elif any(k in name_lower for k in ["回收期", "成本", "费用"]):
                        is_good = delta < 0
                    else:
                        is_good = delta > 0

                delta_color = "#22c55e" if is_good else "#ef4444"

                delta_html = ""
                if delta_escaped:
                    delta_html = f'<br/><span style="font-size:0.85em;color:{delta_color};">Δ {delta_escaped}</span>'

                return (
                    f'<div style="flex:1;border:1px solid #e5e7eb;border-radius:6px;padding:8px;margin:2px;">'
                    f'<span style="font-size:0.95em;font-weight:600;color:#374151;">{label_with_unit}</span><br/>'
                    f'<span style="font-size:1.1em;font-weight:700;color:#111827;">{val_escaped}</span>'
                    f'{delta_html}'
                    f'</div>'
                )

            # ── Display metrics in 2 columns (HTML flexbox) ───────────────────────────
            fav_list = st.session_state[fav_key]
            n_show = min(len(fav_list), 12)

            for idx in range(0, n_show, 2):
                # 两列卡片HTML（用flexbox，不嵌套columns）
                cards_html = '<div style="display:flex;gap:8px;">'
                cards_html += _build_card_html(idx, fav_list[idx], task.id)
                if idx + 1 < n_show:
                    cards_html += _build_card_html(idx + 1, fav_list[idx + 1], task.id)
                cards_html += '</div>'
                st.markdown(cards_html, unsafe_allow_html=True)

                # 按钮行（第一层columns，4列：左卡片的↑↓ + 右卡片的↑↓）
                btn_cols = st.columns([1, 1, 1, 1])
                # 左卡片按钮
                with btn_cols[0]:
                    if idx > 0:
                        if st.button("⬆", key=f"up_{idx}_{task.id}"):
                            _move_indicator(idx, "up")
                            st.rerun()
                with btn_cols[1]:
                    if idx < n_show - 1:
                        if st.button("⬇", key=f"down_{idx}_{task.id}"):
                            _move_indicator(idx, "down")
                            st.rerun()
                # 右卡片按钮
                if idx + 1 < n_show:
                    with btn_cols[2]:
                        if idx + 1 > 0:
                            if st.button("⬆", key=f"up_{idx+1}_{task.id}"):
                                _move_indicator(idx + 1, "up")
                                st.rerun()
                    with btn_cols[3]:
                        if idx + 1 < n_show - 1:
                            if st.button("⬇", key=f"down_{idx+1}_{task.id}"):
                                _move_indicator(idx + 1, "down")
                                st.rerun()

            # 受影响 Indicator 详情（仅重算后显示）
            if recalc_result and working_graph:
                aff_ids: set[str] = set()
                for cc in recalc_result.changed_cells:
                    cell = working_graph.cells.get(cc.cell_id)
                    if cell and cell.indicator_id:
                        aff_ids.add(cell.indicator_id)

                if aff_ids:
                    with st.expander(f"全部受影响 Indicator（{len(aff_ids)} 个）"):
                        irows = []
                        for iid in sorted(aff_ids):
                            bi = base_graph.indicators.get(iid)
                            wi = working_graph.indicators.get(iid)
                            irows.append({
                                "Indicator": bi.name if bi else iid,
                                "旧值": bi.summary_value if bi else None,
                                "新值": wi.summary_value if wi else None,
                            })
                        st.dataframe(irows, use_container_width=True, hide_index=True)

                if recalc_result.error_cells:
                    with st.expander(f"求值失败（{len(recalc_result.error_cells)} 个）"):
                        st.write(recalc_result.error_cells[:50])

    # ── Tab 2: Scenario comparison ──────────────────────────────────────
    with r_tabs[1]:
        st.subheader("场景对比")

        # 场景选择器
        comp_col_a, comp_col_b = st.columns(2)
        with comp_col_a:
            comp_base = st.selectbox(
                "基准场景",
                scenario_names,
                index=scenario_names.index("基准") if "基准" in scenario_names else 0,
                key="comp_base",
            )
        with comp_col_b:
            compare_options = [s for s in scenario_names if s != comp_base]
            comp_targets = st.multiselect(
                "对比场景",
                compare_options,
                default=compare_options[:1] if compare_options else [],
                key="comp_targets",
            )

        if comp_targets:
            # 收集所有场景的 overrides
            all_scenarios_in_comp = [comp_base] + list(comp_targets)

            # 获取关键指标
            comp_key_ids = get_key_metrics(base_graph)
            if comp_key_ids:
                # 构建对比数据
                metrics_data = []
                for ind_id in comp_key_ids:
                    ind = base_graph.indicators.get(ind_id)
                    if not ind:
                        continue
                    values = []
                    for scn_name in all_scenarios_in_comp:
                        scn = ws.scenarios.get(scn_name)
                        # 计算该场景下此 indicator 的值
                        if scn and scn.overrides:
                            # 找到此 indicator 关联的 cell 是否有 override
                            for cid, override_val in scn.overrides.items():
                                cell = base_graph.cells.get(cid)
                                if cell and cell.indicator_id == ind_id:
                                    values.append({
                                        "scenario": scn_name,
                                        "value": float(override_val),
                                        "isBaseline": scn_name == comp_base,
                                    })
                                    break
                            else:
                                values.append({
                                    "scenario": scn_name,
                                    "value": float(ind.summary_value) if ind.summary_value is not None else None,
                                    "isBaseline": scn_name == comp_base,
                                })
                        else:
                            values.append({
                                "scenario": scn_name,
                                "value": float(ind.summary_value) if ind.summary_value is not None else None,
                                "isBaseline": scn_name == comp_base,
                            })
                    metrics_data.append({
                        "name": ind.name or ind_id,
                        "values": values,
                    })

                if metrics_data:
                    # 参数覆盖对比表
                    st.caption("参数覆盖差异：")
                    override_rows = []
                    all_override_cells = set()
                    for scn_name in all_scenarios_in_comp:
                        scn = ws.scenarios.get(scn_name)
                        if scn:
                            all_override_cells.update(scn.overrides.keys())

                    for cid in sorted(all_override_cells):
                        info = cell_lookup.get(cid, {})
                        row = {"Cell ID": cid, "Indicator": info.get("Indicator 名称", ""), "基准值": info.get("当前值", "")}
                        for scn_name in all_scenarios_in_comp:
                            scn = ws.scenarios.get(scn_name)
                            row[scn_name] = scn.overrides.get(cid, "—") if scn else "—"
                        override_rows.append(row)

                    if override_rows:
                        st.dataframe(
                            override_rows,
                            use_container_width=True,
                            hide_index=True,
                            height=min(len(override_rows) * 35 + 38, 250),
                        )

                    # ECharts 可视化对比
                    chart_data = {"metrics": metrics_data, "scenarios": all_scenarios_in_comp}
                    html = render_compare_html(json.dumps(chart_data, ensure_ascii=False))
                    components.html(html, height=500, scrolling=True)
                else:
                    st.info("所选场景无参数覆盖差异")
            else:
                st.info("未找到关键指标")
        else:
            st.info("选择至少一个对比场景后显示差异")

    # ── Tab 3: Impact chain ──────────────────────────────────────────────
    with r_tabs[2]:
        if recalc_result and working_graph:
            changed_ids = [c.cell_id for c in recalc_result.changed_cells]
            if changed_ids:
                if f"vizr_{task.id}" not in st.session_state:
                    st.session_state[f"vizr_{task.id}"] = changed_ids[0]

                viz_root = st.selectbox(
                    "传播起点",
                    changed_ids,
                    index=changed_ids.index(st.session_state[f"vizr_{task.id}"]) if st.session_state.get(f"vizr_{task.id}") in changed_ids else 0,
                    format_func=lambda c: f"{c}",
                )
                st.session_state[f"vizr_{task.id}"] = viz_root

                vd, vmn = st.columns(2)
                with vd:
                    depth = st.slider("深度", 1, 15, 5, key="vd2")
                with vmn:
                    max_n = st.slider("最大节点", 50, 2000, 500, key="vmn2")

                auto = st.session_state.get(f"auto_viz_{task.id}", False)
                if auto or st.button("生成传播图", type="secondary"):
                    st.session_state[f"auto_viz_{task.id}"] = False

                    diff_cells = []
                    for c in recalc_result.changed_cells:
                        cell = base_graph.cells.get(c.cell_id)
                        diff_cells.append({
                            "id": c.cell_id,
                            "old": c.old_value,
                            "new": c.new_value,
                            "formula": c.formula or "",
                            "sheet": cell.sheet or "" if cell else "",
                        })

                    pseudo_diff = SnapshotDiff(
                        snapshot_a="修改前",
                        snapshot_b="修改后",
                        changed_cells=diff_cells,
                        affected_indicators=[],
                        summary={"total_changed_cells": len(diff_cells), "total_changed_indicators": 0, "sheets_affected": []},
                    )

                    data = build_propagation_data(base_graph, pseudo_diff, viz_root, max_depth=depth, max_nodes=max_n)
                    html = render_propagation_html(json.dumps(data))
                    components.html(html, height=550, scrolling=True)
            else:
                st.info("无变化单元格")
        else:
            st.info("重算后显示影响链")

    # ── Tab 4: History ───────────────────────────────────────────────────
    with r_tabs[3]:
        if ws.history:
            sorted_hist = sorted(ws.history, key=lambda r: r.timestamp, reverse=True)

            # 按批次分组
            batches: dict[str, list] = {}
            for r in sorted_hist:
                batches.setdefault(r.batch_id, []).append(r)

            # 排序批次（按最早时间）
            sorted_batch_ids = sorted(
                batches.keys(),
                key=lambda bid: min(r.timestamp for r in batches[bid]),
                reverse=True,
            )

            for i, bid in enumerate(sorted_batch_ids[:20]):
                records = batches[bid]
                first_record = records[0]
                batch_time = first_record.timestamp[:19]
                batch_scenario = first_record.scenario
                batch_size = len(records)

                with st.expander(f"批次 {i+1}: {batch_scenario} — {batch_time} ({batch_size} 项修改)", expanded=(i == 0)):
                    # 批次内详情
                    brow = []
                    for r in records:
                        brow.append({
                            "Cell ID": r.cell_id,
                            "Indicator": r.indicator_name,
                            "旧值": r.old_value,
                            "新值": r.new_value,
                        })
                    st.dataframe(brow, use_container_width=True, hide_index=True, height=min(batch_size * 35 + 38, 200))

                    # 批量回滚
                    if st.button("↩ 回滚此批次", key=f"rb_batch_{bid}"):
                        for r in records:
                            rollback_record(ws, r.id)
                        wg = copy.deepcopy(base_graph)
                        with st.spinner("回滚中..."):
                            result = apply_and_recalc(wg, ws, base_graph, record_history=False)
                        st.session_state[f"wg_{task.id}"] = wg
                        st.session_state[f"rr_{task.id}"] = result
                        st.toast(f"已回滚 {len(records)} 条修改", icon="↩️")
                        st.rerun()

            if len(sorted_batch_ids) > 20:
                st.caption(f"仅显示 20 个批次，共 {len(sorted_batch_ids)} 个")

            # 清空历史
            if st.button("清空全部历史", key="ch_clear_all"):
                ws.history = []
                save_workspace(ws)
                st.rerun()
        else:
            st.info("暂无修改记录")

    # ── Tab 5: Recalc settings ───────────────────────────────────────────
    with r_tabs[4]:
        st.caption("循环依赖迭代求值参数（Excel 模型中存在 F9→F10→F42→F38→F9 循环引用时生效）")

        new_max_iter = st.number_input(
            "最大迭代次数",
            min_value=10,
            max_value=500,
            value=ws.recalc_max_iter,
            step=10,
            key="recalc_max_iter_input",
        )
        new_tol = st.number_input(
            "收敛容差",
            min_value=1e-15,
            max_value=1e-3,
            value=ws.recalc_tol,
            format="%.0e",
            key="recalc_tol_input",
        )

        if new_max_iter != ws.recalc_max_iter or new_tol != ws.recalc_tol:
            ws.recalc_max_iter = new_max_iter
            ws.recalc_tol = new_tol
            save_workspace(ws)
            st.toast(f"已更新：迭代={new_max_iter}, 容差={new_tol:.0e}", icon="⚙️")

        st.caption(f"当前：迭代 {ws.recalc_max_iter} 次，容差 {ws.recalc_tol:.0e}")

    # ── Tab 6: Batch edit ──────────────────────────────────────────────
    with r_tabs[5]:
        st.subheader("批量修改")

        batch_mode = st.radio(
            "操作类型",
            ["统一设值", "百分比调整", "增量调整"],
            horizontal=True,
            key="batch_mode",
        )

        # 选中的 cell 列表
        batch_search = st.text_input("搜索 Cell ID / Indicator", placeholder="输入后回车添加", label_visibility="collapsed", key="batch_search")
        if batch_search:
            matches = []
            kw = batch_search.lower()
            for cid, info in cell_lookup.items():
                if kw in cid.lower() or kw in info.get("Indicator 名称", "").lower() or kw in info.get("Table 名称", "").lower():
                    matches.append(f"{cid} — {info.get('Indicator 名称', '')} (当前值: {info.get('当前值', '')})")

            if matches:
                st.caption(f"找到 {len(matches)} 个匹配：")
                for m in matches[:10]:
                    if st.button(m, key=f"batch_add_{m.split(' ')[0]}", use_container_width=True):
                        selected = st.session_state.get("batch_selected", [])
                        if m.split(" ")[0] not in selected:
                            selected.append(m.split(" ")[0])
                            st.session_state["batch_selected"] = selected

        # 已选列表
        batch_selected = st.session_state.get("batch_selected", [])
        if batch_selected:
            st.caption(f"已选 {len(batch_selected)} 个参数：")
            st.write(", ".join(batch_selected[:20]) + ("…" if len(batch_selected) > 20 else ""))

            # 操作值
            val_col, apply_col = st.columns([2, 1])
            with val_col:
                if batch_mode == "统一设值":
                    batch_val = st.number_input("目标值", key="batch_val")
                elif batch_mode == "百分比调整":
                    batch_val = st.number_input("调整百分比 (%)", value=0.0, step=1.0, key="batch_val")
                else:
                    batch_val = st.number_input("增量值", value=0.0, step=1.0, key="batch_val")

            with apply_col:
                if st.button("应用到选中", type="primary", use_container_width=True, key="batch_apply"):
                    pending_key = f"pending_{ws.active_scenario}"
                    gp = st.session_state.get(pending_key, {})
                    for cid in batch_selected:
                        info = cell_lookup.get(cid, {})
                        old_val = info.get("当前值", 0)
                        try:
                            if batch_mode == "统一设值":
                                gp[cid] = batch_val
                            elif batch_mode == "百分比调整":
                                gp[cid] = float(old_val) * (1 + batch_val / 100)
                            else:
                                gp[cid] = float(old_val) + batch_val
                        except (ValueError, TypeError):
                            gp[cid] = batch_val
                    st.session_state[pending_key] = gp
                    ws.pending_edits = gp
                    save_workspace(ws)
                    st.toast(f"已批量修改 {len(batch_selected)} 个参数", icon="✅")
                    st.session_state["batch_selected"] = []
                    st.rerun()

            if st.button("清空已选", use_container_width=True, key="batch_clear_sel"):
                st.session_state["batch_selected"] = []
                st.rerun()
        else:
            st.info("请先搜索并添加参数")

# ── Keyboard shortcuts ─────────────────────────────────────────────────

shortcut_js = """
<script>
document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        var saveBtn = Array.from(document.querySelectorAll('button')).find(
            b => b.textContent.includes('保存到场景')
        );
        if (saveBtn) saveBtn.click();
    }
});
</script>
"""
components.html(shortcut_js, height=0, scrolling=False)
