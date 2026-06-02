"""
派生指标计算器 — IRR/NPV/DSCR/回收期/资产负债率

从现金流量表和利润表计算关键财务指标:
  - IRR: 内部收益率 (全投资/资本金), 使用 XIRR (日期感知, basis=365)
  - NPV: 净现值 (给定折现率)
  - DSCR: 偿债备付率 (EBITDA / 还本付息)
  - 投资回收期: 静态 (累计净现金流转正) + 动态 (折现后)
  - 资产负债率: 年度 (需资产负债表)
  - ROE: 平均净资产收益率

DSCR 定义 (抽蓄项目标准):
  DSCR = (利润总额 + 折旧摊销 + 利息支出) / (还本 + 付息)
       = EBITDA / Debt Service
  其中 EBITDA = PBT_equity + financial_expense + depreciation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy_financial as npf
import pandas as pd

from financial_model.engines.cashflow import CashFlowResult
from financial_model.engines.pnl import PnLResult
from financial_model.engines.xirr import xirr as compute_xirr


@dataclass(frozen=True)
class DerivedMetrics:
    """派生指标计算结果

    Attributes:
        irr_total: 全投资IRR (项目IRR), None 表示无解
        irr_equity: 资本金IRR, None 表示无解
        npv_total: 全投资NPV (万元)
        npv_equity: 资本金NPV (万元)
        dscr_by_year: 年度DSCR {year: ratio}
        dscr_min: 最低DSCR
        dscr_avg: 平均DSCR (运营期)
        payback_static: 静态回收期 (年, 含建设期), None 表示不回收
        payback_dynamic: 动态回收期 (年, 含建设期), None 表示不回收
        asset_liability_ratio: 年度资产负债率 {year: ratio}
        roe_avg: 平均净资产收益率
        discount_rate: NPV使用的折现率
        project_years: 项目总年限
    """

    irr_total: float | None
    irr_equity: float | None
    npv_total: float
    npv_equity: float
    dscr_by_year: dict[int, float]
    dscr_min: float | None
    dscr_avg: float | None
    payback_static: float | None
    payback_dynamic: float | None
    asset_liability_ratio: dict[int, float]
    roe_avg: float | None
    discount_rate: float
    project_years: int

    def summary(self) -> dict[str, str | float | None]:
        """人类可读摘要"""
        def _fmt(v: float | None, pct: bool = True) -> str:
            if v is None:
                return "N/A"
            return f"{v:.2%}" if pct else f"{v:,.2f}"

        return {
            "全投资IRR": _fmt(self.irr_total),
            "资本金IRR": _fmt(self.irr_equity),
            "全投资NPV(万元)": _fmt(self.npv_total, pct=False),
            "资本金NPV(万元)": _fmt(self.npv_equity, pct=False),
            "最低DSCR": _fmt(self.dscr_min),
            "平均DSCR": _fmt(self.dscr_avg),
            "静态回收期(年)": (
                f"{self.payback_static:.1f}" if self.payback_static else "N/A"
            ),
            "动态回收期(年)": (
                f"{self.payback_dynamic:.1f}" if self.payback_dynamic else "N/A"
            ),
            "折现率": _fmt(self.discount_rate),
            "项目年限": self.project_years,
        }


class DerivedMetricsCalculator:
    """派生指标计算器

    输入:
      - cf_total: 全投资现金流量表 CashFlowResult
      - cf_equity: 资本金现金流量表 CashFlowResult
      - pnl_equity: 资本金利润表 PnLResult (DSCR分子)
      - depreciation_result: 折旧摊销 DataFrame (EBITDA)
      - interest_by_year: 利息支出 Series (DSCR分母)
      - principal_by_year: 还本 Series (DSCR分母)
      - balance_sheet: 资产负债表 DataFrame (可选, 资产负债率)
      - discount_rate: 基准收益率 (默认 8%)
    """

    def __init__(
        self,
        cf_total: CashFlowResult,
        cf_equity: CashFlowResult,
        pnl_equity: PnLResult | None = None,
        depreciation_result: pd.DataFrame | None = None,
        interest_by_year: pd.Series | None = None,
        principal_by_year: pd.Series | None = None,
        balance_sheet: pd.DataFrame | None = None,
        discount_rate: float = 0.08,
    ) -> None:
        self._cf_total = cf_total
        self._cf_equity = cf_equity
        self._pnl_equity = pnl_equity
        self._depreciation = depreciation_result
        self._interest = interest_by_year
        self._principal = principal_by_year
        self._balance_sheet = balance_sheet
        self._discount_rate = discount_rate

    def calculate(self) -> DerivedMetrics:
        """计算所有派生指标"""
        # 1. IRR
        irr_total = self._calc_irr(self._cf_total)
        irr_equity = self._calc_irr(self._cf_equity)

        # 2. NPV
        npv_total = self._calc_npv(self._cf_total, self._discount_rate)
        npv_equity = self._calc_npv(self._cf_equity, self._discount_rate)

        # 3. DSCR
        dscr_by_year, dscr_min, dscr_avg = self._calc_dscr()

        # 4. 回收期
        payback_static = self._calc_payback(self._cf_total)
        payback_dynamic = self._calc_dynamic_payback(
            self._cf_total, self._discount_rate
        )

        # 5. 资产负债率
        alr = self._calc_asset_liability_ratio()

        # 6. ROE
        roe = self._calc_roe()

        return DerivedMetrics(
            irr_total=irr_total,
            irr_equity=irr_equity,
            npv_total=npv_total,
            npv_equity=npv_equity,
            dscr_by_year=dscr_by_year,
            dscr_min=dscr_min,
            dscr_avg=dscr_avg,
            payback_static=payback_static,
            payback_dynamic=payback_dynamic,
            asset_liability_ratio=alr,
            roe_avg=roe,
            discount_rate=self._discount_rate,
            project_years=len(self._cf_total.data),
        )

    # ── IRR ────────────────────────────────────────────────────

    @staticmethod
    def _calc_irr(cf_result: CashFlowResult) -> float | None:
        """计算内部收益率

        优先使用 XIRR (日期感知, basis=365), 与 Excel XIRR 完全对齐。
        若 CashFlowResult 无日期信息则回退到等期 IRR。
        """
        values = cf_result.data["net_cashflow"].values.astype(float)

        # 优先使用 XIRR (日期感知)
        if cf_result.dates:
            result = compute_xirr(values, cf_result.dates, basis=365.0)
            if result is not None:
                return result

        # 回退: 等期 IRR (无日期信息时)
        try:
            result = npf.irr(values)
            if np.isnan(result) or np.isinf(result):
                return None
            return float(result)
        except (ValueError, RuntimeError):
            return None

    # ── NPV ────────────────────────────────────────────────────

    @staticmethod
    def _calc_npv(cf_result: CashFlowResult, rate: float) -> float:
        """计算净现值"""
        values = cf_result.data["net_cashflow"].values.astype(float)
        try:
            return float(npf.npv(rate, values))
        except (ValueError, RuntimeError):
            return 0.0

    # ── DSCR ───────────────────────────────────────────────────

    def _calc_dscr(
        self,
    ) -> tuple[dict[int, float], float | None, float | None]:
        """计算偿债备付率 (年度 + 最低 + 平均)

        DSCR = EBITDA / Debt Service
        EBITDA = PBT_equity + financial_expense + depreciation
        Debt Service = principal + interest
        """
        if (
            self._pnl_equity is None
            or self._interest is None
            or self._principal is None
        ):
            return {}, None, None

        dscr: dict[int, float] = {}
        values: list[float] = []

        for year in self._pnl_equity.data.index:
            principal = self._get_series_value(self._principal, year)
            interest = self._get_series_value(self._interest, year)
            debt_service = principal + interest

            if debt_service <= 0:
                continue

            # EBITDA = PBT_equity + financial_expense + depreciation
            pbt = float(self._pnl_equity.data.loc[year, "profit_before_tax"])
            fin_exp = float(
                self._pnl_equity.data.loc[year, "financial_expense"]
            )
            depr = 0.0
            if (
                self._depreciation is not None
                and year in self._depreciation.index
            ):
                depr = float(
                    self._depreciation.loc[year, "total_depreciation"]
                )

            ebitda = pbt + fin_exp + depr
            ratio = ebitda / debt_service

            dscr[int(year)] = round(ratio, 4)
            values.append(ratio)

        if not values:
            return {}, None, None

        return dscr, round(min(values), 4), round(sum(values) / len(values), 4)

    # ── 投资回收期 ─────────────────────────────────────────────

    @staticmethod
    def _calc_payback(cf_result: CashFlowResult) -> float | None:
        """计算静态投资回收期 (年)

        找到累计净现金流转正的位置, 线性插值。
        返回值含建设期，从第一年起算。

        注意: 使用 elapsed years (从第一年起已经过去的年数),
        即 index + fraction - 1, 因为 index 0 = year 1。
        """
        cumulative = cf_result.data["cumulative_cashflow"].values.astype(float)
        net_cf = cf_result.data["net_cashflow"].values.astype(float)

        for i in range(len(cumulative)):
            if cumulative[i] >= 0:
                if i == 0:
                    return 0.0
                if net_cf[i] <= 0:
                    # Edge case: positive cumulative but negative cashflow
                    continue
                fraction = abs(cumulative[i - 1]) / net_cf[i]
                # elapsed years = index - 1 + fraction (index 0 = year 1)
                return round(i - 1 + fraction, 2)

        return None

    @staticmethod
    def _calc_dynamic_payback(
        cf_result: CashFlowResult, rate: float
    ) -> float | None:
        """计算动态投资回收期 (折现)

        使用折现后的累计净现金流转正位置。
        返回 elapsed years (从第一年起算)。
        """
        values = cf_result.data["net_cashflow"].values.astype(float)
        n = len(values)

        # 计算折现累计
        discount_factors = np.array([(1 + rate) ** t for t in range(n)])
        discounted = values / discount_factors
        cumulative = np.cumsum(discounted)

        for i in range(n):
            if cumulative[i] >= 0:
                if i == 0:
                    return 0.0
                if discounted[i] <= 0:
                    continue
                fraction = abs(cumulative[i - 1]) / discounted[i]
                # elapsed years = index - 1 + fraction
                return round(i - 1 + fraction, 2)

        return None

    # ── 资产负债率 ─────────────────────────────────────────────

    def _calc_asset_liability_ratio(self) -> dict[int, float]:
        """计算年度资产负债率"""
        if self._balance_sheet is None:
            return {}

        result: dict[int, float] = {}
        for year in self._balance_sheet.index:
            total_assets = float(self._balance_sheet.loc[year, "total_assets"])
            total_liabilities = float(
                self._balance_sheet.loc[year, "total_liabilities"]
            )
            if total_assets > 0:
                result[int(year)] = round(total_liabilities / total_assets, 4)

        return result

    # ── ROE ─────────────────────────────────────────────────────

    def _calc_roe(self) -> float | None:
        """计算平均净资产收益率"""
        if self._pnl_equity is None or self._balance_sheet is None:
            return None

        net_profit = self._pnl_equity.data["net_profit"]
        total_profit = float(net_profit.sum())
        op_years = int((net_profit != 0).sum())

        if op_years == 0:
            return None

        avg_equity = float(self._balance_sheet["total_equity"].mean())
        if avg_equity <= 0:
            return None

        return round(total_profit / avg_equity / op_years, 4)

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_series_value(series: pd.Series, year: int) -> float:
        """安全获取 Series 值"""
        if series is None or year not in series.index:
            return 0.0
        return float(series.loc[year])
