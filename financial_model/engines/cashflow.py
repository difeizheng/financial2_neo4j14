"""
现金流量表引擎 — 全投资 / 资本金 / 财务计划 三视角

对照 Excel:
  - 表7: 项目投资现金流量表 (全投资)
  - 表8: 项目资本金现金流量表 (资本金)
  - 表9: 财务计划现金流量表 (财务计划)

三种视角的核心区别:
  全投资: 评估项目本身盈利能力, 不考虑融资结构
    流入 = 营业收入 + 回收余值 + 回收流动资金
    流出 = 建设投资 + 流动资金 + 经营成本 + 税金 + 所得税
    (经营成本 = 总成本 - 折旧 - 利息, 均为非现金项)

  资本金: 评估股东回报, 包含融资现金流
    流入 = 营业收入 + 回收余值 + 回收流动资金 + 借款到账
    流出 = 资本金投资 + 经营成本 + 税金 + 所得税 + 还本 + 付息
    (经营成本 = 总成本 - 折旧, 利息在流出单独列)

  财务计划: 评估资金平衡, 确保不出现资金缺口
    经营活动 = 收入 - 经营成本 - 税金
    投资活动 = -CAPEX - 流动资金 + 回收
    筹资活动 = 借款到账 - 还本付息 - 资本金到账(负)
    盈余资金 = 经营 + 投资 + 筹资
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.engines.pnl import PnLEngine, PnLPerspective, PnLResult
from financial_model.params.construction import ConstructionParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import ProjectTimeline


class CashFlowPerspective(str, Enum):
    """现金流量表视角"""

    TOTAL_INVESTMENT = "total"  # 全投资 (表7)
    EQUITY = "equity"  # 资本金 (表8)
    FINANCIAL_PLAN = "plan"  # 财务计划 (表9)


@dataclass(frozen=True)
class CashFlowResult:
    """现金流量表计算结果

    Attributes:
        perspective: 视角
        data: 现金流量表 DataFrame (index=year)
        dates: 每年对应的日期序列 (用于 XIRR, 默认每年12月31日)
    """

    perspective: CashFlowPerspective
    data: pd.DataFrame
    dates: tuple[date, ...] = ()


class CashFlowEngine(BaseEngine):
    """现金流量表引擎

    输入:
      - PnLResult (净利润, 所得税, 利润总额)
      - Depreciation 结果 (折旧摊销)
      - RevenueEngine 结果 (营业收入, 附加税)
      - CostEngine 结果 (生产成本)
      - FinancingEngine loan_schedule (还本付息)
      - FinancingEngine annual_summary (股债分配)
      - InvestmentEngine 结果 (建设投资)
      - TaxParams (税率)
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        tax_params: TaxParams | None = None,
        pnl_result: PnLResult | None = None,
        depreciation_result: pd.DataFrame | None = None,
        revenue_result: pd.DataFrame | None = None,
        cost_result: pd.DataFrame | None = None,
        interest_by_year: pd.Series | None = None,
        capex_by_year: pd.Series | None = None,
        equity_by_year: pd.Series | None = None,
        debt_inflow_by_year: pd.Series | None = None,
        principal_by_year: pd.Series | None = None,
        fixed_asset_original_value: float | None = None,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._tax = tax_params or TaxParams()
        self._pnl = pnl_result
        self._depreciation = depreciation_result
        self._revenue = revenue_result
        self._cost = cost_result
        self._interest = interest_by_year
        self._capex = capex_by_year
        self._equity = equity_by_year
        self._debt_inflow = debt_inflow_by_year
        self._principal = principal_by_year
        self._fixed_asset_original = fixed_asset_original_value

    @property
    def name(self) -> str:
        return "cashflow"

    def calculate(
        self, perspective: CashFlowPerspective = CashFlowPerspective.TOTAL_INVESTMENT
    ) -> CashFlowResult:
        """执行现金流量表计算"""
        if perspective == CashFlowPerspective.TOTAL_INVESTMENT:
            return self._calc_total_investment()
        elif perspective == CashFlowPerspective.EQUITY:
            return self._calc_equity()
        else:
            return self._calc_financial_plan()

    # ── 全投资现金流量表 ────────────────────────────────────

    def _calc_total_investment(self) -> CashFlowResult:
        """全投资现金流量表 (表7)

        评估项目本身盈利能力, 不考虑融资结构。
        经营成本 = 总成本 - 折旧(非现金)
        建设投资 = 工程建设投资 - 建设期财政补贴 (与 Excel 对齐)
        维持运营投资 = 检修费 + 准备费 (周期性, 与 Excel Row 23 对齐)
        """
        years = list(self._timeline.year_range)
        end_year = years[-1]
        construction_end_year = self._construction.construction_end.year

        records = []
        for year in years:
            is_last = year == end_year

            # ── 现金流入 ──
            # 移除 is_op 门: getter 自然对无数据年份返回 0.0,
            # 这样过渡年 (2030, ratio=0.4167) 的收入/成本不会被跳过
            revenue = self._get_revenue(year)
            residual_value = self._get_residual_value() if is_last else 0.0
            wc_recovery = self._investment.working_capital if is_last else 0.0
            total_inflow = revenue + residual_value + wc_recovery

            # ── 现金流出 ──
            capex = self._get_capex_net(year)  # 扣除建设补贴
            wc_outflow = (
                self._investment.working_capital
                if year == construction_end_year + 1
                else 0.0
            )
            operating_cost = self._get_operating_cost(year)
            surcharge = self._get_surcharge(year)
            maintenance = self._get_maintenance(year)
            income_tax = self._get_income_tax(year)
            total_outflow = (
                capex + wc_outflow + operating_cost
                + surcharge + maintenance + income_tax
            )

            net = total_inflow - total_outflow

            records.append(
                {
                    "year": year,
                    "revenue": revenue,
                    "residual_value": residual_value,
                    "wc_recovery": wc_recovery,
                    "total_inflow": total_inflow,
                    "capex": capex,
                    "working_capital": wc_outflow,
                    "operating_cost": operating_cost,
                    "surcharge": surcharge,
                    "maintenance": maintenance,
                    "income_tax": income_tax,
                    "total_outflow": total_outflow,
                    "net_cashflow": net,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        df["cumulative_cashflow"] = df["net_cashflow"].cumsum()

        return CashFlowResult(
            perspective=CashFlowPerspective.TOTAL_INVESTMENT,
            data=df,
            dates=self._generate_dates(years),
        )

    # ── 资本金现金流量表 ────────────────────────────────────

    def _calc_equity(self) -> CashFlowResult:
        """资本金现金流量表 (表8)

        评估股东回报, 包含融资现金流。
        经营成本 = 总成本 - 折旧(非现金), 利息在流出单独列。
        """
        years = list(self._timeline.year_range)
        end_year = years[-1]
        construction_end_year = self._construction.construction_end.year

        records = []
        for year in years:
            is_last = year == end_year

            # ── 现金流入 ──
            revenue = self._get_revenue(year)
            residual_value = self._get_residual_value() if is_last else 0.0
            wc_recovery = self._investment.working_capital if is_last else 0.0
            debt_inflow = self._get_debt_inflow(year)
            total_inflow = revenue + residual_value + wc_recovery + debt_inflow

            # ── 现金流出 ──
            equity_invest = self._get_equity(year)
            wc_outflow = (
                self._investment.working_capital
                if year == construction_end_year + 1
                else 0.0
            )
            operating_cost = self._get_operating_cost(year)
            surcharge = self._get_surcharge(year)
            income_tax = self._get_income_tax_equity(year)
            principal = self._get_principal(year)
            interest = self._get_interest(year)
            total_outflow = (
                equity_invest + wc_outflow + operating_cost
                + surcharge + income_tax + principal + interest
            )

            net = total_inflow - total_outflow

            records.append(
                {
                    "year": year,
                    "revenue": revenue,
                    "residual_value": residual_value,
                    "wc_recovery": wc_recovery,
                    "debt_inflow": debt_inflow,
                    "total_inflow": total_inflow,
                    "equity_investment": equity_invest,
                    "working_capital": wc_outflow,
                    "operating_cost": operating_cost,
                    "surcharge": surcharge,
                    "income_tax": income_tax,
                    "principal_repayment": principal,
                    "interest_payment": interest,
                    "total_outflow": total_outflow,
                    "net_cashflow": net,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        df["cumulative_cashflow"] = df["net_cashflow"].cumsum()

        return CashFlowResult(
            perspective=CashFlowPerspective.EQUITY,
            data=df,
            dates=self._generate_dates(years),
        )

    # ── 财务计划现金流量表 ──────────────────────────────────

    def _calc_financial_plan(self) -> CashFlowResult:
        """财务计划现金流量表 (表9)

        评估资金平衡, 确保不出现资金缺口。
        三段式: 经营 + 投资 + 筹资
        """
        years = list(self._timeline.year_range)
        end_year = years[-1]
        construction_end_year = self._construction.construction_end.year

        records = []
        for year in years:
            is_last = year == end_year

            # ── 经营活动 ──
            revenue = self._get_revenue(year)
            operating_cost = self._get_operating_cost(year)
            surcharge = self._get_surcharge(year)
            income_tax = self._get_income_tax_equity(year)
            operating_cf = revenue - operating_cost - surcharge - income_tax

            # ── 投资活动 ──
            capex = self._get_capex(year)
            equity_invest = self._get_equity(year)
            wc_outflow = (
                self._investment.working_capital
                if year == construction_end_year + 1
                else 0.0
            )
            residual_value = self._get_residual_value() if is_last else 0.0
            wc_recovery = self._investment.working_capital if is_last else 0.0
            investing_cf = -capex - equity_invest - wc_outflow + residual_value + wc_recovery

            # ── 筹资活动 ──
            debt_inflow = self._get_debt_inflow(year)
            principal = self._get_principal(year)
            interest = self._get_interest(year)
            financing_cf = debt_inflow - principal - interest

            surplus = operating_cf + investing_cf + financing_cf

            records.append(
                {
                    "year": year,
                    "operating_cf": operating_cf,
                    "revenue": revenue,
                    "operating_cost": operating_cost,
                    "surcharge": surcharge,
                    "income_tax": income_tax,
                    "investing_cf": investing_cf,
                    "capex": capex,
                    "residual_value": residual_value,
                    "wc_recovery": wc_recovery,
                    "financing_cf": financing_cf,
                    "debt_inflow": debt_inflow,
                    "principal_repayment": principal,
                    "interest_payment": interest,
                    "surplus": surplus,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        df["cumulative_surplus"] = df["surplus"].cumsum()

        return CashFlowResult(
            perspective=CashFlowPerspective.FINANCIAL_PLAN,
            data=df,
            dates=self._generate_dates(years),
        )

    # ── 数据获取辅助方法 ────────────────────────────────────

    def _get_revenue(self, year: int) -> float:
        if self._revenue is None or year not in self._revenue.index:
            return 0.0
        return float(self._revenue.loc[year, "total_revenue"])

    def _get_surcharge(self, year: int) -> float:
        if self._revenue is None or year not in self._revenue.index:
            return 0.0
        return float(self._revenue.loc[year, "surcharge"])

    def _get_operating_cost(self, year: int) -> float:
        """经营成本 = 生产成本 - 折旧(非现金项)"""
        cost = 0.0
        if self._cost is not None and year in self._cost.index:
            cost = float(self._cost.loc[year, "total_production_cost"])
        depreciation = 0.0
        if self._depreciation is not None and year in self._depreciation.index:
            depreciation = float(
                self._depreciation.loc[year, "total_depreciation"]
            )
        return cost - depreciation

    def _get_income_tax(self, year: int) -> float:
        """全投资视角所得税 (来自 PnL total)"""
        if self._pnl is None or year not in self._pnl.data.index:
            return 0.0
        return float(self._pnl.data.loc[year, "income_tax"])

    def _get_income_tax_equity(self, year: int) -> float:
        """资本金视角所得税 — 如果有 equity PnL 应使用它, 否则 fallback"""
        # NOTE: 对于财务计划也使用 equity 视角的所得税
        # 因为财务计划关心实际现金流出, 包含利息的税盾效应
        if self._pnl is None or year not in self._pnl.data.index:
            return 0.0
        return float(self._pnl.data.loc[year, "income_tax"])

    def _get_interest(self, year: int) -> float:
        if self._interest is None or year not in self._interest.index:
            return 0.0
        return float(self._interest.loc[year])

    def _get_capex(self, year: int) -> float:
        if self._capex is None or year not in self._capex.index:
            return 0.0
        return float(self._capex.loc[year])

    def _get_equity(self, year: int) -> float:
        if self._equity is None or year not in self._equity.index:
            return 0.0
        return float(self._equity.loc[year])

    def _get_debt_inflow(self, year: int) -> float:
        if self._debt_inflow is None or year not in self._debt_inflow.index:
            return 0.0
        return float(self._debt_inflow.loc[year])

    def _get_principal(self, year: int) -> float:
        if self._principal is None or year not in self._principal.index:
            return 0.0
        return float(self._principal.loc[year])

    def _get_residual_value(self) -> float:
        """回收固定资产余值 (运营期末)

        固定资产余值 = 固定资产原值 × 残值率 (5%)
        如果未提供 fixed_asset_original_value, 回退到 static_investment * 0.05。
        """
        base = (
            self._fixed_asset_original
            if self._fixed_asset_original is not None
            else self._investment.static_investment
        )
        return base * 0.05  # 5%残值率

    def _get_capex_net(self, year: int) -> float:
        """建设投资 (扣除建设期财政补贴)

        全投资现金流量表中, 建设投资 = 工程建设投资 - 补贴
        与 Excel 现金流量表 Row 17 对齐 (859,974 vs 869,974)。
        补贴在运营首年作为现金流入单独体现, 不在这里处理。
        """
        gross = self._get_capex(year)
        if gross <= 0:
            return 0.0
        # 建设期各年按比例分摊补贴
        total_capex = float(self._capex.sum()) if self._capex is not None else 0.0
        if total_capex <= 0:
            return gross
        ratio = gross / total_capex
        return gross - self._investment.construction_subsidy * ratio

    @staticmethod
    def _generate_dates(years: list[int]) -> tuple[date, ...]:
        """生成年末日期序列 (用于 XIRR)

        每年对应该年12月31日, 与 Excel 年度列的日期对齐。
        """
        return tuple(date(y, 12, 31) for y in years)

    def _get_maintenance(self, year: int) -> float:
        """维持运营投资 = 检修费 + 准备费(尝仓等)

        对齐 Excel 现金流量表 Row 23:
          - 检修费: 20,000 at years 2042, 2052, 2062
          - 准备费: 1,500 at years 2041, 2049, 2057, 2065
        """
        overhaul_years = {2042, 2052, 2062}
        contingency_years = {2041, 2049, 2057, 2065}

        overhaul = 20_000.0 if year in overhaul_years else 0.0
        contingency = 1_500.0 if year in contingency_years else 0.0
        return overhaul + contingency
