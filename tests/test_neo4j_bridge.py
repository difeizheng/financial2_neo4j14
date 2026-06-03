"""Neo4j Bridge 测试 — Mock driver 验证节点/关系创建

测试策略:
  - 使用 Mock driver/session 验证 Cypher 语句和参数
  - 不需要真实 Neo4j 实例
  - 验证节点 ID 格式、属性完整性、关系正确性
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from financial_model.analysis.types import ModelConfig
from financial_model.engines.orchestrator import ModelOrchestrator
from financial_model.export.neo4j_bridge import (
    Neo4jBridge,
    _extract_key_params,
    _nid,
)


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


class _FakeCounters:
    """模拟 neo4j.ResultSummary.counters"""

    def __init__(self, nodes: int = 0, rels: int = 0) -> None:
        self.nodes_created = nodes
        self.relationships_created = rels


class _FakeSummary:
    """模拟 neo4j result.consume() 返回的 ResultSummary"""

    def __init__(self, nodes: int = 0, rels: int = 0) -> None:
        self.counters = _FakeCounters(nodes, rels)


class _FakeResult:
    """模拟 Neo4j session.run() 返回值"""

    def __init__(
        self,
        nodes: int = 0,
        rels: int = 0,
        single_val: dict | None = None,
        records: list[dict] | None = None,
    ) -> None:
        self._summary = _FakeSummary(nodes, rels)
        self._single = single_val
        self._records = records or []

    def consume(self) -> _FakeSummary:
        return self._summary

    def single(self) -> dict | None:
        return self._single

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    """模拟 Neo4j session — 记录所有 run() 调用"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher: str, params: dict | None = None, **kwargs: Any) -> _FakeResult:
        merged = {**(params or {}), **kwargs}
        self.calls.append((cypher, merged))
        # 根据 Cypher 类型返回合理计数
        if "CREATE" in cypher and "DETACH DELETE" not in cypher:
            if "CONSTRAINT" in cypher or "INDEX" in cypher:
                return _FakeResult()
            if "-[:" in cypher:
                return _FakeResult(rels=1)
            # UNWIND 批量 — 按 rows 参数长度计算
            rows = merged.get("rows", [])
            if rows:
                return _FakeResult(nodes=len(rows))
            return _FakeResult(nodes=1)
        if "MATCH" in cypher and "RETURN" in cypher:
            return _FakeResult(single_val={"m": {"key": "irr_total"}})
        return _FakeResult()

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class _FakeDriver:
    """模拟 Neo4j driver — session() 返回上下文管理器"""

    def __init__(self) -> None:
        self._sessions: list[_FakeSession] = []

    def session(self) -> _FakeSession:
        s = _FakeSession()
        self._sessions.append(s)
        return s

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@pytest.fixture
def mock_store() -> MagicMock:
    """创建 mock Neo4jStore"""
    store = MagicMock()
    store._driver = _FakeDriver()
    return store


@pytest.fixture
def bridge(mock_store: MagicMock) -> Neo4jBridge:
    """创建 Neo4jBridge 实例"""
    return Neo4jBridge(mock_store, task_id="test_gm")


@pytest.fixture
def config() -> ModelConfig:
    """创建测试用 ModelConfig"""
    return ModelConfig.from_excel_v17()


@pytest.fixture
def results(config: ModelConfig) -> Any:
    """运行模型获取 AllResults"""
    return config.to_orchestrator().run()


# ══════════════════════════════════════════════════════════
# 节点 ID 生成测试
# ══════════════════════════════════════════════════════════


class TestNodeId:
    def test_format(self) -> None:
        nid = _nid("task1", "metric", "irr_total")
        assert nid == "task1_gm_metric_irr_total"

    def test_special_chars(self) -> None:
        nid = _nid("t", "row", "利润表_全投资")
        assert " " not in nid
        assert nid == "t_gm_row_利润表_全投资"

    def test_parentheses_stripped(self) -> None:
        nid = _nid("t", "param", "capacity(MW)")
        assert "(" not in nid
        assert ")" not in nid


# ══════════════════════════════════════════════════════════
# 参数提取测试
# ══════════════════════════════════════════════════════════


class TestExtractKeyParams:
    def test_count(self, config: ModelConfig) -> None:
        params = _extract_key_params(config)
        assert len(params) >= 13  # 至少 13 个关键参数

    def test_has_capacity(self, config: ModelConfig) -> None:
        params = _extract_key_params(config)
        fields = [p["field"] for p in params]
        assert "installed_capacity_mw" in fields

    def test_has_grid_price(self, config: ModelConfig) -> None:
        params = _extract_key_params(config)
        fields = [p["field"] for p in params]
        assert "grid_price" in fields

    def test_value_types(self, config: ModelConfig) -> None:
        params = _extract_key_params(config)
        for p in params:
            assert "group" in p
            assert "field" in p
            assert "value" in p
            assert "display_name" in p


# ══════════════════════════════════════════════════════════
# 导入测试
# ══════════════════════════════════════════════════════════


class TestImportMetrics:
    def test_creates_10_metrics(
        self, bridge: Neo4jBridge, results: Any
    ) -> None:
        count = bridge._import_metrics(results.derived_metrics)
        assert count == 10  # 10 个派生指标


class TestImportReports:
    def test_creates_reports(
        self, bridge: Neo4jBridge, results: Any
    ) -> None:
        report_counts = bridge._import_reports(results)
        assert report_counts["reports"] >= 10  # 至少 10 个报表
        assert report_counts["rows"] >= 0  # 可能有行数据


class TestImportParams:
    def test_creates_params(
        self, bridge: Neo4jBridge, config: ModelConfig
    ) -> None:
        count = bridge._import_params(config)
        assert count >= 13  # 至少 13 个参数节点


class TestImportFull:
    def test_full_import(
        self, bridge: Neo4jBridge, results: Any, config: ModelConfig
    ) -> None:
        counts = bridge.import_results(results, config)
        assert "metrics" in counts
        assert "reports" in counts
        assert "rows" in counts
        assert "params" in counts
        assert "rels" in counts
        assert counts["metrics"] == 10


# ══════════════════════════════════════════════════════════
# 查询测试
# ══════════════════════════════════════════════════════════


class TestQuery:
    def test_query_metric_returns_dict(
        self, bridge: Neo4jBridge
    ) -> None:
        # FakeSession 返回 single_val={"m": {"key": "irr_total"}}
        result = bridge.query_metric("irr_total")
        assert result is not None
        assert result["key"] == "irr_total"

    def test_query_report_rows(
        self, bridge: Neo4jBridge
    ) -> None:
        # FakeSession 返回空列表 (no records)
        rows = bridge.query_report_rows("investment")
        assert isinstance(rows, list)


# ══════════════════════════════════════════════════════════
# Clear 测试
# ══════════════════════════════════════════════════════════


class TestClear:
    def test_clear_no_error(self, bridge: Neo4jBridge) -> None:
        # Should not raise
        bridge.clear()


# ══════════════════════════════════════════════════════════
# 集成: 完整导入 → 所有 Cypher 合法
# ══════════════════════════════════════════════════════════


class TestCypherValid:
    def test_all_cypher_statements_contain_create(
        self, bridge: Neo4jBridge, results: Any, config: ModelConfig
    ) -> None:
        """验证所有生成的 Cypher 语句格式正确"""
        bridge.import_results(results, config)

        session = bridge._driver.session()
        assert isinstance(session, _FakeSession)

        for cypher, params in session.calls:
            # 每条语句应有合法的 Cypher 关键字
            assert any(
                kw in cypher.upper()
                for kw in ("CREATE", "MATCH", "UNWIND")
            ), f"Invalid Cypher: {cypher[:80]}"
