# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Financial model knowledge graph system for pumped-storage hydropower Excel models. Parses Excel files into a 3-layer knowledge graph (Cell → Indicator → Table), supports formula dependency tracking, full-model recalculation, snapshot diffing, Neo4j export, and LLM-powered Q&A.

Excel source: ~58K cells, 49 tables, 2968 indicators, 73% formula cells, 14 sheets.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Streamlit UI
streamlit run main.py

# Parse an Excel file (standalone)
python parse_excel.py

# Run a single test
pytest test_recalc.py -v

# Run all tests
pytest --cov=financial_kg --cov-report=term-missing
```

## Environment

- **`.env`**: LLM API key (OpenAI-compatible) and Neo4j credentials. Copy `.env.example` as template.
- **Streamlit config** (`.streamlit/config.toml`): 500MB upload limit, XSRF disabled, no usage stats.
- **`workspaces/`**: JSON scenario files for parameter editing and recalculation.

## Code Architecture

```
financial_kg/
├── models/              # Data classes
│   ├── cell.py          # Cell — individual Excel cells (Layer 1)
│   ├── indicator.py     # Indicator — business line items (Layer 2)
│   ├── table.py         # Table — logical tables (Layer 3)
│   └── graph.py         # FinancialGraph — DiGraph + dicts container
├── parser/              # Excel → knowledge graph extraction
│   ├── excel_reader.py  # Dual-mode openpyxl (formulas + computed values)
│   ├── formula_parser.py# Tokenizer for Excel formula tokens
│   ├── reference_resolver.py # Normalize all Excel ref types, range expansion
│   ├── cell_extractor.py     # Build Cell-layer NetworkX DiGraph
│   ├── table_detector.py     # Rule-based table header detection
│   ├── indicator_builder.py  # Build Indicators row-by-row
│   ├── relationship_builder.py # Derive CALCULATES_FROM / FEEDS_INTO
│   └── format_utils.py       # Cell format detection and value formatting
├── engine/              # Recalculation and workspace
│   ├── dependency.py    # Kahn topological sort + BFS downstream discovery
│   ├── evaluator.py     # `formulas` library bridge (cell_id ↔ Excel refs)
│   ├── recalculator.py  # Incremental recalc with Indicator sync
│   ├── snapshot.py      # Create / save / load / diff snapshots
│   ├── workspace.py     # Parameter workspace (scenario management)
│   ├── excel_export.py  # Export recalculated results back to Excel
│   ├── derived_metrics.py # Pre-computed IRR, NPV, DSCR, payback period
│   ├── sensitivity.py   # Parameter perturbation → recalc → sensitivity table
│   └── report_export.py # Word report export (财务效益分析报告)
├── storage/             # Persistence
│   ├── json_store.py    # 3-layer JSON serialization
│   ├── task_db.py       # SQLite task/snapshot/QA history registry
│   └── neo4j_store.py   # Neo4j import (UNWIND batch), read-only Cypher
├── llm/                 # LLM-powered Q&A
│   ├── retriever.py     # Fuzzy match + category/year filter + graph context
│   ├── prompt_builder.py# Structured system prompt + Cypher prompt
│   ├── cypher_gen.py    # Graph-traversal question detection + Cypher generation
│   └── qa_engine.py     # QAEngine — 3 modes: Neo4j+LLM / memory+LLM / retrieval-only
├── qa/                  # Q&A quality and timing
│   ├── stage_timer.py   # Per-stage timing measurement for QA pipeline
│   └── quality_score.py # QA response quality scoring
├── viz/                 # Visualization
│   ├── graph_viz.py     # pyvis interactive graph
│   ├── echarts_graph.py # ECharts renderer (force/circular/radial/tree/mindmap)
│   ├── echarts_template.py # ECharts HTML template
│   ├── propagation_graph.py # Snapshot diff propagation visualization
│   ├── qa_chart.py      # ECharts time-series charts for Q&A
│   └── compare_viz.py   # Comparison visualization
└── config.py            # .env-based config (LLM, Neo4j)

pages/                  # Streamlit pages
├── 01_upload.py        # Upload + parse + Neo4j import
├── 02_explorer.py      # Hierarchical drill-down: Sheet → Table → Indicator → Cell
├── 03_recalc.py        # Parameter workspace + recalculation
├── 04_compare.py       # Snapshot comparison with heatmap
└── 05_qa.py            # Structured Q&A dashboard (not chat)

main.py                 # Entry point (dashboard landing)
parse_excel.py           # Standalone Excel parser entry point
excel_table_extractor.py # Standalone table extraction utility
tasks.db                # SQLite task/snapshot registry
.env                    # LLM and Neo4j credentials

**Test files:** `test_baseline.py`, `test_280.py`, `test_recalc.py` — regression and recalculation tests.
```

## Key Design Decisions

**Cell ID format:** `{sheet}_{row}_{col}` (e.g. `表1_250_I`). Sheet names may contain underscores, so use `rsplit("_", 2)` to parse.

**Edge direction:** `cell_graph.add_edge(from_id, to_id)` means "from_id DEPENDS_ON to_id" (from_id's formula references to_id). So to find cells affected by changing B, look at PREDECESSORS of B, not successors.

**Formula evaluation:** The `formulas` library expects Excel reference strings (`F5`, `$I$250`, `参数输入表!I250`), not cell IDs. The evaluator bridges the two representations via `_cell_id_to_ref()` and `_resolve_input_key()`.

**Snapshot diff tolerance:** Uses both relative (1e-9) and absolute (1e-9) floating-point tolerance. Values like `0` vs `-3.64e-11` are considered equal.

**Neo4j multi-task:** Node IDs prefixed with `{task_id}_` to isolate tasks. Community Edition compatible.

## Streamlit UI Pages

| Page | Purpose |
|------|---------|
| 01_upload | Upload Excel, parse to 3-layer graph, Neo4j import |
| 02_explorer | Hierarchical drill-down (Sheet → Table → Indicator → Cell), ECharts 5 layouts |
| 03_recalc | Parameter workspace: scenario management, batch edit, recalculation |
| 04_compare | Snapshot diff: heatmap, propagation chain, dual Excel export |
| 05_qa | Structured Q&A dashboard with confidence score, charts, data tables |

## UI Layout Preferences

- Two-column layout: left (editing 60%) / right (results 40%)
- Scenario selection via button row (NOT `st.tabs()`)
- Unidirectional flow: select scenario → edit parameters → recalc → refresh results
- Parameters grouped by `indicator.category` tabs
- Q&A: structured dashboard display (not chat interface)

## Development Conventions

- **Formatting:** black + isort + ruff
- **Testing:** pytest, 80% minimum coverage
- **Commits:** conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`)
- **Type annotations:** required on all function signatures
- **Immutability:** prefer frozen dataclasses, NamedTuples
- **Security:** no hardcoded secrets — use `.env` + `os.environ`

## Common Tasks

```bash
# Format code
ruff format .
ruff check --fix .

# Run all tests
pytest --cov=financial_kg --cov-report=term-missing

# Run a single test file
pytest test_recalc.py -v

# Type check
mypy financial_kg/
```

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **financial2_neo4j5_claude** (2436 symbols, 3695 relationships, 92 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/financial2_neo4j5_claude/context` | Codebase overview, check index freshness |
| `gitnexus://repo/financial2_neo4j5_claude/clusters` | All functional areas |
| `gitnexus://repo/financial2_neo4j5_claude/processes` | All execution flows |
| `gitnexus://repo/financial2_neo4j5_claude/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
