"""
时间轴生成器验证测试

用现有 Excel 模型 (v17) 的实际值作为黄金基准。
所有期望值来自 参数输入表 rows 4-28 和 时间序列表 rows 4-8。

模型参数:
  construction_start = 2023-02-01 (参数输入表!I5)
  construction_end   = 2030-07-31 (参数输入表!I7)
  operation_years    = 40         (参数输入表!I26)
"""

from __future__ import annotations

import pytest
from datetime import date

from financial_model.params.construction import ConstructionParams
from financial_model.timeline.generator import generate_timeline


# ── 黄金基准值 (来自 Excel 模型) ──────────────────────────────

# 参数输入表 dates
EXCEL_START = date(2023, 2, 1)  # I5
EXCEL_END = date(2030, 7, 31)  # I7
EXCEL_OP_YEARS = 40  # I26

# 派生日期 (Excel 公式结果)
EXPECTED_OP_START = date(2030, 8, 1)  # I27 = I7+1
EXPECTED_OP_END = date(2070, 7, 31)  # I28 = I7+40*365+INT(40/4)

# 派生月份数 (Excel ROUND(DATEDIF(...)) 结果)
EXPECTED_TOTAL_MONTHS = 90  # Row 9
EXPECTED_FIRST_YEAR_MONTHS = 11  # Row 11
EXPECTED_LAST_YEAR_MONTHS = 7  # Row 12
EXPECTED_CONSTRUCTION_YEARS = 8  # Row 4 (ROUNDUP)
EXPECTED_COMMISSIONING_YEAR_INDEX = 8  # Row 25

# 时间序列 Row 8 (SUMIF months per year) — 无里程碑版本的期望
# 注意: 当前 Excel 有里程碑 (D:2023-03-31, K:2029-08-31)
# 无里程碑时，months_by_year 应该等于 SUMIF 值
# 因为 SUMIF 是按 end_year 聚合，与里程碑无关
EXPECTED_MONTHS_BY_YEAR = {
    2023: 11,
    2024: 12,
    2025: 12,
    2026: 12,
    2027: 12,
    2028: 12,
    2029: 12,
    2030: 12,  # 7 (建设期) + 1 (8月) + 4 (9-12月)
    2031: 12,
    2032: 12,
    2033: 12,
    2034: 12,
    2035: 12,
    2036: 12,
    2037: 12,
    2038: 12,
    2039: 12,
    2040: 12,
    2041: 12,
    2042: 12,
    2043: 12,
    2044: 12,
    2045: 12,
    2046: 12,
    2047: 12,
    2048: 12,
    2049: 12,
    2050: 12,
    2051: 12,
    2052: 12,
    2053: 12,
    2054: 12,
    2055: 12,
    2056: 12,
    2057: 12,
    2058: 12,
    2059: 12,
    2060: 12,
    2061: 12,
    2062: 12,
    2063: 12,
    2064: 12,
    2065: 12,
    2066: 12,
    2067: 12,
    2068: 12,
    2069: 12,
    2070: 7,  # 运营期末年 (1月-7月)
}

# Excel 时间序列 Row 4 端点 (含里程碑版, 共54个端点 B-BC)
# 不含里程碑的端点 (51个)
EXPECTED_ENDPOINTS_NO_MILESTONES = [
    date(2023, 2, 1),  # 建设期开始
    date(2023, 12, 31),
    date(2024, 12, 31),
    date(2025, 12, 31),
    date(2026, 12, 31),
    date(2027, 12, 31),
    date(2028, 12, 31),
    date(2029, 12, 31),
    date(2030, 7, 31),  # 建设期结束
    date(2030, 8, 31),  # 运营期首月末
    date(2030, 12, 31),
    date(2031, 12, 31),
    date(2032, 12, 31),
    date(2033, 12, 31),
    date(2034, 12, 31),
    date(2035, 12, 31),
    date(2036, 12, 31),
    date(2037, 12, 31),
    date(2038, 12, 31),
    date(2039, 12, 31),
    date(2040, 12, 31),
    date(2041, 12, 31),
    date(2042, 12, 31),
    date(2043, 12, 31),
    date(2044, 12, 31),
    date(2045, 12, 31),
    date(2046, 12, 31),
    date(2047, 12, 31),
    date(2048, 12, 31),
    date(2049, 12, 31),
    date(2050, 12, 31),
    date(2051, 12, 31),
    date(2052, 12, 31),
    date(2053, 12, 31),
    date(2054, 12, 31),
    date(2055, 12, 31),
    date(2056, 12, 31),
    date(2057, 12, 31),
    date(2058, 12, 31),
    date(2059, 12, 31),
    date(2060, 12, 31),
    date(2061, 12, 31),
    date(2062, 12, 31),
    date(2063, 12, 31),
    date(2064, 12, 31),
    date(2065, 12, 31),
    date(2066, 12, 31),
    date(2067, 12, 31),
    date(2068, 12, 31),
    date(2069, 12, 31),
    date(2070, 7, 31),  # 运营期结束
]


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def params() -> ConstructionParams:
    """标准 v17 模型参数"""
    return ConstructionParams(
        construction_start=EXCEL_START,
        construction_end=EXCEL_END,
        operation_years=EXCEL_OP_YEARS,
    )


@pytest.fixture
def timeline(params: ConstructionParams):
    """标准 v17 模型时间轴 (无里程碑)"""
    return generate_timeline(params)


# ── 测试: ConstructionParams 派生值 ──────────────────────────


class TestConstructionParams:
    """验证 ConstructionParams 的所有派生属性与 Excel 一致"""

    def test_construction_months(self, params: ConstructionParams):
        """Row 9: =ROUND(DATEDIF(I5,I7,"D")/365*12,0) = 90"""
        assert params.construction_months == EXPECTED_TOTAL_MONTHS

    def test_construction_years(self, params: ConstructionParams):
        """Row 4: =ROUNDUP(I10,0) = 8"""
        assert params.construction_years == EXPECTED_CONSTRUCTION_YEARS

    def test_first_year_months(self, params: ConstructionParams):
        """Row 11: =ROUND(DATEDIF(I5,I6,"D")/365*12,0) = 11"""
        assert params.first_year_months == EXPECTED_FIRST_YEAR_MONTHS

    def test_last_year_months(self, params: ConstructionParams):
        """Row 12: =ROUND(DATEDIF(I8,I7,"D")/365*12,0) = 7"""
        assert params.last_year_months == EXPECTED_LAST_YEAR_MONTHS

    def test_operation_start(self, params: ConstructionParams):
        """Row 27: =I7+1 = 2030-08-01"""
        assert params.operation_start == EXPECTED_OP_START

    def test_operation_end(self, params: ConstructionParams):
        """Row 28: =I7+I26*365+INT(I26/4) = 2070-07-31"""
        assert params.operation_end == EXPECTED_OP_END

    def test_commissioning_year_index(self, params: ConstructionParams):
        """Row 25: =YEAR(I27)-YEAR(I5)+1 = 8"""
        assert params.commissioning_start_year_index == EXPECTED_COMMISSIONING_YEAR_INDEX

    def test_summary(self, params: ConstructionParams):
        """summary() 包含所有关键值"""
        s = params.summary()
        assert s["construction_months"] == 90
        assert s["construction_years"] == 8
        assert s["operation_years"] == 40

    def test_invalid_date_order(self):
        """结束日期早于起始日期应报错"""
        with pytest.raises(ValueError, match="必须晚于"):
            ConstructionParams(
                construction_start=date(2030, 1, 1),
                construction_end=date(2023, 1, 1),
            )

    def test_invalid_operation_years(self):
        """运营期过短应报错"""
        with pytest.raises(ValueError, match="不能小于"):
            ConstructionParams(
                construction_start=date(2023, 1, 1),
                construction_end=date(2030, 1, 1),
                operation_years=2,
            )


# ── 测试: 时间轴端点生成 ──────────────────────────────────────


class TestTimelineEndpoints:
    """验证时间轴端点与 Excel Row 4 一致"""

    def test_endpoint_count(self, timeline):
        """无里程碑时应有 51 个端点"""
        assert len(timeline.endpoints) == len(EXPECTED_ENDPOINTS_NO_MILESTONES)

    def test_endpoints_match(self, timeline):
        """端点序列与期望完全一致"""
        assert list(timeline.endpoints) == EXPECTED_ENDPOINTS_NO_MILESTONES

    def test_first_endpoint(self, timeline):
        """第一个端点 = 建设期起始"""
        assert timeline.endpoints[0] == EXCEL_START

    def test_last_endpoint(self, timeline):
        """最后一个端点 = 运营期结束"""
        assert timeline.endpoints[-1] == EXPECTED_OP_END

    def test_construction_end_in_endpoints(self, timeline):
        """建设期结束日在端点中"""
        assert EXCEL_END in timeline.endpoints

    def test_operation_first_month_end(self, timeline):
        """运营期首月末 (2030-08-31) 在端点中"""
        assert date(2030, 8, 31) in timeline.endpoints


# ── 测试: 区间 DataFrame ──────────────────────────────────────


class TestTimelineIntervals:
    """验证区间 DataFrame 结构和值"""

    def test_interval_count(self, timeline):
        """区间数 = 端点数 - 1"""
        assert timeline.total_intervals == len(timeline.endpoints) - 1

    def test_dataframe_columns(self, timeline):
        """DataFrame 有所有必要列"""
        expected_cols = {"period_start", "period_end", "months", "end_year", "phase"}
        assert set(timeline.intervals.columns) == expected_cols

    def test_construction_phase_intervals(self, timeline):
        """建设期区间: 从 start 到 construction_end"""
        cons = timeline.construction_intervals()
        assert len(cons) > 0
        assert cons["phase"].eq("construction").all()
        # 第一个区间起始 = 建设期开始
        assert cons.iloc[0]["period_start"] == EXCEL_START
        # 最后一个区间结束 = 建设期结束
        assert cons.iloc[-1]["period_end"] == EXCEL_END

    def test_operation_phase_intervals(self, timeline):
        """运营期区间: 从 construction_end+1 到 operation_end"""
        ops = timeline.operation_intervals()
        assert len(ops) > 0
        assert ops["phase"].eq("operation").all()
        # 第一个运营区间结束 >= 运营期起始
        assert ops.iloc[0]["period_end"] >= EXPECTED_OP_START
        # 最后一个区间结束 = 运营期结束
        assert ops.iloc[-1]["period_end"] == EXPECTED_OP_END

    def test_intervals_contiguous(self, timeline):
        """区间连续: period_end[i] == period_start[i+1]"""
        df = timeline.intervals
        for i in range(len(df) - 1):
            assert df.iloc[i]["period_end"] == df.iloc[i + 1]["period_start"], (
                f"区间不连续: [{i}].end={df.iloc[i]['period_end']} "
                f"!= [{i+1}].start={df.iloc[i+1]['period_start']}"
            )


# ── 测试: 按年度聚合 (SUMIF 替代) ────────────────────────────


class TestMonthsByYear:
    """验证 months_by_year 与 Excel SUMIF Row 8 一致"""

    def test_year_count(self, timeline):
        """应有 48 个年度 (2023-2070)"""
        assert len(timeline.months_by_year) == 48

    def test_year_range(self, timeline):
        """年度范围: 2023 到 2070"""
        yr = timeline.year_range
        assert yr.start == 2023
        assert yr.stop == 2071  # range.stop is exclusive

    def test_first_year(self, timeline):
        """2023年: 首年11个月 (2月-12月)"""
        assert timeline.months_in_year(2023) == 11

    def test_standard_years(self, timeline):
        """2024-2029年: 每年12个月"""
        for year in range(2024, 2030):
            assert timeline.months_in_year(year) == 12, f"{year}年月份数不为12"

    def test_transition_year(self, timeline):
        """2030年: 建设期7月 + 运营8月-12月 = 12个月"""
        assert timeline.months_in_year(2030) == 12

    def test_operation_full_years(self, timeline):
        """2031-2069年: 每年12个月"""
        for year in range(2031, 2070):
            assert timeline.months_in_year(year) == 12, f"{year}年月份数不为12"

    def test_last_year(self, timeline):
        """2070年: 运营期末年 7个月 (1月-7月)"""
        assert timeline.months_in_year(2070) == 7

    def test_all_years_match(self, timeline):
        """所有年度月份数与 Excel SUMIF 完全一致"""
        for year, expected_months in EXPECTED_MONTHS_BY_YEAR.items():
            actual = timeline.months_in_year(year)
            assert actual == expected_months, (
                f"{year}年: 期望{expected_months}个月, 实际{actual}个月"
            )

    def test_total_months_sum(self, timeline):
        """所有年度月份数之和 = 各年度月份数之和 (已含建设+运营分布)"""
        total = timeline.months_by_year.sum()
        # months_by_year 按 end_year 聚合, 建设期月份已分散到各年度中
        # 11(2023) + 12*6(2024-2029) + 12(2030) + 12*39(2031-2069) + 7(2070)
        # = 11 + 72 + 12 + 468 + 7 = 570
        assert total == 570


# ── 测试: 里程碑支持 ─────────────────────────────────────────


class TestMilestones:
    """验证里程碑端点插入"""

    def test_with_two_milestones(self, params: ConstructionParams):
        """两个里程碑 (模拟 Excel 中的 2023-03-31 和 2029-08-31)"""
        milestones = [
            date(2023, 3, 31),  # 类似 Excel D列
            date(2029, 8, 31),  # 类似 Excel K列
        ]
        tl = generate_timeline(params, milestones=milestones)

        # 有里程碑时端点更多
        assert len(tl.endpoints) == len(EXPECTED_ENDPOINTS_NO_MILESTONES) + 2

        # 里程碑端点存在
        assert date(2023, 3, 31) in tl.endpoints
        assert date(2029, 8, 31) in tl.endpoints

        # months_by_year 不受里程碑影响 (SUMIF 聚合的是年度总量)
        assert tl.months_in_year(2023) == 11
        assert tl.months_in_year(2029) == 12

    def test_invalid_milestone_outside_range(self, params: ConstructionParams):
        """里程碑在建设期外应报错"""
        with pytest.raises(ValueError, match="里程碑"):
            generate_timeline(params, milestones=[date(2020, 1, 1)])

    def test_milestone_at_boundary_fails(self, params: ConstructionParams):
        """里程碑等于起止日期应报错"""
        with pytest.raises(ValueError, match="里程碑"):
            generate_timeline(params, milestones=[EXCEL_START])


# ── 测试: 通用性 — 不同建设期长度 ─────────────────────────────


class TestGenericTimelines:
    """验证不同建设期长度下时间轴生成正确"""

    def test_short_construction_5_years(self):
        """5年建设期 (整年起止)"""
        params = ConstructionParams(
            construction_start=date(2025, 1, 1),
            construction_end=date(2029, 12, 31),
            operation_years=30,
        )
        tl = generate_timeline(params)

        assert params.construction_months == 60
        assert params.construction_years == 5
        assert tl.months_in_year(2025) == 12  # 全年
        assert tl.months_in_year(2029) == 12
        # 整年结束: operation_end 也落在 12月31日, 无部分年
        # 2029-12-31 + 30*365 + 7 = 2059-12-31
        assert tl.months_in_year(2059) == 12  # 运营期末年 = 整年

    def test_long_construction_10_years(self):
        """10年建设期"""
        params = ConstructionParams(
            construction_start=date(2020, 6, 1),
            construction_end=date(2030, 5, 31),
            operation_years=50,
        )
        tl = generate_timeline(params)

        assert params.construction_years == 10
        # 首年 (2020): 6月-12月 = 7个月
        assert tl.months_in_year(2020) == 7
        # 末年 (2030): 1月-5月 = 5个月 + 6月运营 + 7-12月
        assert tl.months_in_year(2030) == 12

    def test_construction_starting_mid_month(self):
        """月中开始的建设期"""
        params = ConstructionParams(
            construction_start=date(2024, 7, 15),
            construction_end=date(2028, 6, 30),
            operation_years=25,
        )
        tl = generate_timeline(params)

        assert params.construction_months > 0
        assert tl.total_intervals > 0
        # 所有年度月份数之和应合理
        total = tl.months_by_year.sum()
        assert total > 0

    def test_operation_end_year_consistency(self):
        """运营期结束日期的年度与 operation_years 一致"""
        params = ConstructionParams(
            construction_start=date(2022, 3, 1),
            construction_end=date(2028, 2, 28),
            operation_years=35,
        )
        tl = generate_timeline(params)

        # 运营期结束年 = 建设期结束年 + operation_years
        expected_last_year = params.construction_end.year + params.operation_years
        assert tl.operation_end.year == expected_last_year
