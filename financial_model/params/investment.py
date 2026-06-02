"""
投资概算参数模型 — 工程预算与投资分配

对照 Excel:
  - 投资概算明细 (rows 4-24): 分年度投资分配 + 价差预备费
  - 参数输入表 (rows 193-222): 工程概算明细 + 增值税率 + 补贴

投资预算层级:
  枢纽工程 (Hub works)              ← 8个子项
    ├─ 施工辅助工程
    ├─ 建筑工程
    ├─ 环境保护和水土保持专项工程
    ├─ 机电设备安装工程
    ├─ 金属结构设备安装工程
    ├─ 机电设备采购工程
    ├─ 金属结构设备采购工程
    └─ 建设征地和移民安置补偿费用
  独立费用 (Independent fees)        ← 4个子项
    ├─ 建设管理费
    ├─ 生产准备费
    ├─ 科研勘查设计费
    └─ 其他税费
  基本预备费 (Basic contingency)    ← 按费率计算
  送出线路投资
  储能投资
  ─────────────
  = 静态投资 (Static investment)
  + 价差预备费 (Price contingency)
  = 建设投资 (Construction investment)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class BudgetItem(NamedTuple):
    """单项预算 — 预算科目叶节点

    Attributes:
        name: 科目名称 (如 "施工辅助工程")
        amount: 总金额 (万元)
        vat_rate: 可抵扣增值税率 (如 0.09), 0 表示不可抵扣
    """

    name: str
    amount: float
    vat_rate: float = 0.0


@dataclass(frozen=True)
class PriceContingencyConfig:
    """价差预备费配置

    价差预备费按年度投资额乘以 (1+r)^n - 1 计算，其中:
      - r = 年物价上涨率 (price_escalation_rate)
      - n = 距基准年的年数

    当 rate = 0 时不计价差预备费。
    """

    price_escalation_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.price_escalation_rate < 0:
            raise ValueError(
                f"物价上涨率({self.price_escalation_rate})不能为负数"
            )


@dataclass(frozen=True)
class InvestmentParams:
    """投资概算参数 — 控制工程预算和投资分配

    预算科目分两层:
      - hub_budget_items: 枢纽工程子项 (对应 Excel 投资概算明细 rows 5-12)
      - independent_fee_items: 独立费用子项 (对应 Excel rows 14-17)

    派生计算:
      - 枢纽工程 = sum(hub_budget_items)
      - 独立费用 = sum(independent_fee_items)
      - 基本预备费 = (枢纽工程 + 独立费用) × 费率 (或直接指定金额)
      - 静态投资 = 枢纽工程 + 独立费用 + 基本预备费 + 送出 + 储能

    Attributes:
        hub_budget_items: 枢纽工程子项
        independent_fee_items: 独立费用子项
        basic_contingency_rate: 基本预备费费率 (默认 5%)
        basic_contingency_override: 直接指定基本预备费金额 (优先于费率计算)
        price_contingency: 价差预备费配置
        transmission_investment: 送出线路投资 (万元)
        energy_storage_investment: 储能投资 (万元)
        energy_storage_vat_rate: 储能投资增值税率
        construction_subsidy: 建设补贴 (万元)
        working_capital: 流动资金 (万元)
    """

    # 预算科目 (两层分类)
    hub_budget_items: tuple[BudgetItem, ...] = ()  # 枢纽工程子项 (7 items)
    independent_fee_items: tuple[BudgetItem, ...] = ()  # 独立费用子项 (4 items)

    # 建设征地和移民安置 (独立于枢纽工程, Excel Row 12)
    land_resettlement: float = 0.0

    # 基本预备费
    basic_contingency_rate: float = 0.05
    basic_contingency_override: float | None = None

    # 价差预备费
    price_contingency: PriceContingencyConfig = field(
        default_factory=PriceContingencyConfig
    )

    # 专项投资
    transmission_investment: float = 0.0
    energy_storage_investment: float = 0.0
    energy_storage_vat_rate: float = 0.13

    # 补贴与流动资金
    construction_subsidy: float = 0.0
    working_capital: float = 700.0

    def __post_init__(self) -> None:
        if self.basic_contingency_rate < 0:
            raise ValueError("基本预备费费率不能为负数")
        if self.working_capital < 0:
            raise ValueError("流动资金不能为负数")
        if self.construction_subsidy < 0:
            raise ValueError("建设补贴不能为负数")

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def hub_works_total(self) -> float:
        """枢纽工程合计 = sum(hub_budget_items)"""
        return sum(item.amount for item in self.hub_budget_items)

    @property
    def independent_fees_total(self) -> float:
        """独立费用合计 = sum(independent_fee_items)"""
        return sum(item.amount for item in self.independent_fee_items)

    @property
    def basic_contingency(self) -> float:
        """基本预备费

        优先使用 basic_contingency_override (直接金额),
        否则 = (枢纽工程 + 独立费用) × 费率
        """
        if self.basic_contingency_override is not None:
            return self.basic_contingency_override
        return (self.hub_works_total + self.independent_fees_total) * self.basic_contingency_rate

    @property
    def static_investment(self) -> float:
        """静态投资 (工程) = 枢纽工程 + 建设征地 + 独立费用
        + 基本预备费 + 送出线路 + 储能投资
        """
        return (
            self.hub_works_total
            + self.land_resettlement
            + self.independent_fees_total
            + self.basic_contingency
            + self.transmission_investment
            + self.energy_storage_investment
        )

    @property
    def static_investment_self_funded(self) -> float:
        """静态投资 (自主投资) = 静态投资(工程) - 建设补贴"""
        return self.static_investment - self.construction_subsidy

    @property
    def deductible_input_vat(self) -> float:
        """可抵扣进项税 = 各预算项金额 × 增值税率"""
        vat = sum(item.amount * item.vat_rate for item in self.hub_budget_items)
        vat += sum(
            item.amount * item.vat_rate for item in self.independent_fee_items
        )
        vat += self.energy_storage_investment * self.energy_storage_vat_rate
        return vat

    @property
    def all_budget_items(self) -> tuple[BudgetItem, ...]:
        """所有预算项 (枢纽 + 独立费用)"""
        return self.hub_budget_items + self.independent_fee_items

    # ── 便利构造器 ─────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls) -> InvestmentParams:
        """从 Excel v17 模型创建参数实例 (黄金基准)

        数据来源: 投资概算明细 rows 4-24, 参数输入表 rows 193-222
        """
        return cls(
            hub_budget_items=(
                BudgetItem("施工辅助工程", 62992.58, 0.09),
                BudgetItem("建筑工程", 331236.41, 0.09),
                BudgetItem("环境保护和水土保持专项工程", 17893.90, 0.09),
                BudgetItem("机电设备安装工程", 68286.20, 0.09),
                BudgetItem("金属结构设备安装工程", 157940.87, 0.09),
                BudgetItem("机电设备采购工程", 0.0, 0.13),
                BudgetItem("金属结构设备采购工程", 0.0, 0.13),
            ),
            land_resettlement=5205.27,
            independent_fee_items=(
                BudgetItem("建设管理费", 54324.05, 0.06),
                BudgetItem("生产准备费", 8001.30, 0.0),
                BudgetItem("科研勘查设计费", 56990.05, 0.06),
                BudgetItem("其他税费", 3540.49, 0.0),
            ),
            basic_contingency_override=38197.30,  # 直接指定 (Excel 给定值)
            price_contingency=PriceContingencyConfig(price_escalation_rate=0.0),
            transmission_investment=0.0,
            energy_storage_investment=6000.0,
            energy_storage_vat_rate=0.13,
            construction_subsidy=10000.0,
            working_capital=700.0,
        )

    def summary(self) -> dict[str, float]:
        """返回投资概算摘要"""
        return {
            "hub_works": self.hub_works_total,
            "independent_fees": self.independent_fees_total,
            "basic_contingency": self.basic_contingency,
            "static_investment": self.static_investment,
            "static_self_funded": self.static_investment_self_funded,
            "construction_subsidy": self.construction_subsidy,
            "deductible_vat": self.deductible_input_vat,
            "working_capital": self.working_capital,
        }
