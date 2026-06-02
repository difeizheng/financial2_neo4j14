"""
税务参数模型 — 增值税、所得税、附加税费

对照 Excel 参数输入表:
  - Rows 378-430: 成本与税务参数
  - 增值税率: 13% (标准) / 9% (建筑) / 6% (服务)
  - 所得税率: 25% (标准)
  - 附加税费: 城建税7% + 教育附加3% = 10% × (增值税)

税务结构:
  营业收入 × 增值税率 = 销项税
  销项税 - 可抵扣进项税 = 应缴增值税
  应缴增值税 × 附加税率 = 附加税费

  利润总额 × 所得税率 = 所得税
  (利润 < 0 时所得税 = 0, 亏损可结转5年抵减)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxParams:
    """税务参数 — 控制增值税、所得税、附加税费

    Attributes:
        vat_rate: 增值税率 (默认 0.13, 即13%)
        income_tax_rate: 企业所得税率 (默认 0.25, 即25%)
        surcharge_rate: 附加税费率 (城建税+教育附加, 默认 0.10)
            城建税 = 7% × 应缴增值税
            教育附加 = 3% × 应缴增值税
            合计 = 10% × 应缴增值税
        loss_carryforward_years: 亏损弥补年限 (默认5年)
            企业纳税年度发生的亏损, 准予向以后年度结转,
            但结转年限最长不得超过5年。
        deductible_input_vat: 可抵扣进项税总额 (万元, 如 67754.50)
            建设期投资的增值税进项税, 在运营期分年度抵扣。
        deductible_vat_amort_years: 进项税抵扣年限 (默认10年)
    """

    # 增值税
    vat_rate: float = 0.13

    # 企业所得税
    income_tax_rate: float = 0.25
    loss_carryforward_years: int = 5

    # 附加税费
    surcharge_rate: float = 0.10  # 城建税7% + 教育附加3%

    # 可抵扣进项税
    deductible_input_vat: float = 0.0
    deductible_vat_amort_years: int = 10

    def __post_init__(self) -> None:
        if not (0.0 <= self.vat_rate <= 1.0):
            raise ValueError(f"增值税率({self.vat_rate})必须在[0,1]范围内")
        if not (0.0 <= self.income_tax_rate <= 1.0):
            raise ValueError(f"所得税率({self.income_tax_rate})必须在[0,1]范围内")
        if not (0.0 <= self.surcharge_rate <= 1.0):
            raise ValueError(f"附加税费率({self.surcharge_rate})必须在[0,1]范围内")
        if self.loss_carryforward_years < 0:
            raise ValueError(
                f"亏损弥补年限({self.loss_carryforward_years})不能为负数"
            )
        if self.deductible_vat_amort_years < 1:
            raise ValueError(
                f"进项税抵扣年限({self.deductible_vat_amort_years})必须>=1"
            )

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def deductible_vat_per_year(self) -> float:
        """每年可抵扣进项税额 (万元)"""
        return self.deductible_input_vat / self.deductible_vat_amort_years

    # ── 便利构造器 ─────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls, deductible_input_vat: float = 67754.50) -> TaxParams:
        """从 Excel v17 模型创建参数实例 (黄金基准)

        可抵扣进项税从 InvestmentParams.deductible_input_vat 获取,
        这里用默认值 67754.50 万元。
        """
        return cls(
            vat_rate=0.13,
            income_tax_rate=0.25,
            loss_carryforward_years=5,
            surcharge_rate=0.10,
            deductible_input_vat=deductible_input_vat,
            deductible_vat_amort_years=10,
        )
