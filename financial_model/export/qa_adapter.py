"""通用模型 Q&A 适配器 — 自然语言查询模型结果

支持两种模式:
  1. 无 LLM (retrieval-only): 关键词匹配 → 从 AllResults 检索数据 → 格式化返回
  2. 有 LLM: 关键词匹配 → 构建 prompt → LLM 生成自然语言回答

典型用法::

    from financial_model.export.qa_adapter import GenericModelQAAdapter

    adapter = GenericModelQAAdapter(results, config)

    # 无 LLM 模式
    answer = adapter.ask("IRR 是多少?")
    print(answer)

    # 带 LLM 模式
    answer = adapter.ask("DSCR 趋势如何?", llm_client=openai_client)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from financial_model.analysis.types import METRIC_DISPLAY, MetricKey
from financial_model.engines.orchestrator import AllResults


# ══════════════════════════════════════════════════════════
# 关键词映射
# ══════════════════════════════════════════════════════════

_TOPIC_RULES: list[tuple[list[str], str]] = [
    # (keywords, topic)
    (["IRR", "内部收益率", "收益率", "投资回报"], "irr"),
    (["NPV", "净现值", "现值"], "npv"),
    (["DSCR", "偿债备付率", "偿债能力", "还本付息"], "dscr"),
    (["回收期", "投资回收", "几年回本"], "payback"),
    (["ROE", "净资产收益率", "股东回报"], "roe"),
    (["投资", "总投资", "建设投资", "建设期利息", "概算"], "investment"),
    (["收入", "营业收入", "发电收入", "电费"], "revenue"),
    (["成本", "费用", "运维", "经营成本"], "cost"),
    (["折旧", "摊销", "固定资产"], "depreciation"),
    (["利润", "净利润", "利润总额", "所得税"], "profit"),
    (["现金", "现金流", "净现金流"], "cashflow"),
    (["资产", "负债", "资产负债率", "资产负债表"], "balance_sheet"),
    (["电价", "上网电价", "抽水电价", "电费"], "price"),
    (["装机", "容量", "MW", "利用小时"], "capacity"),
    (["贷款", "还款", "利率", "融资", "利息"], "financing"),
    (["税", "增值税", "所得税", "附加税"], "tax"),
    (["全部", "所有", "概览", "汇总", "关键指标", "总览"], "summary"),
]


# ══════════════════════════════════════════════════════════
# 数据点
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DataPoint:
    """单个数据点"""

    topic: str
    label: str
    value: str
    source: str  # 来源描述


# ══════════════════════════════════════════════════════════
# GenericModelQAAdapter
# ══════════════════════════════════════════════════════════


class GenericModelQAAdapter:
    """将通用模型结果适配为 Q&A 可检索的结构

    用法::

        adapter = GenericModelQAAdapter(results, config)
        answer = adapter.ask("全投资IRR是多少?")
    """

    def __init__(
        self,
        results: AllResults,
        config: Any,
    ) -> None:
        self._results = results
        self._config = config
        self._dm = results.derived_metrics

    # ── 公开接口 ──────────────────────────────────────────

    def ask(
        self,
        question: str,
        llm_client: Any = None,
        llm_model: str = "gpt-4o",
    ) -> str:
        """回答问题

        Args:
            question: 用户问题
            llm_client: OpenAI 兼容客户端 (可选)
            llm_model: LLM 模型名

        Returns:
            回答文本
        """
        topics = self._detect_topics(question)
        data_points = self._find_relevant_data(topics)

        if not data_points:
            return (
                f"抱歉，未找到与 \"{question}\" 相关的数据。\n\n"
                f"可查询的主题: IRR、NPV、DSCR、回收期、投资、收入、成本、"
                f"折旧、利润、现金流、资产负债、电价、融资、税务等。"
            )

        if llm_client is not None:
            return self._ask_with_llm(question, data_points, llm_client, llm_model)

        return self._format_answer(data_points, question)

    def get_preset_questions(self) -> list[str]:
        """返回预设常见问题列表"""
        return [
            "全投资IRR是多少?",
            "资本金IRR是多少?",
            "全投资NPV是多少?",
            "最低DSCR是多少?",
            "静态回收期是几年?",
            "建设投资总额是多少?",
            "年收入有多少?",
            "电价是多少?",
            "请给出关键指标概览。",
        ]

    # ── 主题检测 ──────────────────────────────────────────

    def _detect_topics(self, question: str) -> list[str]:
        """从问题中检测相关主题"""
        topics: list[str] = []
        for keywords, topic in _TOPIC_RULES:
            for kw in keywords:
                if kw in question:
                    if topic not in topics:
                        topics.append(topic)
                    break
        return topics or ["summary"]

    # ── 数据检索 ──────────────────────────────────────────

    def _find_relevant_data(self, topics: list[str]) -> list[DataPoint]:
        """根据主题检索数据点"""
        points: list[DataPoint] = []
        seen: set[str] = set()

        for topic in topics:
            new_points = self._retrieve_topic(topic)
            for p in new_points:
                if p.label not in seen:
                    seen.add(p.label)
                    points.append(p)

        return points

    def _retrieve_topic(self, topic: str) -> list[DataPoint]:
        """检索单个主题的数据"""
        dm = self._dm
        results = self._results
        config = self._config

        def _p(label: str, value: Any, fmt: str = "text", source: str = "派生指标") -> DataPoint:
            if value is None:
                formatted = "N/A"
            elif fmt == "pct":
                formatted = f"{value:.2%}"
            elif fmt == "money":
                formatted = f"{value:,.2f} 万元"
            elif fmt == "year":
                formatted = f"{value:.1f} 年"
            elif fmt == "ratio":
                formatted = f"{value:.2f}"
            else:
                formatted = str(value)
            return DataPoint(topic=topic, label=label, value=formatted, source=source)

        if topic == "irr":
            return [
                _p("全投资IRR", dm.irr_total, "pct"),
                _p("资本金IRR", dm.irr_equity, "pct"),
            ]

        if topic == "npv":
            return [
                _p("全投资NPV", dm.npv_total, "money"),
                _p("资本金NPV", dm.npv_equity, "money"),
            ]

        if topic == "dscr":
            points = [
                _p("最低DSCR", dm.dscr_min, "ratio"),
                _p("平均DSCR", dm.dscr_avg, "ratio"),
            ]
            # 添加年度 DSCR (运营期前 10 年)
            if dm.dscr_by_year:
                years_sorted = sorted(dm.dscr_by_year.keys())
                sample_years = years_sorted[:10]
                for y in sample_years:
                    points.append(DataPoint(
                        topic=topic, label=f"DSCR {y}年",
                        value=f"{dm.dscr_by_year[y]:.2f}",
                        source="年度DSCR",
                    ))
            return points

        if topic == "payback":
            return [
                _p("静态回收期", dm.payback_static, "year"),
                _p("动态回收期", dm.payback_dynamic, "year"),
            ]

        if topic == "roe":
            return [_p("平均ROE", dm.roe_avg, "pct")]

        if topic == "investment":
            invest_total = float(results.investment["construction_investment"].sum())
            fin = results.financing
            return [
                _p("建设投资", invest_total, "money", "投资概算"),
                _p("建设期利息", fin.construction_interest_total, "money", "融资引擎"),
                _p("动态总投资", fin.dynamic_total_investment, "money", "融资引擎"),
            ]

        if topic == "revenue":
            rev_df = results.revenue
            points: list[DataPoint] = []
            if "total_revenue" in rev_df.columns:
                total_rev = float(rev_df["total_revenue"].sum())
                points.append(_p("总收入(全期)", total_rev, "money", "收入引擎"))
                # 运营期前 5 年
                for year in list(rev_df.index)[:5]:
                    v = rev_df.loc[year, "total_revenue"] if year in rev_df.index else None
                    if pd.notna(v):
                        points.append(DataPoint(
                            topic=topic, label=f"{year}年收入",
                            value=f"{v:,.2f} 万元", source="收入引擎",
                        ))
            return points

        if topic == "cost":
            cost_df = results.cost
            points = []
            if "total_production_cost" in cost_df.columns:
                total_cost = float(cost_df["total_production_cost"].sum())
                points.append(_p("总成本(全期)", total_cost, "money", "成本引擎"))
            return points

        if topic == "depreciation":
            dep_df = results.depreciation
            points = []
            if "depreciation_total" in dep_df.columns:
                total_dep = float(dep_df["depreciation_total"].sum())
                points.append(_p("总折旧(全期)", total_dep, "money", "折旧引擎"))
            return points

        if topic == "profit":
            pnl = results.pnl_total.data
            points = []
            if "net_profit" in pnl.columns:
                total_profit = float(pnl["net_profit"].sum())
                points.append(_p("净利润合计(全期)", total_profit, "money", "利润表"))
                for year in list(pnl.index)[:5]:
                    v = pnl.loc[year, "net_profit"] if year in pnl.index else None
                    if pd.notna(v):
                        points.append(DataPoint(
                            topic=topic, label=f"{year}年净利润",
                            value=f"{v:,.2f} 万元", source="利润表",
                        ))
            return points

        if topic == "cashflow":
            cf = results.cf_total.data
            points = []
            if "net_cashflow" in cf.columns:
                total_cf = float(cf["net_cashflow"].sum())
                points.append(_p("累计净现金流", total_cf, "money", "现金流量表"))
            return points

        if topic == "balance_sheet":
            alr = dm.asset_liability_ratio
            points = []
            if alr:
                years_sorted = sorted(alr.keys())
                for y in years_sorted[:5]:
                    points.append(DataPoint(
                        topic=topic, label=f"{y}年资产负债率",
                        value=f"{alr[y]:.2%}", source="资产负债表",
                    ))
            return points

        if topic == "price":
            op = config.operating
            return [
                DataPoint(topic=topic, label="上网电价",
                          value=f"{op.grid_price:.4f} 元/kWh", source="运营参数"),
                DataPoint(topic=topic, label="抽水电价",
                          value=f"{op.pump_price:.4f} 元/kWh", source="运营参数"),
            ]

        if topic == "capacity":
            op = config.operating
            c = config.construction
            return [
                DataPoint(topic=topic, label="装机容量",
                          value=f"{op.installed_capacity_mw:.0f} MW", source="运营参数"),
                DataPoint(topic=topic, label="年利用小时",
                          value=f"{op.annual_utilization_hours:.2f} h", source="运营参数"),
                DataPoint(topic=topic, label="建设期",
                          value=f"{c.construction_years} 年", source="建设参数"),
                DataPoint(topic=topic, label="运营期",
                          value=f"{c.operation_years} 年", source="建设参数"),
            ]

        if topic == "financing":
            fin = results.financing
            return [
                DataPoint(topic=topic, label="资本金比例",
                          value=f"{config.financing.equity_ratio:.0%}", source="融资参数"),
                DataPoint(topic=topic, label="建设期利率",
                          value=f"{config.financing.construction_interest_rate:.2%}", source="融资参数"),
                DataPoint(topic=topic, label="建设期利息总额",
                          value=f"{fin.construction_interest_total:,.2f} 万元", source="融资引擎"),
            ]

        if topic == "tax":
            tx = config.tax
            return [
                DataPoint(topic=topic, label="增值税率",
                          value=f"{tx.vat_rate:.0%}", source="税务参数"),
                DataPoint(topic=topic, label="所得税率",
                          value=f"{tx.income_tax_rate:.0%}", source="税务参数"),
                DataPoint(topic=topic, label="附加税费率",
                          value=f"{tx.surcharge_rate:.2%}", source="税务参数"),
            ]

        if topic == "summary":
            invest_total = float(results.investment["construction_investment"].sum())
            return [
                _p("全投资IRR", dm.irr_total, "pct"),
                _p("资本金IRR", dm.irr_equity, "pct"),
                _p("全投资NPV", dm.npv_total, "money"),
                _p("最低DSCR", dm.dscr_min, "ratio"),
                _p("静态回收期", dm.payback_static, "year"),
                _p("动态回收期", dm.payback_dynamic, "year"),
                DataPoint(topic=topic, label="建设投资",
                          value=f"{invest_total:,.0f} 万元", source="投资概算"),
                DataPoint(topic=topic, label="装机容量",
                          value=f"{config.operating.installed_capacity_mw:.0f} MW", source="运营参数"),
            ]

        return []

    # ── 格式化回答 ──────────────────────────────────────────

    def _format_answer(
        self, data_points: list[DataPoint], question: str
    ) -> str:
        """格式化数据点为回答文本"""
        lines = [f"**关于: {question}**\n"]
        current_topic = ""
        for dp in data_points:
            if dp.topic != current_topic:
                current_topic = dp.topic
            lines.append(f"- **{dp.label}**: {dp.value} *(来源: {dp.source})*")
        return "\n".join(lines)

    # ── LLM 增强模式 ──────────────────────────────────────

    def _ask_with_llm(
        self,
        question: str,
        data_points: list[DataPoint],
        llm_client: Any,
        llm_model: str,
    ) -> str:
        """使用 LLM 生成自然语言回答"""
        context = self._format_answer(data_points, question)

        system_prompt = (
            "你是一个专业的抽水蓄能电站财务分析助手。\n"
            "基于以下模型计算结果回答用户问题。\n"
            "要求:\n"
            "1. 引用具体数据\n"
            "2. 必要时给出分析建议\n"
            "3. 如果数据不足，说明需要哪些信息\n\n"
            f"## 模型数据\n{context}"
        )

        try:
            response = llm_client.chat.completions.create(
                model=llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.3,
                max_tokens=1000,
            )
            return response.choices[0].message.content
        except Exception as e:
            # LLM 调用失败时降级为纯检索
            return f"{context}\n\n*(LLM 调用失败: {e}，以上为原始数据)*"
