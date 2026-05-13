"""Dependency DAG utilities: topological sort and downstream BFS."""
from __future__ import annotations
from collections import deque
from typing import Iterable

import networkx as nx

from financial_kg.models.graph import FinancialGraph


def topological_order(graph: FinancialGraph) -> list[str]:
    """Return all cell IDs in topological (evaluation) order via Kahn's algorithm.

    Raises ValueError if a cycle is detected.
    """
    g = graph.cell_graph
    in_degree: dict[str, int] = {n: g.in_degree(n) for n in g.nodes}
    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for successor in g.successors(node):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(order) != g.number_of_nodes():
        cycle_nodes = set(g.nodes) - set(order)
        raise ValueError(f"Cycle detected among {len(cycle_nodes)} cells: {list(cycle_nodes)[:5]}")

    return order


def downstream_cells(graph: FinancialGraph, changed_ids: Iterable[str]) -> list[str]:
    """BFS from changed_ids; return all downstream (dependent) cell IDs in topological order.

    The returned list excludes the seed cells themselves and is sorted so that
    each cell appears after all its predecessors.

    Note: Edge direction is A → B meaning "A depends on B".
    So to find cells that depend on B (i.e., will be affected when B changes),
    we must look at PREDECESSORS of B, not successors.

    When cycles exist, the fallback order groups strongly connected components
    together so the caller can use iterative evaluation.
    """
    g = graph.cell_graph
    seeds = set(changed_ids)
    visited: set[str] = set()
    queue: deque[str] = deque(seeds)

    while queue:
        node = queue.popleft()
        # Find cells that depend on this node (dependents = predecessors in our edge direction)
        for pred in g.predecessors(node):
            if pred not in visited and pred not in seeds:
                visited.add(pred)
                queue.append(pred)

    if not visited:
        return []

    # Return in topological order (only the affected subgraph)
    subgraph = g.subgraph(visited | seeds)
    try:
        full_order = list(nx.topological_sort(subgraph))
    except nx.NetworkXUnfeasible:
        # Cycle detected — use SCC-based ordering for iterative evaluation
        full_order = _scc_topological_order(subgraph, visited)

    return [n for n in full_order if n in visited]


def _scc_topological_order(subgraph: nx.DiGraph, visited: set[str]) -> list[str]:
    """Return evaluation order that handles cycles via SCC grouping.

    Cells within the same SCC are grouped together (caller should iterate
    until convergence). The overall order respects dependencies between SCCs.
    """
    sccs = list(nx.strongly_connected_components(subgraph))

    # Build node -> SCC index mapping
    node_to_scc: dict[str, int] = {}
    for i, scc in enumerate(sccs):
        for node in scc:
            node_to_scc[node] = i

    # Build meta-DAG
    meta_graph = nx.DiGraph()
    for i in range(len(sccs)):
        meta_graph.add_node(i)

    for node in subgraph.nodes:
        src_idx = node_to_scc.get(node)
        if src_idx is None:
            continue
        for successor in subgraph.successors(node):
            dst_idx = node_to_scc.get(successor)
            if dst_idx is not None and src_idx != dst_idx:
                # node depends on successor → successor's SCC should come first
                meta_graph.add_edge(dst_idx, src_idx)

    # Topological sort of meta-DAG, flatten
    try:
        meta_order = list(nx.topological_sort(meta_graph))
    except nx.NetworkXUnfeasible:
        meta_order = list(range(len(sccs)))

    result = []
    for idx in meta_order:
        scc = sccs[idx]
        scc_in_visited = scc & visited
        if not scc_in_visited:
            continue
        if len(scc_in_visited) == 1:
            result.append(next(iter(scc_in_visited)))
        else:
            # Cyclic group: add all together
            result.extend(sorted(scc_in_visited))

    return result


def build_subgraph_order(graph: FinancialGraph, cell_ids: Iterable[str]) -> list[str]:
    """Topological order for an explicit set of cell IDs (used by recalculator)."""
    nodes = set(cell_ids)
    subgraph = graph.cell_graph.subgraph(nodes)
    try:
        return list(nx.topological_sort(subgraph))
    except nx.NetworkXUnfeasible:
        return list(nodes)
