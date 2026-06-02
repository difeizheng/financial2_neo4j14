"""
折旧摊销参数模型 — 5类资产的折旧/摊销规则

对照 Excel 表2-折旧摊销表 (rows 3-21):
  - Row 4:  固定资产原值 = 819,191.18 (静态投资 + 建设期利息 - 可抵扣进项税)
  - Row 6:  折旧年限 = 29年
  - Row 7:  残值率 = 5%
  - Row 8-12: 长期待摊费用 (每5年一笔, 每笔458.72)
  - Row 14: 长期待摊费用摊销年限 = 5年
  - Row 15: 无形资产原值 = 80,000
  - Row 17: 无形资产摊销年限 = 18年
  - Row 18: 无形资产残值率 = 5%
  - Row 19: 大修及更新重置资产原值 = 0
  - Row 21: 大修摊销年限 = 10年

折旧方法: 直线法 (年限平均法)
  年折旧 = 原值 × (1 - 残值率) / 折旧年限
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


class AssetCategory(NamedTuple):
    """资产类别定义

    Attributes:
        name: 资产类别名称
        original_value: 原始价值 (万元)
        useful_life: 折旧/摊销年限 (年)
        residual_rate: 残值率 (如 0.05)
    """

    name: str
    original_value: float
    useful_life: int
    residual_rate: float = 0.05

    @property
    def annual_depreciation(self) -> float:
        """年折旧/摊销额 = 原值 × (1 - 残值率) / 年限"""
        if self.useful_life <= 0:
            return 0.0
        return self.original_value * (1 - self.residual_rate) / self.useful_life


@dataclass(frozen=True)
class DepreciationParams:
    """折旧摊销参数 — 5类资产的折旧/摊销规则

    资产分类:
      1. 固定资产 (主要) — 房屋建筑物+机器设备, 折旧29年, 残值5%
      2. 无形资产 — 80,000万元, 摊销18年, 残值5%
      3. 长期待摊费用 — 每5年一笔, 每笔~458.72万元, 摊销5年
      4. 大修及更新重置 — 运营期大修, 摊销10年
      5. 储能资产 — 储能投资形成的资产

    Attributes:
        fixed_assets: 固定资产参数
        intangible_assets: 无形资产参数
        long_term_prepaid: 长期待摊费用 (金额, 摊销年限, 周期)
        overhaul_assets: 大修及更新重置资产
        energy_storage_assets: 储能资产
    """

    # 固定资产
    fixed_assets: AssetCategory = AssetCategory(
        "固定资产", 819191.18, 29, 0.05
    )

    # 无形资产
    intangible_assets: AssetCategory = AssetCategory(
        "无形资产", 80000.0, 18, 0.05
    )

    # 长期待摊费用
    long_term_prepaid_amount: float = 458.72  # 每笔金额 (万元)
    long_term_prepaid_period: int = 5  # 摊销年限
    long_term_prepaid_cycle: int = 5  # 每N年一笔

    # 大修及更新重置
    overhaul_assets: AssetCategory = AssetCategory(
        "大修及更新重置", 0.0, 10, 0.0
    )

    # 储能资产
    energy_storage_assets: AssetCategory = AssetCategory(
        "储能资产", 6000.0, 10, 0.05
    )

    # 期末资产确认比例
    terminal_asset_ratio: float = 0.0

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def total_annual_depreciation(self) -> float:
        """主要资产年折旧/摊销合计 (不含长期待摊和运营期大修)"""
        return (
            self.fixed_assets.annual_depreciation
            + self.intangible_assets.annual_depreciation
            + self.energy_storage_assets.annual_depreciation
        )

    @property
    def long_term_prepaid_annual(self) -> float:
        """长期待摊费用年均摊销额"""
        if self.long_term_prepaid_period <= 0:
            return 0.0
        return self.long_term_prepaid_amount / self.long_term_prepaid_period

    # ── 便利构造器 ─────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls) -> DepreciationParams:
        """从 Excel v17 模型创建 (黄金基准)"""
        return cls(
            fixed_assets=AssetCategory("固定资产", 819191.18, 29, 0.05),
            intangible_assets=AssetCategory("无形资产", 80000.0, 18, 0.05),
            long_term_prepaid_amount=458.72,
            long_term_prepaid_period=5,
            long_term_prepaid_cycle=5,
            overhaul_assets=AssetCategory("大修及更新重置", 0.0, 10, 0.0),
            energy_storage_assets=AssetCategory("储能资产", 6000.0, 10, 0.05),
        )
