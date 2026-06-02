"""分析工具公共类型 — 参数规格、指标提取、模型配置

Phase 5 的基础类型:
  - ModelConfig: 编排器参数的不可变容器, 分析层与引擎层之间的桥梁
  - ParamSpec: 声明式描述"要扰动哪个参数"
  - MetricKey: 可提取的派生指标键
  - extract_metrics(): 从 AllResults 提取指标值
  - COMMON_PARAMS: 常用参数规格预设

设计原则:
  - 分析层只依赖 ModelConfig + AllResults, 不直接操作引擎内部
  - 参数扰动通过 dataclasses.replace() 实现不可变修改
  - 所有类型 frozen, 线程安全
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from financial_model.engines.orchestrator import AllResults, ModelOrchestrator
from datetime import date

from financial_model.params import (
    ConstructionParams,
    DepreciationParams,
    FinancingParams,
    InvestmentParams,
    OperatingParams,
    TaxParams,
)


# ══════════════════════════════════════════════════════════
# MetricKey — 可提取的派生指标
# ══════════════════════════════════════════════════════════


class MetricKey(str, Enum):
    """可提取的派生指标键 — 直接映射 DerivedMetrics 字段"""

    IRR_TOTAL = "irr_total"
    IRR_EQUITY = "irr_equity"
    NPV_TOTAL = "npv_total"
    NPV_EQUITY = "npv_equity"
    DSCR_MIN = "dscr_min"
    DSCR_AVG = "dscr_avg"
    PAYBACK_STATIC = "payback_static"
    PAYBACK_DYNAMIC = "payback_dynamic"
    ROE_AVG = "roe_avg"
    PROJECT_YEARS = "project_years"


METRIC_DISPLAY: dict[MetricKey, str] = {
    MetricKey.IRR_TOTAL: "全投资IRR",
    MetricKey.IRR_EQUITY: "资本金IRR",
    MetricKey.NPV_TOTAL: "全投资NPV(万元)",
    MetricKey.NPV_EQUITY: "资本金NPV(万元)",
    MetricKey.DSCR_MIN: "最低DSCR",
    MetricKey.DSCR_AVG: "平均DSCR",
    MetricKey.PAYBACK_STATIC: "静态回收期(年)",
    MetricKey.PAYBACK_DYNAMIC: "动态回收期(年)",
    MetricKey.ROE_AVG: "平均ROE",
    MetricKey.PROJECT_YEARS: "项目年限",
}

DEFAULT_METRICS: list[MetricKey] = [
    MetricKey.IRR_TOTAL,
    MetricKey.IRR_EQUITY,
    MetricKey.NPV_TOTAL,
    MetricKey.NPV_EQUITY,
    MetricKey.DSCR_MIN,
    MetricKey.DSCR_AVG,
    MetricKey.PAYBACK_STATIC,
]


# ══════════════════════════════════════════════════════════
# ModelConfig — 编排器参数配置
# ══════════════════════════════════════════════════════════

# group name → ModelConfig field name
_GROUP_MAP: dict[str, str] = {
    "construction": "construction",
    "investment": "investment",
    "financing": "financing",
    "operating": "operating",
    "tax": "tax",
    "depreciation": "depreciation",
}


@dataclass(frozen=True)
class ModelConfig:
    """编排器参数配置 — 分析层与引擎层之间的桥梁

    用法::

        # 从黄金基准创建
        config = ModelConfig.from_excel_v17()

        # 修改参数
        config2 = config.with_param("operating", "grid_price", 0.40)

        # 运行
        results = config.to_orchestrator().run()
    """

    construction: ConstructionParams
    investment: InvestmentParams
    financing: FinancingParams
    operating: OperatingParams
    tax: TaxParams
    depreciation: DepreciationParams
    discount_rate: float = 0.08

    def to_orchestrator(self) -> ModelOrchestrator:
        """创建编排器实例"""
        return ModelOrchestrator(
            params_construction=self.construction,
            params_investment=self.investment,
            params_financing=self.financing,
            params_operating=self.operating,
            params_tax=self.tax,
            params_depreciation=self.depreciation,
            discount_rate=self.discount_rate,
        )

    def with_param(self, group: str, field: str, value: Any) -> ModelConfig:
        """修改一个参数字段, 返回新 ModelConfig

        Args:
            group: 参数组名 ("construction", "operating", ...)
            field: 字段名 ("grid_price", "annual_rate", ...)
            value: 新值

        Returns:
            新的 ModelConfig, 仅指定字段被修改
        """
        attr_name = _GROUP_MAP.get(group)
        if attr_name is None:
            raise ValueError(
                f"Unknown param group: {group}. "
                f"Valid: {list(_GROUP_MAP.keys())}"
            )

        old_param = getattr(self, attr_name)
        new_param = replace(old_param, **{field: value})
        return replace(self, **{attr_name: new_param})

    # ── 工厂方法 ─────────────────────────────────────────

    @classmethod
    def from_excel_v17(cls, **overrides: Any) -> ModelConfig:
        """从 Excel v17 黄金基准创建

        Args:
            **overrides: 可覆盖任意参数组, 如 operating=OperatingParams(...)
        """
        return cls(
            construction=overrides.pop(
                "construction",
                ConstructionParams(
                    construction_start=date(2023, 2, 1),
                    construction_end=date(2030, 7, 31),
                    operation_years=40,
                ),
            ),
            investment=overrides.pop(
                "investment", InvestmentParams.from_excel_v17()
            ),
            financing=overrides.pop("financing", FinancingParams.from_excel_v17()),
            operating=overrides.pop("operating", OperatingParams.from_excel_v17()),
            tax=overrides.pop("tax", TaxParams.from_excel_v17()),
            depreciation=overrides.pop(
                "depreciation", DepreciationParams.from_excel_v17()
            ),
            discount_rate=overrides.pop("discount_rate", 0.08),
        )


# ══════════════════════════════════════════════════════════
# ParamSpec — 声明式参数规格
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ParamSpec:
    """声明式参数规格 — 描述"要扰动哪个参数"

    用法::

        spec = ParamSpec("operating", "grid_price", "上网电价")
        new_config = spec.perturb(config, 0.1)  # +10%
    """

    group: str  # "operating", "financing", ...
    field: str  # "grid_price", "annual_rate", ...
    display_name: str  # "上网电价"

    def perturb(self, config: ModelConfig, multiplier: float) -> ModelConfig:
        """按比例扰动参数

        对标量: value * (1 + multiplier)
        对 tuple (时间序列): 每个元素 * (1 + multiplier)

        Args:
            config: 基准配置
            multiplier: 扰动比例 (0.1 = +10%, -0.1 = -10%)

        Returns:
            扰动后的新 ModelConfig
        """
        attr_name = _GROUP_MAP.get(self.group)
        if attr_name is None:
            raise ValueError(f"Unknown param group: {self.group}")

        old_param = getattr(config, attr_name)
        old_value = getattr(old_param, self.field)

        # tuple (时间序列) — 每个元素按比例扰动
        if isinstance(old_value, tuple):
            new_value = tuple(v * (1 + multiplier) for v in old_value)
        else:
            new_value = old_value * (1 + multiplier)

        return config.with_param(self.group, self.field, new_value)

    def set_value(self, config: ModelConfig, value: Any) -> ModelConfig:
        """设置参数为绝对值"""
        return config.with_param(self.group, self.field, value)

    def get_value(self, config: ModelConfig) -> Any:
        """获取当前参数值"""
        attr_name = _GROUP_MAP.get(self.group)
        if attr_name is None:
            raise ValueError(f"Unknown param group: {self.group}")
        old_param = getattr(config, attr_name)
        return getattr(old_param, self.field)


# ══════════════════════════════════════════════════════════
# 指标提取
# ══════════════════════════════════════════════════════════


def extract_metrics(
    results: AllResults,
    keys: list[MetricKey] | None = None,
) -> dict[MetricKey, float | None]:
    """从 AllResults 提取指定指标

    Args:
        results: 完整模型结果
        keys: 要提取的指标列表, 默认 DEFAULT_METRICS

    Returns:
        {MetricKey: 值} 字典, 不可用的指标为 None
    """
    if keys is None:
        keys = DEFAULT_METRICS

    dm = results.derived_metrics
    source: dict[MetricKey, float | None] = {
        MetricKey.IRR_TOTAL: dm.irr_total,
        MetricKey.IRR_EQUITY: dm.irr_equity,
        MetricKey.NPV_TOTAL: dm.npv_total,
        MetricKey.NPV_EQUITY: dm.npv_equity,
        MetricKey.DSCR_MIN: dm.dscr_min,
        MetricKey.DSCR_AVG: dm.dscr_avg,
        MetricKey.PAYBACK_STATIC: dm.payback_static,
        MetricKey.PAYBACK_DYNAMIC: dm.payback_dynamic,
        MetricKey.ROE_AVG: dm.roe_avg,
        MetricKey.PROJECT_YEARS: float(dm.project_years),
    }
    return {k: source[k] for k in keys}


# ══════════════════════════════════════════════════════════
# 常用参数规格预设
# ══════════════════════════════════════════════════════════

COMMON_PARAMS: list[ParamSpec] = [
    # 运营参数 — 电价/产能
    ParamSpec("operating", "grid_price", "上网电价(元/kWh)"),
    ParamSpec("operating", "pump_price", "抽水电价(元/kWh)"),
    ParamSpec("operating", "capacity_price", "容量电价(元/kW·年)"),
    ParamSpec("operating", "installed_capacity_mw", "装机容量(MW)"),
    ParamSpec("operating", "annual_utilization_hours", "年利用小时(h)"),
    ParamSpec("operating", "auxiliary_power_rate", "厂用电率"),
    # 融资参数 — 利率/资本金
    ParamSpec("financing", "construction_interest_rate", "建设期利率"),
    ParamSpec("financing", "short_term_loan_rate", "短期贷款利率"),
    ParamSpec("financing", "equity_ratio", "资本金比例"),
    # 税务参数
    ParamSpec("tax", "income_tax_rate", "所得税率"),
    ParamSpec("tax", "vat_rate", "增值税率"),
    ParamSpec("tax", "surcharge_rate", "附加税费率"),
    # 折旧参数 — 摊销年限/周期
    ParamSpec("depreciation", "long_term_prepaid_period", "长期待摊摊销年限"),
]
