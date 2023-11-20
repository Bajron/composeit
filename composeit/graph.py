from typing import Dict, List


def topological_sequence(nodes: List[str], neighbors: Dict[str, str]):
    sequence = []
    visited = set()
    for n in nodes:
        if n in visited:
            continue
        sequence += topological_sort(n, neighbors, visited, set())
    return sequence


def topological_sort(node: str, neighbors: Dict[str, str], visited: set, resolving: set):
    if node in resolving:
        raise Exception(f"Cyclic dependency ({node})")
    resolving.add(node)

    seq = []
    for d in neighbors[node]:
        if d in visited:
            continue
        seq += topological_sort(d, neighbors, visited, resolving)

    resolving.remove(node)

    visited.add(node)
    return seq + [node]
