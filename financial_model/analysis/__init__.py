"""分析工具 — 敏感性分析、情景分析、蒙特卡罗模拟

第五层: 分析工具层

模块:
  - types: 公共类型 (ModelConfig, ParamSpec, MetricKey, extract_metrics)
  - scenario: 情景分析 (悲观/基准/乐观)
  - sensitivity: 敏感性分析 (单参数扰动 → 龙卷风图)
  - monte_carlo: 蒙特卡罗模拟 (概率分布 → 统计指标)
"""

from financial_model.analysis.monte_carlo import (
    DistributionType,
    MonteCarloEngine,
    MonteCarloResult,
    ParamDistribution,
    SimulationRun,
)
from financial_model.analysis.scenario import (
    PresetScenario,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioEngine,
    ScenarioResult,
)
from financial_model.analysis.sensitivity import (
    SensitivityEngine,
    SensitivityItem,
    SensitivityResult,
)
from financial_model.analysis.types import (
    COMMON_PARAMS,
    DEFAULT_METRICS,
    METRIC_DISPLAY,
    MetricKey,
    ModelConfig,
    ParamSpec,
    extract_metrics,
)

__all__ = [
    # Types
    "COMMON_PARAMS",
    "DEFAULT_METRICS",
    "METRIC_DISPLAY",
    "MetricKey",
    "ModelConfig",
    "ParamSpec",
    "extract_metrics",
    # Scenario
    "PresetScenario",
    "ScenarioComparison",
    "ScenarioDefinition",
    "ScenarioEngine",
    "ScenarioResult",
    # Sensitivity
    "SensitivityEngine",
    "SensitivityItem",
    "SensitivityResult",
    # Monte Carlo
    "DistributionType",
    "MonteCarloEngine",
    "MonteCarloResult",
    "ParamDistribution",
    "SimulationRun",
]
