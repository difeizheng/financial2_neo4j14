from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .retriever import IndicatorRetriever, IndicatorContext, RetrievalResult
from .prompt_builder import PromptBuilder
from .cypher_gen import CypherGenerator
from ..models.graph import FinancialGraph


@dataclass
class QAResponse:
    answer: str
    retrieved_contexts: list[IndicatorContext] = field(default_factory=list)
    cypher_query: Optional[str] = None
    cypher_results: Optional[str] = None
    error: Optional[str] = None


class QAEngine:
    """Orchestrates retrieval, context building, and LLM calls for Q&A."""

    def __init__(
        self,
        graph: FinancialGraph,
        neo4j_store=None,
        llm_base_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "gpt-4o",
        task_id: str = "",  # Required for Neo4j queries
    ) -> None:
        self._graph = graph
        self._neo4j = neo4j_store
        self._task_id = task_id
        self._model = llm_model
        self._retriever = IndicatorRetriever(graph, neo4j_store, task_id)
        self._prompt_builder = PromptBuilder(graph, task_id)
        self._client = None
        self._cypher_gen = None

        if llm_api_key.strip():
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=llm_base_url or None, api_key=llm_api_key)
                if neo4j_store:
                    self._cypher_gen = CypherGenerator(self._client, llm_model, neo4j_store, task_id)
            except ImportError:
                pass

    def ask(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
        top_k: int = 8,
    ) -> QAResponse:
        retrieval = self._retriever.search(question, top_k)

        if not self._client:
            return self._retrieval_only_response(retrieval)

        schema = ""
        if self._neo4j and self._task_id:
            try:
                schema = self._neo4j.get_graph_schema(self._task_id)
            except Exception:
                pass

        system_prompt = self._prompt_builder.build_system_prompt(retrieval, schema)

        cypher_query: Optional[str] = None
        cypher_results: Optional[str] = None
        if self._cypher_gen and self._cypher_gen.should_use_cypher(question):
            cypher_prompt = self._prompt_builder.build_cypher_prompt(question, schema)
            try:
                cypher_query, cypher_results = self._cypher_gen.generate_and_execute(
                    question, schema, cypher_prompt
                )
                system_prompt += f"\n\n## 图遍历查询结果\n{cypher_results}"
            except Exception as e:
                cypher_results = f"（Cypher 生成失败：{e}）"

        messages = [{"role": "system", "content": system_prompt}]
        for h in (chat_history or [])[:-1]:
            msg = dict(h)
            if isinstance(msg.get("content"), dict):
                msg["content"] = msg["content"].get("text", "")
            messages.append(msg)
        messages.append({"role": "user", "content": question})

        try:
            resp = self._client.chat.completions.create(
                model=self._model, messages=messages, max_tokens=1024
            )
            answer = resp.choices[0].message.content
        except Exception as e:
            answer = f"LLM 调用失败：{e}"
            return QAResponse(
                answer=answer,
                retrieved_contexts=retrieval.contexts,
                cypher_query=cypher_query,
                cypher_results=cypher_results,
                error=str(e),
            )

        return QAResponse(
            answer=answer,
            retrieved_contexts=retrieval.contexts,
            cypher_query=cypher_query,
            cypher_results=cypher_results,
        )

    def _retrieval_only_response(self, retrieval: RetrievalResult) -> QAResponse:
        """Format retrieval results as a readable answer when no LLM is configured."""
        if not retrieval.contexts:
            return QAResponse(answer="未找到相关指标数据。", retrieved_contexts=[])
        lines = ["以下是检索到的相关指标数据（未配置 LLM，仅展示原始数据）：\n"]
        for ctx in retrieval.contexts:
            ind = ctx.indicator
            line = f"- **{ind.name}**"
            if ind.unit:
                line += f" [{ind.unit}]"
            if retrieval.query_years and ind.time_series:
                hits = [(k, v) for k, v in ind.time_series.items() if any(y in str(k) for y in retrieval.query_years)]
                for k, v in hits:
                    line += f"\n  {k}: {v}"
            elif ind.summary_value is not None:
                line += f": {ind.summary_value}"
            lines.append(line)
        return QAResponse(answer="\n".join(lines), retrieved_contexts=retrieval.contexts)

    def ask_stream(
        self,
        question: str,
        chat_history: Optional[list[dict]] = None,
        top_k: int = 8,
    ):
        """Generator yielding (event_type, data) for streaming UI.

        Event types:
          ("retrieval", RetrievalResult)
          ("cypher", (query_str, results_str))
          ("chunk", str)          — LLM token chunk
          ("answer", str)         — full answer (retrieval-only mode)
          ("error", str)
        """
        retrieval = self._retriever.search(question, top_k)
        yield ("retrieval", retrieval)

        if not self._client:
            yield ("answer", self._retrieval_only_response(retrieval).answer)
            return

        schema = ""
        if self._neo4j and self._task_id:
            try:
                schema = self._neo4j.get_graph_schema(self._task_id)
            except Exception:
                pass

        system_prompt = self._prompt_builder.build_system_prompt(retrieval, schema)

        if self._cypher_gen and self._cypher_gen.should_use_cypher(question):
            cypher_prompt = self._prompt_builder.build_cypher_prompt(question, schema)
            try:
                cypher_query, cypher_results = self._cypher_gen.generate_and_execute(
                    question, schema, cypher_prompt
                )
                system_prompt += f"\n\n## 图遍历查询结果\n{cypher_results}"
                yield ("cypher", (cypher_query, cypher_results))
            except Exception as e:
                yield ("cypher", (None, f"（Cypher 生成失败：{e}）"))

        messages = [{"role": "system", "content": system_prompt}]
        for h in (chat_history or [])[:-1]:
            msg = dict(h)
            if isinstance(msg.get("content"), dict):
                msg["content"] = msg["content"].get("text", "")
            messages.append(msg)
        messages.append({"role": "user", "content": question})

        try:
            stream = self._client.chat.completions.create(
                model=self._model, messages=messages, max_tokens=1024, stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield ("chunk", delta)
        except Exception as e:
            yield ("error", f"LLM 调用失败：{e}")
