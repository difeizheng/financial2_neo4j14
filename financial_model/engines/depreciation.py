"""
折旧摊销引擎 — 5类资产的直线法折旧/摊销计算

对照 Excel 表2-折旧摊销表:
  - Rows 18-24: 各类资产年度折旧/摊销额
  - 固定资产: (原值×(1-残值率))/29年 × 达产比例
  - 无形资产: (80000×0.95)/18年 × 达产比例
  - 长期待摊: 每5年一笔458.72, 分5年摊完
  - 储能资产: (6000×0.95)/10年 × 达产比例

折旧仅在运营期发生, 建设期为0。
达产比例影响折旧: 部分投产年份按比例折旧。
"""

from __future__ import annotations

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.depreciation import DepreciationParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.operating import OperatingParams
from financial_model.timeline.generator import ProjectTimeline


class DepreciationEngine(BaseEngine):
    """折旧摊销引擎

    输入:
      - DepreciationParams (5类资产参数)
      - OperatingParams (达产比例序列)
      - ProjectTimeline (运营期年度)

    输出 DataFrame (index=year, 全项目年度):
      - fixed_depreciation: 固定资产折旧
      - intangible_amortization: 无形资产摊销
      - long_term_prepaid: 长期待摊费用摊销
      - energy_storage_depreciation: 储能资产折旧
      - overhaul_amortization: 大修及更新重置摊销
      - total_depreciation: 折旧/摊销合计
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        depreciation_params: DepreciationParams | None = None,
        operating_params: OperatingParams | None = None,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._depreciation = depreciation_params or DepreciationParams()
        self._operating = operating_params or OperatingParams()

    @property
    def name(self) -> str:
        return "depreciation"

    def calculate(self) -> pd.DataFrame:
        """执行折旧/摊销计算

        Returns:
            DataFrame, index=year (建设起始年..运营结束年)
        """
        years = list(self._timeline.year_range)
        construction_end_year = self._construction.construction_end.year

        # 达产比例序列
        ratios = self._get_production_ratios(len(years))

        records = []
        for i, year in enumerate(years):
            ratio = ratios[i]
            is_operation = year > construction_end_year or (
                year == construction_end_year and ratio > 0
            )

            if not is_operation:
                # 建设期无折旧
                records.append(self._zero_record(year))
                continue

            # 固定资产折旧
            fixed = self._depreciation.fixed_assets.annual_depreciation * ratio

            # 无形资产摊销
            intangible = (
                self._depreciation.intangible_assets.annual_depreciation * ratio
            )

            # 储能资产折旧
            storage = (
                self._depreciation.energy_storage_assets.annual_depreciation * ratio
            )

            # 长期待摊费用摊销 (每N年一笔)
            ltp = self._calculate_long_term_prepaid(year, construction_end_year)

            # 大修及更新重置
            overhaul = (
                self._depreciation.overhaul_assets.annual_depreciation * ratio
            )

            total = fixed + intangible + ltp + storage + overhaul

            records.append(
                {
                    "year": year,
                    "production_ratio": ratio,
                    "fixed_depreciation": fixed,
                    "intangible_amortization": intangible,
                    "long_term_prepaid": ltp,
                    "energy_storage_depreciation": storage,
                    "overhaul_amortization": overhaul,
                    "total_depreciation": total,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        return df

    def _get_production_ratios(self, total_years: int) -> list[float]:
        """获取达产比例序列"""
        if self._operating.production_ratios:
            ratios = list(self._operating.production_ratios)
            # 如果序列长度不足, 用最后值填充
            while len(ratios) < total_years:
                ratios.append(ratios[-1] if ratios else 0.0)
            return ratios[:total_years]
        # 默认: 建设期0, 运营期全满
        construction_years = self._construction.construction_years
        return [0.0] * construction_years + [1.0] * (total_years - construction_years)

    def _calculate_long_term_prepaid(
        self, year: int, construction_end_year: int
    ) -> float:
        """计算长期待摊费用当年摊销额

        规则: 从运营期开始, 每5年产生一笔新费用, 每笔分5年摊完。
        因此运营期任意年份, 最多有1笔待摊费用在摊销。
        """
        cycle = self._depreciation.long_term_prepaid_cycle
        amort_years = self._depreciation.long_term_prepaid_period
        amount = self._depreciation.long_term_prepaid_amount

        if amount <= 0 or amort_years <= 0:
            return 0.0

        # 运营年开始偏移 (0-based)
        op_year_offset = year - construction_end_year - 1
        if op_year_offset < 0:
            return 0.0

        # 检查当前年份是否有待摊费用在摊销
        # 每5年产生一笔: 在offset 0, 5, 10, 15, 20, ...
        # 每笔持续5年: offset 0-4受第1笔, 5-9受第2笔, ...
        cycle_index = op_year_offset // cycle
        years_into_cycle = op_year_offset % cycle

        if years_into_cycle < amort_years:
            return amount / amort_years
        return 0.0

    def _zero_record(self, year: int) -> dict:
        """建设期的零值记录"""
        return {
            "year": year,
            "production_ratio": 0.0,
            "fixed_depreciation": 0.0,
            "intangible_amortization": 0.0,
            "long_term_prepaid": 0.0,
            "energy_storage_depreciation": 0.0,
            "overhaul_amortization": 0.0,
            "total_depreciation": 0.0,
        }
