from __future__ import annotations
import re
from typing import Optional


_GRAPH_KEYWORDS = frozenset([
    "影响", "依赖", "上游", "下游", "路径", "传播", "关联",
    "哪些报表", "来源", "流向",
    "有关", "相关", "组成", "构成", "包含", "涉及",
])

_WRITE_KEYWORDS = frozenset(["CREATE", "DELETE", "SET", "MERGE", "REMOVE", "DROP"])

_ALLOWED_KEYWORDS = frozenset(
    ["MATCH", "WHERE", "RETURN", "WITH", "ORDER", "LIMIT", "OPTIONAL", "UNWIND", "BY", "AS", "DISTINCT"]
)


class CypherGenerator:
    """Generates and validates read-only Cypher queries from natural language."""

    def __init__(self, llm_client, model: str, neo4j_store, task_id: str = "") -> None:
        self._client = llm_client
        self._model = model
        self._neo4j = neo4j_store
        self._task_id = task_id

    def should_use_cypher(self, question: str) -> bool:
        return any(kw in question for kw in _GRAPH_KEYWORDS)

    def generate_and_execute(self, question: str, schema: str, cypher_prompt: str) -> tuple[str, str]:
        """Returns (cypher_query, formatted_results)."""
        messages = [
            {"role": "system", "content": cypher_prompt},
            {"role": "user", "content": question},
        ]
        resp = self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=512
        )
        raw = resp.choices[0].message.content.strip()
        query = self._extract_cypher(raw)

        if not self._validate_cypher(query):
            return query, "（Cypher 查询包含不允许的操作，已拒绝执行）"

        try:
            rows = self._neo4j.run_cypher(query)
            return query, self._format_results(rows)
        except Exception as e:
            return query, f"（Cypher 执行失败：{e}）"

    def _extract_cypher(self, text: str) -> str:
        # Strip markdown code fences if present
        match = re.search(r"```(?:cypher)?\s*([\s\S]+?)```", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _validate_cypher(self, query: str) -> bool:
        upper = query.upper()
        for kw in _WRITE_KEYWORDS:
            if re.search(r"\b" + kw + r"\b", upper):
                return False
        return True

    def _format_results(self, rows: list[dict]) -> str:
        if not rows:
            return "（查询无结果）"
        lines = []
        for row in rows[:20]:
            parts = []
            for k, v in row.items():
                if isinstance(v, dict):
                    v = v.get("name") or v.get("id") or str(v)
                parts.append(f"{k}: {v}")
            lines.append("  ".join(parts))
        return "\n".join(lines)
