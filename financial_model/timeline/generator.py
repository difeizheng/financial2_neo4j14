"""
时间轴生成器 — 参数驱动的动态时间轴

核心思想: 给定建设期起止日期和运营年限，生成完整的项目时间轴。
用 pandas DataFrame + DatetimeIndex 替代 Excel 的固定列位置绑定。

对照 Excel 时间序列表 (rows 3-8):
  - Row 4 (日期端点)     → endpoints
  - Row 5 (间隔月份数)   → intervals["months"]  (ROUND(days/30))
  - Row 6 (YEAR)         → intervals["end_year"]
  - Row 7 (年度标签)     → months_by_year.index
  - Row 8 (SUMIF聚合)    → months_by_year.values
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import pandas as pd

from financial_model.params.construction import ConstructionParams


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ProjectTimeline:
    """项目时间轴 — 由 ConstructionParams 驱动，完全动态。

    Attributes:
        params: 驱动此时间轴的参数
        endpoints: 所有日期端点 (对应 Excel Row 4)
        intervals: 区间 DataFrame，每行一个区间 (period_start → period_end)
            Columns:
                period_start: date  — 区间起始日期
                period_end:   date  — 区间结束日期 (= 下一个端点)
                months:       int   — 区间月份数 (ROUND(days/30))
                end_year:     int   — 区间结束日期的日历年度
                phase:        str   — "construction" | "operation"
        months_by_year: 按年度聚合的月份数 (替代 Excel SUMIF Row 8)
            Index: year (int), Values: total months
        construction_months: 建设期总月份数
        construction_years:  建设期年数 (向上取整)
        operation_years:     运营期年数
    """

    params: ConstructionParams
    endpoints: tuple[date, ...]
    intervals: pd.DataFrame
    months_by_year: pd.Series

    construction_months: int
    construction_years: int
    operation_years: int

    # ── 便利属性 ──────────────────────────────────────────────

    @property
    def construction_start(self) -> date:
        return self.params.construction_start

    @property
    def construction_end(self) -> date:
        return self.params.construction_end

    @property
    def operation_start(self) -> date:
        return self.params.operation_start

    @property
    def operation_end(self) -> date:
        return self.params.operation_end

    @property
    def total_intervals(self) -> int:
        return len(self.intervals)

    @property
    def total_years(self) -> int:
        """项目总跨度年数 (含建设+运营)"""
        return len(self.months_by_year)

    @property
    def year_range(self) -> range:
        """年度范围 (如 2023..2070)"""
        idx = self.months_by_year.index
        return range(int(idx.min()), int(idx.max()) + 1)

    def months_in_year(self, year: int) -> int:
        """指定年度的总月份数"""
        return int(self.months_by_year.get(year, 0))

    def construction_intervals(self) -> pd.DataFrame:
        """仅建设期的区间"""
        return self.intervals[self.intervals["phase"] == "construction"].copy()

    def operation_intervals(self) -> pd.DataFrame:
        """仅运营期的区间"""
        return self.intervals[self.intervals["phase"] == "operation"].copy()


# ── 生成函数 ──────────────────────────────────────────────────


def generate_timeline(
    params: ConstructionParams,
    milestones: Sequence[date] = (),
) -> ProjectTimeline:
    """
    从参数生成完整的项目时间轴。

    Args:
        params: 建设期核心参数
        milestones: 建设期里程碑日期 (可选, 对应 Excel 中的自定义时间点)
            里程碑会作为额外端点插入建设期时间轴，不影响运营期。

    Returns:
        ProjectTimeline 完整时间轴

    Raises:
        ValueError: 里程碑日期不在建设期范围内

    Examples:
        >>> from datetime import date
        >>> params = ConstructionParams(
        ...     construction_start=date(2023, 2, 1),
        ...     construction_end=date(2030, 7, 31),
        ...     operation_years=40,
        ... )
        >>> tl = generate_timeline(params)
        >>> tl.construction_months
        90
        >>> tl.months_in_year(2023)
        11
    """
    # 验证里程碑
    validated_milestones = _validate_milestones(
        params.construction_start, params.construction_end, milestones
    )

    # 生成端点
    endpoints = _generate_endpoints(params, validated_milestones)

    # 构建区间 DataFrame
    intervals = _build_intervals(endpoints, params.operation_start)

    # 按年度聚合月份数 (替代 Excel SUMIF)
    months_by_year = _aggregate_months_by_year(intervals)

    return ProjectTimeline(
        params=params,
        endpoints=tuple(endpoints),
        intervals=intervals,
        months_by_year=months_by_year,
        construction_months=params.construction_months,
        construction_years=params.construction_years,
        operation_years=params.operation_years,
    )


# ── 内部实现 ──────────────────────────────────────────────────


def _validate_milestones(
    start: date, end: date, milestones: Sequence[date]
) -> list[date]:
    """验证里程碑日期在建设期范围内"""
    result = []
    for i, m in enumerate(milestones):
        if not (start < m < end):
            raise ValueError(
                f"里程碑{i + 1}({m})必须在建设期范围({start} < date < {end})内"
            )
        result.append(m)
    return sorted(result)


def _generate_endpoints(
    params: ConstructionParams,
    milestones: list[date],
) -> list[date]:
    """
    生成所有日期端点 (对应 Excel Row 4)。

    规则:
        建设期:
            1. construction_start (起始)
            2. 里程碑日期 (如有)
            3. 每年12月31日
            4. construction_end (结束)

        运营期:
            5. 首个运营月末 (operation_start所在月的最后一天)
            6. 每年12月31日
            7. operation_end (结束)
    """
    start = params.construction_start
    end = params.construction_end
    op_start = params.operation_start
    op_end = params.operation_end

    endpoints: list[date] = []

    # ── 建设期端点 ──
    endpoints.append(start)

    # 收集建设期内所有需要插入的日期
    construction_key_dates: set[date] = set()
    for m in milestones:
        construction_key_dates.add(m)
    for year in range(start.year, end.year + 1):
        year_end = date(year, 12, 31)
        if start < year_end < end:
            construction_key_dates.add(year_end)

    # 按时间顺序插入
    for d in sorted(construction_key_dates):
        endpoints.append(d)

    # 建设期结束
    endpoints.append(end)

    # ── 运营期端点 ──
    # 首个运营月末 (= EDATE(construction_start, total_months) - 1 的近似)
    _, last_day = monthrange(op_start.year, op_start.month)
    first_op_month_end = date(op_start.year, op_start.month, last_day)
    endpoints.append(first_op_month_end)

    # 运营期年度截止日
    for year in range(first_op_month_end.year, op_end.year + 1):
        year_end = date(year, 12, 31)
        if first_op_month_end < year_end < op_end:
            endpoints.append(year_end)

    # 运营期结束
    endpoints.append(op_end)

    # 去重 & 排序 (防止边界重复)
    seen = set()
    unique_endpoints = []
    for ep in endpoints:
        if ep not in seen:
            seen.add(ep)
            unique_endpoints.append(ep)

    return sorted(unique_endpoints)


def _build_intervals(endpoints: list[date], operation_start: date) -> pd.DataFrame:
    """
    从端点列表构建区间 DataFrame。

    每个区间 = endpoints[i] → endpoints[i+1]
    months = round(days / 30)  (与 Excel ROUND(DATEDIF(prev,this,"D")/30,0) 兼容)
    """
    records = []
    for i in range(len(endpoints) - 1):
        period_start = endpoints[i]
        period_end = endpoints[i + 1]
        days = (period_end - period_start).days
        months = round(days / 30)
        end_year = period_end.year

        # 阶段判定: 区间结束日期 >= 运营期起始 → 运营期
        phase = "operation" if period_end >= operation_start else "construction"

        records.append(
            {
                "period_start": period_start,
                "period_end": period_end,
                "months": months,
                "end_year": end_year,
                "phase": phase,
            }
        )

    return pd.DataFrame(records)


def _aggregate_months_by_year(intervals: pd.DataFrame) -> pd.Series:
    """
    按年度聚合月份数 (替代 Excel SUMIF Row 8)。

    SUMIF 逻辑: =SUMIF($C$6:$BC$6, <year>, $C$5:$BC$5)
    等价于:     months WHERE end_year == year → SUM(months)

    Returns:
        Series, index=year (int), values=total_months (int)
        确保所有年份连续无间断。
    """
    grouped = intervals.groupby("end_year")["months"].sum()

    # 确保年度连续
    year_min = int(grouped.index.min())
    year_max = int(grouped.index.max())
    full_index = range(year_min, year_max + 1)

    return grouped.reindex(full_index, fill_value=0).astype(int)
