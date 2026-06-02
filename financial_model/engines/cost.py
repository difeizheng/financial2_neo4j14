"""
成本费用引擎 — 生产经营成本计算

对照 Excel 表3-成本费用表:
  - Row 8:  材料费 = 容量 × 单价 × 达产比例
  - Row 9:  抽水电费 = 抽水电量 × 抽水电价 × 达产比例
  - Row 15: 维修费 = 建设投资 × 维修费率 × 达产比例
  - Row 16: 生产人员薪酬及福利
  - Row 17: 长期待摊费用当期摊销
  - Row 18: 折旧及摊销合计

成本结构:
  生产成本 = 材料费 + 抽水电费 + 补水费 + 维修费 + 人工 + 折旧摊销
  生产经营成本 = 生产成本 + 财务费用(利息)
"""

from __future__ import annotations

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.operating import OperatingParams
from financial_model.timeline.generator import ProjectTimeline


class CostEngine(BaseEngine):
    """成本费用引擎

    输入:
      - OperatingParams (材料费、抽水电价、维修费率)
      - Depreciation 结果 (折旧/摊销)
      - Financing 结果 (贷款利息)
      - construction_investment_total (建设投资总额, 来自 InvestmentEngine)

    输出 DataFrame (index=year):
      - material_cost: 材料费
      - pump_electricity_cost: 抽水电费
      - maintenance_cost: 维修费
      - labor_cost: 生产人员薪酬
      - depreciation_total: 折旧及摊销
      - total_production_cost: 生产成本
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        operating_params: OperatingParams | None = None,
        depreciation_result: pd.DataFrame | None = None,
        loan_interest_by_year: pd.Series | None = None,
        construction_investment_total: float | None = None,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._operating = operating_params or OperatingParams()
        self._depreciation_result = depreciation_result
        self._loan_interest = loan_interest_by_year
        self._construction_investment = (
            construction_investment_total
            if construction_investment_total is not None
            else self._investment.static_investment
        )

    @property
    def name(self) -> str:
        return "cost"

    def calculate(self) -> pd.DataFrame:
        """执行成本费用计算"""
        years = list(self._timeline.year_range)
        construction_end_year = self._construction.construction_end.year

        ratios = self._get_production_ratios(len(years))

        records = []
        for i, year in enumerate(years):
            ratio = ratios[i]
            is_operation = year > construction_end_year or ratio > 0

            if not is_operation:
                records.append(self._zero_record(year))
                continue

            # 材料费: 容量(MW) × 单价(万元/MW) × 比例
            material = self._operating.installed_capacity_mw * 0.2 / 10.0 * ratio

            # 抽水电费: 发电量 × 抽水电价 × (1/效率) × 比例
            pump_energy = self._operating.annual_generation_mwh / 0.75 * ratio
            pump_cost = pump_energy * self._operating.pump_price * 1000 / 10000

            # 维修费: 建设投资 × 费率 × 比例
            # 费率: 前7年0, 第8年起 ~0.83% (基于Excel数据)
            maintenance_rate = 0.0 if year <= construction_end_year else 0.0083
            maintenance = self._construction_investment * maintenance_rate * ratio

            # 人工: 固定年薪酬 × 比例
            # Excel: 111,100.8万元 (40年合计) → 约2,777万/年
            labor = 2777.5 * ratio

            # 折旧/摊销
            depreciation = 0.0
            if self._depreciation_result is not None and year in self._depreciation_result.index:
                depreciation = float(
                    self._depreciation_result.loc[year, "total_depreciation"]
                )

            total = material + pump_cost + maintenance + labor + depreciation

            records.append(
                {
                    "year": year,
                    "production_ratio": ratio,
                    "material_cost": material,
                    "pump_electricity_cost": pump_cost,
                    "maintenance_cost": maintenance,
                    "labor_cost": labor,
                    "depreciation_total": depreciation,
                    "total_production_cost": total,
                }
            )

        df = pd.DataFrame(records)
        df.set_index("year", inplace=True)
        return df

    def _get_production_ratios(self, total_years: int) -> list[float]:
        if self._operating.production_ratios:
            ratios = list(self._operating.production_ratios)
            while len(ratios) < total_years:
                ratios.append(ratios[-1] if ratios else 0.0)
            return ratios[:total_years]
        construction_years = self._construction.construction_years
        return [0.0] * construction_years + [1.0] * (total_years - construction_years)

    def _zero_record(self, year: int) -> dict:
        return {
            "year": year,
            "production_ratio": 0.0,
            "material_cost": 0.0,
            "pump_electricity_cost": 0.0,
            "maintenance_cost": 0.0,
            "labor_cost": 0.0,
            "depreciation_total": 0.0,
            "total_production_cost": 0.0,
        }
