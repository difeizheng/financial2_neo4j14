"""
运营参数模型 — 装机容量、电价、利用小时、达产比例

对照 Excel 参数输入表:
  - Rows 30-100: 生产技术参数 (装机容量、利用小时、达产比例48年序列)
  - Rows 378-430: 成本参数 (材料费、维修费)

关键参数:
  - 装机容量: 1,400 MW (Row 33)
  - 年利用小时: 1,169.29 h (Row 34)
  - 容量电价: 696.5 元/kW·年
  - 上网电价: 0.35 元/kWh
  - 抽水电价: 0.23085 元/kWh
  - 达产比例: 48年序列 (建设期0, 第8年41.67%, 第9年起100%)
  - 厂用电率: 2%

注意: 税率参数已迁移到 params/tax.py (TaxParams)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatingParams:
    """运营参数 — 控制发电量、电价、达产比例

    Attributes:
        installed_capacity_mw: 装机容量 (MW, 如 1400)
        annual_utilization_hours: 年利用小时数 (如 1169.29)
        capacity_price: 容量电价 (元/kW·年, 如 696.5)
        grid_price: 上网电价 (元/kWh, 如 0.35)
        pump_price: 抽水电价 (元/kWh, 如 0.23085)
        auxiliary_power_rate: 厂用电率 (如 0.02)
        production_ratios: 达产比例序列 (48年, 建设期=0, 逐步到1.0)
    """

    # 生产技术参数
    installed_capacity_mw: float = 1400.0  # MW (Row 33)
    annual_utilization_hours: float = 1169.29  # h (Row 34)

    # 电价参数
    capacity_price: float = 696.5  # 元/kW·年
    grid_price: float = 0.35  # 元/kWh
    pump_price: float = 0.23085  # 元/kWh

    # 生产参数
    auxiliary_power_rate: float = 0.02  # 厂用电率 (Row 12)
    production_ratios: tuple[float, ...] = ()  # 48年达产比例

    def __post_init__(self) -> None:
        if self.installed_capacity_mw <= 0:
            raise ValueError("装机容量必须大于0")
        if self.annual_utilization_hours < 0:
            raise ValueError("年利用小时数不能为负")
        if self.grid_price < 0:
            raise ValueError("上网电价不能为负")
        if self.auxiliary_power_rate < 0 or self.auxiliary_power_rate >= 1:
            raise ValueError("厂用电率必须在[0,1)范围内")

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def annual_generation_mwh(self) -> float:
        """年发电量 (MWh) = 装机容量 × 利用小时"""
        return self.installed_capacity_mw * self.annual_utilization_hours

    @property
    def annual_grid_energy_mwh(self) -> float:
        """年上网电量 (MWh) = 发电量 × (1 - 厂用电率)"""
        return self.annual_generation_mwh * (1 - self.auxiliary_power_rate)

    @property
    def annual_pump_energy_mwh(self) -> float:
        """年抽水电量 (MWh) — 通常大于发电量 (抽蓄效率约 75%)"""
        # 简化: 抽水电量 = 发电量 / 0.75 (典型抽蓄效率)
        # 实际 Excel 中由具体公式计算
        return self.annual_generation_mwh / 0.75

    @property
    def annual_capacity_revenue(self) -> float:
        """年容量电费收入 (万元) = 装机容量(MW) × 容量电价(元/kW) / 10"""
        return self.installed_capacity_mw * self.capacity_price / 10.0

    @property
    def annual_energy_revenue(self) -> float:
        """年电量电费收入 (万元) = 上网电量(MWh) × 上网电价(元/kWh) × 1000 / 10000"""
        return self.annual_grid_energy_mwh * self.grid_price * 1000 / 10000

    # ── 便利构造器 ─────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls) -> OperatingParams:
        """从 Excel v17 模型创建 (黄金基准)

        达产比例: 48年 (8年建设期 + 40年运营期)
          年1-7: 0 (建设期)
          年8: 0.4167 (投产年, 5/12个月)
          年9-47: 1.0 (满产)
          年48: 0.5833 (末年, 7/12个月)
        """
        ratios = []
        for year_idx in range(1, 49):
            if year_idx <= 7:
                ratios.append(0.0)
            elif year_idx == 8:
                ratios.append(5.0 / 12)  # 0.4167
            elif year_idx == 48:
                ratios.append(7.0 / 12)  # 0.5833
            else:
                ratios.append(1.0)

        return cls(
            installed_capacity_mw=1400.0,
            annual_utilization_hours=1169.29,
            capacity_price=696.5,
            grid_price=0.35,
            pump_price=0.23085,
            auxiliary_power_rate=0.02,
            production_ratios=tuple(ratios),
        )
