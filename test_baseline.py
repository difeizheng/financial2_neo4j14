"""Check if baseline graph is internally consistent."""
import time
t0 = time.time()

from financial_kg.storage.json_store import load_graph
from financial_kg.engine.evaluator import evaluate_cell, clear_formula_cache
from financial_kg.engine.dependency import downstream_cells
import networkx as nx

graph = load_graph("output/6b340f08_cells.json")
clear_formula_cache()

# Check ALL formula cells
total = 0
mismatch = 0
formula_cells = [cid for cid, c in graph.cells.items() if c.formula_raw]
print(f"Total formula cells: {len(formula_cells)}")

# Sample check
sample_size = 100
checked = 0
for cid in formula_cells[:sample_size]:
    cell = graph.cells.get(cid)
    if not cell:
        continue
    val = evaluate_cell(cid, graph)
    if val is None:
        continue
    diff = abs(float(val) - float(cell.value)) if cell.value is not None else float('inf')
    if diff > 1e-6:
        mismatch += 1
        if mismatch <= 5:
            print(f"  MISMATCH {cid}: baseline={cell.value}, computed={val}, diff={diff:.6f}, formula={cell.formula_raw[:60]}")
    checked += 1

print(f"Checked {checked} formula cells, {mismatch} mismatches")

# Check the 53 mismatch cells at baseline
print(f"\n=== Checking 287_* cells at baseline ===")
mismatch_287 = 0
for cid in sorted(graph.cells.keys()):
    if cid.startswith("表1-资金筹措及还本付息表_287_"):
        cell = graph.cells.get(cid)
        if cell and cell.formula_raw:
            val = evaluate_cell(cid, graph)
            if val is None:
                mismatch_287 += 1
                continue
            diff = abs(float(val) - float(cell.value)) if cell.value is not None else float('inf')
            if diff > 1e-6:
                mismatch_287 += 1
                if mismatch_287 <= 3:
                    print(f"  MISMATCH {cid}: baseline={cell.value}, computed={val}, diff={diff:.6f}")

print(f"287_* mismatches at baseline: {mismatch_287}")
