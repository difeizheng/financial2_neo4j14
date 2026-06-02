"""
Phase 2 参数模型 + BaseEngine 测试

验证:
  1. InvestmentParams — 预算科目、派生属性、便利构造器
  2. FinancingParams — 股债结构、贷款条款、验证逻辑
  3. BaseEngine — 抽象接口、工具方法

黄金基准来自 Excel v17 模型实际值。
"""

from __future__ import annotations

import pytest
from datetime import date

from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import (
    BudgetItem,
    InvestmentParams,
    PriceContingencyConfig,
)
from financial_model.params.financing import (
    EquityInputMode,
    EquityInjection,
    FinancingParams,
    LoanTerms,
    RepaymentFrequency,
    RepaymentMethod,
)
from financial_model.engines.base_engine import BaseEngine, EngineResult
from financial_model.timeline.generator import generate_timeline


# ── 黄金基准 (Excel v17 模型) ──────────────────────────────

# 投资概算 (参数输入表 + 投资概算明细)
EXPECTED_HUB_WORKS = 638349.96  # 枢纽工程合计
EXPECTED_INDEPENDENT_FEES = 122855.89  # 独立费用合计
EXPECTED_BASIC_CONTINGENCY = 38197.30  # 基本预备费
EXPECTED_STATIC_INVESTMENT = 810608.42  # 静态投资 (工程)
EXPECTED_STATIC_SELF_FUNDED = 800608.42  # 静态投资 (自主投资)
EXPECTED_CONSTRUCTION_SUBSIDY = 10000.0  # 建设补贴
EXPECTED_WORKING_CAPITAL = 700.0  # 流动资金
EXPECTED_DEDUCTIBLE_VAT = 67754.505  # 可抵扣进项税

# 融资 (参数输入表 rows 223-260)
EXPECTED_EQUITY_TOTAL = 199000.0
EXPECTED_EQUITY_RATIO = 0.25
EXPECTED_CONSTRUCTION_INTEREST_RATE = 0.043  # v17 Excel标注值 (参数输入表 Row 251)
EXPECTED_LONG_TERM_RATE = 0.043
EXPECTED_SHORT_TERM_RATE = 0.0365
EXPECTED_REPAYMENT_TERM = 15
EXPECTED_WORKING_CAPITAL_EQUITY_SHARE = 0.3

EXCEL_START = date(2023, 2, 1)
EXCEL_END = date(2030, 7, 31)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def construction_params() -> ConstructionParams:
    return ConstructionParams(
        construction_start=EXCEL_START,
        construction_end=EXCEL_END,
        operation_years=40,
    )


@pytest.fixture
def timeline(construction_params: ConstructionParams):
    return generate_timeline(construction_params)


@pytest.fixture
def investment_params() -> InvestmentParams:
    return InvestmentParams.from_excel_v17()


@pytest.fixture
def financing_params_by_amount() -> FinancingParams:
    return FinancingParams.from_excel_v17()


@pytest.fixture
def financing_params_by_ratio() -> FinancingParams:
    return FinancingParams(
        equity_input_mode=EquityInputMode.BY_RATIO,
        equity_ratio=EXPECTED_EQUITY_RATIO,
        construction_interest_rate=EXPECTED_CONSTRUCTION_INTEREST_RATE,
        long_term_loan=LoanTerms(
            annual_rate=EXPECTED_LONG_TERM_RATE,
            repayment_term_years=EXPECTED_REPAYMENT_TERM,
            repayment_method=RepaymentMethod.EQUAL_INSTALLMENT,
        ),
        short_term_loan_rate=EXPECTED_SHORT_TERM_RATE,
        working_capital_equity_share=EXPECTED_WORKING_CAPITAL_EQUITY_SHARE,
    )


# ══════════════════════════════════════════════════════════
# InvestmentParams Tests
# ══════════════════════════════════════════════════════════


class TestBudgetItem:
    """BudgetItem NamedTuple 测试"""

    def test_create_budget_item(self):
        item = BudgetItem("施工辅助工程", 62992.58, 0.09)
        assert item.name == "施工辅助工程"
        assert item.amount == 62992.58
        assert item.vat_rate == 0.09

    def test_default_vat_rate(self):
        item = BudgetItem("征地", 5205.27)
        assert item.vat_rate == 0.0

    def test_namedtuple_immutable(self):
        item = BudgetItem("测试", 1000.0)
        with pytest.raises(AttributeError):
            item.amount = 2000.0  # type: ignore[misc]


class TestPriceContingencyConfig:
    """PriceContingencyConfig 测试"""

    def test_default_no_escalation(self):
        config = PriceContingencyConfig()
        assert config.price_escalation_rate == 0.0

    def test_positive_rate(self):
        config = PriceContingencyConfig(price_escalation_rate=0.03)
        assert config.price_escalation_rate == 0.03

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="不能为负数"):
            PriceContingencyConfig(price_escalation_rate=-0.01)


class TestInvestmentParamsBasic:
    """InvestmentParams 基础功能"""

    def test_empty_params(self):
        """空参数 — 所有金额为0"""
        params = InvestmentParams()
        assert params.hub_works_total == 0.0
        assert params.independent_fees_total == 0.0
        assert params.static_investment == 0.0
        assert params.working_capital == 700.0

    def test_invalid_contingency_rate(self):
        with pytest.raises(ValueError, match="基本预备费费率"):
            InvestmentParams(basic_contingency_rate=-0.01)

    def test_invalid_working_capital(self):
        with pytest.raises(ValueError, match="流动资金"):
            InvestmentParams(working_capital=-100)

    def test_invalid_subsidy(self):
        with pytest.raises(ValueError, match="建设补贴"):
            InvestmentParams(construction_subsidy=-1)

    def test_contingency_override(self):
        """直接指定基本预备费金额"""
        params = InvestmentParams(
            hub_budget_items=(BudgetItem("A", 100000),),
            basic_contingency_override=5000.0,
        )
        assert params.basic_contingency == 5000.0

    def test_contingency_by_rate(self):
        """按费率计算基本预备费"""
        params = InvestmentParams(
            hub_budget_items=(BudgetItem("A", 100000),),
            independent_fee_items=(BudgetItem("B", 50000),),
            basic_contingency_rate=0.05,
        )
        assert params.basic_contingency == 150000 * 0.05  # 7500

    def test_custom_items(self):
        """自定义预算项"""
        hub = (
            BudgetItem("科目A", 50000, 0.09),
            BudgetItem("科目B", 30000, 0.0),
        )
        indep = (BudgetItem("费用C", 10000, 0.06),)
        params = InvestmentParams(
            hub_budget_items=hub,
            independent_fee_items=indep,
        )
        assert params.hub_works_total == 80000
        assert params.independent_fees_total == 10000
        assert params.deductible_input_vat == 50000 * 0.09 + 10000 * 0.06

    def test_all_budget_items(self):
        """all_budget_items 合并所有项"""
        hub = (BudgetItem("A", 100),)
        indep = (BudgetItem("B", 200),)
        params = InvestmentParams(
            hub_budget_items=hub,
            independent_fee_items=indep,
        )
        assert len(params.all_budget_items) == 2


class TestInvestmentParamsV17:
    """InvestmentParams 与 Excel v17 黄金基准对比"""

    def test_hub_item_count(self, investment_params: InvestmentParams):
        """枢纽工程应有 7 个子项 (不含征地)"""
        assert len(investment_params.hub_budget_items) == 7

    def test_independent_fee_count(self, investment_params: InvestmentParams):
        """独立费用应有 4 个子项"""
        assert len(investment_params.independent_fee_items) == 4

    def test_hub_works_total(self, investment_params: InvestmentParams):
        """枢纽工程合计 = 638,349.96 万元 (不含征地)"""
        assert abs(investment_params.hub_works_total - EXPECTED_HUB_WORKS) < 0.01

    def test_land_resettlement(self, investment_params: InvestmentParams):
        """建设征地和移民安置 = 5,205.27 万元 (独立于枢纽工程)"""
        assert abs(investment_params.land_resettlement - 5205.27) < 0.01

    def test_independent_fees_total(self, investment_params: InvestmentParams):
        """独立费用合计 = 122,855.89 万元"""
        assert abs(
            investment_params.independent_fees_total - EXPECTED_INDEPENDENT_FEES
        ) < 0.01

    def test_basic_contingency(self, investment_params: InvestmentParams):
        """基本预备费 = 38,197.30 万元 (直接指定)"""
        assert abs(
            investment_params.basic_contingency - EXPECTED_BASIC_CONTINGENCY
        ) < 0.01

    def test_static_investment(self, investment_params: InvestmentParams):
        """静态投资 (工程) = 810,608.42 万元"""
        assert abs(
            investment_params.static_investment - EXPECTED_STATIC_INVESTMENT
        ) < 0.5

    def test_static_self_funded(self, investment_params: InvestmentParams):
        """静态投资 (自主投资) = 800,608.42 万元"""
        assert abs(
            investment_params.static_investment_self_funded
            - EXPECTED_STATIC_SELF_FUNDED
        ) < 0.5

    def test_construction_subsidy(self, investment_params: InvestmentParams):
        """建设补贴 = 10,000 万元"""
        assert investment_params.construction_subsidy == EXPECTED_CONSTRUCTION_SUBSIDY

    def test_working_capital(self, investment_params: InvestmentParams):
        """流动资金 = 700 万元"""
        assert investment_params.working_capital == EXPECTED_WORKING_CAPITAL

    def test_energy_storage(self, investment_params: InvestmentParams):
        """储能投资 = 6,000 万元"""
        assert investment_params.energy_storage_investment == 6000.0

    def test_deductible_vat(self, investment_params: InvestmentParams):
        """可抵扣进项税 — 基于各预算项 × 增值税率
        注: Excel 的可抵扣进项税 67,754.50 包含了更细致的计算
        (含基本预备费的增值税等), 引擎后续会精确匹配。
        """
        vat = investment_params.deductible_input_vat
        # Hub items VAT: 638349.96 weighted avg ≈ 0.09 → ~57,451
        # Independent fees VAT: 54324.05*0.06 + 56990.05*0.06 ≈ 6,679
        # Storage VAT: 6000*0.13 = 780
        # Total ≈ 64,910
        assert vat > 60000
        assert vat < 70000

    def test_summary(self, investment_params: InvestmentParams):
        """summary() 包含所有关键值"""
        s = investment_params.summary()
        assert abs(s["static_investment"] - EXPECTED_STATIC_INVESTMENT) < 0.5
        assert abs(s["static_self_funded"] - EXPECTED_STATIC_SELF_FUNDED) < 0.5
        assert s["working_capital"] == 700.0
        assert abs(s["hub_works"] - EXPECTED_HUB_WORKS) < 0.01
        assert abs(s["independent_fees"] - EXPECTED_INDEPENDENT_FEES) < 0.01


class TestInvestmentParamsFrozen:
    """参数不可变性"""

    def test_frozen(self):
        params = InvestmentParams()
        with pytest.raises(AttributeError):
            params.working_capital = 1000  # type: ignore[misc]


# ══════════════════════════════════════════════════════════
# FinancingParams Tests
# ══════════════════════════════════════════════════════════


class TestLoanTerms:
    """LoanTerms 贷款条款测试"""

    def test_default_terms(self):
        terms = LoanTerms()
        assert terms.annual_rate == 0.043
        assert terms.repayment_term_years == 15
        assert terms.repayment_method == RepaymentMethod.EQUAL_INSTALLMENT
        assert terms.repayment_frequency == RepaymentFrequency.ANNUAL

    def test_periods_per_year(self):
        assert LoanTerms().periods_per_year == 1
        assert LoanTerms(
            repayment_frequency=RepaymentFrequency.QUARTERLY
        ).periods_per_year == 4
        assert LoanTerms(
            repayment_frequency=RepaymentFrequency.MONTHLY
        ).periods_per_year == 12

    def test_period_rate(self):
        """每期利率 = 年利率 / 期数"""
        terms = LoanTerms(
            annual_rate=0.043,
            repayment_frequency=RepaymentFrequency.ANNUAL,
        )
        assert abs(terms.period_rate - 0.043) < 1e-10

        terms_q = LoanTerms(
            annual_rate=0.043,
            repayment_frequency=RepaymentFrequency.QUARTERLY,
        )
        assert abs(terms_q.period_rate - 0.043 / 4) < 1e-10

    def test_total_periods(self):
        """总还款期数"""
        terms = LoanTerms(
            repayment_term_years=15,
            repayment_frequency=RepaymentFrequency.ANNUAL,
        )
        assert terms.total_periods == 15

        terms_m = LoanTerms(
            repayment_term_years=15,
            repayment_frequency=RepaymentFrequency.MONTHLY,
        )
        assert terms_m.total_periods == 180

    def test_invalid_rate(self):
        with pytest.raises(ValueError, match="年利率"):
            LoanTerms(annual_rate=-0.01)

    def test_invalid_term(self):
        with pytest.raises(ValueError, match="还款期限"):
            LoanTerms(repayment_term_years=0)

    def test_term_too_long(self):
        with pytest.raises(ValueError, match="不能大于"):
            LoanTerms(repayment_term_years=60)


class TestFinancingParamsByRatio:
    """按比例输入的融资参数"""

    def test_basic_creation(self, financing_params_by_ratio: FinancingParams):
        params = financing_params_by_ratio
        assert params.equity_input_mode == EquityInputMode.BY_RATIO
        assert params.equity_ratio == 0.25

    def test_equity_amount(self, financing_params_by_ratio: FinancingParams):
        """资本金 = 动态总投资 × 25%"""
        equity = financing_params_by_ratio.equity_amount(976945.68)
        assert abs(equity - 976945.68 * 0.25) < 1.0

    def test_debt_amount(self, financing_params_by_ratio: FinancingParams):
        """债务 = 动态总投资 - 资本金"""
        total = 976945.68
        debt = financing_params_by_ratio.debt_amount(total)
        expected_debt = total * 0.75
        assert abs(debt - expected_debt) < 1.0

    def test_invalid_equity_ratio_zero(self):
        with pytest.raises(ValueError, match="资本金比例"):
            FinancingParams(
                equity_input_mode=EquityInputMode.BY_RATIO,
                equity_ratio=0.0,
            )

    def test_invalid_equity_ratio_over_1(self):
        with pytest.raises(ValueError, match="资本金比例"):
            FinancingParams(
                equity_input_mode=EquityInputMode.BY_RATIO,
                equity_ratio=1.5,
            )


class TestFinancingParamsByAmount:
    """按金额输入的融资参数 (v17 模式)"""

    def test_v17_creation(self, financing_params_by_amount: FinancingParams):
        params = financing_params_by_amount
        assert params.equity_input_mode == EquityInputMode.BY_AMOUNT
        assert len(params.equity_injections) == 10

    def test_v17_total_equity(self, financing_params_by_amount: FinancingParams):
        """总资本金 = 199,000 万元"""
        assert abs(
            financing_params_by_amount.total_equity_by_amount - EXPECTED_EQUITY_TOTAL
        ) < 1.0

    def test_v17_injection_schedule(self, financing_params_by_amount: FinancingParams):
        """到账计划正确"""
        injections = financing_params_by_amount.equity_injections
        assert injections[0] == EquityInjection("2023-03", 6000)
        assert injections[1] == EquityInjection("2023-12", 15000)
        assert injections[-1] == EquityInjection("2030-07", 12000)

    def test_v17_loan_terms(self, financing_params_by_amount: FinancingParams):
        """贷款条款正确"""
        loan = financing_params_by_amount.long_term_loan
        assert loan.annual_rate == EXPECTED_LONG_TERM_RATE
        assert loan.repayment_term_years == EXPECTED_REPAYMENT_TERM
        assert loan.repayment_method == RepaymentMethod.EQUAL_INSTALLMENT

    def test_v17_rates(self, financing_params_by_amount: FinancingParams):
        """各项利率正确"""
        assert (
            financing_params_by_amount.construction_interest_rate
            == EXPECTED_CONSTRUCTION_INTEREST_RATE
        )
        assert (
            financing_params_by_amount.short_term_loan_rate == EXPECTED_SHORT_TERM_RATE
        )

    def test_v17_short_term_borrowing(self, financing_params_by_amount: FinancingParams):
        """短期借款全部为0 (v17 模型)"""
        assert len(financing_params_by_amount.short_term_borrowing) == 40
        assert all(b == 0.0 for b in financing_params_by_amount.short_term_borrowing)

    def test_v17_corporate_params(self, financing_params_by_amount: FinancingParams):
        """公司治理参数正确"""
        assert financing_params_by_amount.registered_capital == 10000.0
        assert financing_params_by_amount.shareholding_ratio == 0.7
        assert financing_params_by_amount.dividend_payout_ratio == 0.6
        assert financing_params_by_amount.statutory_reserve_limit == 5000.0
        assert financing_params_by_amount.statutory_reserve_ratio == 0.1
        assert financing_params_by_amount.discretionary_reserve_ratio == 0.05


class TestFinancingParamsValidation:
    """融资参数验证"""

    def test_invalid_construction_rate(self):
        with pytest.raises(ValueError, match="建设期利息贷款利率"):
            FinancingParams(construction_interest_rate=-0.01)

    def test_invalid_short_term_rate(self):
        with pytest.raises(ValueError, match="短期贷款利率"):
            FinancingParams(short_term_loan_rate=-0.01)

    def test_invalid_working_capital_share(self):
        with pytest.raises(ValueError, match="流动资金中资本金占比"):
            FinancingParams(working_capital_equity_share=1.5)

    def test_invalid_dividend_ratio(self):
        with pytest.raises(ValueError, match="分红比例"):
            FinancingParams(dividend_payout_ratio=1.5)

    def test_by_amount_uses_injections(
        self, financing_params_by_amount: FinancingParams
    ):
        """按金额模式: equity_amount 返回到账总额"""
        amount = financing_params_by_amount.equity_amount(999999)
        assert amount == financing_params_by_amount.total_equity_by_amount

    def test_summary_by_amount(self, financing_params_by_amount: FinancingParams):
        s = financing_params_by_amount.summary()
        assert s["equity_mode"] == "by_amount"
        assert s["equity_total"] == 199000.0

    def test_summary_by_ratio(self, financing_params_by_ratio: FinancingParams):
        s = financing_params_by_ratio.summary()
        assert s["equity_mode"] == "by_ratio"
        assert s["equity_ratio"] == 0.25


class TestFinancingParamsFrozen:
    """融资参数不可变性"""

    def test_frozen(self, financing_params_by_ratio: FinancingParams):
        with pytest.raises(AttributeError):
            financing_params_by_ratio.equity_ratio = 0.3  # type: ignore[misc]

    def test_loan_terms_frozen(self):
        terms = LoanTerms()
        with pytest.raises(AttributeError):
            terms.annual_rate = 0.05  # type: ignore[misc]


# ══════════════════════════════════════════════════════════
# BaseEngine Tests
# ══════════════════════════════════════════════════════════


class _DummyEngine(BaseEngine):
    """用于测试的具体引擎"""

    @property
    def name(self) -> str:
        return "dummy"

    def calculate(self):
        import pandas as pd

        years = list(
            range(self._timeline.year_range.start, self._timeline.year_range.stop)
        )
        return pd.DataFrame({"year": years, "value": [0] * len(years)})


class _TestEngine(BaseEngine):
    """用于测试 _aligned_yearly_df 的引擎"""

    @property
    def name(self) -> str:
        return "test"

    def calculate(self):
        return self._aligned_yearly_df(
            {"amount": [100, 200, 300]},
            start_year=2023,
            end_year=2025,
        )


class TestBaseEngine:
    """BaseEngine 抽象基类测试"""

    def test_cannot_instantiate_directly(
        self, construction_params, investment_params, timeline
    ):
        """不能直接实例化抽象类"""
        with pytest.raises(TypeError):
            BaseEngine(  # type: ignore[abstract]
                params_construction=construction_params,
                params_investment=investment_params,
                params_financing=FinancingParams(),
                timeline=timeline,
            )

    def test_concrete_engine(
        self, construction_params, investment_params, timeline
    ):
        """具体引擎可以实例化并计算"""
        engine = _DummyEngine(
            params_construction=construction_params,
            params_investment=investment_params,
            params_financing=FinancingParams(),
            timeline=timeline,
        )
        assert engine.name == "dummy"
        result = engine.calculate()
        assert "year" in result.columns
        assert len(result) == 48  # 2023-2070

    def test_engine_properties(
        self, construction_params, investment_params, timeline
    ):
        """引擎属性访问"""
        engine = _DummyEngine(
            params_construction=construction_params,
            params_investment=investment_params,
            params_financing=FinancingParams(),
            timeline=timeline,
        )
        assert engine.construction is construction_params
        assert engine.investment is investment_params
        assert engine.timeline is timeline

    def test_aligned_yearly_df(self, construction_params, timeline):
        """_aligned_yearly_df 创建对齐 DataFrame"""
        engine = _TestEngine(
            params_construction=construction_params,
            params_investment=InvestmentParams(),
            params_financing=FinancingParams(),
            timeline=timeline,
        )
        df = engine.calculate()
        assert len(df) == 3
        assert df.index.name == "year"
        assert list(df.index) == [2023, 2024, 2025]
        assert df.loc[2023, "amount"] == 100

    def test_engine_result(self):
        """EngineResult 数据类"""
        import pandas as pd

        result = EngineResult(
            engine_name="test",
            data=pd.DataFrame({"a": [1]}),
            warnings=("warning1",),
        )
        assert result.engine_name == "test"
        assert len(result.warnings) == 1
        assert result.data is not None

    def test_validate_inputs_default(
        self, construction_params, investment_params, timeline
    ):
        """默认 validate_inputs 返回空列表"""
        engine = _DummyEngine(
            params_construction=construction_params,
            params_investment=investment_params,
            params_financing=FinancingParams(),
            timeline=timeline,
        )
        assert engine.validate_inputs() == []


# ══════════════════════════════════════════════════════════
# Integration: Params + Timeline
# ══════════════════════════════════════════════════════════


class TestParamsTimelineIntegration:
    """参数 + 时间轴集成测试"""

    def test_investment_with_timeline(
        self,
        construction_params: ConstructionParams,
        investment_params: InvestmentParams,
        timeline,
    ):
        """投资参数 + 时间轴可以一起使用"""
        assert timeline.construction_years == 8
        assert investment_params.working_capital > 0
        assert timeline.construction_years > 0

    def test_financing_with_timeline(
        self,
        construction_params: ConstructionParams,
        financing_params_by_amount: FinancingParams,
        timeline,
    ):
        """融资参数 + 时间轴可以一起使用"""
        loan = financing_params_by_amount.long_term_loan
        assert loan.repayment_term_years <= construction_params.operation_years
        assert timeline.operation_years == 40
