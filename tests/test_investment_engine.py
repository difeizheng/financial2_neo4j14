"""
Phase 2B: InvestmentEngine 测试

验证投资概算引擎的计算结果与 Excel v17 模型一致。

黄金基准来自:
  - 投资概算明细 rows 5-24
  - 参数输入表 rows 193-222
"""

from __future__ import annotations

import pytest
from datetime import date

import pandas as pd

from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import FinancingParams
from financial_model.timeline.generator import generate_timeline
from financial_model.engines.investment import (
    InvestmentAllocation,
    InvestmentEngine,
)


# ── 黄金基准 (Excel v17 投资概算明细) ──────────────────────

# 静态投资分年度 (Row 21 time-series, 含里程碑拆分)
EXPECTED_STATIC_BY_YEAR = {
    2023: 66534.377 + 28514.733,  # 2023 + 2023-03
    2024: 90984.75,
    2025: 101033.82,
    2026: 107918.60,
    2027: 139530.50,
    2028: 126118.92,
    2029: 41960.732 + 62941.098,  # 2029 + 2029-08
    2030: 45070.89,
}

# 价差预备费分年度 (Row 22) — v17 模型中 price_escalation_rate = 0, 全部为0
# 但 Excel 给出了非零值, 说明价差预备费是手动计算的
EXPECTED_PRICE_CONTINGENCY_TOTAL = 59365.53

# 建设投资分年度 (Row 23)
EXPECTED_CONSTRUCTION_BY_YEAR = {
    2023: 66534.377 + 28514.733,
    2024: 92411.92,
    2025: 104888.03,
    2026: 114702.85,
    2027: 153329.43,
    2028: 140584.94,
    2029: 47348.30 + 71022.45,
    2030: 50636.92,
}

# 总量
EXPECTED_STATIC_TOTAL = 810608.42
EXPECTED_CONSTRUCTION_TOTAL = 869973.95

# 投资进度 (Row 24)
EXPECTED_PROGRESS = {
    2023: 0.0764785853645388 + 0.0327765365848023,
    2024: 0.106223778309684,
    2025: 0.120564564030911,
    2026: 0.131846304133589,
    2027: 0.176246001388892,
    2028: 0.161596723672013,
    2029: 0.0544249629543505 + 0.0816374444315258,
    2030: 0.0582050991296923,
}

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
def financing_params() -> FinancingParams:
    return FinancingParams()


@pytest.fixture
def allocation() -> InvestmentAllocation:
    return InvestmentAllocation.from_excel_v17()


@pytest.fixture
def engine(
    construction_params: ConstructionParams,
    investment_params: InvestmentParams,
    financing_params: FinancingParams,
    timeline,
    allocation: InvestmentAllocation,
) -> InvestmentEngine:
    return InvestmentEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        allocation=allocation,
    )


# ══════════════════════════════════════════════════════════
# InvestmentAllocation Tests
# ══════════════════════════════════════════════════════════


class TestInvestmentAllocation:
    """投资分配计划数据结构测试"""

    def test_v17_allocation_creation(self, allocation: InvestmentAllocation):
        """v17 分配数据正确创建"""
        assert not allocation.data.empty
        assert len(allocation.years) == 8  # 2023-2030

    def test_v17_allocation_items(self, allocation: InvestmentAllocation):
        """v17 分配包含 10 个科目 (含价差预备费)"""
        expected_items = {
            "施工辅助工程",
            "建筑工程",
            "环境保护和水土保持专项工程",
            "机电设备安装工程",
            "金属结构设备安装工程",
            "建设征地和移民安置补偿费用",
            "独立费用",
            "基本预备费",
            "储能投资",
            "价差预备费",
        }
        assert set(allocation.items) == expected_items

    def test_total_by_year(self, allocation: InvestmentAllocation):
        """年度合计应接近 Excel 静态投资年度值"""
        by_year = allocation.total_by_year()
        # 2023 年: 各科目之和 (含价差预备费=0)
        total_2023 = by_year.loc[2023]
        expected = (
            (10295.579 + 4412.391)
            + (41657.469 + 17853.201)
            + (10055.668 + 4309.572)
            + (1628.025 + 697.725)
            + (2547.636 + 1091.844)
            + (350 + 150)
            + 0.0  # 价差预备费首年为0
        )
        assert abs(total_2023 - expected) < 1.0

    def test_grand_total(self, allocation: InvestmentAllocation):
        """总投资额 ≈ 869,974 万元 (含价差预备费)"""
        total = allocation.grand_total()
        assert abs(total - EXPECTED_CONSTRUCTION_TOTAL) < 100

    def test_empty_raises(self):
        """空分配数据应报错"""
        with pytest.raises(ValueError, match="不能为空"):
            InvestmentAllocation(data=pd.DataFrame())


# ══════════════════════════════════════════════════════════
# InvestmentEngine Tests
# ══════════════════════════════════════════════════════════


class TestInvestmentEngineCalculation:
    """投资引擎计算结果验证"""

    def test_engine_name(self, engine: InvestmentEngine):
        assert engine.name == "investment"

    def test_calculate_returns_dataframe(self, engine: InvestmentEngine):
        """calculate() 返回 DataFrame"""
        result = engine.calculate()
        assert isinstance(result, pd.DataFrame)

    def test_result_columns(self, engine: InvestmentEngine):
        """结果包含必要的计算列"""
        result = engine.calculate()
        required = {
            "static_investment",
            "price_contingency",
            "construction_investment",
            "investment_progress",
        }
        assert required.issubset(set(result.columns))

    def test_result_years(self, engine: InvestmentEngine):
        """结果年度覆盖建设期"""
        result = engine.calculate()
        years = list(result.index)
        assert years[0] == 2023
        assert years[-1] == 2030
        assert len(years) == 8

    def test_static_investment_by_year(self, engine: InvestmentEngine):
        """各年度静态投资与 Excel 一致"""
        result = engine.calculate()
        static = result["static_investment"]

        for year, expected in EXPECTED_STATIC_BY_YEAR.items():
            actual = static.loc[year]
            assert abs(actual - expected) < 1.0, (
                f"{year}年静态投资: 期望{expected:.2f}, 实际{actual:.2f}"
            )

    def test_static_investment_total(self, engine: InvestmentEngine):
        """静态投资总额 ≈ 810,608.42 万元"""
        result = engine.calculate()
        total = result["static_investment"].sum()
        assert abs(total - EXPECTED_STATIC_TOTAL) < 50.0, (
            f"静态投资总额: 期望{EXPECTED_STATIC_TOTAL:.2f}, 实际{total:.2f}"
        )

    def test_price_contingency_from_allocation(self, engine: InvestmentEngine):
        """v17 价差预备费来自 allocation 数据"""
        result = engine.calculate()
        pc = result["price_contingency"]
        # 首年为 0
        assert pc.loc[2023] == 0.0
        # 后续年份有值
        assert pc.loc[2024] > 0.0
        # 总额 ≈ 59,365.53
        assert abs(pc.sum() - EXPECTED_PRICE_CONTINGENCY_TOTAL) < 10.0

    def test_construction_investment_by_year(self, engine: InvestmentEngine):
        """各年度建设投资 = 静态投资 + 价差预备费"""
        result = engine.calculate()
        ci = result["construction_investment"]

        for year, expected in EXPECTED_CONSTRUCTION_BY_YEAR.items():
            actual = ci.loc[year]
            assert abs(actual - expected) < 10.0, (
                f"{year}年建设投资: 期望{expected:.2f}, 实际{actual:.2f}"
            )

    def test_construction_investment_total(self, engine: InvestmentEngine):
        """建设投资总额 = 静态投资 + 价差预备费 ≈ 869,973.95"""
        result = engine.calculate()
        total = result["construction_investment"].sum()
        assert abs(total - EXPECTED_CONSTRUCTION_TOTAL) < 50.0, (
            f"建设投资总额: 期望{EXPECTED_CONSTRUCTION_TOTAL:.2f}, 实际{total:.2f}"
        )

    def test_investment_progress(self, engine: InvestmentEngine):
        """投资进度比例总和 = 1"""
        result = engine.calculate()
        progress_sum = result["investment_progress"].sum()
        assert abs(progress_sum - 1.0) < 0.001

    def test_investment_progress_by_year(self, engine: InvestmentEngine):
        """各年度投资进度比例与 Excel 一致"""
        result = engine.calculate()
        progress = result["investment_progress"]

        # 预期进度 = Excel 投资进度 (基于建设投资, 含价差预备费)
        # 总建设投资 = 869,973.95
        for year, expected in EXPECTED_PROGRESS.items():
            actual = progress.loc[year]
            assert abs(actual - expected) < 0.005, (
                f"{year}年投资进度: 期望{expected:.4f}, 实际{actual:.4f}"
            )


class TestInvestmentEnginePriceContingency:
    """价差预备费计算测试 (非零物价上涨率)"""

    def test_with_price_escalation(
        self,
        construction_params: ConstructionParams,
        financing_params: FinancingParams,
        timeline,
    ):
        """有物价上涨率时, 价差预备费 > 0 (使用不含价差预备费的分配)"""
        from financial_model.params.investment import PriceContingencyConfig

        invest_params = InvestmentParams(
            price_contingency=PriceContingencyConfig(price_escalation_rate=0.03),
        )
        # 使用不含价差预备费的分配
        alloc = InvestmentAllocation.from_excel_v17()
        alloc_no_pc = InvestmentAllocation(
            data=alloc.data.drop(columns=["价差预备费"])
        )
        engine = InvestmentEngine(
            params_construction=construction_params,
            params_investment=invest_params,
            params_financing=financing_params,
            timeline=timeline,
            allocation=alloc_no_pc,
        )

        result = engine.calculate()
        pc = result["price_contingency"]

        # 首年无价差预备费
        assert pc.loc[2023] == 0.0
        # 后续年份有价差预备费
        assert pc.loc[2024] > 0.0
        assert pc.loc[2030] > 0.0

        # 总价差预备费 > 0
        assert pc.sum() > 0.0

        # 建设投资 > 静态投资
        assert result["construction_investment"].sum() > result[
            "static_investment"
        ].sum()

    def test_price_escalation_formula(
        self,
        construction_params: ConstructionParams,
        financing_params: FinancingParams,
        timeline,
    ):
        """价差预备费公式: investment * ((1+r)^n - 1)"""
        from financial_model.params.investment import PriceContingencyConfig

        rate = 0.03
        invest_params = InvestmentParams(
            price_contingency=PriceContingencyConfig(price_escalation_rate=rate),
        )
        # 使用不含价差预备费的分配, 引擎自行计算
        alloc = InvestmentAllocation.from_excel_v17()
        alloc_no_pc = InvestmentAllocation(
            data=alloc.data.drop(columns=["价差预备费"])
        )
        engine = InvestmentEngine(
            params_construction=construction_params,
            params_investment=invest_params,
            params_financing=financing_params,
            timeline=timeline,
            allocation=alloc_no_pc,
        )

        result = engine.calculate()
        static = result["static_investment"]
        pc = result["price_contingency"]

        # 2024年: n=2, pc = static_2024 * ((1.03)^2 - 1)
        n_2024 = 2
        expected_pc_2024 = static.loc[2024] * ((1 + rate) ** n_2024 - 1)
        assert abs(pc.loc[2024] - expected_pc_2024) < 1.0


class TestInvestmentEngineValidation:
    """引擎输入验证"""

    def test_valid_no_warnings(self, engine: InvestmentEngine):
        """v17 数据不应有警告"""
        warnings = engine.validate_inputs()
        assert len(warnings) == 0

    def test_missing_year_warning(
        self,
        construction_params: ConstructionParams,
        investment_params: InvestmentParams,
        financing_params: FinancingParams,
        timeline,
    ):
        """缺少建设期年度应产生警告"""
        # 只有 2024-2029 的分配
        alloc = InvestmentAllocation.from_excel_v17()
        reduced = alloc.data.drop(index=[2023, 2030])
        bad_alloc = InvestmentAllocation(data=reduced)

        engine = InvestmentEngine(
            params_construction=construction_params,
            params_investment=investment_params,
            params_financing=financing_params,
            timeline=timeline,
            allocation=bad_alloc,
        )

        warnings = engine.validate_inputs()
        assert len(warnings) > 0
        assert any("2023" in w for w in warnings)
        assert any("2030" in w for w in warnings)


class TestInvestmentEngineSummary:
    """引擎摘要输出"""

    def test_summary(self, engine: InvestmentEngine):
        """summary() 返回正确值"""
        s = engine.summary()
        assert "static_investment" in s
        assert "price_contingency" in s
        assert "construction_investment" in s
        assert abs(s["static_investment"] - EXPECTED_STATIC_TOTAL) < 100.0
        # v17 价差预备费来自 allocation 数据
        assert abs(s["price_contingency"] - EXPECTED_PRICE_CONTINGENCY_TOTAL) < 10.0
        assert abs(s["construction_investment"] - EXPECTED_CONSTRUCTION_TOTAL) < 100.0

    def test_from_excel_v17_factory(
        self,
        construction_params: ConstructionParams,
        investment_params: InvestmentParams,
        financing_params: FinancingParams,
        timeline,
    ):
        """from_excel_v17 工厂方法正确创建引擎"""
        engine = InvestmentEngine.from_excel_v17(
            params_construction=construction_params,
            params_investment=investment_params,
            params_financing=financing_params,
            timeline=timeline,
        )
        result = engine.calculate()
        assert len(result) == 8
        assert "static_investment" in result.columns
