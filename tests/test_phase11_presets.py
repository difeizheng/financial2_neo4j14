"""Phase 11 测试: 预设模板 + 边界条件

验证:
  - 3套预设模板可一键运行并输出完整结果
  - 边界条件 (建设期3年/15年, 运营期20年/50年) 下无异常
  - YAML 加载器健壮性
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_model.analysis.types import ModelConfig
from financial_model.engines.orchestrator import AllResults, ModelOrchestrator
from financial_model.params import (
    ConstructionParams,
    DepreciationParams,
    FinancingParams,
    InvestmentParams,
    OperatingParams,
    TaxParams,
)
from financial_model.params.depreciation import AssetCategory
from financial_model.params.presets import list_presets, load_preset, load_preset_metadata


# ══════════════════════════════════════════════════════════
# 11.1 YAML 加载器测试
# ══════════════════════════════════════════════════════════


class TestPresetLoader:
    """YAML 预设加载器测试"""

    def test_list_presets_returns_three(self) -> None:
        names = list_presets()
        assert len(names) == 3
        assert "pshp_1400mw_8yr" in names
        assert "pshp_1800mw_10yr" in names
        assert "pshp_600mw_5yr" in names

    def test_load_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="不存在"):
            load_preset("nonexistent_project")

    def test_load_preset_metadata(self) -> None:
        meta = load_preset_metadata("pshp_1400mw_8yr")
        assert "1400" in meta["name"]
        assert meta["version"] == "1.0"

    def test_load_preset_metadata_nonexistent(self) -> None:
        meta = load_preset_metadata("nonexistent")
        assert meta["version"] == "?"


# ══════════════════════════════════════════════════════════
# 11.2 预设模板运行测试
# ══════════════════════════════════════════════════════════


class TestPresetRuns:
    """每套预设模板必须可一键运行并输出完整结果"""

    @pytest.fixture(params=list_presets())
    def preset_results(self, request: pytest.FixtureRequest) -> AllResults:
        """参数化: 为每套预设加载配置并运行"""
        config = load_preset(request.param)
        return config.to_orchestrator().run()

    def test_results_not_none(self, preset_results: AllResults) -> None:
        assert preset_results is not None

    def test_has_investment(self, preset_results: AllResults) -> None:
        assert not preset_results.investment.empty
        total = float(preset_results.investment["construction_investment"].sum())
        assert total > 0

    def test_has_depreciation(self, preset_results: AllResults) -> None:
        assert not preset_results.depreciation.empty

    def test_has_cost(self, preset_results: AllResults) -> None:
        assert not preset_results.cost.empty

    def test_has_revenue(self, preset_results: AllResults) -> None:
        assert not preset_results.revenue.empty

    def test_has_pnl(self, preset_results: AllResults) -> None:
        assert not preset_results.pnl_total.data.empty
        assert not preset_results.pnl_equity.data.empty

    def test_has_cashflow(self, preset_results: AllResults) -> None:
        assert not preset_results.cf_total.data.empty
        assert not preset_results.cf_equity.data.empty
        assert not preset_results.cf_plan.data.empty

    def test_has_balance_sheet(self, preset_results: AllResults) -> None:
        assert not preset_results.balance_sheet.data.empty

    def test_has_derived_metrics(self, preset_results: AllResults) -> None:
        dm = preset_results.derived_metrics
        assert dm.project_years > 0
        assert dm.discount_rate > 0


class TestPresetSpecificResults:
    """特定预设的结果验证"""

    def test_1400mw_matches_golden(self) -> None:
        """1400MW 基准预设必须与 v17 黄金基准一致"""
        config = load_preset("pshp_1400mw_8yr")
        results = config.to_orchestrator().run()
        dm = results.derived_metrics

        # IRR 应与 from_excel_v17() 基准一致 (5.39%)
        assert dm.irr_total is not None
        assert abs(dm.irr_total - 0.0539) < 0.001

        # NPV 为负 (项目财务可行性边界)
        assert dm.npv_total < 0

        # DSCR > 1 (可偿债)
        assert dm.dscr_min is not None
        assert dm.dscr_min > 1.0

    def test_1800mw_higher_irr(self) -> None:
        """1800MW 大型项目 IRR 应高于 1400MW (规模经济)"""
        r_1400 = load_preset("pshp_1400mw_8yr").to_orchestrator().run()
        r_1800 = load_preset("pshp_1800mw_10yr").to_orchestrator().run()

        assert r_1400.derived_metrics.irr_total is not None
        assert r_1800.derived_metrics.irr_total is not None
        assert r_1800.derived_metrics.irr_total > r_1400.derived_metrics.irr_total

    def test_600mw_small_project(self) -> None:
        """600MW 小型项目应能完整运行"""
        config = load_preset("pshp_600mw_5yr")
        results = config.to_orchestrator().run()

        # 应能完成全部计算
        assert results.derived_metrics.project_years > 0
        assert not results.investment.empty

        # 30年运营期
        assert config.construction.operation_years == 30


# ══════════════════════════════════════════════════════════
# 11.3 边界条件测试
# ══════════════════════════════════════════════════════════


def _run_boundary(
    start: date,
    end: date,
    op_years: int,
    capacity: float = 1400.0,
) -> AllResults:
    """构建边界配置并运行"""
    config = ModelConfig(
        construction=ConstructionParams(
            construction_start=start,
            construction_end=end,
            operation_years=op_years,
        ),
        investment=InvestmentParams.from_excel_v17(),
        financing=FinancingParams.from_excel_v17(),
        operating=OperatingParams(
            installed_capacity_mw=capacity,
            annual_utilization_hours=1169.29,
            capacity_price=696.5,
            grid_price=0.35,
            pump_price=0.23085,
            auxiliary_power_rate=0.02,
            production_ratios=OperatingParams.from_excel_v17().production_ratios,
        ),
        tax=TaxParams.from_excel_v17(),
        depreciation=DepreciationParams.from_excel_v17(),
        discount_rate=0.08,
    )
    return config.to_orchestrator().run()


class TestBoundaryConditions:
    """极端建设期/运营期条件测试"""

    def test_3yr_construction_40yr_operation(self) -> None:
        """最短建设期: 3年"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2027, 12, 31),
            op_years=40,
        )
        assert results.derived_metrics.project_years > 0
        assert not results.investment.empty

    def test_15yr_construction_40yr_operation(self) -> None:
        """最长建设期: 15年"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2039, 12, 31),
            op_years=40,
        )
        assert results.derived_metrics.project_years > 0

    def test_5yr_construction_20yr_operation(self) -> None:
        """最短运营期: 20年"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2029, 12, 31),
            op_years=20,
        )
        assert results.derived_metrics.project_years == 25  # 5 + 20

    def test_5yr_construction_50yr_operation(self) -> None:
        """最长运营期: 50年"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2029, 12, 31),
            op_years=50,
        )
        assert results.derived_metrics.project_years == 55  # 5 + 50

    def test_8yr_construction_5yr_operation(self) -> None:
        """最短允许运营期: 5年"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2032, 12, 31),
            op_years=5,
        )
        assert results.derived_metrics.project_years == 13  # 8 + 5

    def test_leap_year_boundary(self) -> None:
        """闰年起始日期"""
        results = _run_boundary(
            start=date(2024, 2, 29),
            end=date(2031, 12, 31),
            op_years=30,
        )
        assert results.derived_metrics.project_years > 0

    def test_mid_year_start(self) -> None:
        """年中开工 (非年初)"""
        results = _run_boundary(
            start=date(2025, 7, 15),
            end=date(2032, 6, 30),
            op_years=40,
        )
        assert results.derived_metrics.project_years > 0

    def test_small_capacity_100mw(self) -> None:
        """最小容量: 100MW"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2029, 12, 31),
            op_years=30,
            capacity=100.0,
        )
        assert results.derived_metrics.project_years > 0

    def test_large_capacity_3000mw(self) -> None:
        """大容量: 3000MW"""
        results = _run_boundary(
            start=date(2025, 1, 1),
            end=date(2034, 12, 31),
            op_years=40,
            capacity=3000.0,
        )
        assert results.derived_metrics.project_years > 0

    def test_zero_price_escalation(self) -> None:
        """零价差预备费"""
        from dataclasses import replace
        from financial_model.params.investment import PriceContingencyConfig

        base = ModelConfig.from_excel_v17()
        config = replace(
            base,
            investment=replace(
                base.investment,
                price_contingency=PriceContingencyConfig(price_escalation_rate=0.0),
            ),
        )
        results = config.to_orchestrator().run()
        assert results.derived_metrics.irr_total is not None

    def test_high_interest_rate(self) -> None:
        """高利率场景: 8% — 全投资 IRR 不受利率影响，
        但 DSCR 和资本金 IRR 应受影响"""
        from dataclasses import replace

        base = ModelConfig.from_excel_v17()
        config = replace(
            base,
            financing=replace(
                base.financing,
                construction_interest_rate=0.08,
                long_term_loan=replace(
                    base.financing.long_term_loan,
                    annual_rate=0.08,
                ),
            ),
        )
        results = config.to_orchestrator().run()
        # 全投资 IRR 存在
        assert results.derived_metrics.irr_total is not None
        # 高利率下 DSCR 应低于基准 (基准 1.07)
        assert results.derived_metrics.dscr_min is not None
        assert results.derived_metrics.dscr_min < 1.07

    def test_high_equity_ratio(self) -> None:
        """高资本金比例: 50%"""
        from dataclasses import replace

        base = ModelConfig.from_excel_v17()
        config = replace(
            base,
            financing=replace(base.financing, equity_ratio=0.50),
        )
        results = config.to_orchestrator().run()
        assert results.derived_metrics.project_years > 0
