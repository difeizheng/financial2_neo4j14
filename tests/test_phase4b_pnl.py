"""
Phase 4B 测试: TaxCalculator + PnLEngine

黄金基准来自 Excel v17:
  - 表5-利润与利润分配表 (资本金)
  - 表6-利润与利润分配表 (全投资)

验证:
  1. TaxCalculator 亏损弥补逻辑 (FIFO, 5年窗口)
  2. PnL 全投资视角 (无利息)
  3. PnL 资本金视角 (含利息)
  4. 引擎链式集成 (Revenue+Cost+Financing → PnL)
"""

from __future__ import annotations

import pytest
from datetime import date

import pandas as pd

from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import FinancingParams
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import generate_timeline
from financial_model.engines.depreciation import DepreciationEngine
from financial_model.params.depreciation import DepreciationParams
from financial_model.engines.cost import CostEngine
from financial_model.engines.revenue import RevenueEngine
from financial_model.engines.financing import FinancingEngine
from financial_model.engines.tax_calculator import TaxCalculator, TaxCalcResult
from financial_model.engines.pnl import PnLEngine, PnLPerspective, PnLResult


# ── 黄金基准 ─────────────────────────────────────────────

EXCEL_START = date(2023, 2, 1)
EXCEL_END = date(2030, 7, 31)

# 容差
TOL_PCT = 0.20  # 20% for simplified model


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def construction_params() -> ConstructionParams:
    return ConstructionParams(
        construction_start=EXCEL_START,
        construction_end=EXCEL_END,
        operation_years=40,
    )


@pytest.fixture
def timeline(construction_params):
    return generate_timeline(construction_params)


@pytest.fixture
def investment_params() -> InvestmentParams:
    return InvestmentParams.from_excel_v17()


@pytest.fixture
def financing_params() -> FinancingParams:
    return FinancingParams()


@pytest.fixture
def operating_params() -> OperatingParams:
    return OperatingParams.from_excel_v17()


@pytest.fixture
def tax_params() -> TaxParams:
    return TaxParams.from_excel_v17()


@pytest.fixture
def depreciation_params() -> DepreciationParams:
    return DepreciationParams.from_excel_v17()


@pytest.fixture
def revenue_result(
    construction_params, investment_params, financing_params,
    timeline, operating_params, tax_params,
) -> pd.DataFrame:
    engine = RevenueEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        tax_params=tax_params,
    )
    return engine.calculate()


@pytest.fixture
def depreciation_result(
    construction_params, investment_params, financing_params,
    timeline, depreciation_params, operating_params,
) -> pd.DataFrame:
    engine = DepreciationEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        depreciation_params=depreciation_params,
        operating_params=operating_params,
    )
    return engine.calculate()


@pytest.fixture
def cost_result(
    construction_params, investment_params, financing_params,
    timeline, operating_params, depreciation_result,
) -> pd.DataFrame:
    engine = CostEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        depreciation_result=depreciation_result,
    )
    return engine.calculate()


@pytest.fixture
def financing_result_df(
    construction_params, investment_params, financing_params, timeline,
) -> pd.DataFrame:
    """FinancingEngine 的 loan_schedule"""
    engine = FinancingEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        investment_result=pd.DataFrame(
            {"construction_investment": [100000] * 8},
            index=list(range(2023, 2031)),
        ),
    )
    result = engine.calculate()
    return result.loan_schedule


@pytest.fixture
def interest_by_year(financing_result_df) -> pd.Series:
    """利息按年度聚合"""
    if financing_result_df.empty:
        return pd.Series(dtype=float)
    return financing_result_df.set_index("year")["interest_payment"]


@pytest.fixture
def pnl_engine(
    construction_params, investment_params, financing_params,
    timeline, tax_params, revenue_result, cost_result, interest_by_year,
) -> PnLEngine:
    return PnLEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        tax_params=tax_params,
        revenue_result=revenue_result,
        cost_result=cost_result,
        interest_by_year=interest_by_year,
    )


@pytest.fixture
def pnl_total(pnl_engine) -> PnLResult:
    return pnl_engine.calculate(PnLPerspective.TOTAL_INVESTMENT)


@pytest.fixture
def pnl_equity(pnl_engine) -> PnLResult:
    return pnl_engine.calculate(PnLPerspective.EQUITY)


# ══════════════════════════════════════════════════════════
# TaxCalculator Tests
# ══════════════════════════════════════════════════════════


class TestTaxCalculator:
    """所得税计算器 — 基础逻辑"""

    def test_all_profitable(self):
        """全部盈利: 简单按率计算"""
        profit = pd.Series({2030: 10000, 2031: 20000, 2032: 30000})
        calc = TaxCalculator(income_tax_rate=0.25)
        result = calc.calculate(profit)
        assert result.income_tax.loc[2030] == pytest.approx(2500)
        assert result.income_tax.loc[2031] == pytest.approx(5000)
        assert result.income_tax.loc[2032] == pytest.approx(7500)

    def test_loss_no_tax(self):
        """亏损年不缴税"""
        profit = pd.Series({2030: -5000, 2031: 3000})
        calc = TaxCalculator(income_tax_rate=0.25)
        result = calc.calculate(profit)
        assert result.income_tax.loc[2030] == 0.0

    def test_loss_carryforward_basic(self):
        """亏损弥补: 第1年亏损, 第2年盈利先弥补"""
        profit = pd.Series({2030: -5000, 2031: 10000})
        calc = TaxCalculator(income_tax_rate=0.25, loss_carryforward_years=5)
        result = calc.calculate(profit)
        # 2031: 10000 - 5000 = 5000 taxable
        assert result.taxable_income.loc[2031] == pytest.approx(5000)
        assert result.income_tax.loc[2031] == pytest.approx(1250)
        assert result.loss_utilized.loc[2031] == pytest.approx(5000)

    def test_loss_exceeds_profit(self):
        """亏损大于盈利: 部分弥补, 剩余继续结转"""
        profit = pd.Series({2030: -10000, 2031: 3000, 2032: 4000, 2033: 5000})
        calc = TaxCalculator(income_tax_rate=0.25, loss_carryforward_years=5)
        result = calc.calculate(profit)
        # 2031: 3000弥补, 剩余亏损7000
        assert result.loss_utilized.loc[2031] == pytest.approx(3000)
        assert result.income_tax.loc[2031] == 0.0
        # 2032: 4000弥补, 剩余亏损3000
        assert result.loss_utilized.loc[2032] == pytest.approx(4000)
        assert result.income_tax.loc[2032] == 0.0
        # 2033: 5000 - 3000 = 2000 taxable
        assert result.taxable_income.loc[2033] == pytest.approx(2000)
        assert result.income_tax.loc[2033] == pytest.approx(500)

    def test_loss_carryforward_expiry(self):
        """亏损弥补过期: 超过5年窗口作废"""
        profit = pd.Series({
            2025: -10000,  # 亏损
            2026: 0, 2027: 0, 2028: 0, 2029: 0, 2030: 0,  # 5年不盈利
            2031: 15000,  # 第7年 — 超过5年窗口
        })
        calc = TaxCalculator(income_tax_rate=0.25, loss_carryforward_years=5)
        result = calc.calculate(profit)
        # 2031 - 2025 = 6年 > 5年窗口, 亏损过期
        assert result.loss_utilized.loc[2031] == 0.0
        assert result.taxable_income.loc[2031] == pytest.approx(15000)

    def test_fifo_order(self):
        """FIFO: 先亏损的先弥补"""
        profit = pd.Series({
            2028: -3000,  # 第1笔亏损
            2029: -2000,  # 第2笔亏损
            2030: 4000,   # 盈利: 先弥补2028的3000, 剩1000弥补2029的2000
        })
        calc = TaxCalculator(income_tax_rate=0.25, loss_carryforward_years=5)
        result = calc.calculate(profit)
        # 4000 - 3000 - 1000 = 0 taxable
        assert result.taxable_income.loc[2030] == pytest.approx(0.0)
        assert result.loss_utilized.loc[2030] == pytest.approx(4000)

    def test_zero_profit_no_loss(self):
        """利润=0: 不缴税, 不产生亏损"""
        profit = pd.Series({2030: 0.0, 2031: 10000})
        calc = TaxCalculator(income_tax_rate=0.25)
        result = calc.calculate(profit)
        assert result.income_tax.loc[2030] == 0.0
        assert result.loss_carried.loc[2030] == 0.0

    def test_loss_carried_tracking(self):
        """待弥补亏损余额追踪"""
        profit = pd.Series({2030: -10000, 2031: 3000, 2032: 2000})
        calc = TaxCalculator(income_tax_rate=0.25, loss_carryforward_years=5)
        result = calc.calculate(profit)
        # 2030末: 10000待弥补
        assert result.loss_carried.loc[2030] == pytest.approx(10000)
        # 2031末: 10000 - 3000 = 7000
        assert result.loss_carried.loc[2031] == pytest.approx(7000)
        # 2032末: 7000 - 2000 = 5000
        assert result.loss_carried.loc[2032] == pytest.approx(5000)

    def test_zero_rate(self):
        """零税率"""
        profit = pd.Series({2030: 10000})
        calc = TaxCalculator(income_tax_rate=0.0)
        result = calc.calculate(profit)
        assert result.income_tax.loc[2030] == 0.0

    def test_invalid_rate(self):
        with pytest.raises(ValueError):
            TaxCalculator(income_tax_rate=-0.1)

    def test_invalid_carryforward(self):
        with pytest.raises(ValueError):
            TaxCalculator(loss_carryforward_years=-1)


# ══════════════════════════════════════════════════════════
# PnLEngine Tests
# ══════════════════════════════════════════════════════════


class TestPnLTotal:
    """全投资利润表"""

    def test_engine_name(self, pnl_engine):
        assert pnl_engine.name == "pnl"

    def test_perspective(self, pnl_total):
        assert pnl_total.perspective == PnLPerspective.TOTAL_INVESTMENT

    def test_result_shape(self, pnl_total):
        """48年完整覆盖"""
        assert len(pnl_total.data) == 48

    def test_required_columns(self, pnl_total):
        required = {
            "revenue", "surcharge", "production_cost", "financial_expense",
            "total_cost", "profit_before_tax", "income_tax",
            "net_profit", "cumulative_profit",
        }
        assert required.issubset(set(pnl_total.data.columns))

    def test_no_interest_in_total(self, pnl_total):
        """全投资: 财务费用=0"""
        assert (pnl_total.data["financial_expense"] == 0.0).all()

    def test_construction_period_zero(self, pnl_total):
        """建设期利润表为0"""
        for year in range(2023, 2030):
            assert pnl_total.data.loc[year, "revenue"] == 0.0
            assert pnl_total.data.loc[year, "total_cost"] == 0.0
            assert pnl_total.data.loc[year, "net_profit"] == 0.0

    def test_operation_revenue_positive(self, pnl_total):
        """运营期营业收入 > 0"""
        assert pnl_total.data.loc[2031, "revenue"] > 0

    def test_profit_formula(self, pnl_total):
        """利润总额 = 收入 - 附加税 - 总成本"""
        for year in range(2031, 2035):
            row = pnl_total.data.loc[year]
            expected = row["revenue"] - row["surcharge"] - row["total_cost"]
            assert row["profit_before_tax"] == pytest.approx(expected, abs=1.0)

    def test_net_profit_formula(self, pnl_total):
        """净利润 = 利润总额 - 所得税"""
        for year in range(2031, 2035):
            row = pnl_total.data.loc[year]
            expected = row["profit_before_tax"] - row["income_tax"]
            assert row["net_profit"] == pytest.approx(expected, abs=1.0)

    def test_cumulative_profit(self, pnl_total):
        """累计净利润 = 逐年累加"""
        actual = pnl_total.data["cumulative_profit"]
        expected = pnl_total.data["net_profit"].cumsum()
        for year in actual.index:
            assert actual[year] == pytest.approx(expected[year], abs=0.01)

    def test_tax_rate_25pct(self, pnl_total):
        """所得税率 = 25% (在盈利年)"""
        for year in [2032, 2033, 2035]:
            pbt = pnl_total.data.loc[year, "profit_before_tax"]
            tax = pnl_total.data.loc[year, "income_tax"]
            if pbt > 0 and tax > 0:
                effective_rate = tax / pbt
                # 有效税率 <= 25% (亏损弥补可能降低)
                assert effective_rate <= 0.25 + 0.01


class TestPnLEquity:
    """资本金利润表"""

    def test_perspective(self, pnl_equity):
        assert pnl_equity.perspective == PnLPerspective.EQUITY

    def test_has_interest(self, pnl_equity):
        """资本金: 财务费用 > 0 (还款期间)"""
        interest_years = pnl_equity.data["financial_expense"]
        assert interest_years.sum() > 0

    def test_equity_profit_lower(self, pnl_total, pnl_equity):
        """资本金利润 <= 全投资利润 (因为多扣利息)"""
        for year in range(2031, 2050):
            eq_profit = pnl_equity.data.loc[year, "profit_before_tax"]
            tt_profit = pnl_total.data.loc[year, "profit_before_tax"]
            # 资本金利润应 <= 全投资利润 (或相等, 如果无利息)
            assert eq_profit <= tt_profit + 1.0

    def test_interest_from_financing(self, pnl_equity):
        """利息支出只在还款期间存在"""
        # 建设期无利息
        for year in range(2023, 2030):
            assert pnl_equity.data.loc[year, "financial_expense"] == 0.0
        # 运营期有利息
        interest_sum = pnl_equity.data["financial_expense"].sum()
        assert interest_sum > 0

    def test_total_cost_includes_interest(self, pnl_equity):
        """总成本 = 生产成本 + 财务费用"""
        for year in range(2031, 2040):
            row = pnl_equity.data.loc[year]
            expected = row["production_cost"] + row["financial_expense"]
            assert row["total_cost"] == pytest.approx(expected, abs=1.0)


class TestPnLCalculateBoth:
    """calculate_both 一次性双视角"""

    def test_returns_two_results(self, pnl_engine):
        total, equity = pnl_engine.calculate_both()
        assert total.perspective == PnLPerspective.TOTAL_INVESTMENT
        assert equity.perspective == PnLPerspective.EQUITY

    def test_revenue_identical(self, pnl_engine):
        """两个视角收入完全一致"""
        total, equity = pnl_engine.calculate_both()
        pd.testing.assert_series_equal(
            total.data["revenue"], equity.data["revenue"]
        )

    def test_production_cost_identical(self, pnl_engine):
        """两个视角生产成本完全一致"""
        total, equity = pnl_engine.calculate_both()
        pd.testing.assert_series_equal(
            total.data["production_cost"], equity.data["production_cost"]
        )


class TestPnLIntegration:
    """Phase 4B 引擎链式集成"""

    def test_revenue_feeds_pnl(self, pnl_total, revenue_result):
        """PnL收入 = RevenueEngine输出"""
        for year in [2031, 2035, 2040]:
            pnl_rev = pnl_total.data.loc[year, "revenue"]
            eng_rev = revenue_result.loc[year, "total_revenue"]
            assert pnl_rev == pytest.approx(eng_rev, abs=1.0)

    def test_cost_feeds_pnl(self, pnl_total, cost_result):
        """PnL生产成本 = CostEngine输出"""
        for year in [2031, 2035, 2040]:
            pnl_cost = pnl_total.data.loc[year, "production_cost"]
            eng_cost = cost_result.loc[year, "total_production_cost"]
            assert pnl_cost == pytest.approx(eng_cost, abs=1.0)

    def test_profit_reasonable_range(self, pnl_total):
        """满产年利润在合理范围"""
        profit_2031 = pnl_total.data.loc[2031, "profit_before_tax"]
        # 收入~13万/年 - 成本~8万/年 ≈ ~5万/年 (不含利息)
        assert profit_2031 > 0, "满产年利润总额应 > 0"
        assert profit_2031 < 200000, "利润总额应在合理范围"

    def test_net_profit_positive_eventually(self, pnl_total):
        """运营中期净利润转正"""
        # 前几年可能因投产比例低或亏损弥补导致负值
        # 但中长期应转正
        later_years = pnl_total.data.loc[2045:2055, "net_profit"]
        assert later_years.sum() > 0

    def test_tax_result_attached(self, pnl_total):
        """PnLResult 包含 TaxCalcResult"""
        assert pnl_total.tax_result is not None
        assert isinstance(pnl_total.tax_result, TaxCalcResult)

    def test_tax_result_has_loss_tracking(self, pnl_total):
        """所得税结果包含亏损追踪"""
        tr = pnl_total.tax_result
        assert "loss_utilized" in tr.__dataclass_fields__
        assert "loss_carried" in tr.__dataclass_fields__
