"""敏感性分析引擎 — 单参数扰动 → 敏感度表 → 龙卷风图数据

逐个参数按 ±N% 扰动, 记录关键指标变化, 生成敏感性矩阵和龙卷风图排序。

典型用法::

    from financial_model.analysis.types import ModelConfig, ParamSpec, COMMON_PARAMS

    config = ModelConfig.from_excel_v17()
    engine = SensitivityEngine(config)

    # 使用常用参数列表
    result = engine.run(
        params=COMMON_PARAMS[:5],
        perturbations=[-0.1, -0.05, 0.05, 0.1],
    )

    # 龙卷风图数据
    tornado = result.tornado_data(MetricKey.IRR_TOTAL)

    # 敏感性矩阵
    print(result.matrix_table())
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from financial_model.analysis.types import (
    DEFAULT_METRICS,
    METRIC_DISPLAY,
    MetricKey,
    ModelConfig,
    ParamSpec,
    extract_metrics,
)


# ══════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SensitivityItem:
    """单参数单次扰动的结果

    Attributes:
        param: 参数规格
        perturbation: 扰动比例 (0.1 = +10%)
        original_value: 原始参数值
        perturbed_value: 扰动后参数值
        metrics: 扰动后的指标值
        delta: 与基准的指标差异 {MetricKey: delta_value}
    """

    param: ParamSpec
    perturbation: float
    original_value: float | tuple[float, ...]
    perturbed_value: float | tuple[float, ...]
    metrics: dict[MetricKey, float | None]
    delta: dict[MetricKey, float | None]


@dataclass
class SensitivityResult:
    """敏感性分析完整结果

    Attributes:
        base_metrics: 基准指标值
        params: 分析的参数列表
        perturbations: 扰动比例列表
        items: 所有扰动结果 (参数 × 扰动)
    """

    base_metrics: dict[MetricKey, float | None]
    params: list[ParamSpec]
    perturbations: list[float]
    items: list[SensitivityItem] = field(default_factory=list)

    def matrix_table(self) -> pd.DataFrame:
        """生成敏感性矩阵 (参数 × 扰动比例)

        每个单元格显示指定指标的值。
        默认显示全投资IRR。

        Returns:
            DataFrame with param display names as index,
            perturbation percentages as columns.
        """
        if not self.items:
            return pd.DataFrame()

        # 默认用第一个IRR类指标
        metric_key = MetricKey.IRR_TOTAL
        if self.base_metrics.get(metric_key) is None:
            metric_key = MetricKey.NPV_TOTAL

        return self._matrix_for_metric(metric_key)

    def _matrix_for_metric(self, key: MetricKey) -> pd.DataFrame:
        """为指定指标生成矩阵"""
        rows: dict[str, dict[str, str]] = {}

        for param in self.params:
            row: dict[str, str] = {}
            for pert in self.perturbations:
                item = self._find_item(param, pert)
                if item is not None:
                    val = item.metrics.get(key)
                    if val is not None:
                        if key in (
                            MetricKey.IRR_TOTAL,
                            MetricKey.IRR_EQUITY,
                            MetricKey.DSCR_MIN,
                            MetricKey.DSCR_AVG,
                        ):
                            row[f"{pert:+.0%}"] = f"{val:.2%}"
                        else:
                            row[f"{pert:+.0%}"] = f"{val:,.2f}"
                    else:
                        row[f"{pert:+.0%}"] = "N/A"
                else:
                    row[f"{pert:+.0%}"] = "-"
            rows[param.display_name] = row

        return pd.DataFrame(rows).T

    def tornado_data(
        self, key: MetricKey = MetricKey.IRR_TOTAL
    ) -> pd.DataFrame:
        """生成龙卷风图数据

        对每个参数, 计算最大正/负扰动对指定指标的影响,
        按|最大影响|降序排列。

        Args:
            key: 目标指标 (默认 IRR_TOTAL)

        Returns:
            DataFrame with columns: param, negative, positive, spread
        """
        if not self.items:
            return pd.DataFrame()

        base_val = self.base_metrics.get(key)
        if base_val is None:
            return pd.DataFrame()

        rows: list[dict[str, float | str]] = []
        for param in self.params:
            # 找到最大正/负扰动
            pos_delta = 0.0
            neg_delta = 0.0
            for item in self.items:
                if item.param != param:
                    continue
                d = item.delta.get(key)
                if d is not None:
                    if d > pos_delta:
                        pos_delta = d
                    if d < neg_delta:
                        neg_delta = d

            rows.append(
                {
                    "param": param.display_name,
                    "negative": neg_delta,
                    "positive": pos_delta,
                    "spread": pos_delta - neg_delta,
                }
            )

        df = pd.DataFrame(rows)
        return df.sort_values("spread", ascending=False).reset_index(drop=True)

    def _find_item(
        self, param: ParamSpec, perturbation: float
    ) -> SensitivityItem | None:
        """查找特定参数和扰动比例的结果"""
        for item in self.items:
            if item.param == param and item.perturbation == perturbation:
                return item
        return None


# ══════════════════════════════════════════════════════════
# SensitivityEngine
# ══════════════════════════════════════════════════════════


class SensitivityEngine:
    """敏感性分析引擎

    用法::

        engine = SensitivityEngine(ModelConfig.from_excel_v17())
        result = engine.run(
            params=COMMON_PARAMS[:5],
            perturbations=[-0.1, -0.05, 0.05, 0.1],
        )
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
        params: list[ParamSpec],
        perturbations: list[float] | None = None,
    ) -> SensitivityResult:
        """运行敏感性分析

        对每个参数 × 每个扰动比例, 执行一次完整模型计算。

        Args:
            params: 要分析的参数规格列表
            perturbations: 扰动比例列表, 默认 [-10%, -5%, +5%, +10%]

        Returns:
            SensitivityResult with all perturbation results.
        """
        if perturbations is None:
            perturbations = [-0.10, -0.05, 0.05, 0.10]

        # 运行基准
        base_results = self._base.to_orchestrator().run()
        base_metrics = extract_metrics(base_results, self._metrics)

        result = SensitivityResult(
            base_metrics=base_metrics,
            params=params,
            perturbations=perturbations,
        )

        # 逐参数 × 逐扰动
        for param in params:
            original_value = param.get_value(self._base)
            for pct in perturbations:
                perturbed_config = param.perturb(self._base, pct)
                perturbed_results = perturbed_config.to_orchestrator().run()
                perturbed_metrics = extract_metrics(
                    perturbed_results, self._metrics
                )
                perturbed_value = param.get_value(perturbed_config)

                # 计算偏差
                delta: dict[MetricKey, float | None] = {}
                for key in self._metrics:
                    base_v = base_metrics.get(key)
                    pert_v = perturbed_metrics.get(key)
                    if base_v is not None and pert_v is not None:
                        delta[key] = pert_v - base_v
                    else:
                        delta[key] = None

                result.items.append(
                    SensitivityItem(
                        param=param,
                        perturbation=pct,
                        original_value=original_value,
                        perturbed_value=perturbed_value,
                        metrics=perturbed_metrics,
                        delta=delta,
                    )
                )

        return result
