"""Phase 5 测试: 分析工具层 — types / scenario / sensitivity / monte_carlo

验证:
  1. types: ModelConfig 创建/扰动/工厂, ParamSpec 操作, extract_metrics 完整性
  2. scenario: 预设情景运行, 自定义情景, 对比表/偏差表
  3. sensitivity: 扰动正确性, 方向验证, 对称性, 龙卷风图数据
  4. monte_carlo: 可复现性, 统计量正确性, 概率计算, 分布类型
"""
from __future__ import annotations

import pytest
from datetime import date

import numpy as np
import pandas as pd

from financial_model.analysis.types import (
    COMMON_PARAMS,
    DEFAULT_METRICS,
    METRIC_DISPLAY,
    MetricKey,
    ModelConfig,
    ParamSpec,
    extract_metrics,
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
from financial_model.analysis.monte_carlo import (
    DistributionType,
    MonteCarloEngine,
    MonteCarloResult,
    ParamDistribution,
    SimulationRun,
)
from financial_model.params.operating import OperatingParams
from financial_model.params.financing import FinancingParams


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def base_config() -> ModelConfig:
    """黄金基准配置"""
    return ModelConfig.from_excel_v17()


@pytest.fixture
def base_metrics(base_config: ModelConfig) -> dict[MetricKey, float | None]:
    """基准指标"""
    results = base_config.to_orchestrator().run()
    return extract_metrics(results)


@pytest.fixture
def grid_price_spec() -> ParamSpec:
    """上网电价参数规格"""
    return ParamSpec("operating", "grid_price", "上网电价(元/kWh)")


@pytest.fixture
def pump_price_spec() -> ParamSpec:
    """抽水电价参数规格"""
    return ParamSpec("operating", "pump_price", "抽水电价(元/kWh)")


# ══════════════════════════════════════════════════════════
# 1. Types 测试
# ══════════════════════════════════════════════════════════


class TestModelConfig:
    """ModelConfig 创建和修改"""

    def test_from_excel_v17_creates_valid_config(
        self, base_config: ModelConfig
    ) -> None:
        assert base_config.construction.construction_start == date(2023, 2, 1)
        assert base_config.construction.construction_end == date(2030, 7, 31)
        assert base_config.operating.grid_price == 0.35
        assert base_config.discount_rate == 0.08

    def test_to_orchestrator_runs(self, base_config: ModelConfig) -> None:
        results = base_config.to_orchestrator().run()
        assert results.derived_metrics is not None
        assert results.derived_metrics.irr_total is not None

    def test_with_param_scalar(
        self, base_config: ModelConfig
    ) -> None:
        new_config = base_config.with_param("operating", "grid_price", 0.40)
        assert new_config.operating.grid_price == 0.40
        # 原始配置不变
        assert base_config.operating.grid_price == 0.35

    def test_with_param_immutable(
        self, base_config: ModelConfig
    ) -> None:
        c1 = base_config.with_param("operating", "grid_price", 0.40)
        c2 = base_config.with_param("operating", "grid_price", 0.50)
        assert c1.operating.grid_price == 0.40
        assert c2.operating.grid_price == 0.50
        assert base_config.operating.grid_price == 0.35

    def test_with_param_invalid_group(
        self, base_config: ModelConfig
    ) -> None:
        with pytest.raises(ValueError, match="Unknown param group"):
            base_config.with_param("nonexistent", "field", 0)

    def test_from_excel_v17_with_overrides(self) -> None:
        custom_operating = OperatingParams(
            installed_capacity_mw=1200.0,
            annual_utilization_hours=1000.0,
            grid_price=0.30,
            pump_price=0.20,
        )
        config = ModelConfig.from_excel_v17(operating=custom_operating)
        assert config.operating.installed_capacity_mw == 1200.0


class TestParamSpec:
    """ParamSpec 参数扰动"""

    def test_get_value(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        assert grid_price_spec.get_value(base_config) == 0.35

    def test_perturb_multiply(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        new_config = grid_price_spec.perturb(base_config, 0.1)
        assert new_config.operating.grid_price == pytest.approx(0.385)

    def test_perturb_negative(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        new_config = grid_price_spec.perturb(base_config, -0.1)
        assert new_config.operating.grid_price == pytest.approx(0.315)

    def test_perturb_zero_is_identity(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        new_config = grid_price_spec.perturb(base_config, 0.0)
        assert new_config.operating.grid_price == pytest.approx(0.35)

    def test_set_value(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        new_config = grid_price_spec.set_value(base_config, 0.50)
        assert new_config.operating.grid_price == 0.50

    def test_perturb_tuple_field(self, base_config: ModelConfig) -> None:
        """时间序列 tuple 扰动 — 每个元素乘以比例"""
        spec = ParamSpec("operating", "production_ratios", "达产比例")
        ratios = base_config.operating.production_ratios
        assert len(ratios) > 0

        new_config = spec.perturb(base_config, 0.1)
        new_ratios = new_config.operating.production_ratios
        # 每个元素应为 original * 1.1
        for old, new in zip(ratios, new_ratios):
            assert new == pytest.approx(old * 1.1, rel=1e-10)

    def test_invalid_group_raises(self, base_config: ModelConfig) -> None:
        spec = ParamSpec("invalid_group", "field", "bad")
        with pytest.raises(ValueError):
            spec.get_value(base_config)


class TestExtractMetrics:
    """extract_metrics 函数"""

    def test_default_metrics(self, base_config: ModelConfig) -> None:
        results = base_config.to_orchestrator().run()
        metrics = extract_metrics(results)
        assert set(metrics.keys()) == set(DEFAULT_METRICS)

    def test_custom_keys(self, base_config: ModelConfig) -> None:
        results = base_config.to_orchestrator().run()
        keys = [MetricKey.IRR_TOTAL, MetricKey.NPV_TOTAL]
        metrics = extract_metrics(results, keys)
        assert set(metrics.keys()) == set(keys)

    def test_irr_total_is_positive(self, base_config: ModelConfig) -> None:
        results = base_config.to_orchestrator().run()
        metrics = extract_metrics(results, [MetricKey.IRR_TOTAL])
        assert metrics[MetricKey.IRR_TOTAL] is not None
        assert metrics[MetricKey.IRR_TOTAL] > 0

    def test_npv_total_is_negative(self, base_config: ModelConfig) -> None:
        """全投资 NPV 在 8% 折现率下应为负 (基准模型 IRR < 8%)"""
        results = base_config.to_orchestrator().run()
        metrics = extract_metrics(results, [MetricKey.NPV_TOTAL])
        assert metrics[MetricKey.NPV_TOTAL] is not None
        assert metrics[MetricKey.NPV_TOTAL] < 0

    def test_dscr_min_above_one(self, base_config: ModelConfig) -> None:
        """DSCR 应 > 1 (项目有偿债能力)"""
        results = base_config.to_orchestrator().run()
        metrics = extract_metrics(results, [MetricKey.DSCR_MIN])
        assert metrics[MetricKey.DSCR_MIN] is not None
        assert metrics[MetricKey.DSCR_MIN] > 1.0

    def test_project_years(self, base_config: ModelConfig) -> None:
        results = base_config.to_orchestrator().run()
        metrics = extract_metrics(results, [MetricKey.PROJECT_YEARS])
        assert metrics[MetricKey.PROJECT_YEARS] == 48.0


class TestCommonParams:
    """COMMON_PARAMS 预设列表"""

    def test_count(self) -> None:
        assert len(COMMON_PARAMS) >= 10

    def test_all_groups_valid(self) -> None:
        valid_groups = {
            "construction", "investment", "financing",
            "operating", "tax", "depreciation",
        }
        for spec in COMMON_PARAMS:
            assert spec.group in valid_groups, f"Invalid group: {spec.group}"

    def test_all_fields_readable(self, base_config: ModelConfig) -> None:
        """所有预设参数的 get_value 应可读取"""
        for spec in COMMON_PARAMS:
            val = spec.get_value(base_config)
            assert val is not None, f"Failed to read {spec.display_name}"


class TestMetricDisplay:
    """METRIC_DISPLAY 映射"""

    def test_all_keys_have_display(self) -> None:
        for key in MetricKey:
            assert key in METRIC_DISPLAY
            assert len(METRIC_DISPLAY[key]) > 0


# ══════════════════════════════════════════════════════════
# 2. Scenario 测试
# ══════════════════════════════════════════════════════════


class TestScenarioDefinition:
    """ScenarioDefinition 参数覆盖"""

    def test_empty_overrides_is_identity(
        self, base_config: ModelConfig
    ) -> None:
        defn = ScenarioDefinition("基准", {})
        new_config = defn.apply_to(base_config)
        assert new_config.operating.grid_price == base_config.operating.grid_price

    def test_apply_single_override(
        self, base_config: ModelConfig
    ) -> None:
        defn = ScenarioDefinition(
            "高电价", {"operating": {"grid_price": 0.40}}
        )
        new_config = defn.apply_to(base_config)
        assert new_config.operating.grid_price == 0.40

    def test_apply_multi_group_override(
        self, base_config: ModelConfig
    ) -> None:
        defn = ScenarioDefinition(
            "多参数",
            {
                "operating": {"grid_price": 0.40},
                "financing": {"construction_interest_rate": 0.05},
            },
        )
        new_config = defn.apply_to(base_config)
        assert new_config.operating.grid_price == 0.40
        assert new_config.financing.construction_interest_rate == 0.05


class TestScenarioEngine:
    """ScenarioEngine 情景分析"""

    def test_run_custom_scenarios(
        self, base_config: ModelConfig
    ) -> None:
        engine = ScenarioEngine(base_config)
        result = engine.run([
            ScenarioDefinition("高电价", {"operating": {"grid_price": 0.40}}),
            ScenarioDefinition("基准", {}),
            ScenarioDefinition("低电价", {"operating": {"grid_price": 0.30}}),
        ])
        assert len(result.scenarios) == 3

    def test_higher_price_higher_irr(
        self, base_config: ModelConfig
    ) -> None:
        """电价升高 → IRR 升高"""
        engine = ScenarioEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run([
            ScenarioDefinition("高", {"operating": {"grid_price": 0.40}}),
            ScenarioDefinition("低", {"operating": {"grid_price": 0.30}}),
        ])
        high_irr = result.scenarios[0].metrics[MetricKey.IRR_TOTAL]
        low_irr = result.scenarios[1].metrics[MetricKey.IRR_TOTAL]
        assert high_irr is not None and low_irr is not None
        assert high_irr > low_irr

    def test_base_scenario_matches_baseline(
        self, base_config: ModelConfig, base_metrics: dict
    ) -> None:
        """空覆盖情景应与基准结果一致"""
        engine = ScenarioEngine(base_config)
        result = engine.run([ScenarioDefinition("基准", {})])
        base_scenario_irr = result.scenarios[0].metrics[MetricKey.IRR_TOTAL]
        assert base_scenario_irr == pytest.approx(
            base_metrics[MetricKey.IRR_TOTAL], rel=1e-10
        )

    def test_preset_scenarios_all_three(
        self, base_config: ModelConfig
    ) -> None:
        engine = ScenarioEngine(base_config)
        result = engine.run_preset_scenarios()
        assert len(result.scenarios) == 3
        names = [s.name for s in result.scenarios]
        assert "悲观" in names
        assert "基准" in names
        assert "乐观" in names

    def test_pessimistic_lower_than_optimistic(
        self, base_config: ModelConfig
    ) -> None:
        """悲观 IRR < 乐观 IRR"""
        engine = ScenarioEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run_preset_scenarios()
        irr_values = {
            s.name: s.metrics[MetricKey.IRR_TOTAL] for s in result.scenarios
        }
        assert irr_values["悲观"] is not None
        assert irr_values["乐观"] is not None
        assert irr_values["悲观"] < irr_values["乐观"]


class TestScenarioComparison:
    """ScenarioComparison 对比表"""

    def test_comparison_table_not_empty(
        self, base_config: ModelConfig
    ) -> None:
        engine = ScenarioEngine(base_config)
        result = engine.run([
            ScenarioDefinition("A", {}),
            ScenarioDefinition("B", {"operating": {"grid_price": 0.40}}),
        ])
        table = result.comparison_table()
        assert isinstance(table, pd.DataFrame)
        assert len(table) > 0

    def test_delta_table_has_base(
        self, base_config: ModelConfig
    ) -> None:
        engine = ScenarioEngine(base_config)
        result = engine.run([
            ScenarioDefinition("基准", {}),
            ScenarioDefinition("高电价", {"operating": {"grid_price": 0.40}}),
        ], base_name="基准")
        table = result.delta_table()
        assert isinstance(table, pd.DataFrame)
        assert "基准" in table.columns


# ══════════════════════════════════════════════════════════
# 3. Sensitivity 测试
# ══════════════════════════════════════════════════════════


class TestSensitivityEngine:
    """SensitivityEngine 敏感性分析"""

    def test_run_basic(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[-0.1, 0.1],
        )
        assert len(result.items) == 2  # 1 param × 2 perturbations
        assert result.base_metrics[MetricKey.IRR_TOTAL] is not None

    def test_perturbation_values(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        """扰动后参数值正确"""
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[0.1],
        )
        item = result.items[0]
        assert item.original_value == 0.35
        assert item.perturbed_value == pytest.approx(0.385)

    def test_direction_irr(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        """电价+10% → IRR 上升"""
        engine = SensitivityEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[0.1],
        )
        delta = result.items[0].delta[MetricKey.IRR_TOTAL]
        assert delta is not None
        assert delta > 0

    def test_direction_irr_negative(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        """电价-10% → IRR 下降"""
        engine = SensitivityEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[-0.1],
        )
        delta = result.items[0].delta[MetricKey.IRR_TOTAL]
        assert delta is not None
        assert delta < 0

    def test_symmetry(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        """±10% 的 IRR 变化幅度应接近 (非线性但方向对称)"""
        engine = SensitivityEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[-0.1, 0.1],
        )
        neg_delta = abs(result.items[0].delta[MetricKey.IRR_TOTAL])
        pos_delta = abs(result.items[1].delta[MetricKey.IRR_TOTAL])
        # 允许 50% 非对称 (模型非线性)
        if neg_delta is not None and pos_delta is not None:
            ratio = neg_delta / pos_delta
            assert 0.5 < ratio < 2.0, f"Asymmetry too large: {ratio}"

    def test_zero_perturbation_is_identity(
        self, base_config: ModelConfig, grid_price_spec: ParamSpec
    ) -> None:
        """0% 扰动 = 基准值"""
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=[grid_price_spec],
            perturbations=[0.0],
        )
        delta = result.items[0].delta[MetricKey.IRR_TOTAL]
        assert delta is not None
        assert abs(delta) < 1e-10

    def test_pump_price_direction(
        self, base_config: ModelConfig, pump_price_spec: ParamSpec
    ) -> None:
        """抽水电价+10% → IRR 下降 (成本增加)"""
        engine = SensitivityEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            params=[pump_price_spec],
            perturbations=[0.1],
        )
        delta = result.items[0].delta[MetricKey.IRR_TOTAL]
        assert delta is not None
        assert delta < 0

    def test_multi_param(
        self, base_config: ModelConfig
    ) -> None:
        """多参数分析"""
        specs = COMMON_PARAMS[:3]
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=specs,
            perturbations=[-0.1, 0.1],
        )
        assert len(result.items) == 3 * 2  # 3 params × 2 perturbations


class TestSensitivityResult:
    """SensitivityResult 输出表格"""

    def test_matrix_table(
        self, base_config: ModelConfig
    ) -> None:
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=[ParamSpec("operating", "grid_price", "上网电价")],
            perturbations=[-0.1, 0.1],
        )
        table = result.matrix_table()
        assert isinstance(table, pd.DataFrame)
        assert len(table) == 1  # 1 param
        assert "-10%" in table.columns or "+-10%" in table.columns

    def test_tornado_data(
        self, base_config: ModelConfig
    ) -> None:
        engine = SensitivityEngine(base_config)
        result = engine.run(
            params=COMMON_PARAMS[:3],
            perturbations=[-0.1, 0.1],
        )
        tornado = result.tornado_data(MetricKey.IRR_TOTAL)
        assert isinstance(tornado, pd.DataFrame)
        assert "spread" in tornado.columns
        assert len(tornado) == 3
        # 应按 spread 降序
        spreads = tornado["spread"].values
        for i in range(len(spreads) - 1):
            assert spreads[i] >= spreads[i + 1]

    def test_tornado_empty_result(self) -> None:
        result = SensitivityResult(
            base_metrics={}, params=[], perturbations=[]
        )
        tornado = result.tornado_data(MetricKey.IRR_TOTAL)
        assert isinstance(tornado, pd.DataFrame)
        assert len(tornado) == 0


# ══════════════════════════════════════════════════════════
# 4. Monte Carlo 测试
# ══════════════════════════════════════════════════════════


class TestParamDistribution:
    """ParamDistribution 分布采样"""

    def test_normal_sampling(self) -> None:
        spec = ParamSpec("operating", "grid_price", "电价")
        pd_config = ParamDistribution(
            spec=spec,
            distribution=DistributionType.NORMAL,
            dist_params={"std": 0.035},
        )
        rng = np.random.default_rng(42)
        samples = pd_config.sample(0.35, rng, size=1000)
        assert len(samples) == 1000
        assert abs(np.mean(samples) - 0.35) < 0.01  # 均值接近 base

    def test_uniform_sampling(self) -> None:
        spec = ParamSpec("operating", "grid_price", "电价")
        pd_config = ParamDistribution(
            spec=spec,
            distribution=DistributionType.UNIFORM,
            dist_params={"low_offset": -0.05, "high_offset": 0.05},
        )
        rng = np.random.default_rng(42)
        samples = pd_config.sample(0.35, rng, size=1000)
        assert len(samples) == 1000
        assert np.min(samples) >= 0.30
        assert np.max(samples) <= 0.40

    def test_triangular_sampling(self) -> None:
        spec = ParamSpec("operating", "grid_price", "电价")
        pd_config = ParamDistribution(
            spec=spec,
            distribution=DistributionType.TRIANGULAR,
            dist_params={"low_offset": -0.05, "high_offset": 0.05},
        )
        rng = np.random.default_rng(42)
        samples = pd_config.sample(0.35, rng, size=1000)
        assert len(samples) == 1000
        # 三角分布众数应在 base 附近
        assert abs(np.median(samples) - 0.35) < 0.01


class TestMonteCarloEngine:
    """MonteCarloEngine 模拟"""

    def test_basic_run(self, base_config: ModelConfig) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            param_distributions=[
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ],
            iterations=20,
            seed=42,
        )
        assert len(result.runs) == 20
        assert result.iterations == 20

    def test_reproducibility(self, base_config: ModelConfig) -> None:
        """相同 seed → 相同结果"""
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        dist = ParamDistribution(
            spec=ParamSpec("operating", "grid_price", "上网电价"),
            distribution=DistributionType.NORMAL,
            dist_params={"std": 0.035},
        )
        r1 = engine.run([dist], iterations=10, seed=42)
        r2 = engine.run([dist], iterations=10, seed=42)

        for run1, run2 in zip(r1.runs, r2.runs):
            assert run1.metrics[MetricKey.IRR_TOTAL] == pytest.approx(
                run2.metrics[MetricKey.IRR_TOTAL], rel=1e-10
            )

    def test_mean_near_base(
        self, base_config: ModelConfig
    ) -> None:
        """对称分布 → MC 均值应接近基准值"""
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        base_irr = extract_metrics(
            base_config.to_orchestrator().run(), [MetricKey.IRR_TOTAL]
        )[MetricKey.IRR_TOTAL]

        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.01},  # 小方差
                ),
            ],
            iterations=100,
            seed=42,
        )
        stats = result.statistics(MetricKey.IRR_TOTAL)
        # 均值应在基准值 ±0.5个百分点以内
        assert abs(stats["mean"] - base_irr) < 0.005

    def test_statistics_keys(
        self, base_config: ModelConfig
    ) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.UNIFORM,
                    dist_params={"low_offset": -0.05, "high_offset": 0.05},
                ),
            ],
            iterations=20,
            seed=42,
        )
        stats = result.statistics(MetricKey.IRR_TOTAL)
        assert "mean" in stats
        assert "std" in stats
        assert "P10" in stats
        assert "P50" in stats
        assert "P90" in stats
        assert stats["P10"] < stats["P50"] < stats["P90"]

    def test_probability_above(
        self, base_config: ModelConfig
    ) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ],
            iterations=50,
            seed=42,
        )
        # 基准IRR~6.4%, P(>0%) 应为 100%
        prob = result.probability_above(MetricKey.IRR_TOTAL, 0.0)
        assert prob == 1.0
        # P(>100%) 应为 0%
        prob_high = result.probability_above(MetricKey.IRR_TOTAL, 1.0)
        assert prob_high == 0.0

    def test_confidence_interval(
        self, base_config: ModelConfig
    ) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ],
            iterations=100,
            seed=42,
        )
        lower, upper = result.confidence_interval(MetricKey.IRR_TOTAL, 0.95)
        assert lower < upper

    def test_summary_table(
        self, base_config: ModelConfig
    ) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ],
            iterations=20,
            seed=42,
        )
        table = result.summary_table()
        assert isinstance(table, pd.DataFrame)
        assert len(table) >= 1

    def test_percentile_table(
        self, base_config: ModelConfig
    ) -> None:
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.035},
                ),
            ],
            iterations=20,
            seed=42,
        )
        table = result.percentile_table(MetricKey.IRR_TOTAL)
        assert isinstance(table, pd.DataFrame)
        assert len(table) == 21  # 0%, 5%, ..., 100%

    def test_multi_param_distribution(
        self, base_config: ModelConfig
    ) -> None:
        """多参数联合分布"""
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 0.02},
                ),
                ParamDistribution(
                    spec=ParamSpec("operating", "pump_price", "抽水电价"),
                    distribution=DistributionType.UNIFORM,
                    dist_params={"low_offset": -0.03, "high_offset": 0.03},
                ),
            ],
            iterations=10,
            seed=42,
        )
        assert len(result.runs) == 10
        # 每次运行应有2个参数值
        for run in result.runs:
            assert len(run.param_values) == 2

    def test_negative_truncation(
        self, base_config: ModelConfig
    ) -> None:
        """电价不应为负"""
        engine = MonteCarloEngine(
            base_config, metrics=[MetricKey.IRR_TOTAL]
        )
        result = engine.run(
            [
                ParamDistribution(
                    spec=ParamSpec("operating", "grid_price", "上网电价"),
                    distribution=DistributionType.NORMAL,
                    dist_params={"std": 1.0},  # 极大方差
                ),
            ],
            iterations=50,
            seed=42,
        )
        for run in result.runs:
            assert run.param_values["上网电价"] >= 0.0

    def test_empty_metric_series(
        self, base_config: ModelConfig
    ) -> None:
        """空运行的 metric_series"""
        result = MonteCarloResult(
            base_metrics={}, iterations=0, param_distributions=[]
        )
        series = result.metric_series(MetricKey.IRR_TOTAL)
        assert len(series) == 0
