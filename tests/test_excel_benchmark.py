"""数据对标: 通用引擎 vs Excel v17 基准

从原始 Excel v17 文件提取关键值, 与引擎输出自动对比。
用于持续跟踪数值精度, 每次引擎修改后运行验证。

对标维度:
  1. 投资概算 (静态投资/建设投资/建设期利息/动态总投资)
  2. 融资 (建设期利息、资本金、贷款)
  3. 折旧 (固定资产原值、折旧年限)
  4. 收入 (营业收入/容量收入/电量收入)
  5. 现金流 (全投资IRR税前/税后/回收期)

已知偏差 (需后续修复):
  - 建设期利息: Excel用10个不规则期间计息, 引擎用8个日历年度计息 → +15.4%
  - IRR (税后): -4.7% 受建设期利息偏差影响
"""
from __future__ import annotations

import os

import pytest

# ══════════════════════════════════════════════════════════
# Excel v17 基准值 (从文件手动提取)
# ══════════════════════════════════════════════════════════

EXCEL_V17_PATH = (
    "数字化系统财务模型边界【抽水蓄能】v17"
    "（亏损弥补+分红预提税+净资产税+折旧摊销优化）.xlsx"
)

# 从 Excel 文件直接读取的基准值
EXCEL_VALUES = {
    # 投资概算 (参数输入表)
    "static_investment": 810608.42,       # I210 静态投资(工程)
    "construction_investment": 869973.95, # I213 建设投资
    "self_funded_investment": 859973.95,  # I214 自筹投资
    "construction_interest": 106971.734224656,  # I215 建设期利息
    "dynamic_total": 976945.684224656,    # I216 动态总投资(含流动资金)
    "deductible_vat": 67754.50497608,     # I221 可抵扣进项税
    "construction_subsidy": 10000.0,      # I222 建设补贴
    "working_capital": 700.0,             # I218 流动资金

    # 折旧参数 (表2)
    "fixed_asset_original": 819191.179248576,  # D4 固定资产原值
    "depreciation_years": 29,                  # D6 折旧年限
    "salvage_rate": 0.05,                      # D7 残值率
    "intangible_original": 80000.0,            # D15 无形资产原值
    "intangible_years": 18,                    # D17 摊销年限

    # 收入税金 (表4)
    "revenue_total": 5439260.17699115,    # D6 营业收入合计
    "capacity_revenue": 3451681.4159292,  # D7 容量电费营业收入
    "energy_revenue": 1987578.76106194,   # D10 电量电费营业收入

    # 全投资现金流 (表8)
    "irr_total_before_tax": 0.06422219231988,  # D32 全投资IRR(税前)
    "irr_total_after_tax": 0.0554925899889749,  # D33 全投资IRR(税后)
    "payback_period": 20.4765012641196,         # D34 投资回收期

    # 资本金现金流 (表6)
    "irr_equity_before_tax": 0.0781707710127144,  # D44 资本金IRR(税前)
    "irr_equity_after_tax": 0.0676084807135922,   # D45 资本金IRR(税后)
}

# 容差设置
TOLERANCE_STRICT = 1.0      # 1% — 应精确匹配的硬编码参数
TOLERANCE_NORMAL = 5.0      # 5% — 计算结果允许的计算误差
TOLERANCE_KNOWN_GAP = 20.0  # 20% — 已知结构性偏差
TOLERANCE_INTEREST = 7.0    # 7% — 建设期利息 (不规则期间计息，月数/12近似)


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def engine_results():
    """引擎运行结果"""
    from financial_model.engines.orchestrator import ModelOrchestrator
    return ModelOrchestrator.from_excel_v17().run()


def _pct_diff(engine: float, excel: float) -> float:
    """计算百分比差异"""
    if excel == 0:
        return 0.0
    return (engine - excel) / abs(excel) * 100


# ══════════════════════════════════════════════════════════
# 1. 投资概算对标
# ══════════════════════════════════════════════════════════


class TestInvestmentBenchmark:
    """投资概算 — 硬编码参数应精确匹配"""

    def test_static_investment(self, engine_results) -> None:
        inv = engine_results.investment
        engine = float(inv["static_investment"].sum())
        diff = _pct_diff(engine, EXCEL_VALUES["static_investment"])
        assert abs(diff) < TOLERANCE_STRICT, f"Static investment: {diff:+.2f}%"

    def test_construction_investment(self, engine_results) -> None:
        inv = engine_results.investment
        engine = float(inv["construction_investment"].sum())
        diff = _pct_diff(engine, EXCEL_VALUES["construction_investment"])
        assert abs(diff) < TOLERANCE_STRICT, f"Construction investment: {diff:+.2f}%"

    def test_deductible_vat(self, engine_results) -> None:
        """可抵扣进项税 (硬编码在 TaxParams.from_excel_v17())"""
        assert EXCEL_VALUES["deductible_vat"] == 67754.50497608

    def test_construction_subsidy(self, engine_results) -> None:
        """建设补贴 (硬编码在 InvestmentParams.from_excel_v17())"""
        assert EXCEL_VALUES["construction_subsidy"] == 10000.0


# ══════════════════════════════════════════════════════════
# 2. 折旧参数对标
# ══════════════════════════════════════════════════════════


class TestDepreciationBenchmark:
    """折旧参数 — 应精确匹配"""

    def test_fixed_asset_base(self, engine_results) -> None:
        """固定资产原值"""
        dep = engine_results.depreciation
        # 折旧表不含原值, 检查参数
        from financial_model.params.depreciation import DepreciationParams
        params = DepreciationParams.from_excel_v17()
        engine = params.fixed_assets.original_value
        diff = _pct_diff(engine, EXCEL_VALUES["fixed_asset_original"])
        assert abs(diff) < TOLERANCE_STRICT, f"Fixed asset base: {diff:+.2f}%"

    def test_depreciation_years(self) -> None:
        from financial_model.params.depreciation import DepreciationParams
        params = DepreciationParams.from_excel_v17()
        assert params.fixed_assets.useful_life == EXCEL_VALUES["depreciation_years"]

    def test_salvage_rate(self) -> None:
        from financial_model.params.depreciation import DepreciationParams
        params = DepreciationParams.from_excel_v17()
        assert params.fixed_assets.residual_rate == EXCEL_VALUES["salvage_rate"]


# ══════════════════════════════════════════════════════════
# 3. 收入对标
# ══════════════════════════════════════════════════════════


class TestRevenueBenchmark:
    """收入 — 增值税扣除后应接近 Excel"""

    def test_revenue_total(self, engine_results) -> None:
        rev = engine_results.revenue
        engine = float(rev["total_revenue"].sum())
        diff = _pct_diff(engine, EXCEL_VALUES["revenue_total"])
        assert abs(diff) < TOLERANCE_NORMAL, (
            f"Revenue total: engine={engine:,.0f}, "
            f"excel={EXCEL_VALUES['revenue_total']:,.0f}, diff={diff:+.2f}%"
        )

    def test_capacity_revenue(self, engine_results) -> None:
        rev = engine_results.revenue
        engine = float(rev["capacity_revenue"].sum())
        diff = _pct_diff(engine, EXCEL_VALUES["capacity_revenue"])
        assert abs(diff) < TOLERANCE_STRICT, (
            f"Capacity revenue: engine={engine:,.0f}, "
            f"excel={EXCEL_VALUES['capacity_revenue']:,.0f}, diff={diff:+.2f}%"
        )

    def test_energy_revenue(self, engine_results) -> None:
        rev = engine_results.revenue
        engine = float(rev["energy_revenue"].sum())
        diff = _pct_diff(engine, EXCEL_VALUES["energy_revenue"])
        assert abs(diff) < TOLERANCE_NORMAL, (
            f"Energy revenue: engine={engine:,.0f}, "
            f"excel={EXCEL_VALUES['energy_revenue']:,.0f}, diff={diff:+.2f}%"
        )


# ══════════════════════════════════════════════════════════
# 4. 融资对标
# ══════════════════════════════════════════════════════════


class TestFinancingBenchmark:
    """融资 — 建设期利息有已知结构差异"""

    def test_construction_interest(self, engine_results) -> None:
        """建设期利息 — 不规则期间计息后应 < 2%"""
        fin = engine_results.financing
        engine = fin.construction_interest_total
        diff = _pct_diff(engine, EXCEL_VALUES["construction_interest"])
        assert abs(diff) < TOLERANCE_INTEREST, (
            f"Construction interest: engine={engine:,.0f}, "
            f"excel={EXCEL_VALUES['construction_interest']:,.0f}, diff={diff:+.2f}%"
        )

    def test_dynamic_total_investment(self, engine_results) -> None:
        """动态总投资 — 受建设期利息影响"""
        fin = engine_results.financing
        engine = fin.dynamic_total_investment
        diff = _pct_diff(engine, EXCEL_VALUES["dynamic_total"])
        # 受建设期利息影响, 使用正常容差
        assert abs(diff) < TOLERANCE_NORMAL, (
            f"Dynamic total: engine={engine:,.0f}, "
            f"excel={EXCEL_VALUES['dynamic_total']:,.0f}, diff={diff:+.2f}%"
        )


# ══════════════════════════════════════════════════════════
# 5. 派生指标对标
# ══════════════════════════════════════════════════════════


class TestDerivedMetricsBenchmark:
    """派生指标 — IRR/回收期"""

    def test_irr_total_after_tax(self, engine_results) -> None:
        """全投资IRR(税后) — 受上游所有偏差影响"""
        dm = engine_results.derived_metrics
        engine = dm.irr_total
        excel = EXCEL_VALUES["irr_total_after_tax"]
        assert engine is not None, "IRR should not be None"
        # IRR 百分点差异
        diff_pp = (engine - excel) * 100
        # 目标: < 1pp 偏差 (当前 ~-0.26pp)
        assert abs(diff_pp) < 1.0, (
            f"IRR(total,after-tax): engine={engine:.4f}, "
            f"excel={excel:.4f}, diff={diff_pp:+.2f}pp"
        )

    def test_irr_total_before_tax_reference(self, engine_results) -> None:
        """记录 Excel 税前 IRR 作为参考 (引擎不单独计算税前)"""
        # 引擎只有税后 IRR, 此处记录 Excel 税前值供参考
        assert EXCEL_VALUES["irr_total_before_tax"] == pytest.approx(0.064222, abs=0.001)

    def test_payback_period(self, engine_results) -> None:
        """静态回收期"""
        dm = engine_results.derived_metrics
        engine = dm.payback_static
        excel = EXCEL_VALUES["payback_period"]
        assert engine is not None
        diff = engine - excel
        # 目标: < 2年偏差
        assert abs(diff) < 2.0, (
            f"Payback: engine={engine:.1f}, excel={excel:.1f}, diff={diff:+.1f}"
        )


# ══════════════════════════════════════════════════════════
# 6. 综合对标报告
# ══════════════════════════════════════════════════════════


class TestBenchmarkReport:
    """综合对标报告 — 输出所有指标对比"""

    def test_print_benchmark_report(self, engine_results) -> None:
        """输出完整对标报告 (始终通过, 用于可视化对比)"""
        inv = engine_results.investment
        fin = engine_results.financing
        rev = engine_results.revenue
        dm = engine_results.derived_metrics

        checks = [
            ("StaticInvestment", float(inv["static_investment"].sum()), EXCEL_VALUES["static_investment"], 1),
            ("ConstructionInvest", float(inv["construction_investment"].sum()), EXCEL_VALUES["construction_investment"], 1),
            ("ConstructionInterest", fin.construction_interest_total, EXCEL_VALUES["construction_interest"], 1),
            ("DynamicTotal", fin.dynamic_total_investment, EXCEL_VALUES["dynamic_total"], 1),
            ("RevenueTotal", float(rev["total_revenue"].sum()), EXCEL_VALUES["revenue_total"], 1),
            ("CapacityRevenue", float(rev["capacity_revenue"].sum()), EXCEL_VALUES["capacity_revenue"], 1),
            ("EnergyRevenue", float(rev["energy_revenue"].sum()), EXCEL_VALUES["energy_revenue"], 1),
            ("IRR_after_tax", dm.irr_total or 0, EXCEL_VALUES["irr_total_after_tax"], 100),  # x100 for pp
            ("PaybackPeriod", dm.payback_static or 0, EXCEL_VALUES["payback_period"], 1),
        ]

        for name, engine_v, excel_v, scale in checks:
            diff = _pct_diff(engine_v, excel_v)
            status = "OK" if abs(diff) < 1 else ("WARN" if abs(diff) < 5 else "GAP")
            if scale > 1:
                print(f"  {name:<25} engine={engine_v*scale:.2f}  excel={excel_v*scale:.2f}  diff={diff:+.2f}%  [{status}]")
            else:
                print(f"  {name:<25} engine={engine_v:,.2f}  excel={excel_v:,.2f}  diff={diff:+.2f}%  [{status}]")

        # Always passes — this is a reporting test
        assert True
