"""Page 8: 通用模型引擎 — 参数输入 + 运行 + 结果仪表盘 + 分析工具

独立的通用抽水蓄能财务模型页面，不依赖知识图谱解析，
完全参数驱动，支持任意建设期/运营期组合。
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace as _replace
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_model.analysis import (
    COMMON_PARAMS,
    METRIC_DISPLAY,
    ComparisonEngine,
    DistributionType,
    MetricKey,
    ModelConfig,
    MonteCarloEngine,
    ParamDistribution,
    ParamSpec,
    PresetScenario,
    ScenarioEngine,
    SensitivityEngine,
)
from financial_model.engines.orchestrator import AllResults
from financial_model.params.presets import list_presets, load_preset, load_preset_metadata
from financial_model.export.excel_exporter import export_excel
from financial_model.export.qa_adapter import GenericModelQAAdapter
from financial_model.export.report_exporter import export_report
from financial_model.params import (
    ConstructionParams,
    DepreciationParams,
    FinancingParams,
    LoanTerms,
    OperatingParams,
    TaxParams,
)
from financial_model.params.depreciation import AssetCategory

st.set_page_config(layout="wide")
st.title("🔧 通用模型引擎")


# ══════════════════════════════════════════════════════════
# Session State 初始化
# ══════════════════════════════════════════════════════════

def _init_state() -> None:
    """初始化 session state 默认值"""
    defaults = {
        "gm_config": None,
        "gm_results": None,
        "gm_sensitivity": None,
        "gm_scenario": None,
        "gm_monte_carlo": None,
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default
    if st.session_state.gm_config is None:
        st.session_state.gm_config = ModelConfig.from_excel_v17()


_init_state()


# ══════════════════════════════════════════════════════════
# 龙卷风图渲染
# ══════════════════════════════════════════════════════════

_PLT_FONT_CONFIGURED = False


def _setup_matplotlib_fonts() -> None:
    """配置 matplotlib 中文显示（仅首次调用时执行）"""
    global _PLT_FONT_CONFIGURED
    if _PLT_FONT_CONFIGURED:
        return
    _PLT_FONT_CONFIGURED = True
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _render_tornado_chart(
    td: pd.DataFrame,
    metric_name: str,
    base_val: float | None = None,
) -> None:
    """渲染龙卷风图: 水平条形图, 左侧负向/右侧正向, 按 spread 降序."""
    _setup_matplotlib_fonts()

    n = len(td)
    fig_height = max(2.8, n * 0.55 + 0.8)
    fig, ax = plt.subplots(figsize=(6, fig_height), dpi=110)

    params = td["param"].tolist()
    negatives = td["negative"].tolist()
    positives = td["positive"].tolist()
    y_pos = np.arange(n)

    # 负向 (红) / 正向 (绿)
    ax.barh(y_pos, negatives, align="center",
            height=0.55, color="#e74c3c", alpha=0.85, label="负向影响")
    ax.barh(y_pos, positives, align="center",
            height=0.55, color="#27ae60", alpha=0.85, label="正向影响")

    # 条形末端数值标注
    for i in range(n):
        neg_v, pos_v = negatives[i], positives[i]
        if neg_v != 0:
            ax.text(neg_v, y_pos[i], f"{neg_v:+.2f}", va="center",
                    ha="right" if neg_v < 0 else "left",
                    fontsize=8, color="#c0392b")
        if pos_v != 0:
            ax.text(pos_v, y_pos[i], f"{pos_v:+.2f}", va="center",
                    ha="left" if pos_v > 0 else "right",
                    fontsize=8, color="#1e8449")

    # 中心线 & 基准值
    ax.axvline(x=0, color="#2c3e50", linewidth=0.8)
    if base_val is not None:
        ax.text(0.02, 0.98, f"基准: {base_val:.4f}",
                transform=ax.transAxes, va="top", ha="left",
                fontsize=9, color="#2c3e50",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#ecf0f1", alpha=0.8))

    ax.set_yticks(y_pos)
    ax.set_yticklabels(params, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(f"{metric_name} 变化量", fontsize=10)
    ax.set_title(f"{metric_name} 敏感性龙卷风图", fontsize=12, fontweight="bold")
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)

    plt.tight_layout()
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


def _render_mc_distribution(
    mc_result: object,
    metric_name: str,
    key: "MetricKey",
) -> None:
    """渲染蒙特卡罗直方图 + 累计分布曲线 (CDF)."""
    _setup_matplotlib_fonts()

    values = mc_result.metric_series(key)
    if len(values) == 0:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4), dpi=110)

    # ── 左: 直方图 ──
    n_bins = min(50, max(10, len(values) // 20))
    ax1.hist(values, bins=n_bins, color="#3498db", alpha=0.8, edgecolor="white")

    # P10/P50/P90 标注
    p10 = float(np.percentile(values, 10))
    p50 = float(np.percentile(values, 50))
    p90 = float(np.percentile(values, 90))
    for pv, color, label in [(p10, "#e74c3c", "P10"), (p50, "#2c3e50", "P50"), (p90, "#27ae60", "P90")]:
        ax1.axvline(x=pv, color=color, linewidth=1.5, linestyle="--", label=f"{label}: {pv:.4f}")

    ax1.set_xlabel(metric_name, fontsize=10)
    ax1.set_ylabel("频次", fontsize=10)
    ax1.set_title(f"{metric_name} 概率分布", fontsize=11, fontweight="bold")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    # ── 右: CDF ──
    sorted_v = np.sort(values)
    cdf = np.arange(1, len(sorted_v) + 1) / len(sorted_v)
    ax2.plot(sorted_v, cdf, color="#2980b9", linewidth=2)

    for pv, color, label in [(p10, "#e74c3c", "P10"), (p50, "#2c3e50", "P50"), (p90, "#27ae60", "P90")]:
        ax2.axvline(x=pv, color=color, linewidth=1, linestyle="--", alpha=0.7)
        ax2.axhline(y=float(label.replace("P", "")) / 100, color=color, linewidth=0.5, linestyle=":", alpha=0.5)

    ax2.set_xlabel(metric_name, fontsize=10)
    ax2.set_ylabel("累计概率", fontsize=10)
    ax2.set_title(f"{metric_name} 累计分布 (CDF)", fontsize=11, fontweight="bold")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


def _render_scenario_radar(
    scenario_result: object,
    metric_keys: list["MetricKey"],
) -> None:
    """渲染情景对比雷达图 (归一化到 [0,1]).

    兼容 ScenarioComparison (.scenarios) 和 ProjectComparison (.projects)。
    """
    _setup_matplotlib_fonts()
    from financial_model.analysis import METRIC_DISPLAY

    # Duck-type: ScenarioComparison.scenarios 或 ProjectComparison.projects
    items = getattr(scenario_result, "scenarios", None) or getattr(scenario_result, "projects", None)
    if items is None or len(items) < 2:
        return

    n_vars = len(metric_keys)
    if n_vars < 3:
        return

    labels = [METRIC_DISPLAY.get(k, str(k)) for k in metric_keys]

    # 归一化: 每个指标在所有项中的 min → 0, max → 1
    all_vals: dict[MetricKey, list[float]] = {}
    for k in metric_keys:
        vals = []
        for s in items:
            v = s.metrics.get(k)
            vals.append(float(v) if v is not None else 0.0)
        all_vals[k] = vals

    angles = np.linspace(0, 2 * np.pi, n_vars, endpoint=False).tolist()
    angles += angles[:1]  # 闭合

    colors = ["#e74c3c", "#3498db", "#27ae60", "#f39c12", "#9b59b6"]
    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True), dpi=100)

    for idx, s in enumerate(items):
        norm_vals = []
        for i, k in enumerate(metric_keys):
            v = all_vals[k][idx]
            vmin = min(all_vals[k])
            vmax = max(all_vals[k])
            if vmax - vmin > 1e-10:
                norm_vals.append((v - vmin) / (vmax - vmin))
            else:
                norm_vals.append(0.5)
        norm_vals += norm_vals[:1]  # 闭合

        ax.plot(angles, norm_vals, "o-", linewidth=2, label=s.name,
                color=colors[idx % len(colors)])
        ax.fill(angles, norm_vals, alpha=0.15, color=colors[idx % len(colors)])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1.1)
    ax.set_title("情景对比雷达图", fontsize=12, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=9)

    plt.tight_layout()
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


def _render_timeseries(results: "AllResults") -> None:
    """渲染运营期收入/成本/利润时间序列图."""
    _setup_matplotlib_fonts()

    revenue_df = results.revenue
    cost_df = results.cost
    pnl_data = results.pnl_total.data

    if revenue_df is None or cost_df is None or pnl_data is None:
        st.info("缺少收入/成本数据，无法绘制时间序列")
        return

    fig, ax1 = plt.subplots(figsize=(10, 5), dpi=110)

    # DataFrame index 是年份整数
    years_all = list(revenue_df.index)

    # 收入列
    rev_col = _find_col(revenue_df, ["total_revenue", "electricity_revenue", "发电收入"])
    cost_col = _find_col(cost_df, ["total_production_cost", "total_cost", "operating_cost"])
    profit_col = _find_col(pnl_data, ["net_profit", "profit_before_tax"])

    if not rev_col or not cost_col:
        st.info("未找到可绘制的收入/成本列")
        plt.close(fig)
        return

    rev_vals = revenue_df[rev_col].tolist()
    cost_vals = cost_df[cost_col].tolist()

    # 对齐长度
    n = min(len(years_all), len(rev_vals), len(cost_vals))
    years = years_all[:n]
    rev_vals = rev_vals[:n]
    cost_vals = cost_vals[:n]

    ax1.bar([y - 0.2 for y in years], rev_vals, width=0.4, color="#27ae60", alpha=0.8, label="收入")
    ax1.bar([y + 0.2 for y in years], cost_vals, width=0.4, color="#e74c3c", alpha=0.8, label="成本")
    ax1.set_xlabel("年份", fontsize=10)
    ax1.set_ylabel("金额 (万元)", fontsize=10)
    ax1.set_title("运营期收入/成本/利润趋势", fontsize=12, fontweight="bold")

    # 利润折线 (右轴) — 优先用 PnL 的净利润
    if profit_col:
        profit_years = list(pnl_data.index)
        profit_vals = pnl_data[profit_col].tolist()
    else:
        profit_years = years
        profit_vals = [r - c for r, c in zip(rev_vals, cost_vals)]

    ax2 = ax1.twinx()
    ax2.plot(profit_years[:len(profit_vals)], profit_vals,
             color="#2c3e50", linewidth=2, marker="o", markersize=3, label="净利润")
    ax2.set_ylabel("净利润 (万元)", fontsize=10, color="#2c3e50")
    ax2.tick_params(axis="y", labelcolor="#2c3e50")

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

    ax1.spines["top"].set_visible(False)
    ax1.grid(axis="y", alpha=0.3)

    # 如果年数太多，只显示部分 x tick
    if len(years) > 20:
        step = max(1, len(years) // 15)
        ax1.set_xticks(years[::step])

    plt.tight_layout()
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


def _find_col(df: "pd.DataFrame", candidates: list[str]) -> str | None:
    """在 DataFrame 中查找第一个存在的列名"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


# ══════════════════════════════════════════════════════════
# 侧边栏
# ══════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ 快速操作")

    # 预设模板选择
    preset_names = list_presets()
    if preset_names:
        preset_labels = []
        for pn in preset_names:
            meta = load_preset_metadata(pn)
            preset_labels.append(f"{meta['name']}")
        selected_preset_idx = st.selectbox(
            "📦 预设模板",
            range(len(preset_names)),
            format_func=lambda i: preset_labels[i],
            key="gm_preset_select",
        )
        if st.button("📋 加载预设", use_container_width=True):
            preset_name = preset_names[selected_preset_idx]
            st.session_state.gm_config = load_preset(preset_name)
            st.session_state.gm_results = None
            st.session_state.gm_sensitivity = None
            st.session_state.gm_scenario = None
            st.session_state.gm_monte_carlo = None
            meta = load_preset_metadata(preset_name)
            st.toast(f"已加载预设: {meta['name']}", icon="📦")
            st.rerun()

    st.divider()

    if st.button("🔄 重置为默认参数", use_container_width=True):
        st.session_state.gm_config = ModelConfig.from_excel_v17()
        st.session_state.gm_results = None
        st.session_state.gm_sensitivity = None
        st.session_state.gm_scenario = None
        st.session_state.gm_monte_carlo = None
        st.rerun()
    st.divider()
    st.caption("通用抽水蓄能财务模型 v1.2")
    st.caption("参数驱动 · 任意建设期 · 动态时间轴")


# ══════════════════════════════════════════════════════════
# 主布局: 左栏参数 + 右栏结果
# ══════════════════════════════════════════════════════════

left_col, right_col = st.columns([3, 2])
config: ModelConfig = st.session_state.gm_config

# ── 左栏: 参数编辑 ──────────────────────────────────────

with left_col:
    param_tabs = st.tabs(
        ["🏗️ 建设期", "💰 融资", "⚡ 运营", "📊 成本/投资", "🏛️ 税务", "📉 折旧"]
    )

    # ── Tab 0: 建设期参数 ──────────────────────────────
    with param_tabs[0]:
        st.subheader("建设期参数")
        constr = config.construction

        c_row = st.columns(2)
        with c_row[0]:
            cs_date = st.date_input(
                "建设期起始", value=constr.construction_start, key="gm_constr_start"
            )
        with c_row[1]:
            ce_date = st.date_input(
                "建设期结束", value=constr.construction_end, key="gm_constr_end"
            )
        op_years = st.number_input(
            "运营年限 (年)",
            min_value=5, max_value=80,
            value=constr.operation_years, step=1, key="gm_op_years",
        )

        # 显示派生属性
        try:
            preview_constr = ConstructionParams(
                construction_start=cs_date,
                construction_end=ce_date,
                operation_years=op_years,
            )
            s = preview_constr.summary()
            st.info(
                f"建设期 **{s['construction_months']}** 月 "
                f"({s['construction_years']}年) | "
                f"首年 **{s['first_year_months']}** 月 | "
                f"末年 **{s['last_year_months']}** 月\n\n"
                f"运营期 → {preview_constr.operation_end}"
            )
        except ValueError as e:
            st.error(str(e))

    # ── Tab 1: 融资参数 ────────────────────────────────
    with param_tabs[1]:
        st.subheader("融资参数")
        fin = config.financing

        f1 = st.columns(2)
        with f1[0]:
            equity_ratio = st.number_input(
                "资本金比例", min_value=0.0, max_value=1.0,
                value=fin.equity_ratio, step=0.05, format="%.2f",
                key="gm_equity_ratio",
            )
        with f1[1]:
            interest_rate = st.number_input(
                "长期贷款利率", min_value=0.0, max_value=0.20,
                value=fin.long_term_loan.annual_rate, step=0.005,
                format="%.4f", key="gm_interest_rate",
            )

        f2 = st.columns(2)
        with f2[0]:
            st_loan_rate = st.number_input(
                "短期贷款利率", min_value=0.0, max_value=0.20,
                value=fin.short_term_loan_rate, step=0.005,
                format="%.4f", key="gm_st_loan_rate",
            )
        with f2[1]:
            loan_term = st.number_input(
                "还款期限 (年)", min_value=5, max_value=50,
                value=fin.long_term_loan.repayment_term_years, step=1,
                key="gm_loan_term",
            )

    # ── Tab 2: 运营参数 ────────────────────────────────
    with param_tabs[2]:
        st.subheader("运营参数")
        op = config.operating

        o1 = st.columns(3)
        with o1[0]:
            capacity = st.number_input(
                "装机容量 (MW)", min_value=100.0,
                value=op.installed_capacity_mw, step=100.0, key="gm_capacity",
            )
        with o1[1]:
            util_hours = st.number_input(
                "年利用小时 (h)", min_value=0.0,
                value=op.annual_utilization_hours, step=50.0, key="gm_util_hours",
            )
        with o1[2]:
            aux_rate = st.number_input(
                "厂用电率", min_value=0.0, max_value=0.5,
                value=op.auxiliary_power_rate, step=0.01, format="%.2f",
                key="gm_aux_rate",
            )

        o2 = st.columns(3)
        with o2[0]:
            grid_price = st.number_input(
                "上网电价 (元/kWh)", min_value=0.0,
                value=op.grid_price, step=0.01, format="%.4f", key="gm_grid_price",
            )
        with o2[1]:
            pump_price = st.number_input(
                "抽水电价 (元/kWh)", min_value=0.0,
                value=op.pump_price, step=0.01, format="%.5f", key="gm_pump_price",
            )
        with o2[2]:
            cap_price = st.number_input(
                "容量电价 (元/kW·年)", min_value=0.0,
                value=op.capacity_price, step=10.0, key="gm_cap_price",
            )

        # 派生信息
        try:
            new_op = OperatingParams(
                installed_capacity_mw=capacity,
                annual_utilization_hours=util_hours,
                capacity_price=cap_price,
                grid_price=grid_price,
                pump_price=pump_price,
                auxiliary_power_rate=aux_rate,
                production_ratios=op.production_ratios,
            )
            st.info(
                f"年发电量 **{new_op.annual_generation_mwh:,.0f}** MWh | "
                f"年上网电量 **{new_op.annual_grid_energy_mwh:,.0f}** MWh\n\n"
                f"年容量电费 **{new_op.annual_capacity_revenue:,.0f}** 万元 | "
                f"年电量电费 **{new_op.annual_energy_revenue:,.0f}** 万元"
            )
        except ValueError as e:
            st.error(str(e))

    # ── Tab 3: 投资/成本 ────────────────────────────────
    with param_tabs[3]:
        st.subheader("投资概算")
        inv = config.investment

        i1 = st.columns(2)
        with i1[0]:
            price_escalation = st.number_input(
                "价差预备费率 (年)", min_value=0.0, max_value=0.10,
                value=inv.price_contingency.price_escalation_rate,
                step=0.005, format="%.4f", key="gm_price_esc",
            )
        with i1[1]:
            contingency_rate = st.number_input(
                "基本预备费率", min_value=0.0, max_value=0.15,
                value=inv.basic_contingency_rate, step=0.01,
                format="%.2f", key="gm_contingency",
            )
        st.caption("💡 详细工程概算科目请通过 YAML 预设或代码修改")

    # ── Tab 4: 税务参数 ────────────────────────────────
    with param_tabs[4]:
        st.subheader("税务参数")
        tax = config.tax

        t1 = st.columns(2)
        with t1[0]:
            vat_rate = st.number_input(
                "增值税率", min_value=0.0, max_value=1.0,
                value=tax.vat_rate, step=0.01, format="%.2f", key="gm_vat",
            )
        with t1[1]:
            income_tax = st.number_input(
                "企业所得税率", min_value=0.0, max_value=1.0,
                value=tax.income_tax_rate, step=0.01, format="%.2f",
                key="gm_income_tax",
            )

        t2 = st.columns(2)
        with t2[0]:
            surcharge = st.number_input(
                "附加税费率 (城建+教育)", min_value=0.0, max_value=1.0,
                value=tax.surcharge_rate, step=0.01, format="%.2f",
                key="gm_surcharge",
            )
        with t2[1]:
            loss_years = st.number_input(
                "亏损弥补年限", min_value=0, max_value=10,
                value=tax.loss_carryforward_years, step=1, key="gm_loss_years",
            )

    # ── Tab 5: 折旧参数 ────────────────────────────────
    with param_tabs[5]:
        st.subheader("折旧摊销参数")
        dep = config.depreciation

        d1 = st.columns(3)
        with d1[0]:
            fa_orig = st.number_input(
                "固定资产原值 (万元)", min_value=0.0,
                value=dep.fixed_assets.original_value, step=10000.0,
                format="%.2f", key="gm_fa_orig",
            )
        with d1[1]:
            fa_life = st.number_input(
                "折旧年限 (年)", min_value=5, max_value=50,
                value=dep.fixed_assets.useful_life, step=1, key="gm_fa_life",
            )
        with d1[2]:
            fa_residual = st.number_input(
                "残值率", min_value=0.0, max_value=0.5,
                value=dep.fixed_assets.residual_rate, step=0.01,
                format="%.2f", key="gm_fa_residual",
            )

        d2 = st.columns(2)
        with d2[0]:
            intangible = st.number_input(
                "无形资产原值 (万元)", min_value=0.0,
                value=dep.intangible_assets.original_value, step=5000.0,
                format="%.2f", key="gm_intangible",
            )
        with d2[1]:
            intangible_life = st.number_input(
                "无形资产摊销年限", min_value=1, max_value=50,
                value=dep.intangible_assets.useful_life, step=1,
                key="gm_intangible_life",
            )

        # 派生信息
        annual_dep = fa_orig * (1 - fa_residual) / fa_life if fa_life > 0 else 0
        st.info(f"年折旧额 ≈ **{annual_dep:,.0f}** 万元")

    # ── 构建配置 & 运行按钮 ─────────────────────────────
    st.divider()

    run_col1, run_col2 = st.columns([1, 3])
    with run_col1:
        run_clicked = st.button(
            "🚀 运行模型", type="primary", use_container_width=True
        )
    with run_col2:
        discount_rate = st.number_input(
            "基准收益率 (NPV折现率)", min_value=0.0, max_value=0.30,
            value=config.discount_rate, step=0.01, format="%.2f",
            key="gm_discount",
        )

    # 从 UI 参数构建新 ModelConfig
    try:
        new_config = ModelConfig(
            construction=ConstructionParams(
                construction_start=cs_date,
                construction_end=ce_date,
                operation_years=op_years,
            ),
            investment=_replace(
                config.investment,
                price_contingency=_replace(
                    config.investment.price_contingency,
                    price_escalation_rate=price_escalation,
                ),
                basic_contingency_rate=contingency_rate,
            ),
            financing=_replace(
                config.financing,
                equity_ratio=equity_ratio,
                construction_interest_rate=interest_rate,
                long_term_loan=_replace(
                    config.financing.long_term_loan,
                    annual_rate=interest_rate,
                    repayment_term_years=loan_term,
                ),
                short_term_loan_rate=st_loan_rate,
            ),
            operating=OperatingParams(
                installed_capacity_mw=capacity,
                annual_utilization_hours=util_hours,
                capacity_price=cap_price,
                grid_price=grid_price,
                pump_price=pump_price,
                auxiliary_power_rate=aux_rate,
                production_ratios=config.operating.production_ratios,
            ),
            tax=TaxParams(
                vat_rate=vat_rate,
                income_tax_rate=income_tax,
                surcharge_rate=surcharge,
                loss_carryforward_years=loss_years,
                deductible_input_vat=tax.deductible_input_vat,
                deductible_vat_amort_years=tax.deductible_vat_amort_years,
            ),
            depreciation=_replace(
                config.depreciation,
                fixed_assets=AssetCategory("固定资产", fa_orig, fa_life, fa_residual),
                intangible_assets=AssetCategory(
                    "无形资产", intangible, intangible_life,
                    dep.intangible_assets.residual_rate,
                ),
            ),
            discount_rate=discount_rate,
        )
        st.session_state.gm_config = new_config

        if run_clicked:
            with st.spinner("正在运行模型 (9个引擎)..."):
                st.session_state.gm_results = new_config.to_orchestrator().run()
                st.session_state.gm_sensitivity = None
                st.session_state.gm_scenario = None
                st.session_state.gm_monte_carlo = None

    except (ValueError, TypeError) as e:
        st.error(f"参数错误: {e}")


# ══════════════════════════════════════════════════════════
# 右栏: 结果展示
# ══════════════════════════════════════════════════════════

with right_col:
    results: AllResults | None = st.session_state.gm_results

    if results is None:
        st.info("👈 设置参数后点击 **🚀 运行模型** 查看结果")
    else:
        dm = results.derived_metrics

        # ── 关键指标卡片 ──
        st.subheader("📊 关键指标")

        def _fmt(v: float | None, pct: bool = True) -> str:
            if v is None:
                return "N/A"
            return f"{v:.2%}" if pct else f"{v:,.0f}"

        c1 = st.columns(4)
        c1[0].metric("全投资 IRR", _fmt(dm.irr_total))
        c1[1].metric("全投资 NPV (万元)", _fmt(dm.npv_total, pct=False))
        c1[2].metric("最低 DSCR", _fmt(dm.dscr_min))
        c1[3].metric("静态回收期 (年)", f"{dm.payback_static:.1f}" if dm.payback_static else "N/A")

        c2 = st.columns(4)
        c2[0].metric("资本金 IRR", _fmt(dm.irr_equity))
        c2[1].metric("资本金 NPV (万元)", _fmt(dm.npv_equity, pct=False))
        c2[2].metric("平均 DSCR", _fmt(dm.dscr_avg))
        c2[3].metric("动态回收期 (年)", f"{dm.payback_dynamic:.1f}" if dm.payback_dynamic else "N/A")

        # 投资概要
        invest_total = float(results.investment["construction_investment"].sum())
        fin = results.financing
        st.caption(
            f"建设投资 **{invest_total:,.0f}** 万元 | "
            f"建设期利息 **{fin.construction_interest_total:,.0f}** 万元 | "
            f"动态总投资 **{fin.dynamic_total_investment:,.0f}** 万元"
        )

        # ── 收入/成本/利润趋势图 ──
        with st.expander("📈 收入/成本/利润趋势", expanded=False):
            _render_timeseries(results)

        # ── 12张报表 Tab ──
        st.divider()
        st.subheader("📋 报表详情")

        report_tabs = st.tabs([
            "投资概算", "资金筹措", "折旧摊销", "成本费用",
            "收入税金", "利润表(全投资)", "利润表(资本金)",
            "现金流(全投资)", "现金流(资本金)", "现金流(财务计划)",
            "资产负债表", "派生指标",
        ])

        def _show_df(df: pd.DataFrame, height: int = 400) -> None:
            """展示 DataFrame，金额列格式化"""
            fmt = {}
            for col in df.select_dtypes(include=["float"]).columns:
                fmt[col] = "{:,.2f}"
            st.dataframe(df, use_container_width=True, height=height, column_config=None)

        with report_tabs[0]:
            st.markdown("**投资概算** — 分年度投资分配")
            _show_df(results.investment)

        with report_tabs[1]:
            st.markdown("**资金筹措** — 股债分配与还款计划")
            _show_df(results.financing.annual_summary)

        with report_tabs[2]:
            st.markdown("**折旧摊销** — 5类资产折旧明细")
            _show_df(results.depreciation)

        with report_tabs[3]:
            st.markdown("**成本费用** — 运营期年度成本")
            _show_df(results.cost)

        with report_tabs[4]:
            st.markdown("**收入税金** — 发电收入与税费")
            _show_df(results.revenue)

        with report_tabs[5]:
            st.markdown("**利润表 (全投资视角)**")
            _show_df(results.pnl_total.data)

        with report_tabs[6]:
            st.markdown("**利润表 (资本金视角)**")
            _show_df(results.pnl_equity.data)

        with report_tabs[7]:
            st.markdown("**现金流量表 (全投资)**")
            _show_df(results.cf_total.data)

        with report_tabs[8]:
            st.markdown("**现金流量表 (资本金)**")
            _show_df(results.cf_equity.data)

        with report_tabs[9]:
            st.markdown("**现金流量表 (财务计划)**")
            _show_df(results.cf_plan.data)

        with report_tabs[10]:
            st.markdown("**资产负债表**")
            _show_df(results.balance_sheet.data)

        with report_tabs[11]:
            st.markdown("**派生指标汇总**")
            summary = results.summary()
            for k, v in summary.items():
                st.write(f"- **{k}**: {v}")


# ══════════════════════════════════════════════════════════
# 分析工具区 (全宽)
# ══════════════════════════════════════════════════════════

st.divider()
st.header("🔬 分析工具")

if st.session_state.gm_results is None:
    st.info("请先运行模型后再使用分析工具")
    st.stop()

analysis_config: ModelConfig = st.session_state.gm_config

analysis_tabs = st.tabs(["🎯 敏感性分析", "📊 情景对比", "🎲 蒙特卡罗", "📋 项目对比", "💬 智能问答", "📥 导出"])

# ── 敏感性分析 ─────────────────────────────────────────
with analysis_tabs[0]:
    st.subheader("敏感性分析")

    s_col1, s_col2 = st.columns([1, 2])
    with s_col1:
        param_options = {p.display_name: p for p in COMMON_PARAMS}
        selected_names = st.multiselect(
            "选择分析参数",
            options=list(param_options.keys()),
            default=list(param_options.keys())[:5],
            key="gm_sens_params",
        )
        selected_specs = [param_options[n] for n in selected_names]

        perturb_str = st.text_input(
            "扰动比例 (逗号分隔)",
            value="-0.2, -0.1, -0.05, 0.05, 0.1, 0.2",
            key="gm_sens_perturb",
        )

        if st.button("▶️ 运行敏感性分析", key="gm_run_sens"):
            if not selected_specs:
                st.warning("请至少选择一个参数")
            else:
                perturbations = [float(x.strip()) for x in perturb_str.split(",")]
                with st.spinner("敏感性分析中..."):
                    engine = SensitivityEngine(analysis_config)
                    st.session_state.gm_sensitivity = engine.run(
                        params=selected_specs, perturbations=perturbations,
                    )

    with s_col2:
        sens_result = st.session_state.gm_sensitivity
        if sens_result is not None:
            st.markdown("**敏感性矩阵 (IRR%)**")
            st.dataframe(
                sens_result.matrix_table(), use_container_width=True
            )

            st.markdown("**龙卷风图 (按敏感性排序)**")
            for mk in [MetricKey.IRR_TOTAL, MetricKey.NPV_TOTAL]:
                td = sens_result.tornado_data(mk)
                if td is not None and not td.empty:
                    display_name = METRIC_DISPLAY.get(mk, str(mk))
                    base_val = sens_result.base_metrics.get(mk)
                    t_left, t_right = st.columns([3, 2])
                    with t_left:
                        _render_tornado_chart(td, display_name, base_val)
                    with t_right:
                        st.markdown(f"**{display_name} 数据**")
                        st.dataframe(td, use_container_width=True, hide_index=True)
        else:
            st.info("选择参数后点击运行")

# ── 情景对比 ───────────────────────────────────────────
with analysis_tabs[1]:
    st.subheader("情景对比分析")

    sc_col1, sc_col2 = st.columns([1, 2])
    with sc_col1:
        preset_options = {
            "悲观 + 基准 + 乐观": [
                PresetScenario.PESSIMISTIC,
                PresetScenario.BASE,
                PresetScenario.OPTIMISTIC,
            ],
            "基准 + 乐观": [PresetScenario.BASE, PresetScenario.OPTIMISTIC],
            "悲观 + 基准": [PresetScenario.PESSIMISTIC, PresetScenario.BASE],
        }
        scenario_choice = st.selectbox(
            "预设情景组合", list(preset_options.keys()), key="gm_scenario_choice",
        )

        if st.button("▶️ 运行情景分析", key="gm_run_scenario"):
            with st.spinner("情景分析中..."):
                engine = ScenarioEngine(analysis_config)
                st.session_state.gm_scenario = engine.run_preset_scenarios(
                    preset_options[scenario_choice]
                )

    with sc_col2:
        scenario_result = st.session_state.gm_scenario
        if scenario_result is not None:
            # 雷达图
            if len(scenario_result.scenarios) >= 2:
                radar_keys = [
                    MetricKey.IRR_TOTAL, MetricKey.NPV_TOTAL,
                    MetricKey.DSCR_MIN, MetricKey.DSCR_AVG,
                    MetricKey.PAYBACK_STATIC, MetricKey.ROE_AVG,
                ]
                _render_scenario_radar(scenario_result, radar_keys)

            st.markdown("**情景对比表**")
            st.dataframe(
                scenario_result.comparison_table(), use_container_width=True
            )
            if len(scenario_result.scenarios) >= 2:
                st.markdown("**偏差表 (vs 基准)**")
                st.dataframe(
                    scenario_result.delta_table(), use_container_width=True
                )
        else:
            st.info("选择情景组合后点击运行")

# ── 蒙特卡罗 ───────────────────────────────────────────
with analysis_tabs[2]:
    st.subheader("蒙特卡罗模拟")

    mc_col1, mc_col2 = st.columns([1, 2])
    with mc_col1:
        iterations = st.number_input(
            "模拟次数", min_value=100, max_value=10000,
            value=1000, step=100, key="gm_mc_iter",
        )
        seed = st.number_input("随机种子", value=42, key="gm_mc_seed")

        use_default_dist = st.checkbox(
            "使用默认分布配置 (3参数)", value=True, key="gm_mc_default"
        )

        if st.button("▶️ 运行蒙特卡罗", key="gm_run_mc"):
            dist_configs = [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ]
            if use_default_dist:
                dist_configs.extend([
                    ParamDistribution(
                        spec=ParamSpec("operating", "pump_price", "抽水电价"),
                        distribution=DistributionType.UNIFORM,
                        dist_params={"low_offset": -0.05, "high_offset": 0.05},
                    ),
                    ParamDistribution(
                        spec=ParamSpec(
                            "operating", "annual_utilization_hours", "年利用小时"
                        ),
                        distribution=DistributionType.TRIANGULAR,
                        dist_params={"low_offset": -200, "high_offset": 200},
                    ),
                ])

            with st.spinner(f"蒙特卡罗模拟 ({iterations}次)..."):
                engine = MonteCarloEngine(analysis_config)
                st.session_state.gm_monte_carlo = engine.run(
                    param_distributions=dist_configs,
                    iterations=iterations,
                    seed=seed,
                )

    with mc_col2:
        mc_result = st.session_state.gm_monte_carlo
        if mc_result is not None:
            st.markdown("**统计摘要**")
            st.dataframe(
                mc_result.summary_table(), use_container_width=True
            )

            for mk in [MetricKey.IRR_TOTAL, MetricKey.NPV_TOTAL]:
                pct_df = mc_result.percentile_table(mk)
                if pct_df is not None and not pct_df.empty:
                    display_name = METRIC_DISPLAY.get(mk, str(mk))
                    # 概率分布图
                    _render_mc_distribution(mc_result, display_name, mk)
                    st.markdown(f"**{display_name} 分位数**")
                    st.dataframe(pct_df, use_container_width=True)
        else:
            st.info("配置参数后点击运行")

# ── 项目对比 ───────────────────────────────────────────
with analysis_tabs[3]:
    st.subheader("多项目横向对比")

    cmp_col1, cmp_col2 = st.columns([1, 2])
    with cmp_col1:
        available_presets = list_presets()
        selected_presets = st.multiselect(
            "选择预设模板",
            options=available_presets,
            default=available_presets[:2] if len(available_presets) >= 2 else available_presets,
            key="gm_cmp_presets",
        )

        if st.button("▶️ 运行项目对比", key="gm_run_compare"):
            if len(selected_presets) < 2:
                st.warning("请至少选择 2 个预设模板")
            else:
                with st.spinner(f"对比 {len(selected_presets)} 个项目..."):
                    engine = ComparisonEngine()
                    st.session_state.gm_comparison = engine.compare_presets(selected_presets)

    with cmp_col2:
        cmp_result = st.session_state.get("gm_comparison")
        if cmp_result is not None:
            st.markdown("**投资概要对比**")
            st.dataframe(cmp_result.investment_summary(), use_container_width=True)

            st.markdown("**指标对比表**")
            st.dataframe(cmp_result.comparison_table(), use_container_width=True)

            st.markdown("**排名表**")
            st.dataframe(cmp_result.rank_table(), use_container_width=True)

            # 雷达图
            if len(cmp_result.projects) >= 2:
                radar_keys = [
                    MetricKey.IRR_TOTAL, MetricKey.NPV_TOTAL,
                    MetricKey.DSCR_MIN, MetricKey.DSCR_AVG,
                    MetricKey.PAYBACK_STATIC, MetricKey.ROE_AVG,
                ]
                _render_scenario_radar(cmp_result, radar_keys)
        else:
            st.info("选择预设模板后点击运行")

# ── 智能问答 ───────────────────────────────────────────
with analysis_tabs[4]:
    st.subheader("智能问答")

    qa_results = st.session_state.gm_results
    if qa_results is not None:
        qa_config = st.session_state.gm_config
        qa_adapter = GenericModelQAAdapter(qa_results, qa_config)

        # 预设常见问题
        st.markdown("**常见问题** (点击即问)")
        preset_qs = qa_adapter.get_preset_questions()
        preset_cols = st.columns(min(3, len(preset_qs)))
        if "gm_qa_selected" not in st.session_state:
            st.session_state.gm_qa_selected = ""

        for i, q in enumerate(preset_qs[:6]):
            col_idx = i % len(preset_cols)
            with preset_cols[col_idx]:
                if st.button(q, key=f"gm_qa_{i}"):
                    st.session_state.gm_qa_selected = q

        # 自由输入
        user_q = st.text_input(
            "或输入自定义问题:",
            value=st.session_state.gm_qa_selected,
            key="gm_qa_input",
        )

        if user_q.strip():
            answer = qa_adapter.ask(user_q.strip())
            st.markdown(answer)
    else:
        st.info("请先运行模型后再使用智能问答")

# ── 导出 ───────────────────────────────────────────────
with analysis_tabs[5]:
    st.subheader("导出结果")
    results = st.session_state.gm_results

    if results is not None:
        exp_col1, exp_col2 = st.columns(2)

        with exp_col1:
            if st.button(
                "📥 导出 Excel (13 Sheet)", use_container_width=True,
                key="gm_export_excel",
            ):
                with st.spinner("生成 Excel..."):
                    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                        path = export_excel(results, tmp.name, project_name="抽蓄项目")
                    with open(path, "rb") as f:
                        st.download_button(
                            "⬇️ 下载 Excel",
                            data=f.read(),
                            file_name="通用模型_财务报表.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )

        with exp_col2:
            if st.button(
                "📥 导出 Word 报告 (7章)", use_container_width=True,
                key="gm_export_word",
            ):
                with st.spinner("生成 Word 报告..."):
                    # 整合敏感性数据
                    sens_data = None
                    sr = st.session_state.gm_sensitivity
                    if sr is not None:
                        sens_data = []
                        for spec in sr.params:
                            items_for = [it for it in sr.items if it.param == spec]
                            if items_for:
                                neg = min(items_for, key=lambda it: it.perturbation)
                                pos = max(items_for, key=lambda it: it.perturbation)
                                sens_data.append({
                                    "param": spec.display_name,
                                    "negative": neg.delta.get(MetricKey.IRR_TOTAL, 0),
                                    "positive": pos.delta.get(MetricKey.IRR_TOTAL, 0),
                                })

                    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                        path = export_report(
                            results, tmp.name,
                            project_name="抽蓄项目",
                            sensitivity_data=sens_data,
                        )
                    with open(path, "rb") as f:
                        st.download_button(
                            "⬇️ 下载 Word 报告",
                            data=f.read(),
                            file_name="通用模型_财务效益分析报告.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True,
                        )
    else:
        st.info("请先运行模型")
