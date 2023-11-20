from composeit.graph import topological_sequence

import pytest


def test_topological_sequence():
    graph = {"a": ["b"], "b": ["c"], "c": []}
    seq = topological_sequence(["a", "b", "c"], graph)
    assert seq == ["c", "b", "a"]


def test_topological_sequence_for_single():
    graph = {"a": ["b"], "b": ["c"], "c": []}
    seq = topological_sequence(["a"], graph)
    assert seq == ["c", "b", "a"]


def test_topological_sequence2():
    graph = {"a": ["b"], "b": ["c"], "c": [], "d": ["e"], "e": ["f"], "f": []}
    seq = topological_sequence(["a", "b", "c", "d", "e", "f"], graph)
    assert seq == ["c", "b", "a", "f", "e", "d"]


def test_topological_sequence3():
    graph = {"a": ["b"], "b": ["c"], "c": [], "d": ["e"], "e": ["c"]}
    seq = topological_sequence(["a", "b", "c", "d", "e"], graph)
    assert seq == ["c", "b", "a", "e", "d"]


def test_topological_cyclic():
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    with pytest.raises(Exception):
        topological_sequence(["a", "b", "c"], graph)
