"""
Phase 4E 测试: ModelOrchestrator 端到端集成

验证:
  1. 编排器一次性运行所有引擎 (12 个结果)
  2. 引擎链依赖正确 — 前序引擎输出被后续引擎使用
  3. AllResults 结构完整 — 所有字段非空
  4. 黄金基准值匹配 — IRR/NPV/DSCR 与 Phase 4D 一致
  5. from_excel_v17() 工厂方法
  6. summary() 输出格式
  7. 自定义参数传递
"""

from __future__ import annotations

import pytest
from datetime import date

import pandas as pd

from financial_model.engines.orchestrator import AllResults, ModelOrchestrator
from financial_model.engines.cashflow import CashFlowResult
from financial_model.engines.financing import FinancingResult
from financial_model.engines.pnl import PnLResult
from financial_model.engines.balance_sheet import BalanceSheetResult
from financial_model.engines.derived_metrics import DerivedMetrics
from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import FinancingParams
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams
from financial_model.params.depreciation import DepreciationParams


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def orchestrator() -> ModelOrchestrator:
    """黄金基准编排器"""
    return ModelOrchestrator.from_excel_v17()


@pytest.fixture
def results(orchestrator: ModelOrchestrator) -> AllResults:
    """一次性运行所有引擎"""
    return orchestrator.run()


# ══════════════════════════════════════════════════════════
# 1. 结构完整性
# ══════════════════════════════════════════════════════════


class TestAllResultsStructure:
    """AllResults 包含所有 12 个计算结果"""

    def test_investment_is_dataframe(self, results: AllResults):
        assert isinstance(results.investment, pd.DataFrame)
        assert not results.investment.empty

    def test_financing_is_result(self, results: AllResults):
        assert isinstance(results.financing, FinancingResult)

    def test_depreciation_is_dataframe(self, results: AllResults):
        assert isinstance(results.depreciation, pd.DataFrame)
        assert not results.depreciation.empty

    def test_cost_is_dataframe(self, results: AllResults):
        assert isinstance(results.cost, pd.DataFrame)
        assert not results.cost.empty

    def test_revenue_is_dataframe(self, results: AllResults):
        assert isinstance(results.revenue, pd.DataFrame)
        assert not results.revenue.empty

    def test_pnl_total_is_result(self, results: AllResults):
        assert isinstance(results.pnl_total, PnLResult)

    def test_pnl_equity_is_result(self, results: AllResults):
        assert isinstance(results.pnl_equity, PnLResult)

    def test_cf_total_is_result(self, results: AllResults):
        assert isinstance(results.cf_total, CashFlowResult)

    def test_cf_equity_is_result(self, results: AllResults):
        assert isinstance(results.cf_equity, CashFlowResult)

    def test_cf_plan_is_result(self, results: AllResults):
        assert isinstance(results.cf_plan, CashFlowResult)

    def test_balance_sheet_is_result(self, results: AllResults):
        assert isinstance(results.balance_sheet, BalanceSheetResult)

    def test_derived_metrics_is_result(self, results: AllResults):
        assert isinstance(results.derived_metrics, DerivedMetrics)


# ══════════════════════════════════════════════════════════
# 2. 引擎链正确性
# ══════════════════════════════════════════════════════════


class TestEngineChain:
    """引擎间数据传递正确"""

    def test_project_years(self, results: AllResults):
        """投资表仅覆盖建设期 (8 年)"""
        years = results.investment.index
        assert len(years) == 8  # 仅建设期 2023-2030

    def test_depreciation_covers_full_project(self, results: AllResults):
        """折旧表覆盖完整项目期 (48 年)"""
        assert len(results.depreciation) == 48

    def test_revenue_covers_full_project(self, results: AllResults):
        """收入表覆盖完整项目期 (48 年)"""
        assert len(results.revenue) == 48

    def test_pnl_total_no_financial_expense(self, results: AllResults):
        """全投资利润表无财务费用"""
        op_data = results.pnl_total.data
        # 运营期 financial_expense 应全为 0
        op_years = op_data[op_data.index > 2030]
        assert (op_years["financial_expense"] == 0.0).all()

    def test_pnl_equity_has_financial_expense(self, results: AllResults):
        """资本金利润表有财务费用"""
        op_data = results.pnl_equity.data
        op_years = op_data[op_data.index > 2031]  # 还款开始后
        # 至少有一些年份利息 > 0
        assert (op_years["financial_expense"] > 0).any()

    def test_cost_depreciation_from_depreciation_engine(self, results: AllResults):
        """成本引擎使用折旧引擎结果"""
        # 运营期第一年
        op_start = 2031
        cost_depr = float(results.cost.loc[op_start, "depreciation_total"])
        depr_total = float(results.depreciation.loc[op_start, "total_depreciation"])
        assert abs(cost_depr - depr_total) < 1.0

    def test_balance_sheet_balanced(self, results: AllResults):
        """资产负债表恒等: 资产 = 负债 + 权益"""
        bs = results.balance_sheet.data
        for year in bs.index:
            assets = float(bs.loc[year, "total_assets"])
            liabilities = float(bs.loc[year, "total_liabilities"])
            equity = float(bs.loc[year, "total_equity"])
            assert abs(assets - (liabilities + equity)) < 1.0, (
                f"Year {year}: {assets} != {liabilities} + {equity}"
            )


# ══════════════════════════════════════════════════════════
# 3. 黄金基准值
# ══════════════════════════════════════════════════════════


class TestGoldenBenchmarks:
    """与 Phase 4D 黄金基准对齐"""

    def test_irr_total(self, results: AllResults):
        """全投资 IRR ≈ 5.29%"""
        irr = results.derived_metrics.irr_total
        assert irr is not None
        assert 0.04 < irr < 0.06  # 4-6%

    def test_irr_equity_may_be_none(self, results: AllResults):
        """资本金 IRR 可能为 None (全正现金流)"""
        irr = results.derived_metrics.irr_equity
        # 75% 债务 → 资本金全正 CF → 可能无解
        if irr is not None:
            assert irr > 0

    def test_npv_total_negative(self, results: AllResults):
        """全投资 NPV(8%) < 0 (IRR < 折现率)"""
        npv = results.derived_metrics.npv_total
        assert npv < 0

    def test_npv_equity_positive(self, results: AllResults):
        """资本金 NPV(8%) > 0 (杠杆效应)"""
        npv = results.derived_metrics.npv_equity
        assert npv > 0

    def test_dscr_min_above_1(self, results: AllResults):
        """最低 DSCR > 1.0"""
        assert results.derived_metrics.dscr_min is not None
        assert results.derived_metrics.dscr_min > 1.0

    def test_dscr_min_reasonable(self, results: AllResults):
        """最低 DSCR ≈ 1.08"""
        assert results.derived_metrics.dscr_min is not None
        assert 1.0 < results.derived_metrics.dscr_min < 1.2

    def test_payback_static(self, results: AllResults):
        """静态回收期 ≈ 19-20 年"""
        pb = results.derived_metrics.payback_static
        assert pb is not None
        assert 18 < pb < 22

    def test_dynamic_payback_may_be_none(self, results: AllResults):
        """动态回收期可能为 None (NPV < 0)"""
        pb = results.derived_metrics.payback_dynamic
        # 当 IRR < 折现率时, 动态回收期可能为 None
        if pb is not None:
            assert pb > 0

    def test_construction_investment(self, results: AllResults):
        """建设投资 ≈ 870,000 万元"""
        total = float(results.investment["construction_investment"].sum())
        assert 860_000 < total < 880_000

    def test_dynamic_total_investment(self, results: AllResults):
        """动态总投资 > 建设投资 (含建设期利息)"""
        ci = float(results.investment["construction_investment"].sum())
        dt = results.financing.dynamic_total_investment
        assert dt > ci


# ══════════════════════════════════════════════════════════
# 4. 工厂方法
# ══════════════════════════════════════════════════════════


class TestFactoryMethod:
    """from_excel_v17() 创建黄金基准编排器"""

    def test_default_params(self):
        """默认参数创建成功"""
        orch = ModelOrchestrator.from_excel_v17()
        assert orch.timeline is not None

    def test_custom_dates(self):
        """自定义建设期日期"""
        orch = ModelOrchestrator.from_excel_v17(
            construction_start=date(2025, 1, 1),
            construction_end=date(2032, 6, 30),
            operation_years=30,
        )
        assert orch.timeline.year_range.start == 2025

    def test_custom_discount_rate(self):
        """自定义折现率"""
        orch = ModelOrchestrator.from_excel_v17(discount_rate=0.10)
        results = orch.run()
        # 更高折现率 → NPV 更小 (更负)
        assert results.derived_metrics.discount_rate == 0.10

    def test_run_twice_same_results(self):
        """两次 run() 结果一致 (幂等)"""
        orch = ModelOrchestrator.from_excel_v17()
        r1 = orch.run()
        r2 = orch.run()
        assert abs(
            r1.derived_metrics.irr_total - r2.derived_metrics.irr_total
        ) < 1e-10


# ══════════════════════════════════════════════════════════
# 5. Summary 输出
# ══════════════════════════════════════════════════════════


class TestSummary:
    """summary() 返回人类可读摘要"""

    def test_summary_keys(self, results: AllResults):
        """摘要包含关键指标"""
        s = results.summary()
        assert "全投资IRR" in s
        assert "最低DSCR" in s
        assert "静态回收期(年)" in s
        assert "建设投资(万元)" in s
        assert "动态总投资(万元)" in s

    def test_summary_values_formatted(self, results: AllResults):
        """摘要值已格式化"""
        s = results.summary()
        irr_str = s["全投资IRR"]
        assert isinstance(irr_str, str)
        assert "%" in irr_str

    def test_summary_investment_total(self, results: AllResults):
        """摘要包含投资总额"""
        s = results.summary()
        invest_str = s["建设投资(万元)"]
        assert isinstance(invest_str, str)
        assert "," in invest_str  # 千位分隔符


# ══════════════════════════════════════════════════════════
# 6. 自定义参数
# ══════════════════════════════════════════════════════════


class TestCustomParams:
    """自定义参数正确传递"""

    def test_custom_operating_params(self):
        """自定义运营参数影响结果"""
        default_orch = ModelOrchestrator.from_excel_v17()
        default_r = default_orch.run()

        # 更高电价 → 更高收入 → 更高 IRR
        higher_price = OperatingParams(
            installed_capacity_mw=1400.0,
            annual_utilization_hours=1169.29,
            capacity_price=800.0,  # higher
            grid_price=0.40,  # higher
            pump_price=0.23085,
            auxiliary_power_rate=0.02,
            production_ratios=OperatingParams.from_excel_v17().production_ratios,
        )
        custom_orch = ModelOrchestrator(
            params_construction=ConstructionParams(
                construction_start=date(2023, 2, 1),
                construction_end=date(2030, 7, 31),
            ),
            params_investment=InvestmentParams.from_excel_v17(),
            params_financing=FinancingParams(),
            params_operating=higher_price,
            params_tax=TaxParams.from_excel_v17(),
            params_depreciation=DepreciationParams.from_excel_v17(),
        )
        custom_r = custom_orch.run()

        assert custom_r.derived_metrics.irr_total > default_r.derived_metrics.irr_total

    def test_custom_discount_rate_affects_npv(self):
        """折现率影响 NPV"""
        r8 = ModelOrchestrator.from_excel_v17(discount_rate=0.08).run()
        r12 = ModelOrchestrator.from_excel_v17(discount_rate=0.12).run()

        # 更高折现率 → NPV 更小
        assert r12.derived_metrics.npv_total < r8.derived_metrics.npv_total


# ══════════════════════════════════════════════════════════
# 7. 财务计划现金流量表
# ══════════════════════════════════════════════════════════


class TestFinancialPlanCashFlow:
    """财务计划现金流量表验证"""

    def test_cf_plan_has_three_sections(self, results: AllResults):
        """财务计划表包含三段式结构"""
        df = results.cf_plan.data
        assert "operating_cf" in df.columns
        assert "investing_cf" in df.columns
        assert "financing_cf" in df.columns
        assert "surplus" in df.columns

    def test_cf_plan_surplus_equals_sum(self, results: AllResults):
        """盈余资金 = 经营 + 投资 + 筹资"""
        df = results.cf_plan.data
        expected = df["operating_cf"] + df["investing_cf"] + df["financing_cf"]
        pd.testing.assert_series_equal(df["surplus"], expected, check_names=False)

    def test_cf_plan_cumulative_surplus(self, results: AllResults):
        """累计盈余 = cumsum(盈余)"""
        df = results.cf_plan.data
        expected = df["surplus"].cumsum()
        pd.testing.assert_series_equal(
            df["cumulative_surplus"], expected, check_names=False
        )
