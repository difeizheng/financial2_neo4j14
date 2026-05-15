"""Page 3: Parameter workspace — two-column editor + results."""
from __future__ import annotations
import copy
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

st.set_page_config(layout="wide")

# ── Top bar ──────────────────────────────────────────────────────────────────

st.title("⚙️ 参数工作台")

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

st.divider()

# ── Main two-column layout ──────────────────────────────────────────────────

editor_col, results_col = st.columns([3, 2])

# ── Shared data ──────────────────────────────────────────────────────────────

def _build_param_cells(graph):
    rows = []
    for cid, cell in graph.cells.items():
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

    # Sort categories, "未分类" at end
    sorted_cats = sorted(
        [c for c in category_groups if c != "未分类"],
        key=lambda c: len(category_groups[c]),
        reverse=True,
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
                st.toast(f"重算完成：{result.affected_count} 个变化{iter_info}，快照「{snap_name}」已保存（可与「{base_snap_name}」对比）", icon="✅")
                st.rerun()

# ── Right: Results panel ────────────────────────────────────────────────────

recalc_result = st.session_state.get(f"rr_{task.id}")
working_graph = st.session_state.get(f"wg_{task.id}")

with results_col:
    st.subheader("📊 结果面板")

    r_tabs = st.tabs(["关键指标", "影响链", "修改历史", "重算设置"])

    # ── Tab 1: Key metrics ───────────────────────────────────────────────
    with r_tabs[0]:
        # 始终显示关键指标（基线 + 变化）
        all_key_ids = get_key_metrics(base_graph)

        # 用户可选关注指标
        fav_key = f"fav_metrics_{task.id}"
        if fav_key not in st.session_state:
            st.session_state[fav_key] = set(all_key_ids[:12])  # 默认前12个

        with st.expander("⭐ 自定义关注指标", expanded=len(all_key_ids) <= 12):
            st.caption("勾选需要追踪的指标（不勾选则使用自动匹配的关键指标）")
            metric_cols = st.columns(3)
            for idx, ind_id in enumerate(sorted(all_key_ids)):
                ind = base_graph.indicators.get(ind_id)
                label = ind.name if ind else ind_id
                with metric_cols[idx % 3]:
                    checked = ind_id in st.session_state[fav_key]
                    if st.checkbox(label, value=checked, key=f"fav_{ind_id}"):
                        st.session_state[fav_key].add(ind_id)
                    elif ind_id in st.session_state[fav_key]:
                        st.session_state[fav_key].discard(ind_id)

        display_ids = st.session_state[fav_key] if st.session_state[fav_key] else set(all_key_ids)
        if not display_ids:
            st.info("未选择任何关注指标，请在上方勾选")
        else:
            n_show = min(len(display_ids), 12)
            for ri in range((n_show + 2) // 3):
                mc = st.columns(3)
                for j, ind_id in enumerate(sorted(display_ids)[ri * 3:(ri + 1) * 3]):
                    ind = base_graph.indicators.get(ind_id)
                    if not ind:
                        continue
                    old_val = ind.summary_value
                    new_val = old_val

                    # 如果有重算结果，显示新值
                    if recalc_result and working_graph:
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

                    # 有变化时高亮颜色
                    metric_color = "normal"
                    if delta is not None and delta < 0:
                        metric_color = "inverse"

                    with mc[j]:
                        st.metric(
                            label=ind.name or ind_id,
                            value=new_val if new_val is not None else "—",
                            delta=f"{delta:+.2f} ({delta_pct:+.1f}%)" if delta is not None else None,
                            delta_color=metric_color,
                        )

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

    # ── Tab 2: Impact chain ──────────────────────────────────────────────
    with r_tabs[1]:
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

    # ── Tab 3: History ───────────────────────────────────────────────────
    with r_tabs[2]:
        if ws.history:
            sorted_hist = sorted(ws.history, key=lambda r: r.timestamp, reverse=True)[:100]

            hrows = []
            for r in sorted_hist:
                hrows.append({
                    "时间": r.timestamp[:19],
                    "场景": r.scenario,
                    "Cell ID": r.cell_id,
                    "旧值": r.old_value,
                    "新值": r.new_value,
                })
            st.dataframe(hrows, use_container_width=True, hide_index=True, height=250)

            st.caption("回滚最近 10 条：")
            rc = st.columns(min(len(sorted_hist[:10]), 5))
            for i, r in enumerate(sorted_hist[:10]):
                with rc[i % 5]:
                    sc = r.cell_id[:15] + "…" if len(r.cell_id) > 15 else r.cell_id
                    if st.button(f"↩ {sc}", key=f"rb2_{r.id}", use_container_width=True):
                        updates = rollback_record(ws, r.id)
                        if updates is not None:
                            wg = copy.deepcopy(base_graph)
                            with st.spinner("回滚中..."):
                                result = apply_and_recalc(wg, ws, base_graph, record_history=False)
                            st.session_state[f"wg_{task.id}"] = wg
                            st.session_state[f"rr_{task.id}"] = result
                            st.toast(f"已回滚 {r.cell_id}", icon="↩️")
                            st.rerun()
                        else:
                            st.toast("回滚失败", icon="❌")

            if len(ws.history) > 100:
                st.caption(f"仅显示 100 条，共 {len(ws.history)} 条")

            if st.button("清空历史（保留 10 条）", key="ch2"):
                ws.history = ws.history[-10:]
                save_workspace(ws)
                st.rerun()
        else:
            st.info("暂无修改记录")

    # ── Tab 4: Recalc settings ───────────────────────────────────────────
    with r_tabs[3]:
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
