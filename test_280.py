"""Trace 287_AA value through the entire recalculation process."""
from financial_kg.storage.json_store import load_graph
from financial_kg.engine.dependency import downstream_cells
from financial_kg.engine.evaluator import evaluate_cell, clear_formula_cache
import networkx as nx

graph = load_graph("output/6b340f08_cells.json")
clear_formula_cache()

cell_287 = "表1-资金筹措及还本付息表_287_AA"
cell_280 = "表1-资金筹措及还本付息表_280_AA"
cell_283 = "表1-资金筹措及还本付息表_283_AA"
cell_284 = "表1-资金筹措及还本付息表_284_AA"
cell_I253 = "参数输入表_253_I"

c287 = graph.cells.get(cell_287)
c280 = graph.cells.get(cell_280)
c283 = graph.cells.get(cell_283)
c284 = graph.cells.get(cell_284)
cI253 = graph.cells.get(cell_I253)

print("=== BASELINE VALUES ===")
print(f"287_AA: {c287.value}  formula: {c287.formula_raw}")
print(f"283_AA: {c283.value}  formula: {c283.formula_raw}")
print(f"284_AA: {c284.value}  formula: {c284.formula_raw}")
print(f"280_AA: {c280.value}  formula: {c280.formula_raw}")
print(f"I253:   {cI253.value}  formula: {cI253.formula_raw}")

# Manual computation: (283 + 284) * I253 * 280 / 12
expected = (c283.value + c284.value) * cI253.value * c280.value / 12
print(f"\nManual: ({c283.value} + {c284.value}) * {cI253.value} * {c280.value} / 12 = {expected}")
print(f"Baseline 287_AA: {c287.value}")
print(f"Match: {abs(expected - c287.value) < 1e-6}")

# Simulate recalc step by step
print("\n=== SIMULATING RECALC ===")

# Step 1: Apply seed
old_33_I = graph.cells.get("参数输入表_33_I").value
graph.cells.get("参数输入表_33_I").value = 1800.0
print(f"Step 1: 参数输入表_33_I = {old_33_I} -> 1800.0")

affected = downstream_cells(graph, ["参数输入表_33_I"])
affected_set = set(affected)
updates = {"参数输入表_33_I"}

# Detect SCCs
subgraph = graph.cell_graph.subgraph(affected_set | updates)
sccs = list(nx.strongly_connected_components(subgraph))
sccs = [sorted(s & affected_set) for s in sccs if len(s & affected_set) > 1]
cyclic_cells = {c for group in sccs for c in group}

print(f"Affected: {len(affected)} cells")
print(f"Cyclic: {len(cyclic_cells)} cells")
print(f"287_AA in affected: {cell_287 in affected_set}")
print(f"287_AA in cyclic: {cell_287 in cyclic_cells}")
print(f"280_AA in affected: {cell_280 in affected_set}")
print(f"280_AA in cyclic: {cell_280 in cyclic_cells}")

# Step 4: Single pass non-cyclic
print("\n--- Step 4: Single pass (non-cyclic affected) ---")
step4_evaluated = 0
for cid in affected:
    cell = graph.cells.get(cid)
    if cell is None or not cell.formula_raw or cid in cyclic_cells:
        continue
    old = cell.value
    new = evaluate_cell(cid, graph)
    if new is not None:
        cell.value = new
        if cid == cell_283:
            print(f"  283_AA: {old} -> {new}")
        if cid == cell_284:
            print(f"  284_AA: {old} -> {new}")
        step4_evaluated += 1

print(f"Step 4: Evaluated {step4_evaluated} non-cyclic cells")

# Current state of 287_AA deps
print(f"\nAfter Step 4:")
print(f"  283_AA: {graph.cells.get(cell_283).value}")
print(f"  284_AA: {graph.cells.get(cell_284).value}")
print(f"  280_AA: {graph.cells.get(cell_280).value} (NOT in affected, not touched)")
print(f"  I253:   {graph.cells.get(cell_I253).value} (NOT in affected, not touched)")

# Now evaluate 287_AA manually
print("\n--- Evaluate 287_AA with current values ---")
val = evaluate_cell(cell_287, graph)
print(f"evaluate_cell(287_AA) = {val}")
manual = (graph.cells.get(cell_283).value + graph.cells.get(cell_284).value) * graph.cells.get(cell_I253).value * graph.cells.get(cell_280).value / 12
print(f"Manual with current values: {manual}")
print(f"Match: {abs(val - manual) < 1e-6}")

# The key question: does evaluate_cell(287_AA) read current graph values or stale ones?
# Let's trace the actual inputs
from financial_kg.engine.evaluator import _compile_formula, _build_input_map, _resolve_input_key

func = _compile_formula(c287.formula_raw)
print(f"\n--- Tracing formula inputs ---")
print(f"Formula: {c287.formula_raw}")
print(f"Func inputs: {dict(func.inputs)}")

for key in func.inputs:
    resolved = _resolve_input_key(key, c287.sheet, graph)
    print(f"  {key!r} -> {resolved[0][0]}")

# So the question is: when the formulas library compiles the function,
# does it cache the input resolution, or resolve at call time?
# The func.inputs are the KEYS, not the values. Values are resolved at call time.
# So evaluate_cell should always read current graph values.

# Then why mismatch?
# Let's check: after all SCC convergence, what is 287_AA's value?
print("\n--- Full SCC convergence simulation ---")
# First, save current 287_AA value
saved_287 = graph.cells.get(cell_287).value

# Do one SCC iteration
max_delta = 0.0
for cid in affected:
    if cid not in cyclic_cells:
        continue
    cell = graph.cells.get(cid)
    if cell is None or not cell.formula_raw:
        continue
    old = cell.value
    new = evaluate_cell(cid, graph)
    if new is not None:
        cell.value = new
        try:
            max_delta = max(max_delta, abs(float(new) - float(old)))
        except (TypeError, ValueError):
            max_delta = 1.0

print(f"After 1 SCC iteration: max_delta = {max_delta}")
print(f"287_AA after 1 iter: {graph.cells.get(cell_287).value}")

# Now check: with current dep values, what SHOULD 287_AA be?
current_manual = (graph.cells.get(cell_283).value + graph.cells.get(cell_284).value) * graph.cells.get(cell_I253).value * graph.cells.get(cell_280).value / 12
print(f"Manual with current deps: {current_manual}")
print(f"Stored vs manual diff: {abs(graph.cells.get(cell_287).value - current_manual):.10f}")
