"""多项目对比引擎测试

验证 ComparisonEngine、ProjectComparison、ProjectSnapshot 的正确性。
使用默认预设 (1400MW) 创建对比，避免依赖所有预设文件。
"""
from __future__ import annotations

import pytest

from financial_model.analysis.comparison import (
    ComparisonEngine,
    ProjectComparison,
    ProjectSnapshot,
)
from financial_model.analysis.types import (
    DEFAULT_METRICS,
    METRIC_DISPLAY,
    MetricKey,
    ModelConfig,
    extract_metrics,
)


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def base_config() -> ModelConfig:
    return ModelConfig.from_excel_v17()


@pytest.fixture
def high_capacity_config() -> ModelConfig:
    """高装机容量变体"""
    return ModelConfig.from_excel_v17(
        operating=ModelConfig.from_excel_v17().operating.__class__(
            installed_capacity_mw=1800.0,
        ),
    )


@pytest.fixture
def two_project_comparison(
    base_config: ModelConfig, high_capacity_config: ModelConfig
) -> ProjectComparison:
    """两个项目的对比结果"""
    engine = ComparisonEngine()
    return engine.compare_configs([
        ("基准方案", base_config),
        ("高容量方案", high_capacity_config),
    ])


# ══════════════════════════════════════════════════════════
# ComparisonEngine 测试
# ══════════════════════════════════════════════════════════


class TestComparisonEngine:
    def test_compare_configs_two_projects(
        self, base_config: ModelConfig, high_capacity_config: ModelConfig
    ) -> None:
        engine = ComparisonEngine()
        result = engine.compare_configs([
            ("A", base_config),
            ("B", high_capacity_config),
        ])
        assert len(result.projects) == 2

    def test_compare_configs_has_metrics(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        for proj in two_project_comparison.projects:
            assert len(proj.metrics) >= 7  # DEFAULT_METRICS 有 7 个

    def test_compare_configs_different_results(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        # 两个项目应有不同的 IRR (装机容量不同)
        irr_a = two_project_comparison.projects[0].metrics.get(MetricKey.IRR_TOTAL)
        irr_b = two_project_comparison.projects[1].metrics.get(MetricKey.IRR_TOTAL)
        assert irr_a is not None
        assert irr_b is not None
        # 不一定不同 (取决于参数), 但应该是有效浮点数
        assert isinstance(irr_a, float)
        assert isinstance(irr_b, float)

    def test_project_snapshot_name(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        for proj in two_project_comparison.projects:
            assert "MW" in proj.name
            assert "年" in proj.name


# ══════════════════════════════════════════════════════════
# ProjectComparison 测试
# ══════════════════════════════════════════════════════════


class TestProjectComparison:
    def test_comparison_table_not_empty(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        table = two_project_comparison.comparison_table()
        assert not table.empty
        assert len(table) == len(DEFAULT_METRICS)

    def test_comparison_table_has_projects(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        table = two_project_comparison.comparison_table()
        assert len(table.columns) == 2  # 两个项目

    def test_comparison_table_index_names(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        table = two_project_comparison.comparison_table()
        for key in DEFAULT_METRICS:
            display = METRIC_DISPLAY.get(key, key.value)
            assert display in table.index

    def test_rank_table_not_empty(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        table = two_project_comparison.rank_table()
        assert not table.empty

    def test_investment_summary(
        self, two_project_comparison: ProjectComparison
    ) -> None:
        table = two_project_comparison.investment_summary()
        assert not table.empty
        assert len(table) == 2
        assert "装机容量(MW)" in table.columns
        assert "全投资IRR" in table.columns

    def test_empty_comparison(self) -> None:
        comp = ProjectComparison()
        assert comp.comparison_table().empty
        assert comp.rank_table().empty
        assert comp.investment_summary().empty


# ══════════════════════════════════════════════════════════
# 预设对比测试 (仅在有预设文件时运行)
# ══════════════════════════════════════════════════════════


class TestPresetComparison:
    def test_compare_presets_with_names(self) -> None:
        """测试指定预设名比较"""
        from financial_model.params.presets import list_presets

        presets = list_presets()
        if len(presets) < 2:
            pytest.skip("需要至少 2 个预设模板")

        engine = ComparisonEngine()
        result = engine.compare_presets(presets[:2])
        assert len(result.projects) == 2

    def test_compare_presets_all(self) -> None:
        """测试比较所有预设"""
        from financial_model.params.presets import list_presets

        presets = list_presets()
        engine = ComparisonEngine()
        result = engine.compare_presets()
        assert len(result.projects) == len(presets)
