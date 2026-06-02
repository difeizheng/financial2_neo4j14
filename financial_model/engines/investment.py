"""
投资概算引擎 — 分年度投资分配、价差预备费、投资汇总

对照 Excel 投资概算明细 (rows 4-24):
  - Rows 5-20: 各科目的分年度投资金额 (手动输入)
  - Row 21: 静态投资 = SUM(rows 5-20 per period)
  - Row 22: 价差预备费 = 各年投资 × ((1+r)^n - 1)
  - Row 23: 建设投资 = 静态投资 + 价差预备费
  - Row 24: 投资进度 = 各年建设投资 / 总建设投资

设计思路:
  投资分配计划 (allocation) 是引擎的输入参数, 不是计算结果。
  用户指定各年度、各科目的投资金额, 引擎计算汇总和派生值。
  这与 Excel 一致 — 每个预算项的分年金额是手动输入的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.investment import InvestmentParams, PriceContingencyConfig
from financial_model.params.financing import FinancingParams
from financial_model.timeline.generator import ProjectTimeline


@dataclass(frozen=True)
class InvestmentAllocation:
    """投资分配计划 — 各科目在建设期各年度的投资金额 (万元)

    这是引擎的核心输入: 每个预算科目每年花多少钱。

    Attributes:
        data: DataFrame, index=year (int), columns=科目名称, values=金额(万元)
            必须覆盖所有建设期年度。
    """

    data: pd.DataFrame

    def __post_init__(self) -> None:
        if self.data.empty:
            raise ValueError("投资分配计划不能为空")
        if self.data.index.name != "year":
            object.__setattr__(self.data.index, "name", "year")

    @property
    def years(self) -> list[int]:
        """分配涵盖的年度列表"""
        return list(self.data.index)

    @property
    def items(self) -> list[str]:
        """分配包含的科目列表"""
        return list(self.data.columns)

    def total_by_year(self) -> pd.Series:
        """各年度投资合计"""
        return self.data.sum(axis=1)

    def total_by_item(self) -> pd.Series:
        """各科目投资合计"""
        return self.data.sum(axis=0)

    def grand_total(self) -> float:
        """总投资额"""
        return float(self.data.values.sum())

    @classmethod
    def from_excel_v17(cls) -> InvestmentAllocation:
        """从 Excel v17 模型创建投资分配 (黄金基准)

        数据来源: 投资概算明细 rows 5-20 时间序列
        年度金额 = 年度 key 对应的值 (含里程碑拆分后的年度合计)
        """
        # 各科目分年度投资金额 (万元)
        # 数据来自 投资概算明细 时间序列, 按年度聚合
        # 年度值 = year key 对应的值 (已含里程碑分拆)
        records = {
            "year": [2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
            "施工辅助工程": [
                10295.579 + 4412.391,  # 2023 + 2023-03
                19706.12,
                15924.17,
                5522.19,
                1601.06,
                1555.86,
                1019.82 + 1529.73,  # 2029 + 2029-08
                1425.66,
            ],
            "建筑工程": [
                41657.469 + 17853.201,
                46778.98,
                55122.11,
                67791.02,
                52983.10,
                36031.24,
                4668.172 + 7002.258,
                1348.86,
            ],
            "环境保护和水土保持专项工程": [
                0.0,  # 2023年无数据
                3489.31,
                3489.31,
                2863.02,
                2415.68,
                2505.15,
                501.028 + 751.542,
                1878.86,
            ],
            "机电设备安装工程": [
                0.0,
                0.0,
                3243.59,
                3243.59,
                19461.57,
                13315.82,
                8194.344 + 12291.516,
                8535.77,
            ],
            "金属结构设备安装工程": [
                0.0,
                0.0,
                0.0,
                5922.78,
                36721.25,
                47777.11,
                19426.728 + 29140.092,
                18952.91,
            ],
            "建设征地和移民安置补偿费用": [
                1628.025 + 697.725,
                2325.75,
                332.26,
                193.82,
                0.0,
                0.0,
                11.076 + 16.614,
                0.0,
            ],
            "独立费用": [
                10055.668 + 4309.572,
                14510.03,
                17514.68,
                16533.47,
                17305.88,
                17315.25,
                5866.068 + 8799.102,
                10646.17,
            ],
            "基本预备费": [
                2547.636 + 1091.844,
                3374.56,
                4507.70,
                5248.71,
                7941.96,
                6618.49,
                2033.496 + 3050.244,
                1782.66,
            ],
            "储能投资": [
                350.0 + 150.0,
                800.0,
                900.0,
                600.0,
                1100.0,
                1000.0,
                240.0 + 360.0,
                500.0,
            ],
            "价差预备费": [
                0.0,  # 首年无价差预备费
                1427.17,
                3854.21,
                6784.25,
                13798.93,
                14466.02,
                5387.568 + 8081.352,
                5566.03,
            ],
        }

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        return cls(data=df)


class InvestmentEngine(BaseEngine):
    """投资概算引擎

    输入:
      - InvestmentParams (预算科目总金额、费率)
      - ProjectTimeline (建设期年度)
      - InvestmentAllocation (分年度、分科目投资分配)

    输出 DataFrame (index=year):
      - 各科目分年度投资
      - 静态投资 (分年度)
      - 价差预备费 (分年度)
      - 建设投资 (分年度)
      - 投资进度 (累计比例)

    注意: 机电设备采购和金属结构采购在 v17 模型中为 0,
    引擎不单独处理, 而是通过 allocation 数据隐含。
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        allocation: InvestmentAllocation,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._allocation = allocation

    @property
    def name(self) -> str:
        return "investment"

    def calculate(self) -> pd.DataFrame:
        """执行投资概算计算

        Returns:
            DataFrame, index=year, columns:
              - 各科目名称: 分年度投资 (含 '价差预备费' 如已在 allocation 中)
              - 'static_investment': 静态投资 (不含价差预备费的科目之和)
              - 'price_contingency': 价差预备费 (来自 allocation 或引擎计算)
              - 'construction_investment': 建设投资 = 静态投资 + 价差预备费
              - 'investment_progress': 投资进度 (年度比例)
        """
        # 1. 获取分年度投资分配
        alloc = self._allocation.data.copy()

        # 2. 分离价差预备费 (如果已在 allocation 中)
        if "价差预备费" in alloc.columns:
            alloc["price_contingency"] = alloc.pop("价差预备费")
        else:
            # 引擎根据费率计算
            alloc["price_contingency"] = self._calculate_price_contingency(
                alloc.sum(axis=1)
            )

        # 3. 静态投资 = 剩余科目之和 (不含价差预备费)
        alloc["static_investment"] = alloc.drop(
            columns=["price_contingency"], errors="ignore"
        ).sum(axis=1)

        # 4. 建设投资 = 静态投资 + 价差预备费
        alloc["construction_investment"] = (
            alloc["static_investment"] + alloc["price_contingency"]
        )

        # 5. 投资进度 = 各年建设投资 / 总建设投资
        total_construction = alloc["construction_investment"].sum()
        alloc["investment_progress"] = (
            alloc["construction_investment"] / total_construction
            if total_construction > 0
            else 0.0
        )

        return alloc

    def _calculate_price_contingency(
        self, static_investment: pd.Series
    ) -> pd.Series:
        """计算价差预备费

        公式: price_contingency[year] = investment[year] * ((1+r)^n - 1)
        其中:
          - r = 年物价上涨率
          - n = year - base_year + 1
          - base_year = 建设起始年

        注意: 首年 (n=1) 的价差预备费为 0, 因为 (1+r)^1 - 1 = r,
        但 Excel v17 的实现中首年为 0 (可能是因为基准年为起始年)。
        """
        rate = self._investment.price_contingency.price_escalation_rate
        if rate == 0.0:
            return pd.Series(0.0, index=static_investment.index)

        base_year = self._construction.construction_start.year
        result = {}
        for year, inv in static_investment.items():
            n = int(year) - base_year + 1
            if n <= 1:
                # 首年 (基准年) 无价差预备费
                result[year] = 0.0
            else:
                escalation = (1 + rate) ** n - 1
                result[year] = inv * escalation

        return pd.Series(result, index=static_investment.index)

    def validate_inputs(self) -> list[str]:
        """验证输入"""
        warnings = super().validate_inputs()

        # 检查分配数据覆盖建设期
        alloc_years = set(self._allocation.years)
        construction_years = set(
            range(
                self._construction.construction_start.year,
                self._construction.construction_end.year + 1,
            )
        )

        missing = construction_years - alloc_years
        if missing:
            warnings.append(
                f"投资分配缺少建设期年度: {sorted(missing)}"
            )

        extra = alloc_years - construction_years
        if extra:
            warnings.append(
                f"投资分配包含非建设期年度: {sorted(extra)}"
            )

        return warnings

    # ── 便利方法 ────────────────────────────────────────────

    @classmethod
    def from_excel_v17(
        cls,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
    ) -> InvestmentEngine:
        """从 Excel v17 模型创建引擎 (黄金基准)"""
        allocation = InvestmentAllocation.from_excel_v17()
        return cls(
            params_construction=params_construction,
            params_investment=params_investment,
            params_financing=params_financing,
            timeline=timeline,
            allocation=allocation,
        )

    def summary(self) -> dict[str, float]:
        """返回计算结果摘要 (需先 calculate)"""
        result = self.calculate()
        return {
            "static_investment": float(result["static_investment"].sum()),
            "price_contingency": float(result["price_contingency"].sum()),
            "construction_investment": float(
                result["construction_investment"].sum()
            ),
        }
