"""
资产负债表引擎 — 资产 = 负债 + 所有者权益

对照 Excel:
  - 表10: 资产负债表

采用期末余额法, 分两阶段:
  1. 建设期: 在建工程 = 累计投资, 平衡来自股债分配
  2. 运营期: 固定/无形/储能资产净值逐年递减, 贷款余额递减,
     权益通过净利润累积增长

关键等式: 资产合计 = 负债合计 + 所有者权益合计
现金为平衡项: cash = (负债 + 权益) - 非现金资产
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.engines.pnl import PnLResult
from financial_model.params.construction import ConstructionParams
from financial_model.params.depreciation import DepreciationParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.timeline.generator import ProjectTimeline


@dataclass(frozen=True)
class BalanceSheetResult:
    """资产负债表计算结果

    Attributes:
        data: 资产负债表 DataFrame (index=year)
    """

    data: pd.DataFrame


class BalanceSheetEngine(BaseEngine):
    """资产负债表引擎

    输入:
      - DepreciationEngine 结果 (年度折旧/摊销)
      - PnLResult (equity 视角, 净利润 → 未分配利润)
      - DepreciationParams (资产原值)
      - FinancingParams (分红比例, 盈余公积比例)
      - 股债分配序列 (equity_by_year, debt_inflow_by_year)
      - 还款计划 (loan_schedule)

    输出 DataFrame (index=year):
      - construction_in_progress: 在建工程 (建设期)
      - fixed_assets_gross/net: 固定资产原值/净值
      - intangible_gross/net: 无形资产原值/净值
      - storage_gross/net: 储能资产原值/净值
      - working_capital_assets: 流动资产中的流动资金
      - cash: 现金 (平衡项)
      - total_assets: 资产合计
      - long_term_loan: 长期贷款余额
      - wc_borrowing: 流动资金借款
      - total_liabilities: 负债合计
      - paid_in_capital: 实收资本 (资本金)
      - surplus_reserve: 累计盈余公积
      - retained_earnings: 未分配利润
      - total_equity: 所有者权益合计
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        depreciation_result: pd.DataFrame | None = None,
        pnl_equity_result: PnLResult | None = None,
        depreciation_params: DepreciationParams | None = None,
        equity_by_year: pd.Series | None = None,
        debt_inflow_by_year: pd.Series | None = None,
        loan_schedule: pd.DataFrame | None = None,
        construction_interest_total: float = 0.0,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._depreciation_result = depreciation_result
        self._pnl_equity = pnl_equity_result
        self._depreciation_params = depreciation_params or DepreciationParams()
        self._equity_by_year = equity_by_year
        self._debt_inflow_by_year = debt_inflow_by_year
        self._loan_schedule = loan_schedule
        self._construction_interest_total = construction_interest_total

    @property
    def name(self) -> str:
        return "balance_sheet"

    def calculate(self) -> BalanceSheetResult:
        """执行资产负债表计算"""
        years = list(self._timeline.year_range)
        construction_end_year = self._construction.construction_end.year

        # Pre-compute cumulative series
        cum_equity = self._cumulative_equity(years)
        cum_debt = self._cumulative_debt(years)

        # Build loan balance lookup for operation years
        loan_balance_map = self._build_loan_balance_map(construction_end_year)

        # Build equity sub-items (surplus reserve, retained earnings)
        equity_items = self._build_equity_items(years, construction_end_year)

        records = []
        for year in years:
            if year <= construction_end_year:
                rec = self._construction_record(
                    year, cum_equity, cum_debt
                )
            else:
                rec = self._operation_record(
                    year,
                    construction_end_year,
                    cum_equity,
                    loan_balance_map,
                    equity_items,
                )
            records.append(rec)

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        return BalanceSheetResult(data=df)

    # ── 建设期 ─────────────────────────────────────────────────

    def _construction_record(
        self,
        year: int,
        cum_equity: dict[int, float],
        cum_debt: dict[int, float],
    ) -> dict:
        """建设期: 在建工程 = 累计投资"""
        equity = cum_equity.get(year, 0.0)
        debt = cum_debt.get(year, 0.0)
        cip = equity + debt  # 在建工程

        return {
            "year": year,
            # 资产
            "construction_in_progress": cip,
            "fixed_assets_gross": 0.0,
            "fixed_assets_net": 0.0,
            "intangible_gross": 0.0,
            "intangible_net": 0.0,
            "storage_gross": 0.0,
            "storage_net": 0.0,
            "working_capital_assets": 0.0,
            "cash": 0.0,
            "total_assets": cip,
            # 负债
            "long_term_loan": debt,
            "wc_borrowing": 0.0,
            "total_liabilities": debt,
            # 所有者权益
            "paid_in_capital": equity,
            "surplus_reserve": 0.0,
            "retained_earnings": 0.0,
            "total_equity": equity,
        }

    # ── 运营期 ─────────────────────────────────────────────────

    def _operation_record(
        self,
        year: int,
        construction_end_year: int,
        cum_equity: dict[int, float],
        loan_balance_map: dict[int, float],
        equity_items: dict[int, dict[str, float]],
    ) -> dict:
        """运营期: 资产净值递减, 贷款余额递减, 权益累积"""
        dp = self._depreciation_params

        # ── 非现金资产 ──
        fixed_gross = dp.fixed_assets.original_value
        intangible_gross = dp.intangible_assets.original_value
        storage_gross = dp.energy_storage_assets.original_value

        fixed_net = max(fixed_gross - self._cumsum_depreciation(
            year, "fixed_depreciation", construction_end_year
        ), fixed_gross * dp.fixed_assets.residual_rate)

        intangible_net = max(intangible_gross - self._cumsum_depreciation(
            year, "intangible_amortization", construction_end_year
        ), 0.0)

        storage_net = max(storage_gross - self._cumsum_depreciation(
            year, "energy_storage_depreciation", construction_end_year
        ), 0.0)

        # 长期待摊净值 (从折旧引擎获取)
        prepaid_net = 0.0
        if self._depreciation_result is not None:
            # 累计摊销 = 从运营开始到今年的累计
            cumsum_prepaid = 0.0
            for y in range(construction_end_year + 1, year + 1):
                if y in self._depreciation_result.index:
                    cumsum_prepaid += float(
                        self._depreciation_result.loc[y, "long_term_prepaid"]
                    )
            # 原值 = 累计投入 (每5年一笔)
            op_offset = year - construction_end_year - 1
            cycles_completed = op_offset // dp.long_term_prepaid_cycle + 1
            prepaid_gross = cycles_completed * dp.long_term_prepaid_amount
            prepaid_net = max(prepaid_gross - cumsum_prepaid, 0.0)

        wc_assets = self._investment.working_capital

        non_cash_assets = (
            fixed_net + intangible_net + storage_net
            + prepaid_net + wc_assets
        )

        # ── 负债 ──
        loan_bal = loan_balance_map.get(year, 0.0)
        wc_equity_share = self._financing.working_capital_equity_share
        wc_borrowing = wc_assets * (1 - wc_equity_share)
        total_liabilities = loan_bal + wc_borrowing

        # ── 所有者权益 ──
        # 资本金 = 建设期总资本金 + 流动资金中资本金部分
        total_equity_invested = cum_equity.get(construction_end_year, 0.0)
        wc_equity = wc_assets * wc_equity_share
        paid_in_capital = total_equity_invested + wc_equity

        # 盈余公积 + 未分配利润
        eq_item = equity_items.get(year, {"surplus_reserve": 0.0, "retained_earnings": 0.0})
        surplus_reserve = eq_item["surplus_reserve"]
        retained_earnings = eq_item["retained_earnings"]
        total_equity = paid_in_capital + surplus_reserve + retained_earnings

        # ── 现金 (平衡项) ──
        cash = (total_liabilities + total_equity) - non_cash_assets
        total_assets = cash + non_cash_assets

        return {
            "year": year,
            # 资产
            "construction_in_progress": 0.0,
            "fixed_assets_gross": fixed_gross,
            "fixed_assets_net": fixed_net,
            "intangible_gross": intangible_gross,
            "intangible_net": intangible_net,
            "storage_gross": storage_gross,
            "storage_net": storage_net,
            "working_capital_assets": wc_assets,
            "cash": cash,
            "total_assets": total_assets,
            # 负债
            "long_term_loan": loan_bal,
            "wc_borrowing": wc_borrowing,
            "total_liabilities": total_liabilities,
            # 所有者权益
            "paid_in_capital": paid_in_capital,
            "surplus_reserve": surplus_reserve,
            "retained_earnings": retained_earnings,
            "total_equity": total_equity,
        }

    # ── 辅助方法 ───────────────────────────────────────────────

    def _cumulative_equity(self, years: list[int]) -> dict[int, float]:
        """按年累计资本金投入"""
        result: dict[int, float] = {}
        cumulative = 0.0
        for year in years:
            if self._equity_by_year is not None and year in self._equity_by_year.index:
                cumulative += float(self._equity_by_year.loc[year])
            result[year] = cumulative
        return result

    def _cumulative_debt(self, years: list[int]) -> dict[int, float]:
        """按年累计债务到账"""
        result: dict[int, float] = {}
        cumulative = 0.0
        for year in years:
            if self._debt_inflow_by_year is not None and year in self._debt_inflow_by_year.index:
                cumulative += float(self._debt_inflow_by_year.loc[year])
            result[year] = cumulative
        return result

    def _build_loan_balance_map(
        self, construction_end_year: int
    ) -> dict[int, float]:
        """构建运营期各年贷款余额映射

        Returns:
            {year: closing_balance} for operation years
        """
        result: dict[int, float] = {}
        years = list(self._timeline.year_range)

        if self._loan_schedule is not None and not self._loan_schedule.empty:
            # From loan_schedule
            for _, row in self._loan_schedule.iterrows():
                y = int(row["year"])
                result[y] = float(row["closing_balance"])

            # Total principal (initial loan balance)
            total_principal = float(self._loan_schedule["opening_balance"].iloc[0])

            # Fill in years before repayment starts (grace period)
            repayment_start = int(self._loan_schedule["year"].iloc[0])
            for year in years:
                if year > construction_end_year and year < repayment_start:
                    result[year] = total_principal
                elif year > construction_end_year and year not in result:
                    result[year] = 0.0
        else:
            # Fallback: no loan schedule → all zeros
            for year in years:
                if year > construction_end_year:
                    result[year] = 0.0

        return result

    def _cumsum_depreciation(
        self, year: int, column: str, construction_end_year: int
    ) -> float:
        """计算从运营开始到指定年份的累计折旧/摊销"""
        if self._depreciation_result is None:
            return 0.0
        total = 0.0
        for y in range(construction_end_year + 1, year + 1):
            if y in self._depreciation_result.index:
                total += float(self._depreciation_result.loc[y, column])
        return total

    def _build_equity_items(
        self,
        years: list[int],
        construction_end_year: int,
    ) -> dict[int, dict[str, float]]:
        """构建运营期各年盈余公积和未分配利润

        Returns:
            {year: {"surplus_reserve": float, "retained_earnings": float}}
        """
        result: dict[int, dict[str, float]] = {}

        if self._pnl_equity is None:
            for year in years:
                if year > construction_end_year:
                    result[year] = {"surplus_reserve": 0.0, "retained_earnings": 0.0}
            return result

        cum_surplus = 0.0
        cum_retained = 0.0

        fp = self._financing
        statutory_ratio = fp.statutory_reserve_ratio
        statutory_limit = fp.statutory_reserve_limit
        discretionary_ratio = fp.discretionary_reserve_ratio
        dividend_ratio = fp.dividend_payout_ratio

        for year in years:
            if year <= construction_end_year:
                continue

            net_profit = 0.0
            if year in self._pnl_equity.data.index:
                net_profit = float(self._pnl_equity.data.loc[year, "net_profit"])

            if net_profit > 0:
                # 法定盈余公积 (累计不超过上限)
                statutory_this = net_profit * statutory_ratio
                if cum_surplus + statutory_this > statutory_limit:
                    statutory_this = max(0.0, statutory_limit - cum_surplus)

                # 任意盈余公积
                discretionary_this = net_profit * discretionary_ratio

                annual_surplus = statutory_this + discretionary_this
                cum_surplus += annual_surplus

                # 分红 = (净利润 - 盈余公积) × 分红比例
                distributable = net_profit - annual_surplus
                dividends = max(0.0, distributable) * dividend_ratio

                # 未分配利润增加
                retained_increase = net_profit - annual_surplus - dividends
                cum_retained += retained_increase
            else:
                # 亏损年: 不提盈余公积, 不分红, 未分配利润减少
                cum_retained += net_profit

            result[year] = {
                "surplus_reserve": cum_surplus,
                "retained_earnings": cum_retained,
            }

        return result
