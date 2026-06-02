"""蒙特卡罗模拟引擎 — 概率分布采样 → 统计结果

从参数的概率分布中随机采样, 多次运行编排器, 汇总统计指标
(均值、标准差、P10/P50/P90、置信区间)。

典型用法::

    from financial_model.analysis.types import ModelConfig, ParamSpec, MetricKey

    config = ModelConfig.from_excel_v17()
    engine = MonteCarloEngine(config)

    # 定义参数分布
    result = engine.run(
        param_distributions=[
            ParamDistribution(
                spec=ParamSpec("operating", "grid_price", "上网电价"),
                distribution=DistributionType.NORMAL,
                dist_params={"std": 0.035},  # 均值=当前值, 标准差=0.035
            ),
            ParamDistribution(
                spec=ParamSpec("operating", "pump_price", "抽水电价"),
                distribution=DistributionType.UNIFORM,
                dist_params={"low_offset": -0.05, "high_offset": 0.05},
            ),
        ],
        iterations=1000,
        seed=42,
    )

    # 统计结果
    print(result.summary_table())
    print(result.percentile_table(MetricKey.IRR_TOTAL))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
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
# 分布类型
# ══════════════════════════════════════════════════════════


class DistributionType(str, Enum):
    """概率分布类型

    所有分布以当前参数值为基准 (base):
      - NORMAL: 正态分布, base 为均值, std 为标准差
      - UNIFORM: 均匀分布, [base + low_offset, base + high_offset]
      - TRIANGULAR: 三角分布, [base + low_offset, base, base + high_offset]
      - LOGNORMAL: 对数正态分布, base 为中位数, sigma 为形状参数
    """

    NORMAL = "normal"
    UNIFORM = "uniform"
    TRIANGULAR = "triangular"
    LOGNORMAL = "lognormal"


@dataclass(frozen=True)
class ParamDistribution:
    """参数分布配置 — 描述一个参数如何随机变化

    Attributes:
        spec: 参数规格
        distribution: 分布类型
        dist_params: 分布参数, 含义取决于分布类型:
            - normal: {"std": 标准差}
            - uniform: {"low_offset": 下偏移, "high_offset": 上偏移}
            - triangular: {"low_offset": 下偏移, "high_offset": 上偏移}
            - lognormal: {"sigma": 形状参数}
    """

    spec: ParamSpec
    distribution: DistributionType
    dist_params: dict[str, float] = field(default_factory=dict)

    def sample(
        self,
        base_value: float,
        rng: np.random.Generator,
        size: int = 1,
    ) -> np.ndarray:
        """从分布中采样

        Args:
            base_value: 当前参数值 (分布中心)
            rng: NumPy 随机数生成器
            size: 采样数量

        Returns:
            采样值数组
        """
        if self.distribution == DistributionType.NORMAL:
            std = self.dist_params.get("std", base_value * 0.1)
            return rng.normal(loc=base_value, scale=std, size=size)

        if self.distribution == DistributionType.UNIFORM:
            low_off = self.dist_params.get("low_offset", -base_value * 0.1)
            high_off = self.dist_params.get("high_offset", base_value * 0.1)
            return rng.uniform(
                low=base_value + low_off,
                high=base_value + high_off,
                size=size,
            )

        if self.distribution == DistributionType.TRIANGULAR:
            low_off = self.dist_params.get("low_offset", -base_value * 0.1)
            high_off = self.dist_params.get("high_offset", base_value * 0.1)
            return rng.triangular(
                left=base_value + low_off,
                mode=base_value,
                right=base_value + high_off,
                size=size,
            )

        if self.distribution == DistributionType.LOGNORMAL:
            sigma = self.dist_params.get("sigma", 0.1)
            # 对数正态: median = base_value → mu = ln(base_value)
            mu = np.log(base_value) if base_value > 0 else 0.0
            return rng.lognormal(mean=mu, sigma=sigma, size=size)

        raise ValueError(f"Unsupported distribution: {self.distribution}")


# ══════════════════════════════════════════════════════════
# 结果数据结构
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SimulationRun:
    """单次蒙特卡罗模拟结果

    Attributes:
        iteration: 迭代号 (0-based)
        param_values: 实际使用的参数值 {spec.display_name: value}
        metrics: 本次运行的指标值
    """

    iteration: int
    param_values: dict[str, float]
    metrics: dict[MetricKey, float | None]


@dataclass
class MonteCarloResult:
    """蒙特卡罗模拟完整结果

    Attributes:
        base_metrics: 基准指标值
        iterations: 运行次数
        param_distributions: 参数分布配置
        runs: 所有模拟结果
    """

    base_metrics: dict[MetricKey, float | None]
    iterations: int
    param_distributions: list[ParamDistribution]
    runs: list[SimulationRun] = field(default_factory=list)

    def metric_series(self, key: MetricKey) -> np.ndarray:
        """获取某个指标的所有模拟值"""
        values = []
        for run in self.runs:
            v = run.metrics.get(key)
            if v is not None:
                values.append(v)
        return np.array(values)

    def statistics(self, key: MetricKey) -> dict[str, float]:
        """计算单个指标的统计量

        Returns:
            mean, std, min, max, P10, P25, P50, P75, P90
        """
        values = self.metric_series(key)
        if len(values) == 0:
            return {}

        return {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "P10": float(np.percentile(values, 10)),
            "P25": float(np.percentile(values, 25)),
            "P50": float(np.percentile(values, 50)),
            "P75": float(np.percentile(values, 75)),
            "P90": float(np.percentile(values, 90)),
        }

    def summary_table(self) -> pd.DataFrame:
        """生成所有指标的统计摘要表"""
        from financial_model.analysis.types import DEFAULT_METRICS as _DM

        keys = list(set(self.runs[0].metrics.keys()) if self.runs else _DM)
        rows: list[dict[str, Any]] = []
        for key in keys:
            stats = self.statistics(key)
            if not stats:
                continue
            display = METRIC_DISPLAY.get(key, key.value)
            row: dict[str, Any] = {"指标": display}
            row["均值"] = stats["mean"]
            row["标准差"] = stats["std"]
            row["P10"] = stats["P10"]
            row["P50"] = stats["P50"]
            row["P90"] = stats["P90"]
            row["最小值"] = stats["min"]
            row["最大值"] = stats["max"]
            rows.append(row)

        return pd.DataFrame(rows).set_index("指标")

    def percentile_table(self, key: MetricKey) -> pd.DataFrame:
        """生成单个指标的百分位表

        用于绘制累计分布函数 (CDF)。
        """
        values = self.metric_series(key)
        if len(values) == 0:
            return pd.DataFrame()

        percentiles = np.arange(0, 101, 5)
        vals = np.percentile(values, percentiles)
        display = METRIC_DISPLAY.get(key, key.value)

        return pd.DataFrame(
            {"百分位": percentiles, display: vals}
        ).set_index("百分位")

    def confidence_interval(
        self, key: MetricKey, level: float = 0.95
    ) -> tuple[float, float]:
        """计算置信区间

        Args:
            key: 指标键
            level: 置信水平 (默认 95%)

        Returns:
            (下界, 上界) 元组
        """
        values = self.metric_series(key)
        if len(values) == 0:
            return (0.0, 0.0)

        alpha = (1 - level) / 2
        lower = float(np.percentile(values, alpha * 100))
        upper = float(np.percentile(values, (1 - alpha) * 100))
        return (lower, upper)

    def probability_above(
        self, key: MetricKey, threshold: float
    ) -> float:
        """计算指标超过阈值的概率

        如: P(IRR > 8%) = ?
        """
        values = self.metric_series(key)
        if len(values) == 0:
            return 0.0
        return float(np.mean(values > threshold))


# ══════════════════════════════════════════════════════════
# MonteCarloEngine
# ══════════════════════════════════════════════════════════


class MonteCarloEngine:
    """蒙特卡罗模拟引擎

    用法::

        engine = MonteCarloEngine(ModelConfig.from_excel_v17())
        result = engine.run(
            param_distributions=[...],
            iterations=1000,
            seed=42,
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
        param_distributions: list[ParamDistribution],
        iterations: int = 1000,
        seed: int | None = None,
    ) -> MonteCarloResult:
        """运行蒙特卡罗模拟

        Args:
            param_distributions: 参数分布配置列表
            iterations: 模拟次数 (默认 1000)
            seed: 随机种子 (可复现)

        Returns:
            MonteCarloResult with all simulation runs and statistics.
        """
        rng = np.random.default_rng(seed)

        # 基准运行
        base_results = self._base.to_orchestrator().run()
        base_metrics = extract_metrics(base_results, self._metrics)

        # 预采样所有参数值 (iterations × params)
        base_values: dict[str, float] = {}
        sampled: dict[str, np.ndarray] = {}

        for pd_config in param_distributions:
            bv = pd_config.spec.get_value(self._base)
            if isinstance(bv, tuple):
                # 时间序列 — 不支持 MC, 跳过
                continue
            base_values[pd_config.spec.display_name] = float(bv)
            sampled[pd_config.spec.display_name] = pd_config.sample(
                float(bv), rng, size=iterations
            )

        # 逐次运行
        result = MonteCarloResult(
            base_metrics=base_metrics,
            iterations=iterations,
            param_distributions=param_distributions,
        )

        for i in range(iterations):
            # 构建本次参数值
            config = self._base
            param_vals: dict[str, float] = {}

            for pd_config in param_distributions:
                name = pd_config.spec.display_name
                if name not in sampled:
                    continue
                val = float(sampled[name][i])

                # 对非负参数做截断
                if val < 0 and pd_config.spec.field in (
                    "grid_price",
                    "pump_price",
                    "capacity_price",
                    "installed_capacity_mw",
                    "annual_utilization_hours",
                ):
                    val = 0.0

                config = pd_config.spec.set_value(config, val)
                param_vals[name] = val

            # 运行模型
            run_results = config.to_orchestrator().run()
            run_metrics = extract_metrics(run_results, self._metrics)

            result.runs.append(
                SimulationRun(
                    iteration=i,
                    param_values=param_vals,
                    metrics=run_metrics,
                )
            )

        return result
