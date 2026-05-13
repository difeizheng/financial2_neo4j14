from __future__ import annotations
import json
from typing import Any, Optional

from ..models.graph import FinancialGraph

try:
    from neo4j import GraphDatabase, Driver
    _NEO4J_AVAILABLE = True
except ImportError:
    _NEO4J_AVAILABLE = False


class Neo4jStore:
    """Manages Neo4j 5 connection and graph import/query operations."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        if not _NEO4J_AVAILABLE:
            raise ImportError("neo4j package not installed. Run: pip install neo4j")
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── Schema setup ─────────────────────────────────────────────────────────

    def create_constraints(self) -> None:
        """Create constraints compatible with Neo4j Community Edition."""
        with self._driver.session() as s:
            # Community Edition: only UNIQUE constraint (not NODE KEY)
            for label in ("Cell", "Indicator", "Table"):
                s.run(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
            # Indexes for task_id filtering
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Cell) ON (n.task_id)")
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Indicator) ON (n.task_id)")
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Table) ON (n.task_id)")
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Cell) ON (n.sheet)")
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Indicator) ON (n.name)")
            s.run("CREATE INDEX IF NOT EXISTS FOR (n:Indicator) ON (n.category)")

    def clear_database(self) -> None:
        with self._driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    def clear_task(self, task_id: str) -> None:
        """Clear all nodes for a specific task."""
        with self._driver.session() as s:
            s.run("MATCH (n) WHERE n.task_id = $task_id DETACH DELETE n", task_id=task_id)

    def get_task_ids(self) -> list[str]:
        """List all task_ids in the database."""
        with self._driver.session() as s:
            result = s.run("MATCH (n) RETURN DISTINCT n.task_id AS task_id")
            return [r["task_id"] for r in result if r["task_id"]]

    def get_task_stats(self, task_id: str) -> dict[str, int]:
        """Get node/relationship counts for a task."""
        with self._driver.session() as s:
            cells = s.run(
                "MATCH (n:Cell {task_id: $task_id}) RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            indicators = s.run(
                "MATCH (n:Indicator {task_id: $task_id}) RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            tables = s.run(
                "MATCH (n:Table {task_id: $task_id}) RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            deps = s.run(
                "MATCH (a:Cell {task_id: $task_id})-[r:DEPENDS_ON]->(b:Cell {task_id: $task_id}) "
                "RETURN count(r) AS c", task_id=task_id
            ).single()["c"]
        return {"cells": cells, "indicators": indicators, "tables": tables, "depends_on": deps}

    # ── Import ────────────────────────────────────────────────────────────────

    def import_graph(
        self,
        graph: FinancialGraph,
        task_id: str,
        batch_size: int = 500,
        progress_callback=None,
    ) -> dict[str, int]:
        """Import the full 3-layer graph with task_id. Returns counts of created nodes/rels."""
        self.create_constraints()
        counts: dict[str, int] = {}

        def _progress(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        _progress("导入 Cell 节点...")
        counts["cells"] = self._import_cells(list(graph.cells.values()), task_id, batch_size)

        _progress("导入 Indicator 节点...")
        counts["indicators"] = self._import_indicators(
            list(graph.indicators.values()), task_id, batch_size
        )

        _progress("导入 Table 节点...")
        counts["tables"] = self._import_tables(list(graph.tables.values()), task_id, batch_size)

        _progress("导入 DEPENDS_ON 关系...")
        counts["depends_on"] = self._import_cell_dependencies(graph, task_id, batch_size * 2)

        _progress("导入 CALCULATES_FROM 关系...")
        counts["calculates_from"] = self._import_indicator_relationships(
            list(graph.indicators.values()), task_id, batch_size
        )

        _progress("导入 FEEDS_INTO 关系...")
        counts["feeds_into"] = self._import_table_relationships(
            list(graph.tables.values()), task_id, batch_size
        )

        _progress("导入 BELONGS_TO 关系...")
        counts["belongs_to"] = self._import_belongs_to(graph, task_id, batch_size)

        return counts

    def _import_cells(self, cells: list, task_id: str, batch_size: int) -> int:
        batches = [cells[i : i + batch_size] for i in range(0, len(cells), batch_size)]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "id": f"{task_id}_{c.id}",  # Prefix task_id to avoid collision
                        "task_id": task_id,
                        "sheet": c.sheet,
                        "row": c.row,
                        "col": c.col,
                        "value": str(c.value) if c.value is not None else None,
                        "formula_raw": c.formula_raw,
                        "data_type": c.data_type,
                        "is_header": c.is_header,
                        "indicator_id": c.indicator_id,
                        "table_id": c.table_id,
                        "time_period": c.time_period,
                        "orig_id": c.id,  # Store original ID for reference
                    }
                    for c in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "CREATE (n:Cell {id: r.id, task_id: r.task_id, sheet: r.sheet, row: r.row, col: r.col, "
                    "value: r.value, formula_raw: r.formula_raw, data_type: r.data_type, "
                    "is_header: r.is_header, indicator_id: r.indicator_id, table_id: r.table_id, "
                    "time_period: r.time_period, orig_id: r.orig_id})",
                    rows=rows,
                )
                total += result.consume().counters.nodes_created
        return total

    def _import_indicators(self, indicators: list, task_id: str, batch_size: int) -> int:
        batches = [
            indicators[i : i + batch_size] for i in range(0, len(indicators), batch_size)
        ]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "id": f"{task_id}_{ind.id}",  # Prefix task_id
                        "task_id": task_id,
                        "name": ind.name,
                        "sheet": ind.sheet,
                        "row": ind.row,
                        "category": ind.category,
                        "subcategory": ind.subcategory,
                        "unit": ind.unit,
                        "summary_value": str(ind.summary_value)
                        if ind.summary_value is not None
                        else None,
                        "formula_readable": ind.formula_readable,
                        "time_series_json": json.dumps(
                            ind.time_series, ensure_ascii=False
                        ),
                        "table_id": ind.table_id,
                        "orig_id": ind.id,
                    }
                    for ind in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "CREATE (n:Indicator {id: r.id, task_id: r.task_id, name: r.name, sheet: r.sheet, row: r.row, "
                    "category: r.category, subcategory: r.subcategory, unit: r.unit, "
                    "summary_value: r.summary_value, formula_readable: r.formula_readable, "
                    "time_series_json: r.time_series_json, table_id: r.table_id, orig_id: r.orig_id})",
                    rows=rows,
                )
                total += result.consume().counters.nodes_created
        return total

    def _import_tables(self, tables: list, task_id: str, batch_size: int) -> int:
        batches = [tables[i : i + batch_size] for i in range(0, len(tables), batch_size)]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "id": f"{task_id}_{t.id}",  # Prefix task_id
                        "task_id": task_id,
                        "name": t.name,
                        "sheet": t.sheet,
                        "table_type": t.table_type,
                        "description": t.description,
                        "orig_id": t.id,
                    }
                    for t in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "CREATE (n:Table {id: r.id, task_id: r.task_id, name: r.name, sheet: r.sheet, "
                    "table_type: r.table_type, description: r.description, orig_id: r.orig_id})",
                    rows=rows,
                )
                total += result.consume().counters.nodes_created
        return total

    def _import_cell_dependencies(self, graph: FinancialGraph, task_id: str, batch_size: int) -> int:
        edges = list(graph.cell_graph.edges())
        batches = [edges[i : i + batch_size] for i in range(0, len(edges), batch_size)]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "task_id": task_id,
                        "from_id": f"{task_id}_{u}",  # Prefix task_id
                        "to_id": f"{task_id}_{v}",
                    }
                    for u, v in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:Cell {id: r.from_id}), (b:Cell {id: r.to_id}) "
                    "CREATE (a)-[:DEPENDS_ON]->(b)",
                    rows=rows,
                )
                total += result.consume().counters.relationships_created
        return total

    def _import_indicator_relationships(self, indicators: list, task_id: str, batch_size: int) -> int:
        pairs = [
            (ind.id, dep_id)
            for ind in indicators
            for dep_id in ind.depends_on_indicators
        ]
        batches = [pairs[i : i + batch_size] for i in range(0, len(pairs), batch_size)]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "task_id": task_id,
                        "from_id": f"{task_id}_{a}",  # Prefix task_id
                        "to_id": f"{task_id}_{b}",
                    }
                    for a, b in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:Indicator {id: r.from_id}), (b:Indicator {id: r.to_id}) "
                    "CREATE (a)-[:CALCULATES_FROM]->(b)",
                    rows=rows,
                )
                total += result.consume().counters.relationships_created
        return total

    def _import_table_relationships(self, tables: list, task_id: str, batch_size: int) -> int:
        pairs = [
            (tbl.id, target_id)
            for tbl in tables
            for target_id in tbl.feeds_into
        ]
        batches = [pairs[i : i + batch_size] for i in range(0, len(pairs), batch_size)]
        total = 0
        with self._driver.session() as s:
            for batch in batches:
                rows = [
                    {
                        "task_id": task_id,
                        "from_id": f"{task_id}_{a}",  # Prefix task_id
                        "to_id": f"{task_id}_{b}",
                    }
                    for a, b in batch
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:Table {id: r.from_id}), (b:Table {id: r.to_id}) "
                    "CREATE (a)-[:FEEDS_INTO]->(b)",
                    rows=rows,
                )
                total += result.consume().counters.relationships_created
        return total

    def _import_belongs_to(self, graph: FinancialGraph, task_id: str, batch_size: int) -> int:
        cell_ind_pairs = [
            (c.id, c.indicator_id)
            for c in graph.cells.values()
            if c.indicator_id
        ]
        ind_tbl_pairs = [
            (ind.id, ind.table_id)
            for ind in graph.indicators.values()
            if ind.table_id
        ]
        total = 0
        with self._driver.session() as s:
            for i in range(0, len(cell_ind_pairs), batch_size):
                rows = [
                    {
                        "task_id": task_id,
                        "from_id": f"{task_id}_{a}",  # Prefix task_id
                        "to_id": f"{task_id}_{b}",
                    }
                    for a, b in cell_ind_pairs[i : i + batch_size]
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:Cell {id: r.from_id}), (b:Indicator {id: r.to_id}) "
                    "CREATE (a)-[:BELONGS_TO]->(b)",
                    rows=rows,
                )
                total += result.consume().counters.relationships_created
            for i in range(0, len(ind_tbl_pairs), batch_size):
                rows = [
                    {
                        "task_id": task_id,
                        "from_id": f"{task_id}_{a}",  # Prefix task_id
                        "to_id": f"{task_id}_{b}",
                    }
                    for a, b in ind_tbl_pairs[i : i + batch_size]
                ]
                result = s.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:Indicator {id: r.from_id}), (b:Table {id: r.to_id}) "
                    "CREATE (a)-[:BELONGS_TO]->(b)",
                    rows=rows,
                )
                total += result.consume().counters.relationships_created
        return total

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_indicator_by_name(self, task_id: str, name: str, fuzzy: bool = True) -> list[dict]:
        with self._driver.session() as s:
            if fuzzy:
                result = s.run(
                    "MATCH (n:Indicator) WHERE n.task_id = $task_id AND n.name CONTAINS $name RETURN n",
                    task_id=task_id, name=name,
                )
            else:
                result = s.run(
                    "MATCH (n:Indicator) WHERE n.task_id = $task_id AND n.name = $name RETURN n",
                    task_id=task_id, name=name,
                )
            return [dict(r["n"]) for r in result]

    def get_indicators_by_category(self, task_id: str, category: str) -> list[dict]:
        with self._driver.session() as s:
            result = s.run(
                "MATCH (n:Indicator) WHERE n.task_id = $task_id AND n.category CONTAINS $cat RETURN n",
                task_id=task_id, cat=category,
            )
            return [dict(r["n"]) for r in result]

    def get_upstream_indicators(self, task_id: str, indicator_id: str, depth: int = 2) -> list[dict]:
        prefixed_id = f"{task_id}_{indicator_id}"
        with self._driver.session() as s:
            result = s.run(
                f"MATCH (n:Indicator {{id: $id}})-[:CALCULATES_FROM*1..{depth}]->(m:Indicator) "
                "WHERE m.task_id = $task_id RETURN DISTINCT m",
                id=prefixed_id, task_id=task_id,
            )
            return [dict(r["m"]) for r in result]

    def get_downstream_indicators(self, task_id: str, indicator_id: str, depth: int = 2) -> list[dict]:
        prefixed_id = f"{task_id}_{indicator_id}"
        with self._driver.session() as s:
            result = s.run(
                f"MATCH (m:Indicator)-[:CALCULATES_FROM*1..{depth}]->(n:Indicator {{id: $id}}) "
                "WHERE m.task_id = $task_id RETURN DISTINCT m",
                id=prefixed_id, task_id=task_id,
            )
            return [dict(r["m"]) for r in result]

    def get_table_indicators(self, task_id: str, table_id: str) -> list[dict]:
        prefixed_table_id = f"{task_id}_{table_id}"
        with self._driver.session() as s:
            result = s.run(
                "MATCH (i:Indicator)-[:BELONGS_TO]->(t:Table {id: $id}) "
                "WHERE i.task_id = $task_id RETURN i",
                id=prefixed_table_id, task_id=task_id,
            )
            return [dict(r["i"]) for r in result]

    def path_between_indicators(self, task_id: str, from_id: str, to_id: str) -> list[dict]:
        prefixed_from = f"{task_id}_{from_id}"
        prefixed_to = f"{task_id}_{to_id}"
        with self._driver.session() as s:
            result = s.run(
                "MATCH p = shortestPath((a:Indicator {id: $from_id})-[:CALCULATES_FROM*]->(b:Indicator {id: $to_id})) "
                "WHERE a.task_id = $task_id AND b.task_id = $task_id "
                "RETURN [n IN nodes(p) | n.name] AS path",
                from_id=prefixed_from, to_id=prefixed_to, task_id=task_id,
            )
            rows = list(result)
            return rows[0]["path"] if rows else []

    def get_graph_schema(self, task_id: str) -> str:
        with self._driver.session() as s:
            cell_count = s.run(
                "MATCH (n:Cell) WHERE n.task_id = $task_id RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            ind_count = s.run(
                "MATCH (n:Indicator) WHERE n.task_id = $task_id RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            tbl_count = s.run(
                "MATCH (n:Table) WHERE n.task_id = $task_id RETURN count(n) AS c", task_id=task_id
            ).single()["c"]
            dep_count = s.run(
                "MATCH (a:Cell)-[r:DEPENDS_ON]->(b:Cell) "
                "WHERE a.task_id = $task_id RETURN count(r) AS c", task_id=task_id
            ).single()["c"]
        return (
            f"节点：Cell({cell_count})、Indicator({ind_count})、Table({tbl_count})\n"
            f"关系：DEPENDS_ON({dep_count})、CALCULATES_FROM(指标间)、"
            f"FEEDS_INTO(报表间)、BELONGS_TO(Cell→Indicator, Indicator→Table)\n"
            "Cell属性：id, task_id, sheet, row, col, value, formula_raw, data_type\n"
            "Indicator属性：id, task_id, name, sheet, category, unit, summary_value, time_series_json\n"
            "Table属性：id, task_id, name, sheet, table_type"
        )

    def run_cypher(self, query: str) -> list[dict]:
        """Execute a read-only Cypher query and return results as list of dicts."""
        with self._driver.session() as session:
            result = session.run(query)
            return [dict(record) for record in result]
