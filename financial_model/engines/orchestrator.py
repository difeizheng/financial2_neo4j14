"""
模型编排器 — 一键运行所有引擎, 输出完整报表集

将 9 个引擎按依赖顺序串联:
  Investment → Financing → Depreciation → Cost → Revenue
  → PnL (total + equity) → CashFlow (total + equity + plan)
  → BalanceSheet → DerivedMetrics

设计原则:
  - 单一入口: ModelOrchestrator.run() → AllResults
  - 参数即配置: 所有参数通过构造器注入
  - 结果不可变: AllResults 是 frozen dataclass
  - 工厂方法: from_excel_v17() 创建黄金基准编排器
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from financial_model.engines.balance_sheet import BalanceSheetEngine, BalanceSheetResult
from financial_model.engines.cashflow import CashFlowEngine, CashFlowPerspective, CashFlowResult
from financial_model.engines.cost import CostEngine
from financial_model.engines.derived_metrics import DerivedMetrics, DerivedMetricsCalculator
from financial_model.engines.depreciation import DepreciationEngine
from financial_model.engines.financing import FinancingEngine, FinancingResult
from financial_model.engines.investment import InvestmentAllocation, InvestmentEngine
from financial_model.engines.pnl import PnLEngine, PnLPerspective, PnLResult
from financial_model.engines.revenue import RevenueEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.depreciation import DepreciationParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import ProjectTimeline, generate_timeline


@dataclass(frozen=True)
class AllResults:
    """所有引擎计算结果的集合

    按计算顺序排列:
      1. investment: 投资概算 DataFrame
      2. financing: 融资 FinancingResult
      3. depreciation: 折旧摊销 DataFrame
      4. cost: 成本费用 DataFrame
      5. revenue: 收入税金 DataFrame
      6. pnl_total: 全投资利润表 PnLResult
      7. pnl_equity: 资本金利润表 PnLResult
      8. cf_total: 全投资现金流量表 CashFlowResult
      9. cf_equity: 资本金现金流量表 CashFlowResult
     10. cf_plan: 财务计划现金流量表 CashFlowResult
     11. balance_sheet: 资产负债表 BalanceSheetResult
     12. derived_metrics: 派生指标 DerivedMetrics
    """

    investment: pd.DataFrame
    financing: FinancingResult
    depreciation: pd.DataFrame
    cost: pd.DataFrame
    revenue: pd.DataFrame
    pnl_total: PnLResult
    pnl_equity: PnLResult
    cf_total: CashFlowResult
    cf_equity: CashFlowResult
    cf_plan: CashFlowResult
    balance_sheet: BalanceSheetResult
    derived_metrics: DerivedMetrics

    def summary(self) -> dict[str, str | float | None]:
        """返回关键指标的摘要"""
        dm = self.derived_metrics
        dm_summary = dm.summary()

        # 补充投资/融资信息
        invest_total = float(self.investment["construction_investment"].sum())
        fin = self.financing

        return {
            **dm_summary,
            "建设投资(万元)": f"{invest_total:,.0f}",
            "建设期利息(万元)": f"{fin.construction_interest_total:,.0f}",
            "动态总投资(万元)": f"{fin.dynamic_total_investment:,.0f}",
            "项目年限": dm.project_years,
        }


class ModelOrchestrator:
    """模型编排器 — 一键运行所有引擎

    用法::

        # 黄金基准 (Excel v17 参数)
        orch = ModelOrchestrator.from_excel_v17()
        results = orch.run()

        # 自定义参数
        orch = ModelOrchestrator(
            params_construction=ConstructionParams(...),
            params_investment=InvestmentParams(...),
            params_financing=FinancingParams(),
            params_operating=OperatingParams(),
            params_tax=TaxParams(),
            params_depreciation=DepreciationParams(),
            allocation=InvestmentAllocation.from_excel_v17(),
            discount_rate=0.08,
        )
        results = orch.run()
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        params_operating: OperatingParams | None = None,
        params_tax: TaxParams | None = None,
        params_depreciation: DepreciationParams | None = None,
        allocation: InvestmentAllocation | None = None,
        discount_rate: float = 0.08,
    ) -> None:
        self._construction = params_construction
        self._investment = params_investment
        self._financing = params_financing
        self._operating = params_operating or OperatingParams()
        self._tax = params_tax or TaxParams()
        self._depreciation = params_depreciation or DepreciationParams()
        self._allocation = allocation
        self._discount_rate = discount_rate
        self._timeline = generate_timeline(params_construction)

    @property
    def timeline(self) -> ProjectTimeline:
        """项目时间轴"""
        return self._timeline

    def run(self) -> AllResults:
        """运行所有引擎, 返回完整结果集

        引擎依赖链:
          1. InvestmentEngine (独立)
          2. FinancingEngine (← investment)
          3. DepreciationEngine (独立)
          4. CostEngine (← depreciation)
          5. RevenueEngine (独立)
          6. PnLEngine × 2 (← revenue + cost + financing)
          7. CashFlowEngine × 3 (← pnl + depreciation + revenue + cost + financing)
          8. BalanceSheetEngine (← depreciation + pnl_equity + financing)
          9. DerivedMetricsCalculator (← cashflow + pnl + depreciation + balance_sheet)
        """
        # ── Phase 1: 独立引擎 ─────────────────────────────────
        invest_result = self._run_investment()
        financing_result = self._run_financing(invest_result)
        depreciation_result = self._run_depreciation()
        cost_result = self._run_cost(depreciation_result, invest_result)
        revenue_result = self._run_revenue()

        # ── Phase 2: 利润表 (双视角) ─────────────────────────
        interest_by_year = self._extract_interest(financing_result)
        pnl_total, pnl_equity = self._run_pnl(
            revenue_result, cost_result, interest_by_year
        )

        # ── Phase 3: 现金流量表 (三视角) ─────────────────────
        capex_by_year = financing_result.annual_summary["construction_investment"]
        equity_by_year = financing_result.annual_summary["total_equity"]
        debt_inflow_by_year = financing_result.annual_summary["total_debt"]
        principal_by_year = self._extract_principal(financing_result)

        cf_total, cf_equity, cf_plan = self._run_cashflow(
            pnl_total=pnl_total,
            pnl_equity=pnl_equity,
            depreciation_result=depreciation_result,
            revenue_result=revenue_result,
            cost_result=cost_result,
            interest_by_year=interest_by_year,
            capex_by_year=capex_by_year,
            equity_by_year=equity_by_year,
            debt_inflow_by_year=debt_inflow_by_year,
            principal_by_year=principal_by_year,
        )

        # ── Phase 4: 资产负债表 ───────────────────────────────
        bs_result = self._run_balance_sheet(
            depreciation_result=depreciation_result,
            pnl_equity=pnl_equity,
            equity_by_year=equity_by_year,
            debt_inflow_by_year=debt_inflow_by_year,
            loan_schedule=financing_result.loan_schedule,
            construction_interest_total=financing_result.construction_interest_total,
        )

        # ── Phase 5: 派生指标 ─────────────────────────────────
        derived_metrics = self._run_derived_metrics(
            cf_total=cf_total,
            cf_equity=cf_equity,
            pnl_equity=pnl_equity,
            depreciation_result=depreciation_result,
            interest_by_year=interest_by_year,
            principal_by_year=principal_by_year,
            balance_sheet=bs_result,
        )

        return AllResults(
            investment=invest_result,
            financing=financing_result,
            depreciation=depreciation_result,
            cost=cost_result,
            revenue=revenue_result,
            pnl_total=pnl_total,
            pnl_equity=pnl_equity,
            cf_total=cf_total,
            cf_equity=cf_equity,
            cf_plan=cf_plan,
            balance_sheet=bs_result,
            derived_metrics=derived_metrics,
        )

    # ══ Phase 1: 独立引擎 ════════════════════════════════════

    def _run_investment(self) -> pd.DataFrame:
        """运行投资概算引擎"""
        allocation = self._allocation or InvestmentAllocation.from_excel_v17()
        engine = InvestmentEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            allocation=allocation,
        )
        return engine.calculate()

    def _run_financing(self, invest_result: pd.DataFrame) -> FinancingResult:
        """运行融资引擎"""
        engine = FinancingEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            investment_result=invest_result,
        )
        return engine.calculate()

    def _run_depreciation(self) -> pd.DataFrame:
        """运行折旧摊销引擎"""
        engine = DepreciationEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            depreciation_params=self._depreciation,
            operating_params=self._operating,
        )
        return engine.calculate()

    def _run_cost(
        self,
        depreciation_result: pd.DataFrame,
        invest_result: pd.DataFrame,
    ) -> pd.DataFrame:
        """运行成本费用引擎"""
        construction_total = float(invest_result["construction_investment"].sum())
        engine = CostEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            operating_params=self._operating,
            depreciation_result=depreciation_result,
            construction_investment_total=construction_total,
        )
        return engine.calculate()

    def _run_revenue(self) -> pd.DataFrame:
        """运行收入税金引擎"""
        engine = RevenueEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            operating_params=self._operating,
            tax_params=self._tax,
        )
        return engine.calculate()

    # ══ Phase 2: 利润表 ══════════════════════════════════════

    def _run_pnl(
        self,
        revenue_result: pd.DataFrame,
        cost_result: pd.DataFrame,
        interest_by_year: pd.Series,
    ) -> tuple[PnLResult, PnLResult]:
        """运行利润表引擎 (全投资 + 资本金)"""
        engine = PnLEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            tax_params=self._tax,
            revenue_result=revenue_result,
            cost_result=cost_result,
            interest_by_year=interest_by_year,
        )
        return engine.calculate_both()

    # ══ Phase 3: 现金流量表 ══════════════════════════════════

    def _run_cashflow(
        self,
        pnl_total: PnLResult,
        pnl_equity: PnLResult,
        depreciation_result: pd.DataFrame,
        revenue_result: pd.DataFrame,
        cost_result: pd.DataFrame,
        interest_by_year: pd.Series,
        capex_by_year: pd.Series,
        equity_by_year: pd.Series,
        debt_inflow_by_year: pd.Series,
        principal_by_year: pd.Series,
    ) -> tuple[CashFlowResult, CashFlowResult, CashFlowResult]:
        """运行现金流量表引擎 (全投资 + 资本金 + 财务计划)"""
        base_kw = dict(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            tax_params=self._tax,
            depreciation_result=depreciation_result,
            revenue_result=revenue_result,
            cost_result=cost_result,
            interest_by_year=interest_by_year,
            capex_by_year=capex_by_year,
            equity_by_year=equity_by_year,
            debt_inflow_by_year=debt_inflow_by_year,
            principal_by_year=principal_by_year,
            fixed_asset_original_value=self._depreciation.fixed_assets.original_value,
        )

        # 全投资 (用 pnl_total)
        engine_total = CashFlowEngine(
            pnl_result=pnl_total, **base_kw
        )
        cf_total = engine_total.calculate(CashFlowPerspective.TOTAL_INVESTMENT)

        # 资本金 (用 pnl_equity)
        engine_equity = CashFlowEngine(
            pnl_result=pnl_equity, **base_kw
        )
        cf_equity = engine_equity.calculate(CashFlowPerspective.EQUITY)

        # 财务计划 (用 pnl_equity)
        engine_plan = CashFlowEngine(
            pnl_result=pnl_equity, **base_kw
        )
        cf_plan = engine_plan.calculate(CashFlowPerspective.FINANCIAL_PLAN)

        return cf_total, cf_equity, cf_plan

    # ══ Phase 4: 资产负债表 ══════════════════════════════════

    def _run_balance_sheet(
        self,
        depreciation_result: pd.DataFrame,
        pnl_equity: PnLResult,
        equity_by_year: pd.Series,
        debt_inflow_by_year: pd.Series,
        loan_schedule: pd.DataFrame,
        construction_interest_total: float,
    ) -> BalanceSheetResult:
        """运行资产负债表引擎"""
        engine = BalanceSheetEngine(
            params_construction=self._construction,
            params_investment=self._investment,
            params_financing=self._financing,
            timeline=self._timeline,
            depreciation_result=depreciation_result,
            pnl_equity_result=pnl_equity,
            depreciation_params=self._depreciation,
            equity_by_year=equity_by_year,
            debt_inflow_by_year=debt_inflow_by_year,
            loan_schedule=loan_schedule,
            construction_interest_total=construction_interest_total,
        )
        return engine.calculate()

    # ══ Phase 5: 派生指标 ════════════════════════════════════

    def _run_derived_metrics(
        self,
        cf_total: CashFlowResult,
        cf_equity: CashFlowResult,
        pnl_equity: PnLResult,
        depreciation_result: pd.DataFrame,
        interest_by_year: pd.Series,
        principal_by_year: pd.Series,
        balance_sheet: BalanceSheetResult,
    ) -> DerivedMetrics:
        """运行派生指标计算器"""
        calc = DerivedMetricsCalculator(
            cf_total=cf_total,
            cf_equity=cf_equity,
            pnl_equity=pnl_equity,
            depreciation_result=depreciation_result,
            interest_by_year=interest_by_year,
            principal_by_year=principal_by_year,
            balance_sheet=balance_sheet.data,
            discount_rate=self._discount_rate,
        )
        return calc.calculate()

    # ══ 数据提取辅助 ════════════════════════════════════════

    @staticmethod
    def _extract_interest(financing: FinancingResult) -> pd.Series:
        """从还款计划提取年度利息支出"""
        ls = financing.loan_schedule
        if ls.empty:
            return pd.Series(dtype=float)
        return ls.set_index("year")["interest_payment"]

    @staticmethod
    def _extract_principal(financing: FinancingResult) -> pd.Series:
        """从还款计划提取年度还本支出"""
        ls = financing.loan_schedule
        if ls.empty:
            return pd.Series(dtype=float)
        return ls.set_index("year")["principal_repayment"]

    # ══ 工厂方法 ════════════════════════════════════════════

    @classmethod
    def from_excel_v17(
        cls,
        construction_start: date | None = None,
        construction_end: date | None = None,
        operation_years: int = 40,
        discount_rate: float = 0.08,
    ) -> ModelOrchestrator:
        """从 Excel v17 模型创建编排器 (黄金基准)

        Args:
            construction_start: 建设起始日期 (默认 2023-02-01)
            construction_end: 建设结束日期 (默认 2030-07-31)
            operation_years: 运营年限 (默认 40)
            discount_rate: 基准收益率 (默认 8%)
        """
        return cls(
            params_construction=ConstructionParams(
                construction_start=construction_start or date(2023, 2, 1),
                construction_end=construction_end or date(2030, 7, 31),
                operation_years=operation_years,
            ),
            params_investment=InvestmentParams.from_excel_v17(),
            params_financing=FinancingParams.from_excel_v17(),
            params_operating=OperatingParams.from_excel_v17(),
            params_tax=TaxParams.from_excel_v17(),
            params_depreciation=DepreciationParams.from_excel_v17(),
            discount_rate=discount_rate,
        )
