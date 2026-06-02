"""
收入税金引擎 — 容量电费 + 电量电费 + 增值税

对照 Excel 表4-收入税金表:
  - Row 7:  容量电费 = 装机容量 × 容量电价 × 达产月份/12
  - Row 10: 电量电费 = 上网电量 × 上网电价
  - Row 11: 发电量 = 装机容量 × 利用小时 × 达产比例
  - Row 23: 增值税 = 销项 - 进项(可抵扣)

收入结构:
  营业收入 = 容量电费 + 电量电费
  税金 = 增值税 + 附加税
"""

from __future__ import annotations

import pandas as pd

from financial_model.engines.base_engine import BaseEngine
from financial_model.params.construction import ConstructionParams
from financial_model.params.financing import FinancingParams
from financial_model.params.investment import InvestmentParams
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams
from financial_model.timeline.generator import ProjectTimeline


class RevenueEngine(BaseEngine):
    """收入税金引擎

    输入:
      - OperatingParams (容量、电价、达产比例)
      - TaxParams (增值税率、附加税费率、可抵扣进项税)
      - ProjectTimeline (运营期年度)

    输出 DataFrame (index=year):
      - capacity_revenue: 容量电费收入 (万元)
      - energy_revenue: 电量电费收入 (万元)
      - total_revenue: 营业收入合计
      - generation_mwh: 发电量 (MWh)
      - grid_energy_mwh: 上网电量 (MWh)
      - vat_output: 增值税销项税 (万元)
      - vat_input_deductible: 可抵扣进项税 (万元)
      - vat_payable: 应缴增值税 (万元)
      - surcharge: 附加税费 (万元)
    """

    def __init__(
        self,
        params_construction: ConstructionParams,
        params_investment: InvestmentParams,
        params_financing: FinancingParams,
        timeline: ProjectTimeline,
        operating_params: OperatingParams | None = None,
        tax_params: TaxParams | None = None,
    ) -> None:
        super().__init__(
            params_construction, params_investment, params_financing, timeline
        )
        self._operating = operating_params or OperatingParams()
        self._tax = tax_params or TaxParams()

    @property
    def name(self) -> str:
        return "revenue"

    def calculate(self) -> pd.DataFrame:
        """执行收入税金计算"""
        years = list(self._timeline.year_range)
        construction_end_year = self._construction.construction_end.year

        ratios = self._get_production_ratios(len(years))

        # 可抵扣进项税分摊到运营期前几年
        op_years = [y for y in years if y > construction_end_year]
        amort_years = min(self._tax.deductible_vat_amort_years, len(op_years))
        deductible_per_year = (
            self._tax.deductible_input_vat / amort_years if op_years else 0.0
        )

        records = []
        deductible_remaining = self._tax.deductible_input_vat

        for i, year in enumerate(years):
            ratio = ratios[i]
            is_operation = year > construction_end_year or ratio > 0

            if not is_operation:
                records.append(self._zero_record(year))
                continue

            # 发电量
            gen = self._operating.annual_generation_mwh * ratio
            # 上网电量 = 发电量 × (1 - 厂用电率)
            grid = gen * (1 - self._operating.auxiliary_power_rate)

            # 容量电费(含税) = 容量(MW) × 电价(元/kW) × 比例 / 10 (→万元)
            capacity_rev_gross = (
                self._operating.installed_capacity_mw
                * self._operating.capacity_price
                * ratio
                / 10.0
            )

            # 电量电费(含税) = 上网电量(MWh) × 电价(元/kWh) × 1000 / 10000 (→万元)
            energy_rev_gross = grid * self._operating.grid_price * 1000 / 10000

            total_rev_gross = capacity_rev_gross + energy_rev_gross

            # 不含税收入 = 含税收入 / (1 + 增值税率)
            # Excel 表4 收入是不含税口径
            vat_factor = 1.0 + self._tax.vat_rate
            capacity_rev = capacity_rev_gross / vat_factor
            energy_rev = energy_rev_gross / vat_factor
            total_rev = capacity_rev + energy_rev

            # 增值税销项 = 含税收入 × 增值税率 / (1 + 增值税率)
            vat_output = total_rev_gross * self._tax.vat_rate / vat_factor
            vat_deductible = min(deductible_remaining, deductible_per_year)
            deductible_remaining -= vat_deductible
            vat_payable = max(vat_output - vat_deductible, 0.0)

            # 附加税费 = 应缴增值税 × 附加税率
            surcharge = vat_payable * self._tax.surcharge_rate

            records.append(
                {
                    "year": year,
                    "production_ratio": ratio,
                    "capacity_revenue": capacity_rev,
                    "energy_revenue": energy_rev,
                    "total_revenue": total_rev,
                    "generation_mwh": gen,
                    "grid_energy_mwh": grid,
                    "vat_output": vat_output,
                    "vat_input_deductible": vat_deductible,
                    "vat_payable": vat_payable,
                    "surcharge": surcharge,
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
            "capacity_revenue": 0.0,
            "energy_revenue": 0.0,
            "total_revenue": 0.0,
            "generation_mwh": 0.0,
            "grid_energy_mwh": 0.0,
            "vat_output": 0.0,
            "vat_input_deductible": 0.0,
            "vat_payable": 0.0,
            "surcharge": 0.0,
        }
