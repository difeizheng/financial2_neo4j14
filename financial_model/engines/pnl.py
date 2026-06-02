"""
利润表引擎 — 全投资 + 资本金双视角

对照 Excel:
  - 表5: 利润与利润分配表 (资本金)
  - 表6: 利润与利润分配表 (全投资)

两种视角的核心区别:
  全投资: 假设100%自有资金, 不扣除利息支出
  资本金: 利息作为财务费用扣除, 反映股东真实收益

利润表结构:
  营业收入 (capacity_revenue + energy_revenue)
  - 营业税金及附加 (surcharge)
  - 总成本费用
      生产成本 (材料+抽水电+维修+人工+折旧摊销)
    + 财务费用 (长期贷款利息)
        [全投资: 0 / 资本金: 利息支出]
  = 利润总额
  - 所得税 (含亏损弥补)
  = 净利润
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.engines.tax_calculator import TaxCalculator, TaxCalcResult
from financial_model.params.construction import ConstructionParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import ProjectTimeline


class PnLPerspective(str, Enum):
    """利润表视角"""

    TOTAL_INVESTMENT = "total"  # 全投资
    EQUITY = "equity"  # 资本金


@dataclass(frozen=True)
class PnLResult:
    """利润表计算结果

    Attributes:
        perspective: 视角 (total/equity)
        data: 利润表 DataFrame (index=year)
        tax_result: 所得税计算详情
    """

    perspective: PnLPerspective
    data: pd.DataFrame
    tax_result: TaxCalcResult


class PnLEngine(BaseEngine):
    """利润表引擎

    输入:
      - RevenueEngine 结果 (营业收入, 附加税)
      - CostEngine 结果 (生产成本)
      - FinancingEngine.loan_schedule (利息支出)
      - TaxParams (所得税率, 亏损弥补年限)

    输出 PnLResult.data DataFrame (index=year):
      - revenue: 营业收入
      - surcharge: 营业税金及附加
      - production_cost: 生产成本 (含折旧)
      - financial_expense: 财务费用 (利息)
      - total_cost: 总成本费用
      - profit_before_tax: 利润总额
      - income_tax: 所得税
      - net_profit: 净利润
      - cumulative_profit: 累计净利润
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        tax_params: TaxParams | None = None,
        revenue_result: pd.DataFrame | None = None,
        cost_result: pd.DataFrame | None = None,
        interest_by_year: pd.Series | None = None,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._tax = tax_params or TaxParams()
        self._revenue = revenue_result
        self._cost = cost_result
        self._interest = interest_by_year

    @property
    def name(self) -> str:
        return "pnl"

    def calculate(
        self, perspective: PnLPerspective = PnLPerspective.TOTAL_INVESTMENT
    ) -> PnLResult:
        """执行利润表计算

        Args:
            perspective: 全投资 or 资本金视角

        Returns:
            PnLResult 含利润表 DataFrame 和所得税详情
        """
        years = list(self._timeline.year_range)
        construction_end_year = self._construction.construction_end.year

        records = []
        for year in years:

            # 营业收入 — 移除 is_op 门, getter 对无数据年份自然返回 0.0,
            # 过渡年 (2030, ratio=0.4167) 不会被跳过
            revenue = self._get_value(year, "total_revenue")
            surcharge = self._get_value(year, "surcharge")

            # 生产成本 (含折旧)
            production_cost = self._get_cost_value(year, "total_production_cost")

            # 财务费用 (利息)
            if perspective == PnLPerspective.EQUITY:
                financial_expense = self._get_interest(year)
            else:
                financial_expense = 0.0

            total_cost = production_cost + financial_expense

            records.append(
                {
                    "year": year,
                    "revenue": revenue,
                    "surcharge": surcharge,
                    "production_cost": production_cost,
                    "financial_expense": financial_expense,
                    "total_cost": total_cost,
                    "profit_before_tax": 0.0,  # filled after tax calc
                    "income_tax": 0.0,
                    "net_profit": 0.0,
                    "cumulative_profit": 0.0,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)

        # 利润总额 = 营业收入 - 附加税 - 总成本
        df["profit_before_tax"] = df["revenue"] - df["surcharge"] - df["total_cost"]

        # 所得税计算 (含亏损弥补)
        tax_calc = TaxCalculator(
            income_tax_rate=self._tax.income_tax_rate,
            loss_carryforward_years=self._tax.loss_carryforward_years,
        )
        tax_result = tax_calc.calculate(df["profit_before_tax"])

        df["income_tax"] = tax_result.income_tax
        df["net_profit"] = df["profit_before_tax"] - df["income_tax"]
        df["cumulative_profit"] = df["net_profit"].cumsum()

        return PnLResult(
            perspective=perspective,
            data=df,
            tax_result=tax_result,
        )

    # ── 内部方法 ────────────────────────────────────────────

    def _get_value(self, year: int, column: str) -> float:
        """从 RevenueEngine 结果获取值"""
        if self._revenue is None or year not in self._revenue.index:
            return 0.0
        return float(self._revenue.loc[year, column])

    def _get_cost_value(self, year: int, column: str) -> float:
        """从 CostEngine 结果获取值"""
        if self._cost is None or year not in self._cost.index:
            return 0.0
        return float(self._cost.loc[year, column])

    def _get_interest(self, year: int) -> float:
        """获取某年利息支出"""
        if self._interest is None:
            return 0.0
        if year in self._interest.index:
            return float(self._interest.loc[year])
        return 0.0

    # ── 便利方法 ────────────────────────────────────────────

    def calculate_both(self) -> tuple[PnLResult, PnLResult]:
        """一次性计算两种视角的利润表

        Returns:
            (total_result, equity_result) 全投资 + 资本金
        """
        total = self.calculate(PnLPerspective.TOTAL_INVESTMENT)
        equity = self.calculate(PnLPerspective.EQUITY)
        return total, equity
