from __future__ import annotations
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from ..models.indicator import Indicator
from ..models.graph import FinancialGraph


@dataclass
class IndicatorContext:
    indicator: Indicator
    match_score: float
    match_reason: str  # "exact_name" | "fuzzy_name" | "category" | "time_period"
    upstream: list[Indicator] = field(default_factory=list)
    downstream: list[Indicator] = field(default_factory=list)


@dataclass
class RetrievalResult:
    contexts: list[IndicatorContext]
    query_tokens: list[str]
    query_years: list[str]
    total_candidates: int


def _fuzzy_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _extract_years(text: str) -> list[str]:
    return re.findall(r"\d{4}", text)


class IndicatorRetriever:
    """Structured indicator retrieval with fuzzy matching and graph context."""

    # Questions needing expanded context — retrieve related indicators
    _EXPANSION_KEYWORDS = [
        "毛利率", "净利率", "利润率", "收益率",
        "占比", "比例", "比率",
        "对比", "比较",
        "变化", "趋势",
    ]

    def __init__(
        self,
        graph: FinancialGraph,
        neo4j_store=None,  # Optional[Neo4jStore] — avoid circular import
        task_id: str = "",  # Required for Neo4j queries
    ) -> None:
        self._graph = graph
        self._neo4j = neo4j_store
        self._task_id = task_id
        self._indicators = list(graph.indicators.values())

    def search(self, question: str, top_k: int = 8) -> RetrievalResult:
        tokens = self._tokenize(question)
        years = _extract_years(question)
        scored: list[tuple[float, str, Indicator]] = []

        for ind in self._indicators:
            score, reason = self._score(ind, tokens, years)
            if score > 0:
                scored.append((score, reason, ind))

        scored.sort(key=lambda x: -x[0])
        top = scored[:top_k]

        contexts = [
            IndicatorContext(
                indicator=ind,
                match_score=score,
                match_reason=reason,
            )
            for score, reason, ind in top
        ]
        self._enrich_with_graph_context(contexts)

        # If question involves ratios/comparisons, expand with related indicators
        if any(kw in question for kw in self._EXPANSION_KEYWORDS):
            contexts = self._expand_for_aggregation(contexts, top_k)

        return RetrievalResult(
            contexts=contexts,
            query_tokens=tokens,
            query_years=years,
            total_candidates=len(scored),
        )

    def _expand_for_aggregation(
        self,
        contexts: list[IndicatorContext],
        top_k: int,
    ) -> list[IndicatorContext]:
        """When question needs multi-indicator computation, expand context with related indicators."""
        seen_ids: set[str] = {ctx.indicator.id for ctx in contexts}

        for ctx in contexts:
            ind = ctx.indicator
            # Add indicators this one depends on
            for dep_id in ind.depends_on_indicators[:3]:
                if dep_id not in seen_ids and dep_id in self._graph.indicators:
                    dep = self._graph.indicators[dep_id]
                    contexts.append(IndicatorContext(
                        indicator=dep,
                        match_score=ctx.match_score * 0.5,
                        match_reason="dependency_expansion",
                    ))
                    seen_ids.add(dep_id)
            # Add indicators that depend on this one
            for dep_id in ind.depended_by_indicators[:3]:
                if dep_id not in seen_ids and dep_id in self._graph.indicators:
                    dep = self._graph.indicators[dep_id]
                    contexts.append(IndicatorContext(
                        indicator=dep,
                        match_score=ctx.match_score * 0.5,
                        match_reason="dependency_expansion",
                    ))
                    seen_ids.add(dep_id)

        # Sort by score descending, limit to top_k * 2
        contexts.sort(key=lambda c: -c.match_score)
        return contexts[:top_k * 2]

    def _tokenize(self, question: str) -> list[str]:
        # Strip stop words and punctuation that carry no indicator meaning
        cleaned = re.sub(r"[？?！!，,。.：:；;（）()【】\[\]「」""''、的是了吗呢吧啊哪些跟和与在到从对于]", "", question)
        tokens: list[str] = []
        # Split into Chinese and ASCII segments
        for seg in re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", cleaned):
            if re.match(r"[一-鿿]", seg):
                # Chinese: sliding-window n-grams (2–4 chars) to cover all substrings
                for n in range(2, min(len(seg) + 1, 5)):
                    for i in range(len(seg) - n + 1):
                        tokens.append(seg[i : i + n])
            elif len(seg) >= 2:
                tokens.append(seg)
        # Deduplicate while preserving order, then sort longest-first for better exact matching
        seen: set[str] = set()
        result: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                result.append(t)
        result.sort(key=len, reverse=True)
        return result

    def _score(
        self, ind: Indicator, tokens: list[str], years: list[str]
    ) -> tuple[float, str]:
        name = ind.name or ""
        category = ind.category or ""
        combined = name + category

        # Exact name match — highest priority
        for token in tokens:
            if token == name:
                score = 10.0
                if years and any(y in str(k) for y in years for k in ind.time_series):
                    score += 2.0
                return score, "exact_name"

        # Substring match — bidirectional: token-in-name OR name-in-token
        for token in tokens:
            if token in name:
                score = 5.0 + len(token) / max(len(name), 1)
                if years and any(y in str(k) for y in years for k in ind.time_series):
                    score += 2.0
                return score, "fuzzy_name"
            if len(name) >= 2 and name in token:
                score = 5.0 + len(name) / max(len(token), 1)
                if years and any(y in str(k) for y in years for k in ind.time_series):
                    score += 2.0
                return score, "fuzzy_name"

        # Fuzzy match on name
        best_fuzzy = 0.0
        for token in tokens:
            if len(token) >= 2:
                ratio = _fuzzy_score(token, name)
                if ratio > best_fuzzy:
                    best_fuzzy = ratio
        if best_fuzzy >= 0.6:
            return best_fuzzy * 4.0, "fuzzy_name"

        # Category match
        for token in tokens:
            if token in category:
                score = 2.0
                # Boost if also has time series data for the queried year
                if years and any(y in str(k) for y in years for k in ind.time_series):
                    score += 1.0
                return score, "category"

        # Time period filter — if question mentions a year and indicator has it
        if years and ind.time_series:
            for y in years:
                if any(y in str(k) for k in ind.time_series):
                    return 1.0, "time_period"

        return 0.0, ""

    def _enrich_with_graph_context(
        self, contexts: list[IndicatorContext], depth: int = 1
    ) -> None:
        for ctx in contexts:
            ind = ctx.indicator
            if self._neo4j and self._task_id:
                try:
                    up_dicts = self._neo4j.get_upstream_indicators(self._task_id, ind.id, depth)
                    down_dicts = self._neo4j.get_downstream_indicators(self._task_id, ind.id, depth)
                    # Strip task_id prefix from returned IDs to match in-memory graph
                    ctx.upstream = [
                        self._graph.indicators[d.get("orig_id", d.get("id", "").split("_", 1)[-1])]
                        for d in up_dicts
                        if (d.get("orig_id") or d.get("id", "").split("_", 1)[-1]) in self._graph.indicators
                    ]
                    ctx.downstream = [
                        self._graph.indicators[d.get("orig_id", d.get("id", "").split("_", 1)[-1])]
                        for d in down_dicts
                        if (d.get("orig_id") or d.get("id", "").split("_", 1)[-1]) in self._graph.indicators
                    ]
                    continue
                except Exception:
                    pass  # fall through to in-memory

            # In-memory fallback
            ctx.upstream = [
                self._graph.indicators[dep_id]
                for dep_id in ind.depends_on_indicators[:5]
                if dep_id in self._graph.indicators
            ]
            ctx.downstream = [
                self._graph.indicators[dep_id]
                for dep_id in ind.depended_by_indicators[:5]
                if dep_id in self._graph.indicators
            ]
