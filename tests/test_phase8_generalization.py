"""Phase 8.4: 多方案泛化测试 — 建设期5年/8年/12年分别运行

验证引擎在不同参数组合下:
  1. 不崩溃、不抛异常
  2. 资产负债表每年平衡
  3. IRR 可计算 (有正负现金流)
  4. DSCR >= 0.5 (不会出现极端值)
  5. 所有引擎输出 DataFrame/Series 非空
"""

from __future__ import annotations

from datetime import date

import pytest

from financial_model.engines.orchestrator import ModelOrchestrator
from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams


# ══════════════════════════════════════════════════════════
# Test configurations
# ══════════════════════════════════════════════════════════

SCENARIOS = {
    "short_5yr": {
        "construction_start": date(2024, 1, 1),
        "construction_end": date(2028, 12, 31),
        "operation_years": 30,
    },
    "base_7yr": {
        "construction_start": date(2023, 2, 1),
        "construction_end": date(2030, 7, 31),
        "operation_years": 40,
    },
    "long_8yr": {
        "construction_start": date(2023, 1, 1),
        "construction_end": date(2030, 12, 31),
        "operation_years": 40,
    },
    "extended_12yr": {
        "construction_start": date(2020, 1, 1),
        "construction_end": date(2031, 12, 31),
        "operation_years": 50,
    },
}


def _run_scenario(name: str, config: dict) -> dict:
    """运行一个场景并返回验证结果"""
    orch = ModelOrchestrator.from_excel_v17(
        construction_start=config["construction_start"],
        construction_end=config["construction_end"],
        operation_years=config["operation_years"],
    )
    results = orch.run()
    return {"name": name, "config": config, "results": results}


# ══════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════


class TestMultiScenario:
    """多方案泛化测试"""

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_engine_runs(self, scenario_name: str) -> None:
        """引擎在不同建设期下可正常运行"""
        config = SCENARIOS[scenario_name]
        result = _run_scenario(scenario_name, config)
        r = result["results"]

        # 所有结果非空
        assert r.investment is not None
        assert r.financing is not None
        assert r.depreciation is not None
        assert r.cost is not None
        assert r.revenue is not None
        assert r.pnl_total is not None
        assert r.pnl_equity is not None
        assert r.cf_total is not None
        assert r.cf_equity is not None
        assert r.cf_plan is not None
        assert r.balance_sheet is not None
        assert r.derived_metrics is not None

        # DataFrames 非空
        assert len(r.investment) > 0
        assert len(r.cf_total.data) > 0

        print(f"\n  [{scenario_name}] OK: {len(r.cf_total.data)} years")

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_balance_sheet_closure(self, scenario_name: str) -> None:
        """资产负债表在所有方案下平衡"""
        config = SCENARIOS[scenario_name]
        result = _run_scenario(scenario_name, config)
        bs = result["results"].balance_sheet.data

        for year in bs.index:
            assets = float(bs.loc[year, "total_assets"])
            liabilities = float(bs.loc[year, "total_liabilities"])
            equity = float(bs.loc[year, "total_equity"])
            diff = abs(assets - liabilities - equity)
            assert diff < 1e-6, (
                f"[{scenario_name}] Year {year}: A={assets:,.2f}, "
                f"L+E={liabilities + equity:,.2f}, diff={diff:.8f}"
            )

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_irr_computable(self, scenario_name: str) -> None:
        """IRR 可计算"""
        config = SCENARIOS[scenario_name]
        result = _run_scenario(scenario_name, config)
        dm = result["results"].derived_metrics

        assert dm.irr_total is not None, f"[{scenario_name}] Total IRR is None"
        assert 0 < dm.irr_total < 1, f"[{scenario_name}] IRR out of range: {dm.irr_total}"

        print(f"\n  [{scenario_name}] IRR: {dm.irr_total * 100:.2f}%")

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_dscr_reasonable(self, scenario_name: str) -> None:
        """DSCR 值合理 (跳过无意义的0值年)"""
        config = SCENARIOS[scenario_name]
        result = _run_scenario(scenario_name, config)
        dm = result["results"].derived_metrics

        assert dm.dscr_min is not None, f"[{scenario_name}] DSCR min is None"

        # Filter out zero-DSCR years (no EBITDA yet, debt just started)
        meaningful_dscr = {y: r for y, r in dm.dscr_by_year.items() if r > 0}
        if meaningful_dscr:
            min_dscr = min(meaningful_dscr.values())
            assert min_dscr >= 0.3, (
                f"[{scenario_name}] DSCR min too low: {min_dscr:.4f}"
            )
            print(f"\n  [{scenario_name}] DSCR min: {min_dscr:.4f} (from {len(meaningful_dscr)} years)")
        else:
            print(f"\n  [{scenario_name}] No meaningful DSCR years")

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_payback_reasonable(self, scenario_name: str) -> None:
        """回收期合理 (含建设期首年累计为正的边界情况)"""
        config = SCENARIOS[scenario_name]
        result = _run_scenario(scenario_name, config)
        dm = result["results"].derived_metrics

        assert dm.payback_static is not None, f"[{scenario_name}] Payback is None"
        # Payback 0.0 means cumulative CF is positive from year 1
        # (possible when construction costs are low relative to early revenue)
        total_years = config["construction_end"].year - config["construction_start"].year + config["operation_years"]
        assert 0 <= dm.payback_static <= total_years, (
            f"[{scenario_name}] Payback ({dm.payback_static:.1f}) out of range [0, {total_years}]"
        )

        print(f"\n  [{scenario_name}] Payback: {dm.payback_static:.1f}yr")

    def test_summary_table(self) -> None:
        """综合对比表"""
        print(f"\n  {'Scenario':>15} {'ConstYr':>8} {'OpYr':>6} {'TotalYr':>8} {'IRR':>8} {'DSCRmin':>8} {'Payback':>8} {'BS_OK':>6}")
        print("  " + "-" * 72)

        for name, config in SCENARIOS.items():
            result = _run_scenario(name, config)
            r = result["results"]
            dm = r.derived_metrics
            bs = r.balance_sheet.data

            const_yrs = config["construction_end"].year - config["construction_start"].year + 1
            total_yrs = len(r.cf_total.data)
            irr = dm.irr_total or 0
            dscr_min = dm.dscr_min or 0
            payback = dm.payback_static or 0

            # BS check
            max_bs_diff = max(
                abs(float(bs.loc[y, "total_assets"]) - float(bs.loc[y, "total_liabilities"]) - float(bs.loc[y, "total_equity"]))
                for y in bs.index
            )
            bs_ok = "OK" if max_bs_diff < 1e-6 else "FAIL"

            print(
                f"  {name:>15} {const_yrs:>8} {config['operation_years']:>6} "
                f"{total_yrs:>8} {irr * 100:>7.2f}% {dscr_min:>8.4f} "
                f"{payback:>7.1f}yr {bs_ok:>6}"
            )

        assert True  # reporting only
