"""Full parse script (Phase 1 + 2) — run from the project root.

Usage:
    python parse_excel.py <excel_file> [--output-dir <dir>] [--task-id <id>]
"""
from __future__ import annotations
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from financial_kg.parser.excel_reader import read_excel
from financial_kg.parser.cell_extractor import build_cell_graph
from financial_kg.parser.indicator_builder import build_indicators
from financial_kg.parser.relationship_builder import infer_relationships
from financial_kg.storage.json_store import save_graph, verify_cell_count


def main():
    parser = argparse.ArgumentParser(description="Parse Excel financial model to knowledge graph")
    parser.add_argument("excel_file", help="Path to the Excel file")
    parser.add_argument("--output-dir", default="output", help="Directory for JSON output")
    parser.add_argument("--task-id", default="", help="Optional task ID prefix for output files")
    args = parser.parse_args()

    if not os.path.exists(args.excel_file):
        print(f"ERROR: File not found: {args.excel_file}")
        sys.exit(1)

    print(f"Reading Excel: {args.excel_file}")
    t0 = time.time()
    sheet_cells = read_excel(args.excel_file)
    t1 = time.time()

    total_raw = sum(len(v) for v in sheet_cells.values())
    print(f"  Sheets: {len(sheet_cells)}")
    print(f"  Raw cells: {total_raw:,}")
    print(f"  Read time: {t1-t0:.1f}s")

    print("\nBuilding Cell-layer graph...")

    def progress(sheet, done, total):
        pct = done / total * 100
        print(f"  [{pct:5.1f}%] {sheet}", end="\r", flush=True)

    t2 = time.time()
    graph = build_cell_graph(sheet_cells, progress_callback=progress)
    t3 = time.time()
    print()

    stats = graph.stats()
    print(f"\nGraph stats (Phase 1):")
    print(f"  Cell nodes:        {stats['total_cells']:,}")
    print(f"  Formula cells:     {stats['formula_cells']:,}")
    print(f"  Dependency edges:  {stats['dependency_edges']:,}")
    print(f"  Build time:        {t3-t2:.1f}s")

    # ── Phase 2: Indicator + Table layers ────────────────────────────────────
    print("\nBuilding Indicator + Table layers (Phase 2)...")
    t4 = time.time()
    build_indicators(sheet_cells, graph)
    infer_relationships(graph)
    t5 = time.time()

    stats2 = graph.stats()
    print(f"  Indicators:        {stats2['total_indicators']:,}")
    print(f"  Tables:            {stats2['total_tables']:,}")
    unlinked = stats2.get("unlinked_cells", 0)
    print(f"  Unlinked cells:    {unlinked:,}")
    print(f"  Build time:        {t5-t4:.1f}s")

    print(f"\nSaving JSON to: {args.output_dir}/")
    t6 = time.time()
    paths = save_graph(graph, args.output_dir, task_id=args.task_id)
    t7 = time.time()
    for layer, path in paths.items():
        size_kb = os.path.getsize(path) / 1024
        print(f"  {layer:12s}: {path}  ({size_kb:.0f} KB)")
    print(f"  Save time: {t7-t6:.1f}s")

    # Verification
    print(f"\nVerification:")
    check = verify_cell_count(graph, total_raw)
    status = "OK" if check["match"] else f"DIFF={check['diff']:+d}"
    print(f"  Cell count: {check['actual']:,} / {check['expected']:,}  [{status}]")

    # Sample indicators
    print(f"\nSample indicators (first 5 from 表1):")
    for ind in list(graph.indicators.values()):
        if "表1" in ind.sheet:
            ts_count = len(ind.time_series)
            print(f"  [{ind.row:3d}] {ind.name[:35]:35s}  val={str(ind.summary_value)[:12]:12s}  unit={ind.unit or '':6s}  ts={ts_count}")
            if sum(1 for i in graph.indicators.values() if "表1" in i.sheet) >= 5:
                break

    # Table FEEDS_INTO summary
    print(f"\nTable relationships (FEEDS_INTO):")
    for tbl in graph.tables.values():
        if tbl.feeds_into:
            targets = ", ".join(t.split("_", 2)[-1] for t in tbl.feeds_into[:3])
            print(f"  {tbl.name[:30]:30s} -> {targets}")

    print("\nDone.")


if __name__ == "__main__":
    main()
