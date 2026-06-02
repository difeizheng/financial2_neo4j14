"""
所得税计算器 — 含亏损弥补 (5年结转)

对照 Excel 表5-利润表 (资本金):
  - 利润总额 → 弥补以前年度亏损 → 应纳税所得额
  - 应纳税所得额 × 所得税率 = 所得税
  - 亏损可向后结转5年, 超期作废

中国税法:
  企业纳税年度发生的亏损, 准予向以后年度结转,
  但结转年限最长不得超过5年 (企业所得税法第18条)。
  先亏先补 (FIFO), 5年窗口从亏损发生的次年算起。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TaxCalcResult:
    """所得税计算结果

    Attributes:
        income_tax: 所得税额 (Series, index=year)
        taxable_income: 应纳税所得额 (Series, index=year)
        loss_utilized: 当年已弥补亏损额 (Series, index=year)
        loss_carried: 当年末待弥补亏损余额 (Series, index=year)
    """

    income_tax: pd.Series
    taxable_income: pd.Series
    loss_utilized: pd.Series
    loss_carried: pd.Series


class TaxCalculator:
    """所得税计算器

    处理:
      1. 利润 < 0 → 当期不缴税, 亏损进入弥补队列
      2. 利润 > 0 → 先弥补队列中的亏损 (FIFO, 5年窗口)
      3. 弥补后余额 × 税率 = 所得税

    Args:
        income_tax_rate: 企业所得税率 (如 0.25)
        loss_carryforward_years: 亏损弥补年限 (如 5)
    """

    def __init__(
        self,
        income_tax_rate: float = 0.25,
        loss_carryforward_years: int = 5,
    ) -> None:
        if not (0.0 <= income_tax_rate <= 1.0):
            raise ValueError(f"所得税率({income_tax_rate})必须在[0,1]范围内")
        if loss_carryforward_years < 0:
            raise ValueError(f"亏损弥补年限({loss_carryforward_years})不能为负")
        self._rate = income_tax_rate
        self._carryforward_years = loss_carryforward_years

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def carryforward_years(self) -> int:
        return self._carryforward_years

    def calculate(self, profit_before_tax: pd.Series) -> TaxCalcResult:
        """计算所得税, 含亏损弥补

        Args:
            profit_before_tax: 税前利润序列 (index=year, values=万元)
                正值=盈利, 负值=亏损

        Returns:
            TaxCalcResult 含 income_tax, taxable_income, loss_utilized, loss_carried
        """
        years = list(profit_before_tax.index)
        income_tax = pd.Series(0.0, index=years)
        taxable_income = pd.Series(0.0, index=years)
        loss_utilized = pd.Series(0.0, index=years)
        loss_carried = pd.Series(0.0, index=years)

        # 亏损弥补队列: [(loss_year, remaining_amount), ...]
        loss_queue: list[tuple[int, float]] = []

        for year in years:
            profit = float(profit_before_tax.loc[year])

            if profit <= 0:
                # 亏损: 记入队列, 不缴税
                if profit < 0:
                    loss_queue.append((year, -profit))
                income_tax.loc[year] = 0.0
                taxable_income.loc[year] = 0.0
                loss_utilized.loc[year] = 0.0
                loss_carried.loc[year] = sum(
                    amt for loss_yr, amt in loss_queue
                    if year - loss_yr < self._carryforward_years
                )
                continue

            # 盈利: 先弥补亏损 (FIFO + 5年窗口)
            remaining = profit
            utilized_this_year = 0.0
            new_queue: list[tuple[int, float]] = []

            for loss_year, loss_amount in loss_queue:
                years_since = year - loss_year
                if years_since > self._carryforward_years:
                    # 超过弥补期, 作废
                    continue

                if remaining <= 0:
                    # 当年利润已用完, 保留剩余亏损
                    new_queue.append((loss_year, loss_amount))
                    continue

                # 弥补: 取 min(剩余利润, 亏损余额)
                deductible = min(remaining, loss_amount)
                remaining -= deductible
                utilized_this_year += deductible
                leftover = loss_amount - deductible
                if leftover > 0:
                    new_queue.append((loss_year, leftover))

            loss_queue = new_queue

            # 应纳税所得额 = 弥补后剩余利润
            taxable = max(remaining, 0.0)
            taxable_income.loc[year] = taxable
            income_tax.loc[year] = taxable * self._rate
            loss_utilized.loc[year] = utilized_this_year
            loss_carried.loc[year] = sum(amt for _, amt in loss_queue)

        return TaxCalcResult(
            income_tax=income_tax,
            taxable_income=taxable_income,
            loss_utilized=loss_utilized,
            loss_carried=loss_carried,
        )
