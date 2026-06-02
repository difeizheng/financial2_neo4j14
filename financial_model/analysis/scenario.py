"""情景分析引擎 — 命名参数组合的对比分析

定义多组参数方案 (悲观/基准/乐观), 逐一运行编排器, 输出对比表。

典型用法::

    from financial_model.analysis.types import ModelConfig, MetricKey

    config = ModelConfig.from_excel_v17()
    engine = ScenarioEngine(config)

    # 使用预设情景
    result = engine.run_preset_scenarios([
        PresetScenario.PESSIMISTIC,
        PresetScenario.BASE,
        PresetScenario.OPTIMISTIC,
    ])

    # 自定义情景
    custom = engine.run([
        ScenarioDefinition("高电价", {"operating": {"grid_price": 0.40}}),
        ScenarioDefinition("低电价", {"operating": {"grid_price": 0.30}}),
    ])

    # 输出对比表
    print(result.comparison_table())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from financial_model.analysis.types import (
    DEFAULT_METRICS,
    MetricKey,
    ModelConfig,
    extract_metrics,
)


# ══════════════════════════════════════════════════════════
# 预设情景定义
# ══════════════════════════════════════════════════════════


class PresetScenario(str, Enum):
    """预设情景类型"""

    PESSIMISTIC = "pessimistic"
    BASE = "base"
    OPTIMISTIC = "optimistic"


# 预设情景参数覆盖 (标准抽蓄项目)
_PRESET_OVERRIDES: dict[PresetScenario, dict[str, dict[str, Any]]] = {
    PresetScenario.PESSIMISTIC: {
        "operating": {
            "grid_price": 0.315,  # -10%
            "pump_price": 0.2539,  # +10%
            "annual_utilization_hours": 1052.36,  # -10%
        },
    },
    PresetScenario.BASE: {},  # 无覆盖, 使用基准参数
    PresetScenario.OPTIMISTIC: {
        "operating": {
            "grid_price": 0.385,  # +10%
            "pump_price": 0.2078,  # -10%
            "annual_utilization_hours": 1286.22,  # +10%
        },
    },
}

_PRESET_DISPLAY: dict[PresetScenario, str] = {
    PresetScenario.PESSIMISTIC: "悲观",
    PresetScenario.BASE: "基准",
    PresetScenario.OPTIMISTIC: "乐观",
}


# ══════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ScenarioDefinition:
    """一个情景定义 — 名称 + 参数覆盖

    overrides 格式::

        {
            "operating": {"grid_price": 0.40, "pump_price": 0.20},
            "financing": {"construction_interest_rate": 0.05},
        }
    """

    name: str
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    def apply_to(self, config: ModelConfig) -> ModelConfig:
        """将参数覆盖应用到 ModelConfig"""
        result = config
        for group, fields in self.overrides.items():
            for field_name, value in fields.items():
                result = result.with_param(group, field_name, value)
        return result


@dataclass(frozen=True)
class ScenarioResult:
    """单个情景的运行结果"""

    name: str
    metrics: dict[MetricKey, float | None]
    config: ModelConfig


@dataclass
class ScenarioComparison:
    """多情景对比结果"""

    base_name: str
    metrics_keys: list[MetricKey]
    scenarios: list[ScenarioResult] = field(default_factory=list)

    def comparison_table(self) -> pd.DataFrame:
        """生成对比表 (指标 × 情景)

        Returns:
            DataFrame with MetricKey display names as index,
            scenario names as columns.
        """
        if not self.scenarios:
            return pd.DataFrame()

        from financial_model.analysis.types import METRIC_DISPLAY

        rows: list[dict[str, Any]] = []
        for key in self.metrics_keys:
            row: dict[str, Any] = {"指标": METRIC_DISPLAY.get(key, key.value)}
            for s in self.scenarios:
                val = s.metrics.get(key)
                if val is None:
                    row[s.name] = "N/A"
                elif key in (
                    MetricKey.IRR_TOTAL,
                    MetricKey.IRR_EQUITY,
                    MetricKey.DSCR_MIN,
                    MetricKey.DSCR_AVG,
                    MetricKey.ROE_AVG,
                ):
                    row[s.name] = f"{val:.2%}"
                elif key == MetricKey.PROJECT_YEARS:
                    row[s.name] = f"{int(val)}"
                else:
                    row[s.name] = f"{val:,.2f}"
            rows.append(row)

        return pd.DataFrame(rows).set_index("指标")

    def delta_table(self) -> pd.DataFrame:
        """生成偏差表 (与基准情景的差异)

        Returns:
            DataFrame with MetricKey display names as index,
            scenario names as columns, values = scenario - base.
        """
        if not self.scenarios:
            return pd.DataFrame()

        from financial_model.analysis.types import METRIC_DISPLAY

        # 找到基准情景
        base_metrics: dict[MetricKey, float | None] = {}
        for s in self.scenarios:
            if s.name == self.base_name:
                base_metrics = s.metrics
                break

        rows: list[dict[str, Any]] = []
        for key in self.metrics_keys:
            base_val = base_metrics.get(key)
            row: dict[str, Any] = {"指标": METRIC_DISPLAY.get(key, key.value)}
            for s in self.scenarios:
                if s.name == self.base_name:
                    row[s.name] = "基准"
                    continue
                val = s.metrics.get(key)
                if val is None or base_val is None:
                    row[s.name] = "N/A"
                else:
                    diff = val - base_val
                    if key in (
                        MetricKey.IRR_TOTAL,
                        MetricKey.IRR_EQUITY,
                        MetricKey.DSCR_MIN,
                        MetricKey.DSCR_AVG,
                    ):
                        # 百分比指标显示百分点差异
                        row[s.name] = f"{diff:+.2%}"
                    else:
                        row[s.name] = f"{diff:+,.2f}"
            rows.append(row)

        return pd.DataFrame(rows).set_index("指标")


# ══════════════════════════════════════════════════════════
# ScenarioEngine
# ══════════════════════════════════════════════════════════


class ScenarioEngine:
    """情景分析引擎

    用法::

        engine = ScenarioEngine(ModelConfig.from_excel_v17())
        result = engine.run([
            ScenarioDefinition("悲观", {...}),
            ScenarioDefinition("基准", {}),
            ScenarioDefinition("乐观", {...}),
        ])
    """

    def __init__(
        self,
        base_config: ModelConfig,
        metrics: list[MetricKey] | None = None,
    ) -> None:
        self._base = base_config
        self._metrics = metrics or DEFAULT_METRICS

    def run(
        self,
        definitions: list[ScenarioDefinition],
        base_name: str = "基准",
    ) -> ScenarioComparison:
        """运行多个情景

        Args:
            definitions: 情景定义列表
            base_name: 基准情景名称 (用于 delta_table)

        Returns:
            ScenarioComparison with results for all scenarios.
        """
        scenarios: list[ScenarioResult] = []

        for defn in definitions:
            config = defn.apply_to(self._base)
            results = config.to_orchestrator().run()
            metrics = extract_metrics(results, self._metrics)
            scenarios.append(
                ScenarioResult(
                    name=defn.name,
                    metrics=metrics,
                    config=config,
                )
            )

        return ScenarioComparison(
            base_name=base_name,
            metrics_keys=self._metrics,
            scenarios=scenarios,
        )

    def run_preset_scenarios(
        self,
        presets: list[PresetScenario] | None = None,
    ) -> ScenarioComparison:
        """运行预设情景 (悲观/基准/乐观)

        Args:
            presets: 要运行的预设情景列表, 默认全部三个

        Returns:
            ScenarioComparison with preset results.
        """
        if presets is None:
            presets = [
                PresetScenario.PESSIMISTIC,
                PresetScenario.BASE,
                PresetScenario.OPTIMISTIC,
            ]

        definitions: list[ScenarioDefinition] = []
        for preset in presets:
            name = _PRESET_DISPLAY[preset]
            overrides = _PRESET_OVERRIDES[preset]
            definitions.append(ScenarioDefinition(name, overrides))

        return self.run(definitions, base_name="基准")
