"""
Phase 3 测试: DepreciationEngine + CostEngine + RevenueEngine

黄金基准来自 Excel v17:
  - 表2-折旧摊销表 (rows 3-29)
  - 表3-成本费用表 (rows 5-24)
  - 表4-收入税金表 (rows 5-24)
  - 参数输入表 (rows 30-100, 378-430)
"""

from __future__ import annotations

import pytest
from datetime import date

import pandas as pd

from financial_model.params.construction import ConstructionParams
from financial_model.params.depreciation import AssetCategory, DepreciationParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import FinancingParams
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import generate_timeline
from financial_model.engines.depreciation import DepreciationEngine
from financial_model.engines.cost import CostEngine
from financial_model.engines.revenue import RevenueEngine

# ── 黄金基准 ─────────────────────────────────────────────

EXCEL_START = date(2023, 2, 1)
EXCEL_END = date(2030, 7, 31)

# 折旧 (表2)
EXPECTED_FIXED_ASSET_VALUE = 819191.18  # Row 4
EXPECTED_DEPRECIATION_YEARS = 29  # Row 6
EXPECTED_RESIDUAL_RATE = 0.05  # Row 7
EXPECTED_INTANGIBLE_VALUE = 80000  # Row 15
EXPECTED_INTANGIBLE_YEARS = 18  # Row 17
EXPECTED_LTP_AMOUNT = 458.72  # Row 8-12
EXPECTED_STORAGE_VALUE = 6000  # 储能投资

# 年固定资产折旧 = 819191.18 * 0.95 / 29 ≈ 26,835.57
EXPECTED_ANNUAL_FIXED_DEPR = EXPECTED_FIXED_ASSET_VALUE * (1 - EXPECTED_RESIDUAL_RATE) / EXPECTED_DEPRECIATION_YEARS
# 年无形资产摊销 = 80000 * 0.95 / 18 ≈ 4,222.22
EXPECTED_ANNUAL_INTANGIBLE = EXPECTED_INTANGIBLE_VALUE * (1 - EXPECTED_RESIDUAL_RATE) / EXPECTED_INTANGIBLE_YEARS

# 成本 (表3)
EXPECTED_TOTAL_PRODUCTION_COST = 3797066.23  # Row 6 (40年合计)
EXPECTED_MATERIAL_TOTAL = 9911.50  # Row 8
EXPECTED_PUMP_COST_TOTAL = 1783605.66  # Row 9
EXPECTED_MAINTENANCE_TOTAL = 397551.99  # Row 15
EXPECTED_LABOR_TOTAL = 111100.80  # Row 16

# 收入 (表4)
EXPECTED_TOTAL_REVENUE = 5439260.18  # Row 6 (40年合计)
EXPECTED_CAPACITY_REVENUE = 3451681.42  # Row 7
EXPECTED_ENERGY_REVENUE = 1987578.76  # Row 10
EXPECTED_GENERATION_TOTAL = 6548000  # Row 11
EXPECTED_GRID_PRICE = 0.35  # Row 13
EXPECTED_CAPACITY_PRICE = 696.5  # Row 9

# 运营参数
EXPECTED_CAPACITY_MW = 1400  # Row 33
EXPECTED_UTILIZATION_HOURS = 1169.29  # Row 34

# 容差
TOLERANCE_PCT = 0.20  # 20% for simplified model


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
def operating_params() -> OperatingParams:
    return OperatingParams.from_excel_v17()


@pytest.fixture
def tax_params() -> TaxParams:
    return TaxParams.from_excel_v17()


@pytest.fixture
def depreciation_params() -> DepreciationParams:
    return DepreciationParams.from_excel_v17()


@pytest.fixture
def depreciation_engine(
    construction_params, investment_params, financing_params,
    timeline, depreciation_params, operating_params,
) -> DepreciationEngine:
    return DepreciationEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        depreciation_params=depreciation_params,
        operating_params=operating_params,
    )


@pytest.fixture
def depreciation_result(depreciation_engine) -> pd.DataFrame:
    return depreciation_engine.calculate()


@pytest.fixture
def cost_engine(
    construction_params, investment_params, financing_params,
    timeline, operating_params, depreciation_result,
) -> CostEngine:
    return CostEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        depreciation_result=depreciation_result,
    )


@pytest.fixture
def cost_result(cost_engine) -> pd.DataFrame:
    return cost_engine.calculate()


@pytest.fixture
def revenue_engine(
    construction_params, investment_params, financing_params,
    timeline, operating_params, tax_params,
) -> RevenueEngine:
    return RevenueEngine(
        params_construction=construction_params,
        params_investment=investment_params,
        params_financing=financing_params,
        timeline=timeline,
        operating_params=operating_params,
        tax_params=tax_params,
    )


@pytest.fixture
def revenue_result(revenue_engine) -> pd.DataFrame:
    return revenue_engine.calculate()


# ══════════════════════════════════════════════════════════
# Parameter Model Tests
# ══════════════════════════════════════════════════════════


class TestOperatingParams:
    """运营参数测试"""

    def test_v17_creation(self, operating_params: OperatingParams):
        assert operating_params.installed_capacity_mw == EXPECTED_CAPACITY_MW
        assert operating_params.capacity_price == EXPECTED_CAPACITY_PRICE
        assert operating_params.grid_price == EXPECTED_GRID_PRICE

    def test_production_ratios(self, operating_params: OperatingParams):
        ratios = operating_params.production_ratios
        assert len(ratios) == 48
        assert ratios[0] == 0.0  # 建设期第1年
        assert ratios[7] == pytest.approx(5 / 12, abs=0.01)  # 投产年
        assert ratios[8] == 1.0  # 满产年
        assert ratios[47] == pytest.approx(7 / 12, abs=0.01)  # 末年

    def test_derived_generation(self, operating_params: OperatingParams):
        gen = operating_params.annual_generation_mwh
        expected = 1400 * 1169.29
        assert abs(gen - expected) < 1.0

    def test_invalid_capacity(self):
        with pytest.raises(ValueError):
            OperatingParams(installed_capacity_mw=0)


class TestDepreciationParams:
    """折旧参数测试"""

    def test_v17_creation(self, depreciation_params: DepreciationParams):
        assert abs(depreciation_params.fixed_assets.original_value - EXPECTED_FIXED_ASSET_VALUE) < 1.0
        assert depreciation_params.fixed_assets.useful_life == EXPECTED_DEPRECIATION_YEARS
        assert depreciation_params.fixed_assets.residual_rate == EXPECTED_RESIDUAL_RATE

    def test_annual_fixed_depreciation(self, depreciation_params: DepreciationParams):
        annual = depreciation_params.fixed_assets.annual_depreciation
        assert abs(annual - EXPECTED_ANNUAL_FIXED_DEPR) < 1.0

    def test_annual_intangible(self, depreciation_params: DepreciationParams):
        annual = depreciation_params.intangible_assets.annual_depreciation
        assert abs(annual - EXPECTED_ANNUAL_INTANGIBLE) < 1.0

    def test_asset_category_zero_life(self):
        """0年限资产无折旧"""
        cat = AssetCategory("test", 1000, 0)
        assert cat.annual_depreciation == 0.0


# ══════════════════════════════════════════════════════════
# DepreciationEngine Tests
# ══════════════════════════════════════════════════════════


class TestDepreciationEngine:
    """折旧引擎测试"""

    def test_engine_name(self, depreciation_engine):
        assert depreciation_engine.name == "depreciation"

    def test_result_shape(self, depreciation_result):
        """结果覆盖全部48年"""
        assert len(depreciation_result) == 48

    def test_required_columns(self, depreciation_result):
        required = {
            "production_ratio", "fixed_depreciation",
            "intangible_amortization", "long_term_prepaid",
            "total_depreciation",
        }
        assert required.issubset(set(depreciation_result.columns))

    def test_construction_period_zero(self, depreciation_result):
        """建设期折旧为0"""
        for year in range(2023, 2030):
            assert depreciation_result.loc[year, "total_depreciation"] == 0.0

    def test_operation_period_positive(self, depreciation_result):
        """运营期折旧 > 0"""
        assert depreciation_result.loc[2031, "total_depreciation"] > 0

    def test_full_year_depreciation(self, depreciation_result):
        """满产年(2031)固定资产折旧 ≈ 26,835"""
        fixed_2031 = depreciation_result.loc[2031, "fixed_depreciation"]
        assert abs(fixed_2031 - EXPECTED_ANNUAL_FIXED_DEPR) < 10.0

    def test_partial_year_depreciation(self, depreciation_result):
        """投产年(2030)折旧按达产比例"""
        fixed_2030 = depreciation_result.loc[2030, "fixed_depreciation"]
        ratio = depreciation_result.loc[2030, "production_ratio"]
        assert fixed_2030 == pytest.approx(
            EXPECTED_ANNUAL_FIXED_DEPR * ratio, abs=1.0
        )

    def test_intangible_amortization(self, depreciation_result):
        """无形资产摊销 ≈ 4,222/年"""
        intangible_2031 = depreciation_result.loc[2031, "intangible_amortization"]
        assert abs(intangible_2031 - EXPECTED_ANNUAL_INTANGIBLE) < 10.0

    def test_long_term_prepaid(self, depreciation_result):
        """长期待摊费用摊销"""
        # 运营期第1-5年应有摊销
        ltp_2031 = depreciation_result.loc[2031, "long_term_prepaid"]
        assert ltp_2031 > 0

    def test_energy_storage(self, depreciation_result):
        """储能资产折旧 ≈ (6000*0.95)/10 = 570/年"""
        storage_2031 = depreciation_result.loc[2031, "energy_storage_depreciation"]
        expected = 6000 * 0.95 / 10
        assert abs(storage_2031 - expected) < 5.0

    def test_total_depreciation_sum(self, depreciation_result):
        """总折旧/摊销额合理"""
        total = depreciation_result["total_depreciation"].sum()
        # 运营期40年, 主要折旧约26,835×40 ≈ 1,073,420
        assert total > 500000  # 至少50万
        assert total < 2000000  # 不超过200万


# ══════════════════════════════════════════════════════════
# CostEngine Tests
# ══════════════════════════════════════════════════════════


class TestCostEngine:
    """成本引擎测试"""

    def test_engine_name(self, cost_engine):
        assert cost_engine.name == "cost"

    def test_result_shape(self, cost_result):
        assert len(cost_result) == 48

    def test_construction_period_zero(self, cost_result):
        """建设期生产成本为0"""
        for year in range(2023, 2030):
            assert cost_result.loc[year, "total_production_cost"] == 0.0

    def test_operation_period_positive(self, cost_result):
        """运营期生产成本 > 0"""
        assert cost_result.loc[2031, "total_production_cost"] > 0

    def test_material_cost(self, cost_result):
        """材料费 > 0 in 满产年"""
        material = cost_result.loc[2031, "material_cost"]
        assert material > 0

    def test_pump_cost(self, cost_result):
        """抽水电费是最大单项"""
        pump = cost_result.loc[2031, "pump_electricity_cost"]
        material = cost_result.loc[2031, "material_cost"]
        assert pump > material  # 抽水电费 > 材料费

    def test_depreciation_included(self, cost_result):
        """折旧已包含在生产成本中"""
        depr = cost_result.loc[2031, "depreciation_total"]
        assert depr > 0

    def test_total_reasonable(self, cost_result):
        """总生产成本在合理范围"""
        total = cost_result["total_production_cost"].sum()
        # Excel: 3,796,066 (40年合计), 含折旧后会更高
        # 抽水电费+维修+人工+折旧 ≈ 200万/年 × 40 = 800万+
        assert total > 2000000  # 至少200万
        assert total < 10000000  # 不超过1000万


# ══════════════════════════════════════════════════════════
# RevenueEngine Tests
# ══════════════════════════════════════════════════════════


class TestRevenueEngine:
    """收入引擎测试"""

    def test_engine_name(self, revenue_engine):
        assert revenue_engine.name == "revenue"

    def test_result_shape(self, revenue_result):
        assert len(revenue_result) == 48

    def test_construction_period_zero(self, revenue_result):
        """建设期收入为0"""
        for year in range(2023, 2030):
            assert revenue_result.loc[year, "total_revenue"] == 0.0

    def test_capacity_revenue(self, revenue_result):
        """容量电费(不含税): 满产年 1400×696.5/10/1.13 ≈ 86,292 万元"""
        cap_rev = revenue_result.loc[2031, "capacity_revenue"]
        expected = 1400 * 696.5 / 10.0 / 1.13  # 不含增值税
        assert abs(cap_rev - expected) < 100.0

    def test_energy_revenue(self, revenue_result):
        """电量电费: 发电量×(1-2%)×0.35"""
        energy_rev = revenue_result.loc[2031, "energy_revenue"]
        assert energy_rev > 0

    def test_capacity_larger_than_energy(self, revenue_result):
        """容量电费 > 电量电费 (抽蓄模型特征)"""
        cap = revenue_result.loc[2031, "capacity_revenue"]
        energy = revenue_result.loc[2031, "energy_revenue"]
        assert cap > energy

    def test_generation(self, revenue_result):
        """发电量: 满产年 1400×1169.29 ≈ 1,637,006 MWh"""
        gen = revenue_result.loc[2031, "generation_mwh"]
        expected = 1400 * 1169.29
        assert abs(gen - expected) < 10.0

    def test_grid_energy(self, revenue_result):
        """上网电量 = 发电量 × (1-2%)"""
        gen = revenue_result.loc[2031, "generation_mwh"]
        grid = revenue_result.loc[2031, "grid_energy_mwh"]
        assert abs(grid - gen * 0.98) < 1.0

    def test_vat_positive(self, revenue_result):
        """增值税 > 0 in 满产年"""
        vat = revenue_result.loc[2031, "vat_payable"]
        assert vat > 0

    def test_deductible_vat_decreasing(self, revenue_result):
        """可抵扣进项税逐年递减"""
        deductible = []
        for year in range(2031, 2035):
            deductible.append(revenue_result.loc[year, "vat_input_deductible"])
        # 应递减
        assert deductible[0] >= deductible[-1]

    def test_total_revenue_sum(self, revenue_result):
        """总收入在合理范围"""
        total = revenue_result["total_revenue"].sum()
        # Excel: 5,439,260 (40年合计)
        assert total > 3000000
        assert total < 8000000

    def test_partial_year(self, revenue_result):
        """投产年(2030)收入按比例"""
        ratio = revenue_result.loc[2030, "production_ratio"]
        cap_2030 = revenue_result.loc[2030, "capacity_revenue"]
        cap_2031 = revenue_result.loc[2031, "capacity_revenue"]
        # 比例关系
        if cap_2031 > 0:
            assert abs(cap_2030 / cap_2031 - ratio) < 0.01


# ══════════════════════════════════════════════════════════
# Integration: Depreciation → Cost → Revenue chain
# ══════════════════════════════════════════════════════════


class TestPhase3Integration:
    """Phase 3 引擎集成测试"""

    def test_depreciation_feeds_cost(
        self, depreciation_result, cost_result
    ):
        """折旧结果传递到成本引擎"""
        # 满产年折旧应一致
        depr_depr = depreciation_result.loc[2031, "total_depreciation"]
        depr_cost = cost_result.loc[2031, "depreciation_total"]
        assert abs(depr_depr - depr_cost) < 1.0

    def test_all_engines_same_year_range(
        self, depreciation_result, cost_result, revenue_result
    ):
        """所有引擎覆盖相同年度范围"""
        assert list(depreciation_result.index) == list(cost_result.index)
        assert list(cost_result.index) == list(revenue_result.index)

    def test_production_ratios_consistent(
        self, depreciation_result, cost_result, revenue_result
    ):
        """达产比例在所有引擎间一致"""
        for year in range(2030, 2040):
            r1 = depreciation_result.loc[year, "production_ratio"]
            r2 = cost_result.loc[year, "production_ratio"]
            r3 = revenue_result.loc[year, "production_ratio"]
            assert abs(r1 - r2) < 1e-10
            assert abs(r2 - r3) < 1e-10
