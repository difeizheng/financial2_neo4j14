from __future__ import annotations
from typing import Optional

from .retriever import RetrievalResult, IndicatorContext
from ..models.graph import FinancialGraph


class PromptBuilder:
    """Constructs structured system prompts with graph schema and indicator context."""

    def __init__(self, graph: FinancialGraph, task_id: str = "") -> None:
        stats = graph.stats()
        self._stats = stats
        self._task_id = task_id

    def build_system_prompt(
        self,
        retrieval_result: RetrievalResult,
        graph_schema: str = "",
    ) -> str:
        stats = self._stats
        schema_section = graph_schema or (
            f"- Cell层：{stats['total_cells']}个单元格，{stats['dependency_edges']}条依赖关系\n"
            f"- Indicator层：{stats['total_indicators']}个财务指标\n"
            f"- Table层：{stats['total_tables']}个财务报表"
        )

        query_years = getattr(retrieval_result, "query_years", [])
        context_section = self._format_contexts(retrieval_result.contexts, query_years)

        return (
            "你是一个专业的财务分析助手，基于财务模型知识图谱回答用户问题。\n\n"
            "## 图谱结构\n"
            f"{schema_section}\n\n"
            "## 关系类型\n"
            "- DEPENDS_ON: 单元格之间的公式依赖\n"
            "- CALCULATES_FROM: 指标之间的计算依赖\n"
            "- FEEDS_INTO: 报表之间的数据流向\n"
            "- BELONGS_TO: 单元格→指标，指标→报表\n\n"
            "## 相关指标数据\n"
            f"{context_section}\n\n"
            "## 回答要求\n"
            "1. 引用具体数据和指标名称\n"
            "2. 如果涉及计算关系，说明上下游依赖\n"
            "3. 如果数据不足，明确说明\n"
            "4. 数值请保留合理精度，注明单位"
        )

    def _format_contexts(self, contexts: list[IndicatorContext], query_years: list[str] | None = None) -> str:
        if not contexts:
            return "（未找到相关指标）"
        lines = []
        for ctx in contexts:
            lines.append(self.format_indicator_context(ctx, query_years))
        return "\n\n".join(lines)

    def format_indicator_context(self, ctx: IndicatorContext, query_years: list[str] | None = None) -> str:
        ind = ctx.indicator
        parts = [f"**{ind.name}**（{ind.category or ''}）"]
        if ind.unit:
            parts[0] += f" [{ind.unit}]"
        if ind.summary_value is not None:
            parts.append(f"  汇总值: {ind.summary_value}")
        if ind.time_series:
            ts_items = list(ind.time_series.items())
            if query_years:
                # Show direct hits for queried years prominently
                direct_hits = [(k, v) for k, v in ts_items if any(y in str(k) for y in query_years)]
                if direct_hits:
                    parts.append("  查询年份数据: " + ", ".join(f"{k}={v}" for k, v in direct_hits))
                # Smart window: first 3 + queried year neighbors + last 2
                shown: set[int] = set(range(min(3, len(ts_items))))
                shown.update(range(max(0, len(ts_items) - 2), len(ts_items)))
                for idx, (k, _) in enumerate(ts_items):
                    if any(y in str(k) for y in query_years):
                        shown.update(range(max(0, idx - 1), min(len(ts_items), idx + 2)))
                sorted_idx = sorted(shown)
                display_parts: list[str] = []
                for i, idx in enumerate(sorted_idx):
                    if i > 0 and sorted_idx[i] - sorted_idx[i - 1] > 1:
                        display_parts.append("...")
                    k, v = ts_items[idx]
                    display_parts.append(f"{k}={v}")
                ts_str = ", ".join(display_parts)
                if len(ts_items) > len(shown):
                    ts_str += f" (共{len(ts_items)}期)"
            else:
                display = ts_items[:8]
                ts_str = ", ".join(f"{p}={v}" for p, v in display)
                if len(ts_items) > 8:
                    ts_str += f"... (共{len(ts_items)}期)"
            parts.append(f"  时间序列: {ts_str}")
        if ctx.upstream:
            names = [u.name for u in ctx.upstream[:3]]
            parts.append(f"  上游依赖: {', '.join(names)}")
        if ctx.downstream:
            names = [d.name for d in ctx.downstream[:3]]
            parts.append(f"  被依赖: {', '.join(names)}")
        return "\n".join(parts)

    def build_cypher_prompt(self, question: str, schema: str) -> str:
        return (
            "你是一个 Neo4j Cypher 查询专家。根据以下图谱结构和用户问题，生成一条只读 Cypher 查询。\n\n"
            f"## 图谱结构\n{schema}\n\n"
            f"## 当前任务\ntask_id = '{self._task_id}'\n\n"
            f"## 用户问题\n{question}\n\n"
            "## 要求\n"
            "1. 只使用 MATCH、WHERE、RETURN、WITH、ORDER BY、LIMIT、OPTIONAL MATCH\n"
            "2. 禁止 CREATE、DELETE、SET、MERGE 等写操作\n"
            "3. 只返回 Cypher 查询语句，不要解释\n"
            "4. 结果限制在 LIMIT 20 以内\n"
            "5. 所有查询必须包含 task_id 过滤条件：WHERE n.task_id = '{self._task_id}'\n"
            "6. 节点 ID 格式为 '{task_id}_{original_id}'，使用 orig_id 属性可获取原始 ID"
        )
