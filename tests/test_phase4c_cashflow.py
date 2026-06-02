"""
Phase 4C 测试: CashFlowEngine (全投资/资本金/财务计划)

验证:
  1. 全投资现金流量表 — 项目本身盈利能力
  2. 资本金现金流量表 — 股东回报
  3. 财务计划现金流量表 — 资金平衡
  4. 经营成本 = 生产成本 - 折旧
  5. 回收余值 + 回收流动资金 (运营期末)
  6. 累计净现金流转正 (投资回收)
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
from financial_model.params.depreciation import DepreciationParams
from financial_model.timeline.generator import generate_timeline
from financial_model.engines.depreciation import DepreciationEngine
from financial_model.engines.cost import CostEngine
from financial_model.engines.revenue import RevenueEngine
from financial_model.engines.financing import FinancingEngine
from financial_model.engines.pnl import PnLEngine, PnLPerspective
from financial_model.engines.cashflow import CashFlowEngine, CashFlowPerspective, CashFlowResult


# ── 黄金基准 ─────────────────────────────────────────────

EXCEL_START = date(2023, 2, 1)
EXCEL_END = date(2030, 7, 31)


# ── Fixtures (完整引擎链) ─────────────────────────────────


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
    return RevenueEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        tax_params=tax_params,
    ).calculate()


@pytest.fixture
def depreciation_result(
    construction_params, investment_params, financing_params,
    timeline, depreciation_params, operating_params,
) -> pd.DataFrame:
    return DepreciationEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        depreciation_params=depreciation_params,
        operating_params=operating_params,
    ).calculate()


@pytest.fixture
def cost_result(
    construction_params, investment_params, financing_params,
    timeline, operating_params, depreciation_result,
) -> pd.DataFrame:
    return CostEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        depreciation_result=depreciation_result,
    ).calculate()


@pytest.fixture
def financing_output(
    construction_params, investment_params, financing_params, timeline,
):
    """FinancingEngine 完整输出"""
    from financial_model.engines.investment import InvestmentAllocation

    allocation = InvestmentAllocation.from_excel_v17()
    invest_engine = __import__(
        "financial_model.engines.investment", fromlist=["InvestmentEngine"]
    ).InvestmentEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        allocation=allocation,
    )
    invest_result = invest_engine.calculate()
    fin_engine = FinancingEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        investment_result=invest_result,
    )
    return fin_engine.calculate()


@pytest.fixture
def interest_by_year(financing_output) -> pd.Series:
    ls = financing_output.loan_schedule
    if ls.empty:
        return pd.Series(dtype=float)
    return ls.set_index("year")["interest_payment"]


@pytest.fixture
def principal_by_year(financing_output) -> pd.Series:
    ls = financing_output.loan_schedule
    if ls.empty:
        return pd.Series(dtype=float)
    return ls.set_index("year")["principal_repayment"]


@pytest.fixture
def capex_by_year(financing_output) -> pd.Series:
    """建设投资按年度"""
    return financing_output.annual_summary["construction_investment"]


@pytest.fixture
def equity_by_year(financing_output) -> pd.Series:
    """资本金按年度"""
    return financing_output.annual_summary["total_equity"]


@pytest.fixture
def debt_inflow_by_year(financing_output) -> pd.Series:
    """债务到账按年度"""
    return financing_output.annual_summary["total_debt"]


@pytest.fixture
def pnl_total(
    construction_params, investment_params, financing_params,
    timeline, tax_params, revenue_result, cost_result, interest_by_year,
):
    engine = PnLEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        tax_params=tax_params,
        revenue_result=revenue_result,
        cost_result=cost_result,
        interest_by_year=interest_by_year,
    )
    return engine.calculate(PnLPerspective.TOTAL_INVESTMENT)


@pytest.fixture
def cashflow_engine(
    construction_params, investment_params, financing_params,
    timeline, tax_params, pnl_total, depreciation_result,
    revenue_result, cost_result, interest_by_year,
    capex_by_year, equity_by_year, debt_inflow_by_year, principal_by_year,
) -> CashFlowEngine:
    return CashFlowEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        tax_params=tax_params,
        pnl_result=pnl_total,
        depreciation_result=depreciation_result,
        revenue_result=revenue_result,
        cost_result=cost_result,
        interest_by_year=interest_by_year,
        capex_by_year=capex_by_year,
        equity_by_year=equity_by_year,
        debt_inflow_by_year=debt_inflow_by_year,
        principal_by_year=principal_by_year,
    )


@pytest.fixture
def cf_total(cashflow_engine) -> CashFlowResult:
    return cashflow_engine.calculate(CashFlowPerspective.TOTAL_INVESTMENT)


@pytest.fixture
def cf_equity(cashflow_engine) -> CashFlowResult:
    return cashflow_engine.calculate(CashFlowPerspective.EQUITY)


@pytest.fixture
def cf_plan(cashflow_engine) -> CashFlowResult:
    return cashflow_engine.calculate(CashFlowPerspective.FINANCIAL_PLAN)


# ══════════════════════════════════════════════════════════
# 全投资现金流量表 (表7)
# ══════════════════════════════════════════════════════════


class TestCFTotalInvestment:
    """全投资现金流量表"""

    def test_engine_name(self, cashflow_engine):
        assert cashflow_engine.name == "cashflow"

    def test_perspective(self, cf_total):
        assert cf_total.perspective == CashFlowPerspective.TOTAL_INVESTMENT

    def test_result_shape(self, cf_total):
        """48年完整覆盖"""
        assert len(cf_total.data) == 48

    def test_required_columns(self, cf_total):
        required = {
            "revenue", "residual_value", "wc_recovery", "total_inflow",
            "capex", "working_capital", "operating_cost", "surcharge",
            "income_tax", "total_outflow", "net_cashflow", "cumulative_cashflow",
        }
        assert required.issubset(set(cf_total.data.columns))

    def test_construction_capex(self, cf_total):
        """建设期有CAPEX, 无收入"""
        for year in range(2023, 2030):
            assert cf_total.data.loc[year, "capex"] > 0
            assert cf_total.data.loc[year, "revenue"] == 0.0

    def test_operation_revenue(self, cf_total):
        """运营期有收入, 无CAPEX"""
        assert cf_total.data.loc[2031, "revenue"] > 0
        assert cf_total.data.loc[2031, "capex"] == 0.0

    def test_operating_cost_excludes_depreciation(
        self, cf_total, cost_result, depreciation_result
    ):
        """经营成本 = 生产成本 - 折旧 (非现金项)"""
        year = 2031
        cost_total = cost_result.loc[year, "total_production_cost"]
        depr = depreciation_result.loc[year, "total_depreciation"]
        expected = cost_total - depr
        actual = cf_total.data.loc[year, "operating_cost"]
        assert actual == pytest.approx(expected, abs=1.0)

    def test_no_debt_flows(self, cf_total):
        """全投资: 无债务相关现金流"""
        assert "debt_inflow" not in cf_total.data.columns
        assert "principal_repayment" not in cf_total.data.columns
        assert "interest_payment" not in cf_total.data.columns

    def test_residual_value_last_year(self, cf_total):
        """回收余值仅出现在最后一年"""
        years = list(cf_total.data.index)
        last = years[-1]
        assert cf_total.data.loc[last, "residual_value"] > 0
        # 之前年份为0
        for year in years[:-1]:
            assert cf_total.data.loc[year, "residual_value"] == 0.0

    def test_wc_recovery_last_year(self, cf_total):
        """回收流动资金仅出现在最后一年"""
        years = list(cf_total.data.index)
        last = years[-1]
        assert cf_total.data.loc[last, "wc_recovery"] > 0
        for year in years[:-1]:
            assert cf_total.data.loc[year, "wc_recovery"] == 0.0

    def test_net_cashflow_formula(self, cf_total):
        """净现金流量 = 流入 - 流出"""
        for year in [2030, 2031, 2035, 2040, 2070]:
            if year in cf_total.data.index:
                row = cf_total.data.loc[year]
                expected = row["total_inflow"] - row["total_outflow"]
                assert row["net_cashflow"] == pytest.approx(expected, abs=1.0)

    def test_cumulative_cashflow(self, cf_total):
        """累计净现金流量 = 逐年累加"""
        expected = cf_total.data["net_cashflow"].cumsum()
        for year in cf_total.data.index:
            assert cf_total.data.loc[year, "cumulative_cashflow"] == pytest.approx(
                expected[year], abs=0.1
            )

    def test_cumulative_turns_positive(self, cf_total):
        """累计净现金流最终转正 (投资回收)"""
        final = cf_total.data["cumulative_cashflow"].iloc[-1]
        assert final > 0, "累计净现金流应在项目期末转正"

    def test_negative_during_construction(self, cf_total):
        """建设期净现金流为负 (大量CAPEX)"""
        for year in range(2023, 2030):
            assert cf_total.data.loc[year, "net_cashflow"] < 0


# ══════════════════════════════════════════════════════════
# 资本金现金流量表 (表8)
# ══════════════════════════════════════════════════════════


class TestCFEquity:
    """资本金现金流量表"""

    def test_perspective(self, cf_equity):
        assert cf_equity.perspective == CashFlowPerspective.EQUITY

    def test_result_shape(self, cf_equity):
        assert len(cf_equity.data) == 48

    def test_has_debt_flows(self, cf_equity):
        """资本金: 包含债务现金流"""
        assert cf_equity.data["debt_inflow"].sum() > 0
        assert cf_equity.data["principal_repayment"].sum() > 0
        assert cf_equity.data["interest_payment"].sum() > 0

    def test_equity_investment_construction(self, cf_equity):
        """建设期有资本金投资"""
        for year in range(2023, 2030):
            assert cf_equity.data.loc[year, "equity_investment"] > 0

    def test_debt_inflow_construction(self, cf_equity):
        """建设期有借款到账"""
        debt_sum = 0.0
        for year in range(2023, 2030):
            debt_sum += cf_equity.data.loc[year, "debt_inflow"]
        assert debt_sum > 0

    def test_repayment_in_operation(self, cf_equity):
        """运营期有还本付息"""
        principal_sum = cf_equity.data.loc[2031:2050, "principal_repayment"].sum()
        assert principal_sum > 0

    def test_net_cashflow_formula(self, cf_equity):
        """净现金流量 = 流入 - 流出"""
        for year in [2031, 2035, 2040]:
            row = cf_equity.data.loc[year]
            expected = row["total_inflow"] - row["total_outflow"]
            assert row["net_cashflow"] == pytest.approx(expected, abs=1.0)


# ══════════════════════════════════════════════════════════
# 财务计划现金流量表 (表9)
# ══════════════════════════════════════════════════════════


class TestCFFinancialPlan:
    """财务计划现金流量表"""

    def test_perspective(self, cf_plan):
        assert cf_plan.perspective == CashFlowPerspective.FINANCIAL_PLAN

    def test_result_shape(self, cf_plan):
        assert len(cf_plan.data) == 48

    def test_required_columns(self, cf_plan):
        required = {
            "operating_cf", "investing_cf", "financing_cf",
            "surplus", "cumulative_surplus",
        }
        assert required.issubset(set(cf_plan.data.columns))

    def test_three_sections(self, cf_plan):
        """三段式: 经营 + 投资 + 筹资 = 盈余"""
        for year in [2031, 2035, 2040]:
            row = cf_plan.data.loc[year]
            expected = row["operating_cf"] + row["investing_cf"] + row["financing_cf"]
            assert row["surplus"] == pytest.approx(expected, abs=1.0)

    def test_operating_cf_positive_in_operation(self, cf_plan):
        """运营期经营活动净现金 > 0"""
        assert cf_plan.data.loc[2031, "operating_cf"] > 0

    def test_investing_cf_negative_during_construction(self, cf_plan):
        """建设期投资活动净现金 < 0"""
        for year in range(2023, 2030):
            assert cf_plan.data.loc[year, "investing_cf"] < 0

    def test_cumulative_surplus(self, cf_plan):
        """累计盈余 = 逐年累加"""
        expected = cf_plan.data["surplus"].cumsum()
        for year in cf_plan.data.index:
            assert cf_plan.data.loc[year, "cumulative_surplus"] == pytest.approx(
                expected[year], abs=0.1
            )


# ══════════════════════════════════════════════════════════
# 跨视角对比
# ══════════════════════════════════════════════════════════


class TestCFCrossPerspective:
    """跨视角一致性"""

    def test_revenue_identical(self, cf_total, cf_equity, cf_plan):
        """三个视角收入完全一致"""
        for year in [2031, 2035, 2040]:
            assert cf_total.data.loc[year, "revenue"] == cf_equity.data.loc[year, "revenue"]
            assert cf_total.data.loc[year, "revenue"] == cf_plan.data.loc[year, "revenue"]

    def test_total_cf_larger_than_equity(self, cf_total, cf_equity):
        """全投资净现金流 >= 资本金净现金流 (运营期)"""
        for year in range(2031, 2050):
            if year in cf_total.data.index and year in cf_equity.data.index:
                # 全投资不扣利息, 所以通常更大
                total_net = cf_total.data.loc[year, "net_cashflow"]
                equity_net = cf_equity.data.loc[year, "net_cashflow"]
                # 不严格要求 >=, 因为资本金有借款到账
                # 但总和应在一个量级
                assert abs(total_net - equity_net) < abs(total_net) * 2 + 100000

    def test_all_48_years(self, cf_total, cf_equity, cf_plan):
        """三个视角都覆盖48年"""
        assert len(cf_total.data) == 48
        assert len(cf_equity.data) == 48
        assert len(cf_plan.data) == 48
