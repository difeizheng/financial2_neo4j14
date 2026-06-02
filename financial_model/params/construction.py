"""
建设期参数模型 — 模型的时间种子

这三个参数（起始日期、结束日期、运营年限）决定了整个模型的时间框架。
所有其他时间相关值（月份数、年度、运营期起止等）均为派生属性。

对照 Excel 参数输入表 工程计划 category (rows 4-32):
  - Row 5: 建设期起始日期 (input)  → construction_start
  - Row 7: 建设期结束日期 (input)  → construction_end
  - Row 26: 运营期 (input)         → operation_years
  - Row 4/9/10/11/12/25/27/28:     → @property 派生
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ConstructionParams:
    """建设期核心参数 — 改变这三个值，整个模型的时间框架自动适配。"""

    construction_start: date  # 建设期起始日期 (Row 5, 参数输入表!I5)
    construction_end: date  # 建设期结束日期 (Row 7, 参数输入表!I7)
    operation_years: int = 40  # 运营期年限 (Row 26, 参数输入表!I26)

    def __post_init__(self) -> None:
        """跨字段验证"""
        if self.construction_end <= self.construction_start:
            raise ValueError(
                f"建设期结束日期({self.construction_end}) "
                f"必须晚于起始日期({self.construction_start})"
            )
        if self.operation_years < 5:
            raise ValueError(f"运营期年限({self.operation_years})不能小于5年")
        if self.operation_years > 80:
            raise ValueError(f"运营期年限({self.operation_years})不能大于80年")

    # ── 派生日期 (与 Excel 公式对应) ──────────────────────────

    @property
    def first_year_end(self) -> date:
        """建设期首年年末 (Row 6, 参数输入表!I6)
        固定为建设起始年的12月31日。
        """
        return date(self.construction_start.year, 12, 31)

    @property
    def last_year_start(self) -> date:
        """建设期末年年初 (Row 8, 参数输入表!I8)
        固定为建设结束年的1月1日。
        """
        return date(self.construction_end.year, 1, 1)

    @property
    def operation_start(self) -> date:
        """运营期起始日期 (Row 27, =I7+1)"""
        return self.construction_end + timedelta(days=1)

    @property
    def operation_end(self) -> date:
        """运营期结束日期 (Row 28, =I7+I26*365+INT(I26/4))

        与 Excel 公式兼容的闰年近似: INT(years/4) 个闰日。
        对于 20-50 年范围，精度足够。
        """
        days_to_add = self.operation_years * 365 + self.operation_years // 4
        return self.construction_end + timedelta(days=days_to_add)

    # ── 派生月份数 (与 Excel ROUND(DATEDIF(...)) 对应) ──────

    @property
    def construction_months(self) -> int:
        """建设期总月份数 (Row 9, =ROUND(DATEDIF(I5,I7,"D")/365*12,0))"""
        days = (self.construction_end - self.construction_start).days
        return round(days / 365 * 12)

    @property
    def construction_months_as_years(self) -> float:
        """建设期月份数转化为年 (Row 10, =I9/12)"""
        return self.construction_months / 12

    @property
    def construction_years(self) -> int:
        """建设期年数，向上取整 (Row 4, =ROUNDUP(I10,0))"""
        return math.ceil(self.construction_months_as_years)

    @property
    def first_year_months(self) -> int:
        """建设期首年月份 (Row 11, =ROUND(DATEDIF(I5,I6,"D")/365*12,0))"""
        days = (self.first_year_end - self.construction_start).days
        return round(days / 365 * 12)

    @property
    def last_year_months(self) -> int:
        """建设期末年月份 (Row 12, =ROUND(DATEDIF(I8,I7,"D")/365*12,0))"""
        days = (self.construction_end - self.last_year_start).days
        return round(days / 365 * 12)

    @property
    def commissioning_start_year_index(self) -> int:
        """投产起始年份序号 (Row 25, =YEAR(I27)-YEAR(I5)+1)"""
        return self.operation_start.year - self.construction_start.year + 1

    # ── 便利方法 ─────────────────────────────────────────────

    def summary(self) -> dict[str, int | str | float]:
        """返回所有派生值的摘要字典"""
        return {
            "construction_start": str(self.construction_start),
            "construction_end": str(self.construction_end),
            "operation_start": str(self.operation_start),
            "operation_end": str(self.operation_end),
            "construction_months": self.construction_months,
            "construction_years": self.construction_years,
            "first_year_months": self.first_year_months,
            "last_year_months": self.last_year_months,
            "operation_years": self.operation_years,
            "commissioning_year_index": self.commissioning_start_year_index,
        }
