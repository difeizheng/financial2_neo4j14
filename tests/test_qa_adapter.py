"""Q&A 适配器测试 — 验证关键词匹配和数据检索

测试策略:
  - 无 LLM 模式: 验证关键词匹配、数据检索、格式化输出
  - 不需要 LLM 客户端
"""
from __future__ import annotations

import pytest

from financial_model.analysis.types import ModelConfig
from financial_model.engines.orchestrator import ModelOrchestrator
from financial_model.export.qa_adapter import GenericModelQAAdapter


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def adapter() -> GenericModelQAAdapter:
    """创建测试用适配器 (module 级别，只运行一次模型)"""
    config = ModelConfig.from_excel_v17()
    results = config.to_orchestrator().run()
    return GenericModelQAAdapter(results, config)


# ══════════════════════════════════════════════════════════
# 主题检测测试
# ══════════════════════════════════════════════════════════


class TestTopicDetection:
    @pytest.mark.parametrize("question,expected_topic", [
        ("IRR 是多少?", "irr"),
        ("内部收益率多少?", "irr"),
        ("NPV 计算", "npv"),
        ("净现值?", "npv"),
        ("DSCR 多少?", "dscr"),
        ("偿债备付率?", "dscr"),
        ("回收期几年?", "payback"),
        ("ROE?", "roe"),
        ("投资总额", "investment"),
        ("建设投资多少?", "investment"),
        ("收入有多少?", "revenue"),
        ("成本多少?", "cost"),
        ("折旧多少?", "depreciation"),
        ("利润多少?", "profit"),
        ("现金流?", "cashflow"),
        ("资产负债率?", "balance_sheet"),
        ("电价多少?", "price"),
        ("装机容量?", "capacity"),
        ("贷款利率?", "financing"),
        ("增值税?", "tax"),
        ("概览", "summary"),
    ])
    def test_keyword_matching(
        self, adapter: GenericModelQAAdapter, question: str, expected_topic: str
    ) -> None:
        topics = adapter._detect_topics(question)
        assert expected_topic in topics, f"Expected '{expected_topic}' in {topics} for '{question}'"


# ══════════════════════════════════════════════════════════
# 数据检索测试
# ══════════════════════════════════════════════════════════


class TestDataRetrieval:
    def test_irr_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("irr")
        assert len(points) >= 2
        labels = [p.label for p in points]
        assert "全投资IRR" in labels
        assert "资本金IRR" in labels

    def test_npv_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("npv")
        assert len(points) >= 2
        assert any("NPV" in p.label for p in points)

    def test_dscr_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("dscr")
        assert len(points) >= 2
        labels = [p.label for p in points]
        assert "最低DSCR" in labels
        assert "平均DSCR" in labels

    def test_investment_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("investment")
        assert len(points) >= 3
        labels = [p.label for p in points]
        assert "建设投资" in labels

    def test_revenue_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("revenue")
        assert len(points) >= 1

    def test_cost_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("cost")
        assert len(points) >= 1

    def test_profit_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("profit")
        assert len(points) >= 1

    def test_price_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("price")
        assert len(points) == 2
        labels = [p.label for p in points]
        assert "上网电价" in labels
        assert "抽水电价" in labels

    def test_capacity_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("capacity")
        assert len(points) >= 3

    def test_summary_data(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("summary")
        assert len(points) >= 6
        labels = [p.label for p in points]
        assert "全投资IRR" in labels
        assert "装机容量" in labels

    def test_unknown_topic_returns_empty(self, adapter: GenericModelQAAdapter) -> None:
        points = adapter._retrieve_topic("nonexistent_topic")
        assert points == []


# ══════════════════════════════════════════════════════════
# 回答格式化测试
# ══════════════════════════════════════════════════════════


class TestAsk:
    def test_ask_irr(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("IRR 是多少?")
        assert "全投资IRR" in answer
        assert "%" in answer

    def test_ask_npv(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("NPV 是多少?")
        assert "NPV" in answer
        assert "万元" in answer

    def test_ask_dscr(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("DSCR 趋势如何?")
        assert "DSCR" in answer

    def test_ask_investment(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("总投资多少?")
        assert "建设投资" in answer
        assert "万元" in answer

    def test_ask_price(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("电价是多少?")
        assert "上网电价" in answer
        assert "元/kWh" in answer

    def test_ask_summary(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("概览")
        assert "全投资IRR" in answer
        assert "装机容量" in answer

    def test_ask_unknown_returns_suggestions(
        self, adapter: GenericModelQAAdapter
    ) -> None:
        answer = adapter.ask("天气xyz?")
        # 未知问题可能触发 summary 兜底或返回未找到
        # 只要返回了有意义的内容即可
        assert len(answer) > 10

    def test_data_source_annotated(self, adapter: GenericModelQAAdapter) -> None:
        answer = adapter.ask("IRR 是多少?")
        assert "来源:" in answer


# ══════════════════════════════════════════════════════════
# 预设问题测试
# ══════════════════════════════════════════════════════════


class TestPresetQuestions:
    def test_preset_questions_available(
        self, adapter: GenericModelQAAdapter
    ) -> None:
        questions = adapter.get_preset_questions()
        assert len(questions) >= 8

    def test_all_preset_questions_answerable(
        self, adapter: GenericModelQAAdapter
    ) -> None:
        for q in adapter.get_preset_questions():
            answer = adapter.ask(q)
            assert len(answer) > 10, f"Empty answer for: {q}"
            assert "未找到" not in answer, f"No data for: {q}"
