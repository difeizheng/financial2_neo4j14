"""Full recalculation validation with dirty-tracking fix."""
import time
t0 = time.time()

print("Loading graph...")
from financial_kg.storage.json_store import load_graph
from financial_kg.engine.recalculator import recalculate
from financial_kg.engine.evaluator import evaluate_cell
from financial_kg.engine.dependency import downstream_cells
import networkx as nx

graph = load_graph("output/87097981_cells.json")
print(f"Loaded in {time.time()-t0:.1f}s, Cells: {len(graph.cells)}")

# Apply recalc
updates = {"参数输入表_33_I": 1800.0}
print(f"Applying recalculation with {updates}...")

t1 = time.time()
result = recalculate(graph, updates)
elapsed = time.time() - t1
print(f"Recalc done in {elapsed:.1f}s")
print(f"Changed cells: {result.affected_count}")
print(f"Error cells: {len(result.error_cells)}")
print(f"SCC iterations: {result.scc_iterations}")

# Check N60 and N63
n60 = graph.cells.get("表1-资金筹措及还本付息表_60_N")
n58 = graph.cells.get("表1-资金筹措及还本付息表_58_N")
n59 = graph.cells.get("表1-资金筹措及还本付息表_59_N")
if n60 and n58 and n59:
    n60_diff = abs(n60.value - (n58.value + n59.value))
    status = "OK" if n60_diff < 1e-6 else "MISMATCH"
    print(f"\nN60: {status}")
    print(f"  N60 = {n60.value}")
    print(f"  N58+N59 = {n58.value + n59.value}")
    print(f"  diff = {n60_diff:.10e}")

n63 = graph.cells.get("表1-资金筹措及还本付息表_63_N")
n61 = graph.cells.get("表1-资金筹措及还本付息表_61_N")
m63 = graph.cells.get("表1-资金筹措及还本付息表_63_M")
if n63 and n61 and m63:
    n63_diff = abs(n63.value - (n61.value + m63.value))
    status = "OK" if n63_diff < 1e-6 else "MISMATCH"
    print(f"\nN63: {status}")
    print(f"  N63 = {n63.value}")
    print(f"  N61+M63 = {n61.value + m63.value}")
    print(f"  diff = {n63_diff:.10e}")

# Check all cyclic cells formula consistency
print(f"\n=== Checking all cyclic cells formula consistency ===")
affected = downstream_cells(graph, ["参数输入表_33_I"])
affected_set = set(affected) | set(["参数输入表_33_I"])
subgraph = graph.cell_graph.subgraph(affected_set)
sccs = list(nx.strongly_connected_components(subgraph))
sccs = [s for s in sccs if len(s) > 1]
cyclic_cells = {c for group in sccs for c in group}

mismatch = 0
total_checked = 0
for cid in sorted(cyclic_cells):
    cell = graph.cells.get(cid)
    if not cell or not cell.formula_raw:
        continue
    total_checked += 1
    computed = evaluate_cell(cid, graph)
    if computed is None:
        continue
    stored = cell.value
    if stored is None:
        continue
    try:
        diff = abs(float(stored) - float(computed))
    except (TypeError, ValueError):
        diff = 1.0
    if diff > 1e-6:
        mismatch += 1
        if mismatch <= 5:
            print(f"  {cid}: stored={stored}, computed={computed}, diff={diff:.6f}, formula={cell.formula_raw[:60]}")

print(f"Total cyclic mismatches: {mismatch}/{total_checked}")
