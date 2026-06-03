"""多项目对比引擎 — 不同预设模板的横向比较分析

将多个项目配置 (不同装机容量/建设期/运营期) 并行运行编排器，
输出指标对比表、雷达图数据和排名表。

典型用法::

    from financial_model.analysis.comparison import ComparisonEngine

    engine = ComparisonEngine()
    result = engine.compare_presets()  # 默认比较所有预设
    print(result.comparison_table())
    print(result.rank_table())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from financial_model.analysis.types import (
    DEFAULT_METRICS,
    METRIC_DISPLAY,
    MetricKey,
    ModelConfig,
    extract_metrics,
)
from financial_model.engines.orchestrator import AllResults


# ══════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProjectSnapshot:
    """单个项目快照

    Attributes:
        name: 显示名称 (如 "1400MW/8年")
        preset_name: 预设名称 (如 "pshp_1400mw_8yr")
        config: 模型配置
        results: 完整计算结果
        metrics: 关键指标值
    """

    name: str
    preset_name: str
    config: ModelConfig
    results: AllResults
    metrics: dict[MetricKey, float | None]


@dataclass
class ProjectComparison:
    """多项目横向对比结果

    Attributes:
        projects: 各项目快照
        metric_keys: 对比指标列表
    """

    projects: list[ProjectSnapshot] = field(default_factory=list)
    metric_keys: list[MetricKey] = field(default_factory=lambda: list(DEFAULT_METRICS))

    def comparison_table(self) -> pd.DataFrame:
        """生成对比表 (指标 × 项目)

        Returns:
            DataFrame with metric display names as index,
            project names as columns.
        """
        if not self.projects:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for key in self.metric_keys:
            row: dict[str, Any] = {"指标": METRIC_DISPLAY.get(key, key.value)}
            for proj in self.projects:
                val = proj.metrics.get(key)
                if val is None:
                    row[proj.name] = "N/A"
                elif key in (
                    MetricKey.IRR_TOTAL,
                    MetricKey.IRR_EQUITY,
                    MetricKey.DSCR_MIN,
                    MetricKey.DSCR_AVG,
                    MetricKey.ROE_AVG,
                ):
                    row[proj.name] = f"{val:.2%}"
                elif key == MetricKey.PROJECT_YEARS:
                    row[proj.name] = f"{int(val)}"
                else:
                    row[proj.name] = f"{val:,.2f}"
            rows.append(row)

        return pd.DataFrame(rows).set_index("指标")

    def rank_table(self) -> pd.DataFrame:
        """生成排名表

        对每个指标排名 (IRR 越大越好, 回收期越短越好)。
        """
        if not self.projects:
            return pd.DataFrame()

        # 排序方向: True = 越大越好, False = 越小越好
        descending: set[MetricKey] = {
            MetricKey.IRR_TOTAL, MetricKey.IRR_EQUITY,
            MetricKey.NPV_TOTAL, MetricKey.NPV_EQUITY,
            MetricKey.DSCR_MIN, MetricKey.DSCR_AVG,
            MetricKey.ROE_AVG,
        }
        ascending_keys: set[MetricKey] = {
            MetricKey.PAYBACK_STATIC, MetricKey.PAYBACK_DYNAMIC,
            MetricKey.PROJECT_YEARS,
        }

        rows: list[dict[str, Any]] = []
        for key in self.metric_keys:
            # 收集有效值
            vals: list[tuple[str, float]] = []
            for proj in self.projects:
                v = proj.metrics.get(key)
                if v is not None:
                    vals.append((proj.name, v))

            if not vals:
                continue

            # 排序
            reverse = key in descending
            vals_sorted = sorted(vals, key=lambda x: x[1], reverse=reverse)

            row: dict[str, Any] = {"指标": METRIC_DISPLAY.get(key, key.value)}
            for rank, (name, val) in enumerate(vals_sorted, 1):
                row[f"#{rank}"] = f"{name} ({val:.4g})"
            rows.append(row)

        return pd.DataFrame(rows).set_index("指标")

    def investment_summary(self) -> pd.DataFrame:
        """投资概要对比表"""
        if not self.projects:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for proj in self.projects:
            invest_total = float(proj.results.investment["construction_investment"].sum())
            fin = proj.results.financing
            dm = proj.results.derived_metrics

            rows.append({
                "项目": proj.name,
                "装机容量(MW)": proj.config.operating.installed_capacity_mw,
                "建设期(年)": proj.config.construction.construction_years,
                "运营期(年)": proj.config.construction.operation_years,
                "建设投资(万元)": f"{invest_total:,.0f}",
                "建设期利息(万元)": f"{fin.construction_interest_total:,.0f}",
                "动态总投资(万元)": f"{fin.dynamic_total_investment:,.0f}",
                "全投资IRR": f"{dm.irr_total:.2%}" if dm.irr_total else "N/A",
                "全投资NPV(万元)": f"{dm.npv_total:,.0f}",
                "最低DSCR": f"{dm.dscr_min:.2f}" if dm.dscr_min else "N/A",
            })

        return pd.DataFrame(rows).set_index("项目")


# ══════════════════════════════════════════════════════════
# ComparisonEngine
# ══════════════════════════════════════════════════════════


class ComparisonEngine:
    """多项目对比引擎

    用法::

        engine = ComparisonEngine()

        # 比较所有预设
        result = engine.compare_presets()

        # 比较指定预设
        result = engine.compare_presets(["pshp_600mw_5yr", "pshp_1400mw_8yr"])

        # 比较自定义配置
        result = engine.compare_configs([
            ("方案A", ModelConfig.from_excel_v17()),
            ("方案B", config2),
        ])
    """

    def compare_presets(
        self,
        preset_names: list[str] | None = None,
        metrics: list[MetricKey] | None = None,
    ) -> ProjectComparison:
        """比较预设模板

        Args:
            preset_names: 预设名称列表, None 则比较所有
            metrics: 对比指标列表, None 使用默认

        Returns:
            ProjectComparison 对比结果
        """
        from financial_model.params.presets import list_presets, load_preset, load_preset_metadata

        if preset_names is None:
            preset_names = list_presets()

        configs: list[tuple[str, ModelConfig]] = []
        for name in preset_names:
            config = load_preset(name)
            meta = load_preset_metadata(name)
            display = meta.get("name", name)
            configs.append((display, config))

        return self.compare_configs(configs, metrics)

    def compare_configs(
        self,
        configs: list[tuple[str, ModelConfig]],
        metrics: list[MetricKey] | None = None,
    ) -> ProjectComparison:
        """比较自定义配置

        Args:
            configs: [(显示名, ModelConfig), ...] 列表
            metrics: 对比指标列表

        Returns:
            ProjectComparison 对比结果
        """
        metric_keys = metrics or list(DEFAULT_METRICS)
        snapshots: list[ProjectSnapshot] = []

        for display_name, config in configs:
            results = config.to_orchestrator().run()
            extracted = extract_metrics(results, metric_keys)

            # 生成简短名称
            c = config.construction
            cap = config.operating.installed_capacity_mw
            short_name = f"{display_name} ({cap:.0f}MW/{c.construction_years}年)"

            snapshots.append(ProjectSnapshot(
                name=short_name,
                preset_name=display_name,
                config=config,
                results=results,
                metrics=extracted,
            ))

        return ProjectComparison(
            projects=snapshots,
            metric_keys=metric_keys,
        )
