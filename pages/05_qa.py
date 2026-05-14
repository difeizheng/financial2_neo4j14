"""Page 5: LLM-powered financial Q&A — structured dashboard."""
from __future__ import annotations
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.storage.json_store import load_graph
from financial_kg.storage.task_db import TaskDB
from financial_kg.llm import QAEngine
from financial_kg.models.graph import FinancialGraph
from financial_kg.engine.derived_metrics import compute_derived_metrics, serialize_metrics, deserialize_metrics
from financial_kg.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    save_config,
)

st.set_page_config(layout="wide")
st.title("💬 财务模型智能问答")

db = TaskDB()
tasks = [t for t in db.list_tasks() if t.status == "done"]

if not tasks:
    st.warning("暂无已解析的任务。")
    st.stop()

task_options = {f"{t.id} — {t.filename}": t for t in tasks}
selected_label = st.selectbox("选择任务", list(task_options.keys()))
task = task_options[selected_label]


@st.cache_resource(show_spinner="加载图谱...")
def _load(task_id: str, output_dir: str):
    cells_path = os.path.join(output_dir, f"{task_id}_cells.json")
    return load_graph(cells_path)


graph = _load(task.id, task.output_dir)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("LLM 配置")
    base_url = st.text_input("Base URL", value=LLM_BASE_URL or "https://api.openai.com/v1")
    api_key = st.text_input("API Key", value=LLM_API_KEY or "", type="password")
    model = st.text_input("Model", value=LLM_MODEL or "gpt-4o-mini")
    top_k = st.slider("检索 Indicator 数量 (top-k)", 3, 20, 8)

    st.divider()
    st.header("Neo4j 配置")
    use_neo4j = st.checkbox("启用 Neo4j 图遍历", value=False)
    neo4j_uri = st.text_input("URI", value=NEO4J_URI)
    neo4j_user = st.text_input("User", value=NEO4J_USER)
    neo4j_pwd = st.text_input("Password", value=NEO4J_PASSWORD, type="password")

    st.divider()
    if st.button("保存配置到 .env", type="secondary"):
        save_config(
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_pwd,
        )
        st.success("配置已保存到 .env 文件")


@st.cache_resource(show_spinner="连接 Neo4j...")
def _get_neo4j(uri: str, user: str, pwd: str):
    try:
        from financial_kg.storage.neo4j_store import Neo4jStore
        return Neo4jStore(uri, user, pwd)
    except Exception as e:
        st.warning(f"Neo4j 连接失败：{e}")
        return None


neo4j_store = None
if use_neo4j and neo4j_pwd.strip():
    neo4j_store = _get_neo4j(neo4j_uri, neo4j_user, neo4j_pwd)


@st.cache_resource(show_spinner="初始化问答引擎...")
def _get_engine(task_id: str, _graph, _neo4j, base_url: str, api_key: str, model: str):
    return QAEngine(
        graph=_graph,
        neo4j_store=_neo4j,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=model,
        task_id=task_id,
    )


engine = _get_engine(task.id, graph, neo4j_store, base_url, api_key, model)

# ── Quick Question Templates ──────────────────────────────────────────────────
_QUICK_QUESTIONS = {
    "基础参数": [
        "静态总投资是多少？",
        "动态总投资是多少？",
        "EPC合同额是多少？",
        "资本金比例是多少？",
    ],
    "盈利能力": [
        "税后全投资内部收益率是多少？",
        "财务净现值是多少？",
        "投资回收期是多少年？",
        "年均净现金流是多少？",
    ],
    "偿债能力": [
        "偿债备付率DSCR均值是多少？",
        "DSCR最低值是多少？",
        "利息备付率是多少？",
        "借款偿还期是多长？",
    ],
    "收入成本": [
        "全期营业收入是多少？",
        "综合购电成本是多少？",
        "利润总额是多少？",
        "净利润是多少？",
    ],
    "税费": [
        "增值税是多少？",
        "所得税总额是多少？",
        "税金及附加是多少？",
    ],
    "现金流": [
        "全期净现金流是多少？",
        "经营活动现金流入是多少？",
        "投资活动现金流出是多少？",
    ],
    "自定义": [],
}

_CATEGORIES = list(_QUICK_QUESTIONS.keys())

# ── Session State ─────────────────────────────────────────────────────────────
_CHAT_KEY = f"qa_chat_{task.id}"
if _CHAT_KEY not in st.session_state:
    st.session_state[_CHAT_KEY] = db.load_qa_history(task.id) or []

_last_answer_key = f"qa_last_answer_{task.id}"

chat_history = st.session_state[_CHAT_KEY]


def _persist_chat():
    db.save_qa_history(task.id, chat_history)


# ── Quick Question Panel ──────────────────────────────────────────────────────
st.subheader("快速提问")
cat_tabs = st.tabs(_CATEGORIES)

for tab, cat in zip(cat_tabs, _CATEGORIES):
    with tab:
        qs = _QUICK_QUESTIONS[cat]
        if cat == "自定义":
            continue
        cols = st.columns(min(len(qs), 4))
        for i, q in enumerate(qs):
            with cols[i % 4]:
                if st.button(q, key=f"qq_{cat}_{q}", use_container_width=True, type="secondary"):
                    chat_history.append({"role": "user", "content": q})
                    _persist_chat()
                    st.session_state["qa_auto_question"] = q
                    st.rerun()

st.divider()

# ── Derived Metrics Summary Panel ────────────────────────────────────────────
@st.cache_resource(show_spinner="加载派生指标...")
def _load_derived_metrics(_graph):
    return serialize_metrics(compute_derived_metrics(_graph))

dm_data = _load_derived_metrics(graph)

if any(v is not None for v in dm_data.values()):
    dm = deserialize_metrics(dm_data)
    with st.expander("核心财务指标", expanded=False):
        # Two-row layout: 5 + 4 aligned metrics, units in labels
        metrics_list = []
        if dm.irr_after_tax is not None:
            metrics_list.append(("税后IRR(%)", f"{dm.irr_after_tax * 100:.2f}"))
        if dm.npv_after_tax is not None:
            metrics_list.append(("财务净现值(万元)", f"{dm.npv_after_tax / 10000:,.2f}"))
        if dm.payback_period is not None:
            metrics_list.append(("投资回收期(年)", f"{dm.payback_period:.1f}"))
        if dm.dscr_avg is not None:
            metrics_list.append(("DSCR均值", f"{dm.dscr_avg:.2f}"))
        if dm.dscr_min is not None:
            metrics_list.append(("DSCR最低值", f"{dm.dscr_min:.2f}"))
        if dm.total_investment_dynamic is not None:
            metrics_list.append(("动态总投资(万元)", f"{dm.total_investment_dynamic / 10000:,.2f}"))
        if dm.total_investment_static is not None:
            metrics_list.append(("静态总投资(万元)", f"{dm.total_investment_static / 10000:,.2f}"))
        if dm.annual_net_cashflow is not None:
            metrics_list.append(("年均净现金流(万元)", f"{dm.annual_net_cashflow / 10000:,.2f}"))
        if dm.total_revenue is not None:
            metrics_list.append(("全期营收(万元)", f"{dm.total_revenue / 10000:,.2f}"))

        row1 = metrics_list[:5]
        row2 = metrics_list[5:]

        if row1:
            cols = st.columns(len(row1))
            for i, (label, val) in enumerate(row1):
                cols[i].metric(label, val)
        if row2:
            cols = st.columns(len(row2))
            for i, (label, val) in enumerate(row2):
                cols[i].metric(label, val)

        # DSCR series chart
        if dm.dscr_series:
            dscr_rows = [{"年份": k, "DSCR": round(v, 3)} for k, v in sorted(dm.dscr_series.items())]
            st.bar_chart(dscr_rows, x="年份", y="DSCR", horizontal=False)

# ── Chat Input ────────────────────────────────────────────────────────────────
question = st.chat_input("或输入自定义财务问题...")

# Handle auto-question from quick buttons
if "qa_auto_question" in st.session_state:
    question = st.session_state.pop("qa_auto_question")

# ── Helper Functions ──────────────────────────────────────────────────────────

# Derived metrics question patterns — detect questions requiring multi-indicator aggregation
_DERIVED_PATTERNS = [
    (["税后.*内部收益率", "全投资.*内部收益率", "IRR.*税后", "税后.*IRR"], "irr_after_tax", "税后全投资内部收益率", "%"),
    (["税前.*内部收益率", "IRR.*税前", "税前.*IRR"], "irr_before_tax", "税前全投资内部收益率", "%"),
    (["财务.*净现值", "净现值.*税后", "NPV.*税后"], "npv_after_tax", "财务净现值", ""),
    (["净现值.*税前", "NPV.*税前"], "npv_before_tax", "税前财务净现值", ""),
    (["投资回收期", "回收期"], "payback_period", "投资回收期", "年"),
    (["DSCR.*均值", "偿债备付率.*均值", "偿债备付率.*平均"], "dscr_avg", "DSCR均值", ""),
    (["DSCR.*最低", "DSCR.*最小", "偿债备付率.*最低"], "dscr_min", "DSCR最低值", ""),
    (["静态总投资"], "total_investment_static", "静态总投资", ""),
    (["动态总投资", "总投资"], "total_investment_dynamic", "动态总投资", ""),
    (["年均.*净现金流", "平均.*净现金流"], "annual_net_cashflow", "年均净现金流", ""),
    (["营业收入", "总收入", "售电收入"], "total_revenue", "全期营业收入", ""),
    (["总成本", "经营成本", "购电成本"], "total_cost", "全期总成本", ""),
    (["所得税.*总额?", "税金.*总额?", "税费.*总额?"], "total_tax", "全期税费", ""),
    (["借款偿还期", "贷款偿还期", "还款期.*多长"], "loan_repayment_period", "借款偿还期", "年"),
]

import re as _re


def _try_derived_metrics_answer(
    question: str,
    dm_data: dict,
    graph: FinancialGraph,
) -> dict | None:
    """Try to answer from pre-computed derived metrics. Returns structured answer dict or None."""
    if not any(v is not None for v in dm_data.values()):
        return None

    q = question
    from financial_kg.engine.derived_metrics import deserialize_metrics, _DSCR_KEYWORDS
    dm = deserialize_metrics(dm_data)

    for patterns, key, display_name, unit in _DERIVED_PATTERNS:
        if any(_re.search(p, q) for p in patterns):
            val = getattr(dm, key, None)
            if val is None:
                return None

            # Format value
            if unit == "%":
                formatted = f"{val * 100:.2f}%"
            elif unit == "年":
                formatted = f"{val:.2f}年"
            elif isinstance(val, float) and abs(val) >= 1000:
                formatted = f"{val:,.2f}"
            else:
                formatted = f"{val:.2f}"

            return {
                "text": f"**{display_name}** 为 **{formatted}**{' ' + unit if unit else ''}",
                "metrics": [{"name": display_name, "value": formatted, "unit": unit, "match_reason": "derived_metrics"}],
                "chart_data": [],
                "confidence": 90,
                "sources": [],
            }

    # DSCR series question
    if any(p in q for p in ["DSCR.*分布", "DSCR.*年度", "偿债备付率.*分布", "DSCR.*各年"]):
        if dm.dscr_series:
            annual = sorted(dm.dscr_series.items())
            import streamlit.components.v1 as components
            from financial_kg.viz.qa_chart import render_time_series_html

            chart_data = [{
                "name": "DSCR",
                "values": {str(k): round(v, 3) for k, v in annual},
            }]
            return {
                "text": f"**DSCR年度分布**（共 {len(annual)} 年）：均值 **{dm.dscr_avg:.2f}**，最低 **{dm.dscr_min:.2f}**",
                "metrics": [
                    {"name": "DSCR均值", "value": f"{dm.dscr_avg:.2f}", "unit": "", "match_reason": "derived_metrics"},
                    {"name": "DSCR最低值", "value": f"{dm.dscr_min:.2f}", "unit": "", "match_reason": "derived_metrics"},
                ],
                "chart_data": chart_data,
                "confidence": 90,
                "sources": [],
            }

    # Multi-metric summary question (e.g. "核心财务指标", "效益概况")
    if any(p in q for p in ["核心.*指标", "效益.*概况", "财务.*概况", "总体.*情况"]):
        metrics_list = []
        if dm.irr_after_tax is not None:
            metrics_list.append({"name": "税后IRR", "value": f"{dm.irr_after_tax * 100:.2f}%", "unit": "", "match_reason": "derived_metrics"})
        if dm.npv_after_tax is not None:
            metrics_list.append({"name": "财务净现值", "value": f"{dm.npv_after_tax:,.0f}", "unit": "", "match_reason": "derived_metrics"})
        if dm.payback_period is not None:
            metrics_list.append({"name": "投资回收期", "value": f"{dm.payback_period:.2f}年", "unit": "", "match_reason": "derived_metrics"})
        if dm.dscr_avg is not None:
            metrics_list.append({"name": "DSCR均值", "value": f"{dm.dscr_avg:.2f}", "unit": "", "match_reason": "derived_metrics"})
        if dm.total_investment_dynamic is not None:
            metrics_list.append({"name": "动态总投资", "value": f"{dm.total_investment_dynamic:,.0f}", "unit": "", "match_reason": "derived_metrics"})

        if metrics_list:
            return {
                "text": f"**核心财务指标概览**（共 {len(metrics_list)} 项）",
                "metrics": metrics_list,
                "chart_data": [],
                "confidence": 85,
                "sources": [],
            }

    # 毛利率: need 营业收入 and 营业成本 → multi-indicator aggregation
    if "毛利率" in q:
        revenue_ind = None
        cost_ind = None
        for ind in graph.indicators.values():
            name = ind.name or ""
            if "营业收入" in name or "总收入" in name:
                revenue_ind = ind
            if "营业成本" in name or "总成本" in name or "购电成本" in name:
                cost_ind = ind

        if revenue_ind and cost_ind:
            rev = revenue_ind.summary_value
            cost = cost_ind.summary_value
            if rev is not None and cost is not None:
                try:
                    rev_f, cost_f = float(rev), float(cost)
                    if rev_f != 0:
                        margin = (rev_f - cost_f) / rev_f * 100
                        return {
                            "text": f"**毛利率** 为 **{margin:.2f}%**\n\n计算：营业收入({rev_f:,.2f}) - 营业成本({cost_f:,.2f}) / 营业收入({rev_f:,.2f})",
                            "metrics": [
                                {"name": "营业收入", "value": f"{rev_f:,.2f}", "unit": revenue_ind.unit or "", "match_reason": "aggregation"},
                                {"name": "营业成本", "value": f"{cost_f:,.2f}", "unit": cost_ind.unit or "", "match_reason": "aggregation"},
                                {"name": "毛利率", "value": f"{margin:.2f}%", "unit": "%", "match_reason": "computed"},
                            ],
                            "chart_data": [],
                            "confidence": 80,
                            "sources": [
                                {"name": revenue_ind.name, "sheet": revenue_ind.sheet, "value": f"{rev_f:,.2f}", "unit": revenue_ind.unit or "", "score": 8.0, "indicator_id": revenue_ind.id},
                                {"name": cost_ind.name, "sheet": cost_ind.sheet, "value": f"{cost_f:,.2f}", "unit": cost_ind.unit or "", "score": 8.0, "indicator_id": cost_ind.id},
                            ],
                        }
                except (TypeError, ValueError):
                    pass

    # 净利率: need 净利润 and 营业收入
    if "净利率" in q:
        net_profit_ind = None
        revenue_ind = None
        for ind in graph.indicators.values():
            name = ind.name or ""
            if "净利润" in name:
                net_profit_ind = ind
            if "营业收入" in name or "总收入" in name:
                revenue_ind = ind

        if net_profit_ind and revenue_ind:
            profit = net_profit_ind.summary_value
            rev = revenue_ind.summary_value
            if profit is not None and rev is not None:
                try:
                    profit_f, rev_f = float(profit), float(rev)
                    if rev_f != 0:
                        net_margin = profit_f / rev_f * 100
                        return {
                            "text": f"**净利率** 为 **{net_margin:.2f}%**\n\n计算：净利润({profit_f:,.2f}) / 营业收入({rev_f:,.2f})",
                            "metrics": [
                                {"name": "净利润", "value": f"{profit_f:,.2f}", "unit": net_profit_ind.unit or "", "match_reason": "aggregation"},
                                {"name": "营业收入", "value": f"{rev_f:,.2f}", "unit": revenue_ind.unit or "", "match_reason": "aggregation"},
                                {"name": "净利率", "value": f"{net_margin:.2f}%", "unit": "%", "match_reason": "computed"},
                            ],
                            "chart_data": [],
                            "confidence": 80,
                            "sources": [
                                {"name": net_profit_ind.name, "sheet": net_profit_ind.sheet, "value": f"{profit_f:,.2f}", "unit": net_profit_ind.unit or "", "score": 8.0, "indicator_id": net_profit_ind.id},
                                {"name": revenue_ind.name, "sheet": revenue_ind.sheet, "value": f"{rev_f:,.2f}", "unit": revenue_ind.unit or "", "score": 8.0, "indicator_id": revenue_ind.id},
                            ],
                        }
                except (TypeError, ValueError):
                    pass

    return None


def _build_structured_answer(question: str, state: dict) -> dict:
    """Build structured answer dict from retrieval result and LLM text."""
    retrieval = state.get("retrieval")
    text = state.get("full_answer", "")

    result = {
        "text": text,
        "metrics": [],
        "chart_data": [],
        "confidence": 0,
        "sources": [],
    }

    if not retrieval or not retrieval.contexts:
        result["confidence"] = 0
        return result

    contexts = retrieval.contexts

    # Detect name conflicts for disambiguation
    name_counts: dict[str, int] = {}
    for ctx in contexts:
        ind = ctx.indicator
        name_counts[ind.name] = name_counts.get(ind.name, 0) + 1
    has_duplicates = {n for n, c in name_counts.items() if c > 1}

    def _unique_name(ind) -> str:
        """Return indicator name with table/sheet suffix if there are duplicates."""
        if ind.name not in has_duplicates:
            return ind.name
        parts = [ind.name]
        if ind.table_id:
            tbl = graph.tables.get(ind.table_id)
            if tbl:
                parts.append(f"[{tbl.name[:15]}]")
        parts.append(f"({ind.sheet})")
        return " ".join(parts)

    # Confidence: based on match scores and coverage
    avg_score = sum(c.match_score for c in contexts) / len(contexts) if contexts else 0
    max_score = max((c.match_score for c in contexts), default=0)
    has_time_series = sum(1 for c in contexts if c.indicator.time_series)
    result["confidence"] = min(100, int(
        (min(avg_score / 10, 1) * 40) +
        (min(max_score / 10, 1) * 30) +
        (min(len(contexts) / 8, 1) * 15) +
        (min(has_time_series / max(len(contexts), 1), 1) * 15)
    ))

    # Top-3 metrics from retrieval
    for ctx in contexts[:3]:
        ind = ctx.indicator
        val = ind.display_value if ind.display_value is not None else (
            f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
            else str(ind.summary_value or "—")
        )
        # Query-year highlight
        year_val = ""
        if retrieval.query_years and ind.time_series:
            for k, v in ind.time_series.items():
                if any(y in str(k) for y in retrieval.query_years):
                    year_val = f"{k}: {v}"
                    break
        display_name = _unique_name(ind) if ind.name in has_duplicates else ind.name
        result["metrics"].append({
            "name": display_name[:25],
            "value": year_val or val,
            "unit": ind.unit or "",
            "match_reason": ctx.match_reason,
        })

    # Chart data: collect time series from matched indicators
    for ctx in contexts:
        ind = ctx.indicator
        if ind.time_series:
            # Filter to query years if present, else show all
            if retrieval.query_years:
                filtered = {k: v for k, v in ind.time_series.items()
                           if any(y in str(k) for y in retrieval.query_years)}
            else:
                filtered = ind.time_series
            if filtered:
                result["chart_data"].append({
                    "name": _unique_name(ind),
                    "values": filtered,
                })

    # Source cards
    for ctx in contexts:
        ind = ctx.indicator
        val = ind.display_value if ind.display_value is not None else (
            f"{ind.summary_value:.2f}" if isinstance(ind.summary_value, float)
            else str(ind.summary_value or "—")
        )
        result["sources"].append({
            "name": ind.name,
            "sheet": ind.sheet,
            "value": val,
            "unit": ind.unit or "",
            "score": ctx.match_score,
            "indicator_id": ind.id,
        })

    return result


def _render_structured_answer(data: dict):
    """Render a structured answer with metrics, charts, tables, and text."""
    text = data.get("text", "")
    metrics = data.get("metrics", [])
    chart_data = data.get("chart_data", [])
    confidence = data.get("confidence", 0)
    sources = data.get("sources", [])

    # Confidence gauge
    if confidence > 0:
        conf_label = "高" if confidence >= 70 else ("中" if confidence >= 40 else "低")
        st.caption(f"置信度: {'█' * (confidence // 10)}{'░' * (10 - confidence // 10)} {confidence}% ({conf_label})")

    # Metric cards
    if metrics:
        m_cols = st.columns(min(len(metrics), 3))
        for i, m in enumerate(metrics):
            with m_cols[i]:
                st.metric(label=m["name"], value=str(m["value"]), delta=m["unit"] if m["unit"] else None)

    # Time series chart
    if chart_data:
        import streamlit.components.v1 as components
        from financial_kg.viz.qa_chart import render_time_series_html

        chart_names = ", ".join(d["name"] for d in chart_data)
        with st.expander(f"📊 数据趋势（{len(chart_data)} 个指标）", expanded=True):
            html = render_time_series_html(chart_data, title=chart_names[:50])
            components.html(html, height=350, scrolling=False)

    # Time series table
    if chart_data:
        with st.expander("📋 数据明细", expanded=False):
            for cd in chart_data:
                st.write(f"**{cd['name']}**")
                rows = [{"年份/期间": str(k), "值": v} for k, v in cd["values"].items()]
                st.dataframe(rows, use_container_width=True, height=200)

    # LLM text answer
    if text:
        st.markdown(text)

    # Data source cards
    if sources:
        st.divider()
        st.caption("数据来源")
        src_cols = st.columns(min(len(sources), 3))
        for i, src in enumerate(sources):
            with src_cols[i % 3]:
                score_color = "#a6e3a1" if src["score"] >= 7 else ("#f9e2af" if src["score"] >= 4 else "#585b70")
                st.markdown(
                    f"**{src['name']}** "
                    f"<span style='color:{score_color}'>[{src['score']:.1f}]</span> "
                    f"= {src['value']} {src['unit']}".strip(),
                    unsafe_allow_html=True,
                )
                st.caption(f"Sheet: {src['sheet']}")


def _generate_follow_ups(answer_data: dict) -> list[str]:
    """Generate follow-up questions based on matched indicators' relationships."""
    sources = answer_data.get("sources", [])
    if not sources:
        return []

    follow_ups: list[str] = []
    seen: set[str] = set()

    for src in sources[:3]:
        ind_id = src["indicator_id"]
        ind = graph.indicators.get(ind_id)
        if not ind:
            continue

        for dep_id in ind.depends_on_indicators[:2]:
            dep = graph.indicators.get(dep_id)
            if dep and dep.name not in seen:
                q = f"{dep.name}是多少？"
                if q not in seen:
                    follow_ups.append(q)
                    seen.add(dep.name)

        for dep_id in ind.depended_by_indicators[:2]:
            dep = graph.indicators.get(dep_id)
            if dep and dep.name not in seen:
                q = f"{dep.name}是多少？"
                if q not in seen:
                    follow_ups.append(q)
                    seen.add(dep.name)

    return follow_ups[:4]


# ── Render existing chat history ─────────────────────────────────────────────
for msg in chat_history:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    elif msg["role"] == "assistant":
        with st.chat_message("assistant"):
            # Check for structured answer data
            if isinstance(msg["content"], dict):
                data = msg["content"]
                _render_structured_answer(data)
            else:
                st.markdown(msg["content"])

# ── Process new question ─────────────────────────────────────────────────────
if question:
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        # ── Step 1: Try derived metrics answer first (fast, no LLM) ──────────
        derived_answer = _try_derived_metrics_answer(question, dm_data, graph)

        if derived_answer:
            # Direct answer from pre-computed metrics — skip LLM
            structured = derived_answer
        else:
            # ── Step 2: Fall back to LLM + retrieval ────────────────────────
            state = {"full_answer": "", "retrieval": None, "cypher": None, "structured": None}

            def _stream():
                for event_type, data in engine.ask_stream(
                    question,
                    chat_history=chat_history,
                    top_k=top_k,
                ):
                    if event_type == "retrieval":
                        state["retrieval"] = data
                    elif event_type == "cypher":
                        state["cypher"] = data
                    elif event_type == "chunk":
                        state["full_answer"] += data
                        yield data
                    elif event_type in ("answer", "error"):
                        state["full_answer"] = data
                        yield data

            answer_text = st.write_stream(_stream())
            state["full_answer"] = answer_text

            # Build structured answer from retrieval + text
            structured = _build_structured_answer(question, state)
            state["structured"] = structured

    # Save user question + structured answer to history
    # Note: quick question / follow-up buttons already appended user message
    # before rerun. Only chat_input direct entry reaches here without user msg.
    # Also guard against chat_input rerun where user msg may already exist.
    def _already_asked(q: str) -> bool:
        return any(m.get("role") == "user" and m.get("content") == q for m in chat_history[-5:])

    if not _already_asked(question):
        chat_history.append({"role": "user", "content": question})
    chat_history.append({
        "role": "assistant",
        "content": structured,
    })
    _persist_chat()
    st.rerun()

# ── Clear button ──────────────────────────────────────────────────────────────
if st.button("清空对话", type="secondary"):
    chat_history.clear()
    db.clear_qa_history(task.id)
    st.rerun()

# ── Follow-up suggestions ─────────────────────────────────────────────────────
if chat_history and chat_history[-1]["role"] == "assistant":
    last_answer = chat_history[-1]["content"]
    if isinstance(last_answer, dict):
        suggestions = _generate_follow_ups(last_answer)
        if suggestions:
            st.divider()
            st.caption("💡 你可能还想问")
            sug_cols = st.columns(min(len(suggestions), 3))
            for i, sq in enumerate(suggestions):
                with sug_cols[i % 3]:
                    if st.button(sq, key=f"fu_{sq}", use_container_width=True, type="secondary"):
                        chat_history.append({"role": "user", "content": sq})
                        _persist_chat()
                        st.session_state["qa_auto_question"] = sq
                        st.rerun()

# ── Export Financial Report ──────────────────────────────────────────────────
st.divider()
exp_col1, exp_col2, exp_col3 = st.columns([3, 1, 1])
with exp_col1:
    st.markdown("#### 导出财务效益分析报告")
    st.caption("按甲方模板结构生成 Word 报告，含基础参数、盈利能力、偿债能力、财务生存能力、敏感性分析。")

    # Pre-check sensitivity history availability
    _export_history_list = db.list_sensitivity(task.id)
    if _export_history_list:
        st.caption(f"已检测到敏感性分析「{_export_history_list[0]['run_name']}」，导出时将自动复用，无需重新计算。")

with exp_col3:
    if st.button("导出 Word 报告", type="primary", use_container_width=True):
        from financial_kg.engine.report_export import export_financial_report, _auto_detect_params
        from financial_kg.engine.sensitivity import SensitivityResult
        from financial_kg.engine.derived_metrics import deserialize_metrics as _deser
        from financial_kg.engine.sensitivity import SensitivityScenario

        # Try to reuse sensitivity analysis from history
        _sen_result = None
        if _export_history_list:
            latest = _export_history_list[0]
            base_metrics = _deser(latest["base_metrics"])
            scenario_objs = [
                SensitivityScenario(
                    name=s["name"], param_name=s["param_name"],
                    param_cell_id=s["param_cell_id"], perturbation=s["perturbation"],
                    original_value=s["original_value"], perturbed_value=s["perturbed_value"],
                    metrics=_deser(s["metrics"]), snapshot_name=s["snapshot_name"],
                )
                for s in latest["scenarios"]
            ]
            _sen_result = SensitivityResult(base_metrics=base_metrics, scenarios=scenario_objs)

        report_path = os.path.join(task.output_dir, f"{task.id}_financial_report.docx")
        with st.spinner("生成报告中..."):
            params = _auto_detect_params(graph)
            export_financial_report(
                graph=graph,
                output_path=report_path,
                task_id=task.id,
                output_dir=task.output_dir,
                project_name=task.filename,
                sensitivity_result=_sen_result,
            )

        with open(report_path, "rb") as f:
            st.download_button(
                "下载财务效益分析报告",
                data=f,
                file_name=f"{task.filename.rsplit('.', 1)[0]}_财务效益分析报告.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

