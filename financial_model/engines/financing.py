"""
融资引擎 — 建设期利息资本化 + 股债分配 + 长期贷款还本付息

对照 Excel 表1-资金筹措及还本付息表:
  - Rows 7-9: 动态总投资、工程总投资、建设期利息
  - Rows 10-21: 各时段建设期利息 (每笔借款的利息累计)
  - Rows 35-44: 资金来源 (资本金/债务) 及其用途分解
  - Rows 133-160: 年度等额本息还款计划
  - Rows 163-170: 长期借款汇总

设计思路:
  融资引擎是所有引擎中最复杂的, 因为建设期利息是迭代计算:
  1. 每个时段有新借款到账 → 增加借款余额
  2. 借款余额产生利息 → 利息资本化 → 进一步增加借款余额
  3. 直到建设期结束 → 确定最终借款余额 (本金)
  4. 进入运营期 → 按还款方式还本付息

  股债分配: 建设投资按资本金比例分配, 建设期利息同理。
"""

from __future__ import annotations

import math
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.engines.investment import InvestmentAllocation, InvestmentEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.financing import (
    ConstructionPeriod,
    EquityInputMode,
    FinancingParams,
    RepaymentMethod,
)
from financial_model.timeline.generator import ProjectTimeline


@dataclass(frozen=True)
class FinancingResult:
    """融资引擎计算结果

    Attributes:
        annual_summary: 年度汇总 DataFrame (index=year)
            Columns: construction_investment, construction_interest,
                     equity_for_construction, equity_for_interest,
                     debt_for_construction, debt_for_interest,
                     total_equity, total_debt, dynamic_total_investment
        loan_schedule: 还款计划 DataFrame (index=period_number)
            Columns: opening_balance, borrowing, repayment,
                     principal_repayment, interest_payment, closing_balance
        construction_interest_total: 建设期利息总额
        dynamic_total_investment: 动态总投资 (工程/自主投资)
    """

    annual_summary: pd.DataFrame
    loan_schedule: pd.DataFrame
    construction_interest_total: float
    dynamic_total_investment: float


class FinancingEngine(BaseEngine):
    """融资引擎 — 建设期利息 + 股债分配 + 还本付息

    依赖:
      - InvestmentEngine (提供分年度建设投资)
      - FinancingParams (股债比例、利率、还款条款)
      - ProjectTimeline (建设/运营期划分)
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        investment_result: pd.DataFrame,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._investment_result = investment_result

    @property
    def name(self) -> str:
        return "financing"

    def calculate(self) -> FinancingResult:
        """执行融资计算

        Returns:
            FinancingResult 包含年度汇总和还款计划
        """
        # 0. 构建建设期计息期间 (按资本金到账计划划分)
        periods = self._build_construction_periods()

        # 1. 获取分年度建设投资
        construction_investment = self._get_annual_construction_investment()

        # 2. 计算建设期利息 (期间精确 或 年度简化)
        construction_interest = self._calculate_construction_interest(
            construction_investment, periods
        )

        # 3. 股债分配 (期间精确 或 比例简化)
        equity_debt = self._allocate_equity_debt(
            construction_investment, construction_interest, periods
        )

        # 4. 构建年度汇总
        annual = self._build_annual_summary(
            construction_investment, construction_interest, equity_debt
        )

        # 5. 计算还款计划
        loan_schedule = self._calculate_repayment_schedule(
            annual, construction_interest
        )

        # 6. 动态总投资
        ci_total = float(construction_investment.sum())
        int_total = float(construction_interest.sum())
        wc = self._investment.working_capital
        dynamic_total = ci_total + int_total + wc

        return FinancingResult(
            annual_summary=annual,
            loan_schedule=loan_schedule,
            construction_interest_total=int_total,
            dynamic_total_investment=dynamic_total,
        )

    def _get_annual_construction_investment(self) -> pd.Series:
        """从投资引擎结果获取分年度建设投资"""
        if "construction_investment" in self._investment_result.columns:
            return self._investment_result["construction_investment"]
        return self._investment_result["static_investment"]

    def _build_construction_periods(self) -> list[ConstructionPeriod]:
        """从资本金到账计划构建建设期计息期间

        逻辑:
          1. 每个 EquityInjection 的 period_label ("YYYY-MM") 定义一个期间端点
          2. 第一个期间从 construction_start 开始
          3. 后续期间从前一个端点的下一天开始
          4. 月份数 = round(days / 30) 与 Excel ROUND(DATEDIF/30) 兼容

        Returns:
            期间列表; 空列表表示使用年度简化模式
        """
        if self._financing.equity_input_mode != EquityInputMode.BY_AMOUNT:
            return []
        if not self._financing.equity_injections:
            return []

        periods: list[ConstructionPeriod] = []
        current_start = self._construction.construction_start

        for injection in self._financing.equity_injections:
            year, month = map(int, injection.period_label.split("-"))
            _, last_day = monthrange(year, month)
            period_end = date(year, month, last_day)

            days = (period_end - current_start).days
            months = round(days / 30)

            periods.append(
                ConstructionPeriod(
                    period_start=current_start,
                    period_end=period_end,
                    months=months,
                    equity_amount=injection.amount,
                )
            )
            current_start = period_end + timedelta(days=1)

        return periods

    def _calculate_construction_interest(
        self,
        construction_investment: pd.Series,
        periods: list[ConstructionPeriod],
    ) -> pd.Series:
        """计算建设期利息 — 期间精确 或 年度简化

        当 periods 非空时, 按不规则期间逐段累加 (精确匹配 Excel)。
        否则回退到年度简化模式 (向后兼容)。
        """
        rate = self._financing.construction_interest_rate
        if rate == 0.0:
            return pd.Series(0.0, index=construction_investment.index)

        if periods:
            return self._calculate_interest_by_periods(
                construction_investment, rate, periods
            )
        return self._calculate_interest_annual(construction_investment, rate)

    def _calculate_interest_by_periods(
        self,
        construction_investment: pd.Series,
        rate: float,
        periods: list[ConstructionPeriod],
    ) -> pd.Series:
        """不规则期间计息 — 年末资本化 + 实际天数/365

        Excel 计息逻辑 (表1 Row 10 公式):
          - 每期债务 = 净建设投资 × 债务比例
          - 净建设投资 = 建设投资 - 建设补贴 (Excel I214 = 859,974)
          - 时间分数 = 实际天数 / 365 (非 round(days/30)/12)
          - 同年内子期间各自计息, 但不交叉复利 (年末统一资本化)
          - 新债务半期利息 = new_debt × rate × days/365 × 0.5
          - 累计余额利息 = cumulative_debt × rate × days/365

        v4.15 改动 (Phase 7C):
          - 逐期资本化 → 年末资本化 (同年内子期间独立计息)
          - months/12 → days/365 (消除 round(days/30) 近似误差)
          - 利息偏差: +2.24% → +0.03%
        """
        period_investments = self._distribute_investment_to_periods(
            construction_investment, periods
        )

        # 扣除建设期财政补贴: Excel I214 = 859,974 vs 引擎 869,974
        total_gross = sum(period_investments)
        subsidy = self._investment.construction_subsidy
        if total_gross > 0 and subsidy > 0:
            net_ratio = (total_gross - subsidy) / total_gross
            period_investments = [inv * net_ratio for inv in period_investments]

        # Excel 固定债务比例
        debt_ratio = self._financing.debt_ratio

        # 年末资本化: 同年内子期间独立计息, 年末统一资本化
        cumulative_debt = 0.0
        interest_by_period: list[float] = []
        current_year: int | None = None
        year_interest = 0.0
        year_new_debt = 0.0

        for i, period in enumerate(periods):
            new_debt = period_investments[i] * debt_ratio
            days = (period.period_end - period.period_start).days
            days_fraction = days / 365.0

            year = period.period_end.year

            # 年末资本化: 遇到新年份时, 将上年的利息+新债加入累计
            if current_year is not None and year != current_year:
                cumulative_debt += year_new_debt + year_interest
                year_interest = 0.0
                year_new_debt = 0.0

            current_year = year

            # 期初余额利息 (cumulative_debt 在年内不变)
            interest = cumulative_debt * rate * days_fraction
            # 新增债务半期利息
            interest += new_debt * rate * days_fraction * 0.5

            interest_by_period.append(interest)
            year_interest += interest
            year_new_debt += new_debt

        # 资本化最后一年
        cumulative_debt += year_new_debt + year_interest

        # 按年聚合
        interest_by_year: dict[int, float] = {}
        for period, interest in zip(periods, interest_by_period):
            year = period.period_end.year
            interest_by_year[year] = interest_by_year.get(year, 0.0) + interest

        # 确保所有建设期年度都有值
        for year in construction_investment.index:
            if int(year) not in interest_by_year:
                interest_by_year[int(year)] = 0.0

        return pd.Series(
            {y: interest_by_year[int(y)] for y in construction_investment.index},
            index=construction_investment.index,
        )

    def _distribute_investment_to_periods(
        self,
        construction_investment: pd.Series,
        periods: list[ConstructionPeriod],
    ) -> list[float]:
        """将年度建设投资分配到各期间

        优先使用 FinancingParams.period_investments (精确值)。
        否则按月数比例分配 (近似)。
        """
        # 精确值: 从参数直接获取
        explicit = self._financing.period_investments
        if explicit and len(explicit) == len(periods):
            return list(explicit)

        # 近似: 按建设期月数比例分配
        construction_months_by_year: dict[int, int] = {}
        for period in periods:
            year = period.period_end.year
            construction_months_by_year[year] = (
                construction_months_by_year.get(year, 0) + period.months
            )

        result: list[float] = []
        for period in periods:
            year = period.period_end.year
            year_construction_months = construction_months_by_year.get(year, 0)

            if year_construction_months > 0:
                fraction = period.months / year_construction_months
            else:
                fraction = 0.0

            year_inv = float(construction_investment.get(year, 0.0))
            result.append(year_inv * fraction)

        return result

    def _calculate_interest_annual(
        self,
        construction_investment: pd.Series,
        rate: float,
    ) -> pd.Series:
        """年度简化计息 (向后兼容, 无期间数据时使用)

        简化模型:
          1. 各年度建设投资按股债比例分配
          2. 债务部分按年利率计算利息
          3. 利息资本化 (加入下一年借款余额)
        """
        equity_ratio = self._get_equity_ratio()

        cumulative_debt = 0.0
        interest_by_year: dict[int, float] = {}

        for year in construction_investment.index:
            annual_debt = float(construction_investment.loc[year]) * (
                1 - equity_ratio
            )

            interest = cumulative_debt * rate
            interest += annual_debt * rate * 0.5

            interest_by_year[int(year)] = interest
            cumulative_debt += annual_debt + interest

        return pd.Series(
            {y: interest_by_year[int(y)] for y in construction_investment.index},
            index=construction_investment.index,
        )

    def _get_equity_ratio(self) -> float:
        """获取资本金比例 (统一为比例值)"""
        if self._financing.equity_input_mode == EquityInputMode.BY_RATIO:
            return self._financing.equity_ratio
        # BY_AMOUNT 模式: 使用 25% 作为默认 (v17 实际比例)
        # 精确计算需要动态总投资, 这里先用近似值
        return 0.25

    def _allocate_equity_debt(
        self,
        construction_investment: pd.Series,
        construction_interest: pd.Series,
        periods: list[ConstructionPeriod],
    ) -> pd.DataFrame:
        """股债分配 — 期间精确 或 比例简化

        当 periods 非空时, 使用实际资本金到账金额按年汇总。
        否则回退到统一比例分配。
        """
        if periods:
            return self._allocate_equity_debt_by_periods(
                construction_investment, construction_interest, periods
            )

        # 原始逻辑: 统一比例
        equity_ratio = self._get_equity_ratio()

        equity_construction = construction_investment * equity_ratio
        debt_construction = construction_investment * (1 - equity_ratio)
        equity_interest = construction_interest * equity_ratio
        debt_interest = construction_interest * (1 - equity_ratio)

        return pd.DataFrame(
            {
                "equity_for_construction": equity_construction,
                "debt_for_construction": debt_construction,
                "equity_for_interest": equity_interest,
                "debt_for_interest": debt_interest,
            },
            index=construction_investment.index,
        )

    def _allocate_equity_debt_by_periods(
        self,
        construction_investment: pd.Series,
        construction_interest: pd.Series,
        periods: list[ConstructionPeriod],
    ) -> pd.DataFrame:
        """基于期间实际资本金的股债分配

        将各期间资本金到账金额按年汇总, 用作 equity_for_construction。
        债务 = 建设(投资 - 资本金); 利息按建设投资比例分配。
        """
        # 按年汇总资本金到账
        equity_by_year: dict[int, float] = {}
        for period in periods:
            year = period.period_end.year
            equity_by_year[year] = equity_by_year.get(year, 0.0) + period.equity_amount

        equity_construction = pd.Series(
            {y: equity_by_year.get(int(y), 0.0) for y in construction_investment.index},
            index=construction_investment.index,
        )
        debt_construction = (construction_investment - equity_construction).clip(
            lower=0
        )

        # 利息按建设投资比例分配
        total_per_year = construction_investment.replace(0, 1)
        equity_share = equity_construction / total_per_year

        equity_interest = construction_interest * equity_share
        debt_interest = construction_interest * (1 - equity_share)

        return pd.DataFrame(
            {
                "equity_for_construction": equity_construction,
                "debt_for_construction": debt_construction,
                "equity_for_interest": equity_interest,
                "debt_for_interest": debt_interest,
            },
            index=construction_investment.index,
        )

    def _build_annual_summary(
        self,
        construction_investment: pd.Series,
        construction_interest: pd.Series,
        equity_debt: pd.DataFrame,
    ) -> pd.DataFrame:
        """构建年度汇总 DataFrame"""
        df = pd.DataFrame(
            {
                "construction_investment": construction_investment,
                "construction_interest": construction_interest,
            },
            index=construction_investment.index,
        )

        # 合并股债分配
        for col in equity_debt.columns:
            df[col] = equity_debt[col]

        # 汇总
        df["total_equity"] = (
            df["equity_for_construction"] + df["equity_for_interest"]
        )
        df["total_debt"] = df["debt_for_construction"] + df["debt_for_interest"]

        return df

    def _calculate_repayment_schedule(
        self,
        annual_summary: pd.DataFrame,
        construction_interest: pd.Series,
    ) -> pd.DataFrame:
        """计算还款计划 (等额本息/等额本金)

        还款从运营期开始 (建设期结束后 + 宽限期)。
        """
        loan_terms = self._financing.long_term_loan
        rate = loan_terms.annual_rate
        term_years = loan_terms.repayment_term_years
        method = loan_terms.repayment_method

        # 总借款本金 = 建设期债务 + 建设期利息中的债务部分
        total_debt_construction = float(annual_summary["debt_for_construction"].sum())
        total_debt_interest = float(annual_summary["debt_for_interest"].sum())
        total_principal = total_debt_construction + total_debt_interest

        if total_principal <= 0:
            return pd.DataFrame(
                columns=[
                    "year",
                    "opening_balance",
                    "repayment",
                    "principal_repayment",
                    "interest_payment",
                    "closing_balance",
                ]
            )

        # 运营期起始年 — 还款从宽限期结束后开始
        # Excel: grace_period_days=91, construction_end=2030-07-31
        # → grace_end ≈ 2030-10-31, first_repayment = 2031-09-01
        op_start_year = self._construction.operation_start.year
        # 宽限期通常跨越到运营期第一年, 还款从运营期第二年开始
        grace_years = 1  # 简化: 宽限期占运营期首年
        repayment_start_year = op_start_year + grace_years

        # 还款期年度列表
        repayment_years = list(
            range(repayment_start_year, repayment_start_year + term_years)
        )

        if method == RepaymentMethod.EQUAL_INSTALLMENT:
            return self._equal_installment_schedule(
                total_principal, rate, repayment_years
            )
        else:
            return self._equal_principal_schedule(
                total_principal, rate, repayment_years
            )

    def _equal_installment_schedule(
        self,
        principal: float,
        annual_rate: float,
        years: list[int],
    ) -> pd.DataFrame:
        """等额本息还款计划

        每年还款额固定: PMT = P * r / (1 - (1+r)^-n)
        """
        n = len(years)
        if annual_rate == 0:
            pmt = principal / n
        else:
            pmt = principal * annual_rate / (1 - (1 + annual_rate) ** -n)

        records = []
        balance = principal

        for year in years:
            interest = balance * annual_rate
            principal_part = pmt - interest
            closing = balance - principal_part

            records.append(
                {
                    "year": year,
                    "opening_balance": balance,
                    "repayment": pmt,
                    "principal_repayment": principal_part,
                    "interest_payment": interest,
                    "closing_balance": max(closing, 0.0),
                }
            )
            balance = max(closing, 0.0)

        return pd.DataFrame(records)

    def _equal_principal_schedule(
        self,
        principal: float,
        annual_rate: float,
        years: list[int],
    ) -> pd.DataFrame:
        """等额本金还款计划

        每年还本金固定 = P / n, 利息递减
        """
        n = len(years)
        annual_principal = principal / n

        records = []
        balance = principal

        for year in years:
            interest = balance * annual_rate
            closing = balance - annual_principal

            records.append(
                {
                    "year": year,
                    "opening_balance": balance,
                    "repayment": annual_principal + interest,
                    "principal_repayment": annual_principal,
                    "interest_payment": interest,
                    "closing_balance": max(closing, 0.0),
                }
            )
            balance = max(closing, 0.0)

        return pd.DataFrame(records)

    def validate_inputs(self) -> list[str]:
        """验证输入"""
        warnings = super().validate_inputs()
        return warnings

    # ── 便利方法 ────────────────────────────────────────────

    @classmethod
    def from_excel_v17(
        cls,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        allocation: InvestmentAllocation,
    ) -> FinancingEngine:
        """从 Excel v17 模型创建引擎"""
        invest_engine = InvestmentEngine(
            params_construction=params_construction,
            params_investment=params_investment,
            params_financing=params_financing,
            timeline=timeline,
            allocation=allocation,
        )
        investment_result = invest_engine.calculate()

        return cls(
            params_construction=params_construction,
            params_investment=params_investment,
            params_financing=params_financing,
            timeline=timeline,
            investment_result=investment_result,
        )
