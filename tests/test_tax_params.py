"""
Phase 4A 测试: TaxParams 参数模型

验证:
  - 参数创建和默认值
  - 边界验证 (税率范围、年限)
  - Excel v17 黄金基准
  - 派生属性 (deductible_vat_per_year)
  - 与 RevenueEngine 集成
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
from financial_model.engines.revenue import RevenueEngine


# ══════════════════════════════════════════════════════════
# TaxParams 基础测试
# ══════════════════════════════════════════════════════════


class TestTaxParamsCreation:
    """TaxParams 创建和默认值"""

    def test_defaults(self):
        """默认税率符合中国税法标准"""
        tp = TaxParams()
        assert tp.vat_rate == pytest.approx(0.13)
        assert tp.income_tax_rate == pytest.approx(0.25)
        assert tp.surcharge_rate == pytest.approx(0.10)
        assert tp.loss_carryforward_years == 5
        assert tp.deductible_input_vat == 0.0
        assert tp.deductible_vat_amort_years == 10

    def test_custom_values(self):
        """自定义税率"""
        tp = TaxParams(
            vat_rate=0.09,
            income_tax_rate=0.15,
            surcharge_rate=0.05,
            loss_carryforward_years=3,
            deductible_input_vat=50000.0,
            deductible_vat_amort_years=5,
        )
        assert tp.vat_rate == 0.09
        assert tp.income_tax_rate == 0.15
        assert tp.surcharge_rate == 0.05
        assert tp.loss_carryforward_years == 3
        assert tp.deductible_input_vat == 50000.0
        assert tp.deductible_vat_amort_years == 5

    def test_zero_tax_rates(self):
        """免税场景"""
        tp = TaxParams(vat_rate=0.0, income_tax_rate=0.0, surcharge_rate=0.0)
        assert tp.vat_rate == 0.0
        assert tp.income_tax_rate == 0.0
        assert tp.surcharge_rate == 0.0

    def test_frozen(self):
        """TaxParams 不可变"""
        tp = TaxParams()
        with pytest.raises(AttributeError):
            tp.vat_rate = 0.20  # type: ignore


class TestTaxParamsValidation:
    """TaxParams 边界验证"""

    def test_vat_rate_negative(self):
        with pytest.raises(ValueError, match="增值税率"):
            TaxParams(vat_rate=-0.01)

    def test_vat_rate_over_one(self):
        with pytest.raises(ValueError, match="增值税率"):
            TaxParams(vat_rate=1.5)

    def test_income_tax_rate_negative(self):
        with pytest.raises(ValueError, match="所得税率"):
            TaxParams(income_tax_rate=-0.01)

    def test_income_tax_rate_over_one(self):
        with pytest.raises(ValueError, match="所得税率"):
            TaxParams(income_tax_rate=2.0)

    def test_surcharge_rate_negative(self):
        with pytest.raises(ValueError, match="附加税费率"):
            TaxParams(surcharge_rate=-0.01)

    def test_loss_carryforward_negative(self):
        with pytest.raises(ValueError, match="亏损弥补年限"):
            TaxParams(loss_carryforward_years=-1)

    def test_deductible_amort_years_zero(self):
        with pytest.raises(ValueError, match="进项税抵扣年限"):
            TaxParams(deductible_vat_amort_years=0)


class TestTaxParamsExcelV17:
    """Excel v17 黄金基准"""

    def test_from_excel_v17(self):
        tp = TaxParams.from_excel_v17()
        assert tp.vat_rate == pytest.approx(0.13)
        assert tp.income_tax_rate == pytest.approx(0.25)
        assert tp.surcharge_rate == pytest.approx(0.10)
        assert tp.loss_carryforward_years == 5
        assert tp.deductible_input_vat == pytest.approx(67754.50)
        assert tp.deductible_vat_amort_years == 10

    def test_from_excel_v17_custom_deductible(self):
        """自定义可抵扣进项税"""
        tp = TaxParams.from_excel_v17(deductible_input_vat=50000.0)
        assert tp.deductible_input_vat == 50000.0


class TestTaxParamsDerived:
    """TaxParams 派生属性"""

    def test_deductible_vat_per_year(self):
        """每年可抵扣进项税 = 总额 / 年限"""
        tp = TaxParams(
            deductible_input_vat=67754.50,
            deductible_vat_amort_years=10,
        )
        expected = 67754.50 / 10
        assert tp.deductible_vat_per_year == pytest.approx(expected, abs=0.01)

    def test_deductible_vat_per_year_zero_input(self):
        """无进项税时"""
        tp = TaxParams(deductible_input_vat=0.0)
        assert tp.deductible_vat_per_year == 0.0


# ══════════════════════════════════════════════════════════
# TaxParams + RevenueEngine 集成
# ══════════════════════════════════════════════════════════


class TestTaxParamsRevenueIntegration:
    """TaxParams 与 RevenueEngine 集成"""

    @pytest.fixture
    def full_revenue_result(self) -> pd.DataFrame:
        """使用 TaxParams.from_excel_v17 构建完整 RevenueEngine"""
        construction = ConstructionParams(
            construction_start=date(2023, 2, 1),
            construction_end=date(2030, 7, 31),
            operation_years=40,
        )
        timeline = generate_timeline(construction)
        investment = InvestmentParams.from_excel_v17()
        financing = FinancingParams()
        operating = OperatingParams.from_excel_v17()
        tax = TaxParams.from_excel_v17()

        engine = RevenueEngine(
            params_construction=construction,
            params_investment=investment,
            params_financing=financing,
            timeline=timeline,
            operating_params=operating,
            tax_params=tax,
        )
        return engine.calculate()

    def test_vat_matches_tax_rate(self, full_revenue_result):
        """增值税销项 = 收入 × 13%"""
        rev_2031 = full_revenue_result.loc[2031, "total_revenue"]
        vat_2031 = full_revenue_result.loc[2031, "vat_output"]
        assert vat_2031 == pytest.approx(rev_2031 * 0.13, abs=1.0)

    def test_surcharge_on_vat(self, full_revenue_result):
        """附加税 = 应缴增值税 × 10%"""
        vat_payable = full_revenue_result.loc[2031, "vat_payable"]
        surcharge = full_revenue_result.loc[2031, "surcharge"]
        assert surcharge == pytest.approx(vat_payable * 0.10, abs=1.0)

    def test_deductible_vat_from_tax_params(self, full_revenue_result):
        """可抵扣进项税来自 TaxParams"""
        expected_per_year = 67754.50 / 10  # 6,775.45
        deductible_2031 = full_revenue_result.loc[2031, "vat_input_deductible"]
        assert deductible_2031 == pytest.approx(expected_per_year, abs=1.0)

    def test_no_deductible_after_exhausted(self, full_revenue_result):
        """进项税抵扣完后不再抵扣"""
        # 10年后应该抵扣完毕
        deductible_2041 = full_revenue_result.loc[2041, "vat_input_deductible"]
        assert deductible_2041 == 0.0

    def test_zero_tax_no_vat(self):
        """零税率场景: 无增值税"""
        construction = ConstructionParams(
            construction_start=date(2023, 2, 1),
            construction_end=date(2030, 7, 31),
            operation_years=40,
        )
        timeline = generate_timeline(construction)
        engine = RevenueEngine(
            params_construction=construction,
            params_investment=InvestmentParams(),
            params_financing=FinancingParams(),
            timeline=timeline,
            operating_params=OperatingParams.from_excel_v17(),
            tax_params=TaxParams(vat_rate=0.0, surcharge_rate=0.0),
        )
        result = engine.calculate()
        assert result.loc[2031, "vat_output"] == 0.0
        assert result.loc[2031, "surcharge"] == 0.0


# ══════════════════════════════════════════════════════════
# OperatingParams 确认无税字段
# ══════════════════════════════════════════════════════════


class TestOperatingParamsNoTax:
    """确认 OperatingParams 不再包含税字段"""

    def test_no_vat_rate(self):
        op = OperatingParams()
        assert not hasattr(op, "vat_rate")

    def test_no_income_tax_rate(self):
        op = OperatingParams()
        assert not hasattr(op, "income_tax_rate")

    def test_no_surcharge_rate(self):
        op = OperatingParams()
        assert not hasattr(op, "surcharge_rate")

    def test_from_excel_v17_no_tax(self):
        """from_excel_v17 不再设置税字段"""
        op = OperatingParams.from_excel_v17()
        assert not hasattr(op, "vat_rate")
