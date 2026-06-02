"""
参数模型层 — 第一层

所有参数由 frozen dataclass 表示，支持 JSON 序列化和预设模板。
"""

from financial_model.params.construction import ConstructionParams
from financial_model.params.depreciation import AssetCategory, DepreciationParams
from financial_model.params.financing import (
    EquityInputMode,
    EquityInjection,
    FinancingParams,
    LoanTerms,
    RepaymentFrequency,
    RepaymentMethod,
)
from financial_model.params.investment import (
    BudgetItem,
    InvestmentParams,
    PriceContingencyConfig,
)
from financial_model.params.operating import OperatingParams
from financial_model.params.tax import TaxParams

__all__ = [
    "ConstructionParams",
    "AssetCategory",
    "BudgetItem",
    "DepreciationParams",
    "InvestmentParams",
    "OperatingParams",
    "PriceContingencyConfig",
    "TaxParams",
    "EquityInputMode",
    "EquityInjection",
    "FinancingParams",
    "LoanTerms",
    "RepaymentFrequency",
    "RepaymentMethod",
]
