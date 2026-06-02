"""YAML 预设加载器 — 从 YAML 文件加载完整 ModelConfig

预设文件结构::

    name: 项目名称
    description: 简要描述
    version: "1.0"

    construction:
      start_date: "2023-02-01"
      end_date: "2030-07-31"
      operation_years: 40

    operating:
      installed_capacity_mw: 1400.0
      annual_utilization_hours: 1169.29
      ...

    financing:
      equity_ratio: 0.25
      construction_interest_rate: 0.043
      ...

    tax:
      vat_rate: 0.13
      ...

    depreciation:
      fixed_assets:
        original_value: 819191.18
        useful_life: 29
        residual_rate: 0.05
      ...

    discount_rate: 0.08

典型用法::

    from financial_model.params.presets import load_preset, list_presets

    for name in list_presets():
        print(name)

    config = load_preset("pshp_1400mw_8yr")
    results = config.to_orchestrator().run()
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from financial_model.analysis.types import ModelConfig
from financial_model.params import (
    ConstructionParams,
    DepreciationParams,
    FinancingParams,
    InvestmentParams,
    LoanTerms,
    OperatingParams,
    TaxParams,
)
from financial_model.params.depreciation import AssetCategory
from financial_model.params.investment import BudgetItem, PriceContingencyConfig

_PRESETS_DIR = Path(__file__).parent


# ══════════════════════════════════════════════════════════
# 公共 API
# ══════════════════════════════════════════════════════════


def list_presets() -> list[str]:
    """列出所有可用的预设模板名称

    Returns:
        预设名称列表 (不含 .yaml 后缀), 按名称排序
    """
    presets = sorted(
        p.stem for p in _PRESETS_DIR.glob("*.yaml")
    )
    return presets


def load_preset(name: str) -> ModelConfig:
    """从 YAML 预设文件加载 ModelConfig

    Args:
        name: 预设名称 (不含 .yaml 后缀), 如 "pshp_1400mw_8yr"

    Returns:
        完整的 ModelConfig 实例

    Raises:
        FileNotFoundError: 预设文件不存在
        ValueError: YAML 内容格式错误
    """
    path = _PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        available = list_presets()
        raise FileNotFoundError(
            f"预设 '{name}' 不存在。可用预设: {available}"
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return _parse_config(raw, name)


def load_preset_metadata(name: str) -> dict[str, str]:
    """加载预设元数据 (不构建完整配置)

    Returns:
        {"name": ..., "description": ..., "version": ...}
    """
    path = _PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        return {"name": name, "description": "未知", "version": "?"}

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return {
        "name": raw.get("name", name),
        "description": raw.get("description", ""),
        "version": raw.get("version", "?"),
    }


# ══════════════════════════════════════════════════════════
# 解析器
# ══════════════════════════════════════════════════════════


def _parse_config(raw: dict[str, Any], name: str) -> ModelConfig:
    """将 YAML 字典解析为 ModelConfig"""
    return ModelConfig(
        construction=_parse_construction(raw.get("construction", {})),
        investment=_parse_investment(raw.get("investment", {})),
        financing=_parse_financing(raw.get("financing", {})),
        operating=_parse_operating(raw.get("operating", {})),
        tax=_parse_tax(raw.get("tax", {})),
        depreciation=_parse_depreciation(raw.get("depreciation", {})),
        discount_rate=raw.get("discount_rate", 0.08),
    )


def _parse_date(s: str) -> date:
    """解析日期字符串 (YYYY-MM-DD)"""
    parts = s.split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _parse_construction(d: dict[str, Any]) -> ConstructionParams:
    return ConstructionParams(
        construction_start=_parse_date(d["start_date"]),
        construction_end=_parse_date(d["end_date"]),
        operation_years=d.get("operation_years", 40),
    )


def _parse_investment(d: dict[str, Any]) -> InvestmentParams:
    """解析投资概算参数

    YAML 中可省略详细的 budget_items, 此时回退到 from_excel_v17() 默认值。
    对于不同规模项目，budget_items 中的金额可按比例缩放。
    """
    # 如果提供了 scale_factor，基于 v17 基准按比例缩放
    scale = d.get("scale_factor", None)

    if scale is not None:
        base = InvestmentParams.from_excel_v17()
        scaled_items = tuple(
            BudgetItem(item.name, round(item.amount * scale, 2), item.vat_rate)
            for item in base.hub_budget_items
        )
        scaled_fees = tuple(
            BudgetItem(item.name, round(item.amount * scale, 2), item.vat_rate)
            for item in base.independent_fee_items
        )
        return InvestmentParams(
            hub_budget_items=scaled_items,
            land_resettlement=round(base.land_resettlement * scale, 2),
            independent_fee_items=scaled_fees,
            basic_contingency_override=(
                round(base.basic_contingency_override * scale, 2)
                if base.basic_contingency_override else None
            ),
            price_contingency=PriceContingencyConfig(
                price_escalation_rate=d.get(
                    "price_escalation_rate",
                    base.price_contingency.price_escalation_rate,
                ),
            ),
            transmission_investment=round(base.transmission_investment * scale, 2),
            energy_storage_investment=round(base.energy_storage_investment * scale, 2),
            construction_subsidy=round(base.construction_subsidy * scale, 2),
            working_capital=base.working_capital,
        )

    # 完整指定模式
    hub_items = tuple(
        BudgetItem(b["name"], b["amount"], b.get("vat_rate", 0.0))
        for b in d.get("hub_budget_items", [])
    )
    fee_items = tuple(
        BudgetItem(b["name"], b["amount"], b.get("vat_rate", 0.0))
        for b in d.get("independent_fee_items", [])
    )

    # 如果没有 budget_items，使用 v17 默认
    if not hub_items:
        return InvestmentParams.from_excel_v17()

    return InvestmentParams(
        hub_budget_items=hub_items,
        land_resettlement=d.get("land_resettlement", 0.0),
        independent_fee_items=fee_items,
        basic_contingency_rate=d.get("basic_contingency_rate", 0.05),
        basic_contingency_override=d.get("basic_contingency_override"),
        price_contingency=PriceContingencyConfig(
            price_escalation_rate=d.get("price_escalation_rate", 0.0),
        ),
        transmission_investment=d.get("transmission_investment", 0.0),
        energy_storage_investment=d.get("energy_storage_investment", 0.0),
        construction_subsidy=d.get("construction_subsidy", 0.0),
        working_capital=d.get("working_capital", 700.0),
    )


def _parse_financing(d: dict[str, Any]) -> FinancingParams:
    """解析融资参数

    简化模式: 只指定关键参数 (利率、期限、资本金比例)，
    回退到 v17 默认的资本金到账计划。
    """
    base = FinancingParams.from_excel_v17()

    # 构建 LoanTerms
    loan_terms = LoanTerms(
        annual_rate=d.get("long_term_rate", base.long_term_loan.annual_rate),
        repayment_term_years=d.get(
            "long_term_years", base.long_term_loan.repayment_term_years
        ),
        repayment_method=base.long_term_loan.repayment_method,
        repayment_frequency=base.long_term_loan.repayment_frequency,
        grace_period_days=d.get(
            "grace_period_days", base.long_term_loan.grace_period_days
        ),
    )

    return FinancingParams(
        equity_input_mode=base.equity_input_mode,
        equity_ratio=d.get("equity_ratio", base.equity_ratio),
        equity_injections=base.equity_injections,
        period_investments=base.period_investments,
        construction_interest_rate=d.get(
            "construction_interest_rate", base.construction_interest_rate
        ),
        long_term_loan=loan_terms,
        short_term_loan_rate=d.get("short_term_rate", base.short_term_loan_rate),
        short_term_borrowing=base.short_term_borrowing,
        working_capital_equity_share=base.working_capital_equity_share,
        registered_capital=base.registered_capital,
        shareholding_ratio=base.shareholding_ratio,
        dividend_payout_ratio=base.dividend_payout_ratio,
        statutory_reserve_limit=base.statutory_reserve_limit,
        statutory_reserve_ratio=base.statutory_reserve_ratio,
        discretionary_reserve_ratio=base.discretionary_reserve_ratio,
    )


def _parse_operating(d: dict[str, Any]) -> OperatingParams:
    """解析运营参数

    production_ratios 自动根据建设期年数生成 (投产年 5/12 或 7/12)。
    """
    base = OperatingParams.from_excel_v17()

    capacity = d.get("installed_capacity_mw", base.installed_capacity_mw)
    util_hours = d.get("annual_utilization_hours", base.annual_utilization_hours)

    # 生成达产比例需要知道建设期年数 — 使用 v17 的 48 年序列作为默认
    ratios = tuple(d.get("production_ratios", base.production_ratios))

    return OperatingParams(
        installed_capacity_mw=capacity,
        annual_utilization_hours=util_hours,
        capacity_price=d.get("capacity_price", base.capacity_price),
        grid_price=d.get("grid_price", base.grid_price),
        pump_price=d.get("pump_price", base.pump_price),
        auxiliary_power_rate=d.get("auxiliary_power_rate", base.auxiliary_power_rate),
        production_ratios=ratios,
    )


def _parse_tax(d: dict[str, Any]) -> TaxParams:
    base = TaxParams.from_excel_v17()
    return TaxParams(
        vat_rate=d.get("vat_rate", base.vat_rate),
        income_tax_rate=d.get("income_tax_rate", base.income_tax_rate),
        surcharge_rate=d.get("surcharge_rate", base.surcharge_rate),
        loss_carryforward_years=d.get(
            "loss_carryforward_years", base.loss_carryforward_years
        ),
        deductible_input_vat=d.get(
            "deductible_input_vat", base.deductible_input_vat
        ),
        deductible_vat_amort_years=d.get(
            "deductible_vat_amort_years", base.deductible_vat_amort_years
        ),
    )


def _parse_depreciation(d: dict[str, Any]) -> DepreciationParams:
    base = DepreciationParams.from_excel_v17()

    fa = d.get("fixed_assets", {})
    ia = d.get("intangible_assets", {})

    return DepreciationParams(
        fixed_assets=AssetCategory(
            "固定资产",
            fa.get("original_value", base.fixed_assets.original_value),
            fa.get("useful_life", base.fixed_assets.useful_life),
            fa.get("residual_rate", base.fixed_assets.residual_rate),
        ),
        intangible_assets=AssetCategory(
            "无形资产",
            ia.get("original_value", base.intangible_assets.original_value),
            ia.get("useful_life", base.intangible_assets.useful_life),
            ia.get("residual_rate", base.intangible_assets.residual_rate),
        ),
        long_term_prepaid_amount=d.get(
            "long_term_prepaid_amount", base.long_term_prepaid_amount
        ),
        long_term_prepaid_period=d.get(
            "long_term_prepaid_period", base.long_term_prepaid_period
        ),
    )
