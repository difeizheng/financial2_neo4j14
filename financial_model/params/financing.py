"""
融资参数模型 — 股债结构、贷款条款、还款计划

对照 Excel 参数输入表 rows 223-377:
  - 权益融资 (rows 223-246): 资本金比例/金额、分红比例、盈余公积金
  - 债务融资 (rows 247-377): 贷款利率、还款方式、还款期限、短期借款

融资结构:
  项目动态总投资 = 静态投资 + 价差预备费 + 建设期利息
    ├─ 资本金 (Equity): 股东出资, 比例通常 20-30%
    │   ├─ 用于建设投资
    │   ├─ 用于建设期利息
    │   └─ 用于流动资金
    └─ 债务资金 (Debt): 银行贷款
        ├─ 长期贷款 (主力): 固定利率, 定期还本付息
        ├─ 短期贷款 (周转): 运营期年度借还
        └─ 流动资金贷款

还款方式:
  - 等额本金 (Equal Principal): 每期还本金固定, 利息递减
  - 等额本息 (Equal Installment): 每期总还款固定, 本金递增
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import NamedTuple


class RepaymentMethod(str, Enum):
    """还款方式"""

    EQUAL_PRINCIPAL = "equal_principal"  # 等额本金
    EQUAL_INSTALLMENT = "equal_installment"  # 等额本息


class EquityInputMode(str, Enum):
    """资本金输入方式"""

    BY_AMOUNT = "by_amount"  # 按金额输入 (Row 225)
    BY_RATIO = "by_ratio"  # 按比例输入 (Row 237)


class RepaymentFrequency(str, Enum):
    """还款频率"""

    ANNUAL = "annual"  # 年度还款 (默认)
    SEMI_ANNUAL = "semi_annual"  # 半年度
    QUARTERLY = "quarterly"  # 季度
    MONTHLY = "monthly"  # 月度


class EquityInjection(NamedTuple):
    """资本金到账计划项

    Attributes:
        period_label: 期间标签 (如 "2023-03", "2024-12")
        amount: 到账金额 (万元)
    """

    period_label: str
    amount: float


class ConstructionPeriod(NamedTuple):
    """建设期计息期间 — 对应 Excel 的不规则时间分段

    Excel v17 使用 10 个不规则期间 (2023-03→2030-07),
    而非 8 个日历年度, 因此利息计算需按期间逐段累加。

    Attributes:
        period_start: 期间起始日期
        period_end: 期间结束日期
        months: 期间月份数 (round(days/30))
        equity_amount: 该期间资本金到账金额 (万元)
    """

    period_start: date
    period_end: date
    months: int
    equity_amount: float


@dataclass(frozen=True)
class LoanTerms:
    """长期贷款条款

    Attributes:
        annual_rate: 年利率 (如 0.043 = 4.3%)
        repayment_term_years: 还款期限 (年, 如 15)
        repayment_method: 还款方式 (等额本金/等额本息)
        repayment_frequency: 还款频率 (年/半年/季/月)
        grace_period_days: 宽限期 (天, 如 91)
    """

    annual_rate: float = 0.043  # 长期贷款利率 (Row 252)
    repayment_term_years: int = 15  # 还款期限 (Row 256)
    repayment_method: RepaymentMethod = RepaymentMethod.EQUAL_INSTALLMENT
    repayment_frequency: RepaymentFrequency = RepaymentFrequency.ANNUAL
    grace_period_days: int = 91  # 宽限期 (Row 249)

    def __post_init__(self) -> None:
        if self.annual_rate < 0:
            raise ValueError(f"年利率({self.annual_rate})不能为负数")
        if self.repayment_term_years < 1:
            raise ValueError(
                f"还款期限({self.repayment_term_years}年)不能小于1年"
            )
        if self.repayment_term_years > 50:
            raise ValueError(
                f"还款期限({self.repayment_term_years}年)不能大于50年"
            )

    @property
    def periods_per_year(self) -> int:
        """每年还款期数"""
        return {
            RepaymentFrequency.ANNUAL: 1,
            RepaymentFrequency.SEMI_ANNUAL: 2,
            RepaymentFrequency.QUARTERLY: 4,
            RepaymentFrequency.MONTHLY: 12,
        }[self.repayment_frequency]

    @property
    def period_rate(self) -> float:
        """每期利率 = 年利率 / 每年还款期数"""
        return self.annual_rate / self.periods_per_year

    @property
    def total_periods(self) -> int:
        """总还款期数 = 还款年数 × 每年还款期数"""
        return self.repayment_term_years * self.periods_per_year


@dataclass(frozen=True)
class FinancingParams:
    """融资参数 — 控制股债结构和还款计划

    Attributes:
        equity_input_mode: 资本金输入方式 (按金额/按比例)
        equity_ratio: 资本金比例 (当 mode=BY_RATIO 时使用, 默认 25%)
        equity_injections: 资本金到账计划 (当 mode=BY_AMOUNT 时使用)
        construction_interest_rate: 建设期利息贷款利率 (Row 251)
        long_term_loan: 长期贷款条款
        short_term_loan_rate: 短期贷款利率 (Row 253)
        short_term_borrowing: 各运营年度短期借款额 (万元)
        working_capital_equity_share: 流动资金中资本金占比 (Row 239)
        registered_capital: 项目公司注册资本 (Row 240)
        shareholding_ratio: 持股比例 (Row 241)
        dividend_payout_ratio: 分红比例 (Row 242)
        statutory_reserve_limit: 法定盈余公积金提取上限 (Row 244)
        statutory_reserve_ratio: 法定盈余公积金提取比例 (Row 245)
        discretionary_reserve_ratio: 任意盈余公积金提取比例 (Row 246)
    """

    # 资本金配置
    equity_input_mode: EquityInputMode = EquityInputMode.BY_RATIO
    equity_ratio: float = 0.25  # Row 237 (资本金比例)
    debt_ratio: float = 0.75  # 1 - equity_ratio (主要计息债务比例)
    equity_injections: tuple[EquityInjection, ...] = ()

    # 子期间建设投资 (与 equity_injections 一一对应)
    # 非空时使用精确值, 否则按月数比例分配
    period_investments: tuple[float, ...] = ()

    # 贷款利率
    construction_interest_rate: float = 0.043  # Row 251
    long_term_loan: LoanTerms = field(default_factory=LoanTerms)
    short_term_loan_rate: float = 0.0365  # Row 253

    # 短期借款计划 (key=运营年序号, value=金额)
    short_term_borrowing: tuple[float, ...] = ()

    # 流动资金配置
    working_capital_equity_share: float = 0.3  # Row 239

    # 公司治理参数
    registered_capital: float = 10000.0  # Row 240
    shareholding_ratio: float = 0.7  # Row 241
    dividend_payout_ratio: float = 0.6  # Row 242
    statutory_reserve_limit: float = 5000.0  # Row 244
    statutory_reserve_ratio: float = 0.1  # Row 245
    discretionary_reserve_ratio: float = 0.05  # Row 246

    def __post_init__(self) -> None:
        if self.equity_input_mode == EquityInputMode.BY_RATIO:
            if not (0 < self.equity_ratio <= 1):
                raise ValueError(
                    f"资本金比例({self.equity_ratio})必须在(0,1]范围内"
                )
        if self.construction_interest_rate < 0:
            raise ValueError("建设期利息贷款利率不能为负数")
        if self.short_term_loan_rate < 0:
            raise ValueError("短期贷款利率不能为负数")
        if not (0 <= self.working_capital_equity_share <= 1):
            raise ValueError("流动资金中资本金占比必须在[0,1]范围内")
        if not (0 <= self.dividend_payout_ratio <= 1):
            raise ValueError("分红比例必须在[0,1]范围内")

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def total_equity_by_amount(self) -> float:
        """按金额模式: 资本金总额 = 各期到账之和"""
        if self.equity_input_mode != EquityInputMode.BY_AMOUNT:
            raise ValueError("仅在 BY_AMOUNT 模式下可用")
        return sum(ei.amount for ei in self.equity_injections)

    def equity_amount(self, dynamic_total_investment: float) -> float:
        """计算资本金总额

        Args:
            dynamic_total_investment: 项目动态总投资 (万元)

        Returns:
            资本金总额 (万元)
        """
        if self.equity_input_mode == EquityInputMode.BY_RATIO:
            return dynamic_total_investment * self.equity_ratio
        return self.total_equity_by_amount

    def debt_amount(self, dynamic_total_investment: float) -> float:
        """计算债务资金总额 = 动态总投资 - 资本金"""
        return dynamic_total_investment - self.equity_amount(
            dynamic_total_investment
        )

    # ── 便利方法 ────────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls) -> FinancingParams:
        """从 Excel v17 模型创建参数实例 (黄金基准)

        v17 模型使用按金额输入模式:
          总资本金 = 199,000 万元
          到账时间点: 2023-03, 2023-12, ..., 2030-07
        """
        return FinancingParams(
            equity_input_mode=EquityInputMode.BY_AMOUNT,
            equity_injections=(
                EquityInjection("2023-03", 6000),
                EquityInjection("2023-12", 15000),
                EquityInjection("2024-12", 20000),
                EquityInjection("2025-12", 25000),
                EquityInjection("2026-12", 28000),
                EquityInjection("2027-12", 35000),
                EquityInjection("2028-12", 33000),
                EquityInjection("2029-08", 15000),
                EquityInjection("2029-12", 10000),
                EquityInjection("2030-07", 12000),
            ),
            # 子期间建设投资 (从投资概算明细的子期间分拆数据提取)
            # 与 equity_injections 一一对应
            period_investments=(
                28514.73,    # 2023-03 (Feb-Mar)
                66534.38,    # 2023-12 (Apr-Dec)
                92411.92,    # 2024-12 (full year)
                104888.03,   # 2025-12
                114702.85,   # 2026-12
                153329.43,   # 2027-12
                140584.94,   # 2028-12
                71022.45,    # 2029-08 (Jan-Aug)
                47348.30,    # 2029-12 (Sep-Dec)
                50636.92,    # 2030-07 (Jan-Jul)
            ),
            # 建设期利息利率: Excel v17 标注值 4.3% (参数输入表 Row 251)
            # 注意: 实际建设期利息与Excel仍有约6%偏差, 可能源于Excel内部
            # 使用不同的计息公式(如实际天数/360或期初余额不同)
            construction_interest_rate=0.043,
            long_term_loan=LoanTerms(
                annual_rate=0.043,
                repayment_term_years=15,
                repayment_method=RepaymentMethod.EQUAL_INSTALLMENT,
                repayment_frequency=RepaymentFrequency.ANNUAL,
                grace_period_days=91,
            ),
            short_term_loan_rate=0.0365,
            short_term_borrowing=tuple(0.0 for _ in range(40)),
            working_capital_equity_share=0.3,
            registered_capital=10000.0,
            shareholding_ratio=0.7,
            dividend_payout_ratio=0.6,
            statutory_reserve_limit=5000.0,
            statutory_reserve_ratio=0.1,
            discretionary_reserve_ratio=0.05,
        )

    def summary(self) -> dict[str, float | str]:
        """返回融资参数摘要"""
        result: dict[str, float | str] = {
            "equity_mode": self.equity_input_mode.value,
            "construction_interest_rate": self.construction_interest_rate,
            "long_term_rate": self.long_term_loan.annual_rate,
            "long_term_years": self.long_term_loan.repayment_term_years,
            "long_term_method": self.long_term_loan.repayment_method.value,
            "short_term_rate": self.short_term_loan_rate,
            "working_capital_equity_share": self.working_capital_equity_share,
            "dividend_payout_ratio": self.dividend_payout_ratio,
        }
        if self.equity_input_mode == EquityInputMode.BY_RATIO:
            result["equity_ratio"] = self.equity_ratio
        else:
            result["equity_total"] = self.total_equity_by_amount
            result["equity_injection_count"] = len(self.equity_injections)
        return result
