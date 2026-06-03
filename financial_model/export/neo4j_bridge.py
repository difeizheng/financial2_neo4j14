"""Neo4j 知识图谱桥接 — 将通用模型 AllResults 注入 Neo4j

将通用模型结果映射为 Neo4j 节点/关系，复用现有 Q&A/可视化体系。

节点类型:
  - GMetric: 派生指标 (IRR, NPV, DSCR, 回收期, ...)
  - GReport: 报表 (投资概算, 资金筹措, 折旧摊销, ...)
  - GRow: 报表行 (逐年数据)
  - GParam: 关键参数 (装机容量, 电价, 利率, ...)

关系类型:
  - HAS_METRIC: GReport → GMetric
  - HAS_ROW: GReport → GRow
  - DRIVES: GParam → GReport
  - DERIVED_FROM: GMetric → GMetric

所有节点 ID 格式: {task_id}_gm_{type}_{key}
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from financial_model.analysis.types import METRIC_DISPLAY, MetricKey
from financial_model.engines.derived_metrics import DerivedMetrics
from financial_model.engines.orchestrator import AllResults

# 避免循环导入 — Neo4jStore 通过 duck typing 使用
_Neo4jStore = Any


# ══════════════════════════════════════════════════════════
# 节点 ID 生成
# ══════════════════════════════════════════════════════════


def _nid(task_id: str, node_type: str, key: str) -> str:
    """生成节点 ID: {task_id}_gm_{type}_{key}"""
    safe_key = key.replace(" ", "_").replace("(", "").replace(")", "")
    return f"{task_id}_gm_{node_type}_{safe_key}"


# ══════════════════════════════════════════════════════════
# 报表名映射
# ══════════════════════════════════════════════════════════

_REPORT_NAMES: list[tuple[str, str, str]] = [
    # (field_name, display_name, engine)
    ("investment", "投资概算", "InvestmentEngine"),
    ("financing", "资金筹措", "FinancingEngine"),
    ("depreciation", "折旧摊销", "DepreciationEngine"),
    ("cost", "成本费用", "CostEngine"),
    ("revenue", "收入税金", "RevenueEngine"),
    ("pnl_total", "利润表_全投资", "PnLEngine"),
    ("pnl_equity", "利润表_资本金", "PnLEngine"),
    ("cf_total", "现金流量表_全投资", "CashFlowEngine"),
    ("cf_equity", "现金流量表_资本金", "CashFlowEngine"),
    ("cf_plan", "现金流量表_财务计划", "CashFlowEngine"),
    ("balance_sheet", "资产负债表", "BalanceSheetEngine"),
    ("derived_metrics", "派生指标", "DerivedMetricsCalculator"),
]


# ══════════════════════════════════════════════════════════
# 关键参数提取
# ══════════════════════════════════════════════════════════


def _extract_key_params(config: Any) -> list[dict[str, Any]]:
    """从 ModelConfig 提取关键参数列表"""
    params: list[dict[str, Any]] = []
    c = config

    # 建设参数
    params.append({
        "group": "construction", "field": "construction_start",
        "value": str(c.construction.construction_start),
        "display_name": "建设期起始",
    })
    params.append({
        "group": "construction", "field": "construction_end",
        "value": str(c.construction.construction_end),
        "display_name": "建设期结束",
    })
    params.append({
        "group": "construction", "field": "operation_years",
        "value": c.construction.operation_years,
        "display_name": "运营期年数",
    })

    # 运营参数
    params.append({
        "group": "operating", "field": "installed_capacity_mw",
        "value": c.operating.installed_capacity_mw,
        "display_name": "装机容量(MW)",
    })
    params.append({
        "group": "operating", "field": "grid_price",
        "value": c.operating.grid_price,
        "display_name": "上网电价(元/kWh)",
    })
    params.append({
        "group": "operating", "field": "pump_price",
        "value": c.operating.pump_price,
        "display_name": "抽水电价(元/kWh)",
    })
    params.append({
        "group": "operating", "field": "annual_utilization_hours",
        "value": c.operating.annual_utilization_hours,
        "display_name": "年利用小时",
    })

    # 融资参数
    params.append({
        "group": "financing", "field": "equity_ratio",
        "value": c.financing.equity_ratio,
        "display_name": "资本金比例",
    })
    params.append({
        "group": "financing", "field": "construction_interest_rate",
        "value": c.financing.construction_interest_rate,
        "display_name": "建设期利率",
    })

    # 折旧参数
    params.append({
        "group": "depreciation", "field": "fixed_assets_original",
        "value": c.depreciation.fixed_assets.original_value,
        "display_name": "固定资产原值(万元)",
    })
    params.append({
        "group": "depreciation", "field": "fixed_assets_life",
        "value": c.depreciation.fixed_assets.useful_life,
        "display_name": "折旧年限(年)",
    })

    # 税务参数
    params.append({
        "group": "tax", "field": "vat_rate",
        "value": c.tax.vat_rate,
        "display_name": "增值税率",
    })
    params.append({
        "group": "tax", "field": "income_tax_rate",
        "value": c.tax.income_tax_rate,
        "display_name": "所得税率",
    })

    return params


# ══════════════════════════════════════════════════════════
# Neo4jBridge
# ══════════════════════════════════════════════════════════


class Neo4jBridge:
    """将通用模型 AllResults 注入 Neo4j 知识图谱

    用法::

        from financial_kg.storage.neo4j_store import Neo4jStore
        from financial_model.export.neo4j_bridge import Neo4jBridge

        with Neo4jStore(uri, user, password) as store:
            bridge = Neo4jBridge(store, task_id="gm_1400mw")
            counts = bridge.import_results(results, config)
            print(f"导入 {counts} 个节点/关系")

            # 查询
            metric = bridge.query_metric("irr_total")
            rows = bridge.query_report_rows("利润表_全投资", year=2035)
    """

    def __init__(self, neo4j_store: _Neo4jStore, task_id: str) -> None:
        self._store = neo4j_store
        self._driver = neo4j_store._driver
        self._task_id = task_id

    # ── 主入口 ────────────────────────────────────────────

    def import_results(
        self,
        results: AllResults,
        config: Any,
    ) -> dict[str, int]:
        """导入全部结果到 Neo4j

        Returns:
            各类型创建数量 {metrics, reports, rows, params, rels}
        """
        counts: dict[str, int] = {}

        # 创建约束
        self._create_constraints()

        # 1. 导入派生指标 (GMetric)
        counts["metrics"] = self._import_metrics(results.derived_metrics)

        # 2. 导入报表 (GReport) + 行数据 (GRow)
        report_counts = self._import_reports(results)
        counts["reports"] = report_counts["reports"]
        counts["rows"] = report_counts["rows"]

        # 3. 导入参数 (GParam)
        counts["params"] = self._import_params(config)

        # 4. 导入关系
        counts["rels"] = self._import_relationships(results, config)

        return counts

    # ── 约束创建 ──────────────────────────────────────────

    def _create_constraints(self) -> None:
        """为通用模型节点创建约束"""
        with self._driver.session() as s:
            for label in ("GMetric", "GReport", "GRow", "GParam"):
                s.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
            s.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:GMetric) ON (n.task_id)"
            )
            s.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:GReport) ON (n.task_id)"
            )
            s.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:GRow) ON (n.task_id)"
            )
            s.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:GRow) ON (n.report_name)"
            )
            s.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:GParam) ON (n.task_id)"
            )

    # ── GMetric 导入 ──────────────────────────────────────

    def _import_metrics(self, dm: DerivedMetrics) -> int:
        """导入派生指标节点"""
        rows = []
        fields = [
            ("irr_total", "全投资IRR", "%", "收益率"),
            ("irr_equity", "资本金IRR", "%", "收益率"),
            ("npv_total", "全投资NPV", "万元", "净现值"),
            ("npv_equity", "资本金NPV", "万元", "净现值"),
            ("dscr_min", "最低DSCR", "", "偿债能力"),
            ("dscr_avg", "平均DSCR", "", "偿债能力"),
            ("payback_static", "静态回收期", "年", "回收期"),
            ("payback_dynamic", "动态回收期", "年", "回收期"),
            ("roe_avg", "平均ROE", "%", "盈利能力"),
            ("project_years", "项目年限", "年", "基础"),
        ]

        for field_name, display, unit, category in fields:
            value = getattr(dm, field_name, None)
            rows.append({
                "id": _nid(self._task_id, "metric", field_name),
                "task_id": self._task_id,
                "key": field_name,
                "display_name": display,
                "value": str(value) if value is not None else None,
                "value_float": float(value) if value is not None else None,
                "unit": unit,
                "category": category,
            })

        total = 0
        with self._driver.session() as s:
            result = s.run(
                "UNWIND $rows AS r "
                "CREATE (n:GMetric {"
                "id: r.id, task_id: r.task_id, key: r.key, "
                "display_name: r.display_name, value: r.value, "
                "value_float: r.value_float, unit: r.unit, category: r.category"
                "})",
                rows=rows,
            )
            total = result.consume().counters.nodes_created
        return total

    # ── GReport + GRow 导入 ───────────────────────────────

    def _import_reports(self, results: AllResults) -> dict[str, int]:
        """导入报表节点 + 行数据节点"""
        report_count = 0
        row_count = 0

        for field_name, display_name, engine in _REPORT_NAMES:
            obj = getattr(results, field_name, None)
            if obj is None:
                continue

            # 创建 GReport 节点
            report_id = _nid(self._task_id, "report", field_name)
            with self._driver.session() as s:
                result = s.run(
                    "CREATE (n:GReport {"
                    "id: $id, task_id: $task_id, name: $name, "
                    "engine: $engine, field: $field"
                    "})",
                    id=report_id,
                    task_id=self._task_id,
                    name=display_name,
                    engine=engine,
                    field=field_name,
                )
                report_count += result.consume().counters.nodes_created

            # 从 DataFrame/PnLResult/CashFlowResult 提取行数据
            rows_data = self._extract_rows(field_name, obj)
            if rows_data:
                row_count += self._import_row_batch(rows_data, field_name)

        return {"reports": report_count, "rows": row_count}

    def _extract_rows(
        self, field_name: str, obj: Any
    ) -> list[dict[str, Any]]:
        """从报表对象提取行数据"""
        rows: list[dict[str, Any]] = []

        # DataFrame 类型的报表
        if isinstance(obj, pd.DataFrame):
            if "year" in obj.columns:
                for _, row in obj.iterrows():
                    rows.append({
                        "report_name": field_name,
                        "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                        "values_json": json.dumps(
                            {k: _safe_json(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                        "category": "annual",
                    })
            else:
                # 无 year 列的汇总表
                rows.append({
                    "report_name": field_name,
                    "year": None,
                    "values_json": json.dumps(
                        {k: _safe_json(v) for k, v in obj.sum().items()},
                        ensure_ascii=False,
                    ),
                    "category": "summary",
                })

        # PnLResult / CashFlowResult — 有 annual 属性
        elif hasattr(obj, "annual") and obj.annual is not None:
            df = obj.annual
            if isinstance(df, pd.DataFrame):
                for _, row in df.iterrows():
                    rows.append({
                        "report_name": field_name,
                        "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                        "values_json": json.dumps(
                            {k: _safe_json(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                        "category": "annual",
                    })

        # DerivedMetrics — 无行数据，跳过
        # FinancingResult — 特殊处理
        elif hasattr(obj, "loan_schedule"):
            schedule = obj.loan_schedule
            if isinstance(schedule, pd.DataFrame):
                for _, row in schedule.iterrows():
                    rows.append({
                        "report_name": field_name,
                        "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                        "values_json": json.dumps(
                            {k: _safe_json(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                        "category": "loan_schedule",
                    })

        # BalanceSheetResult — 有 annual 属性
        elif hasattr(obj, "annual_bs") and obj.annual_bs is not None:
            df = obj.annual_bs
            if isinstance(df, pd.DataFrame):
                for _, row in df.iterrows():
                    rows.append({
                        "report_name": field_name,
                        "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                        "values_json": json.dumps(
                            {k: _safe_json(v) for k, v in row.items()},
                            ensure_ascii=False,
                        ),
                        "category": "annual",
                    })

        return rows

    def _import_row_batch(
        self, rows_data: list[dict[str, Any]], report_name: str
    ) -> int:
        """批量导入行数据节点"""
        total = 0
        batch_size = 500

        batches = [
            rows_data[i : i + batch_size]
            for i in range(0, len(rows_data), batch_size)
        ]

        with self._driver.session() as s:
            for batch in batches:
                cypher_rows = []
                for idx, rd in enumerate(batch):
                    row_id = _nid(
                        self._task_id, "row",
                        f"{report_name}_{rd.get('year', 'sum')}_{idx}",
                    )
                    cypher_rows.append({
                        "id": row_id,
                        "task_id": self._task_id,
                        "report_name": rd["report_name"],
                        "year": rd.get("year"),
                        "values_json": rd["values_json"],
                        "category": rd.get("category", "annual"),
                    })

                result = s.run(
                    "UNWIND $rows AS r "
                    "CREATE (n:GRow {"
                    "id: r.id, task_id: r.task_id, report_name: r.report_name, "
                    "year: r.year, values_json: r.values_json, category: r.category"
                    "})",
                    rows=cypher_rows,
                )
                total += result.consume().counters.nodes_created

        return total

    # ── GParam 导入 ───────────────────────────────────────

    def _import_params(self, config: Any) -> int:
        """导入参数节点"""
        key_params = _extract_key_params(config)
        rows = []
        for p in key_params:
            rows.append({
                "id": _nid(self._task_id, "param", f"{p['group']}_{p['field']}"),
                "task_id": self._task_id,
                "group": p["group"],
                "field": p["field"],
                "value": str(p["value"]),
                "display_name": p["display_name"],
            })

        total = 0
        with self._driver.session() as s:
            result = s.run(
                "UNWIND $rows AS r "
                "CREATE (n:GParam {"
                "id: r.id, task_id: r.task_id, "
                "group: r.group, field: r.field, "
                "value: r.value, display_name: r.display_name"
                "})",
                rows=rows,
            )
            total = result.consume().counters.nodes_created
        return total

    # ── 关系导入 ──────────────────────────────────────────

    def _import_relationships(
        self, results: AllResults, config: Any
    ) -> int:
        """导入关系: HAS_METRIC, HAS_ROW, DRIVES"""
        total = 0

        with self._driver.session() as s:
            # HAS_ROW: GReport → GRow
            for field_name, _, _ in _REPORT_NAMES:
                report_id = _nid(self._task_id, "report", field_name)
                result = s.run(
                    "MATCH (r:GReport {id: $rid}), (row:GRow {task_id: $tid, report_name: $rn}) "
                    "WHERE row.report_name = $rn "
                    "CREATE (r)-[:HAS_ROW]->(row)",
                    rid=report_id, tid=self._task_id, rn=field_name,
                )
                total += result.consume().counters.relationships_created

            # HAS_METRIC: GReport(派生指标) → GMetric
            dm_report_id = _nid(self._task_id, "report", "derived_metrics")
            metric_fields = [
                "irr_total", "irr_equity", "npv_total", "npv_equity",
                "dscr_min", "dscr_avg", "payback_static", "payback_dynamic",
                "roe_avg", "project_years",
            ]
            for mf in metric_fields:
                metric_id = _nid(self._task_id, "metric", mf)
                result = s.run(
                    "MATCH (r:GReport {id: $rid}), (m:GMetric {id: $mid}) "
                    "CREATE (r)-[:HAS_METRIC]->(m)",
                    rid=dm_report_id, mid=metric_id,
                )
                total += result.consume().counters.relationships_created

            # DRIVES: GParam → GReport (所有参数驱动所有报表)
            for p in _extract_key_params(config):
                param_id = _nid(
                    self._task_id, "param", f"{p['group']}_{p['field']}"
                )
                for field_name, _, _ in _REPORT_NAMES:
                    report_id = _nid(self._task_id, "report", field_name)
                    result = s.run(
                        "MATCH (p:GParam {id: $pid}), (r:GReport {id: $rid}) "
                        "CREATE (p)-[:DRIVES]->(r)",
                        pid=param_id, rid=report_id,
                    )
                    total += result.consume().counters.relationships_created

        return total

    # ── 查询方法 ──────────────────────────────────────────

    def query_metric(self, key: str) -> dict[str, Any] | None:
        """查询单个派生指标"""
        metric_id = _nid(self._task_id, "metric", key)
        with self._driver.session() as s:
            result = s.run(
                "MATCH (m:GMetric {id: $id}) RETURN m", id=metric_id,
            )
            record = result.single()
            if record is None:
                return None
            return dict(record["m"])

    def query_report_rows(
        self, report_name: str, year: int | None = None
    ) -> list[dict[str, Any]]:
        """查询报表行数据"""
        with self._driver.session() as s:
            if year is not None:
                result = s.run(
                    "MATCH (r:GRow {task_id: $tid, report_name: $rn, year: $yr}) "
                    "RETURN r ORDER BY r.year",
                    tid=self._task_id, rn=report_name, yr=year,
                )
            else:
                result = s.run(
                    "MATCH (r:GRow {task_id: $tid, report_name: $rn}) "
                    "RETURN r ORDER BY r.year",
                    tid=self._task_id, rn=report_name,
                )
            return [dict(record["r"]) for record in result]

    def clear(self) -> None:
        """清除此 task_id 的所有通用模型节点"""
        with self._driver.session() as s:
            for label in ("GMetric", "GReport", "GRow", "GParam"):
                s.run(
                    f"MATCH (n:{label} {{task_id: $tid}}) DETACH DELETE n",
                    tid=self._task_id,
                )


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════


def _safe_json(v: Any) -> Any:
    """将 pandas/numpy 值转为 JSON 安全类型"""
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v
