import json
import sys
import networkx as nx
from pathlib import Path
from graphify.build import build_from_json
from graphify.cluster import cluster, cohesion_score, remap_communities_to_previous, score_all

FIXTURES = Path(__file__).parent / "fixtures"

def make_graph():
    return build_from_json(json.loads((FIXTURES / "extraction.json").read_text()))

def test_cluster_returns_dict():
    G = make_graph()
    communities = cluster(G)
    assert isinstance(communities, dict)

def test_cluster_covers_all_nodes():
    G = make_graph()
    communities = cluster(G)
    all_nodes = {n for nodes in communities.values() for n in nodes}
    assert all_nodes == set(G.nodes)

def test_cohesion_score_complete_graph():
    G = nx.complete_graph(4)
    G = nx.relabel_nodes(G, {i: str(i) for i in G.nodes})
    score = cohesion_score(G, list(G.nodes))
    assert score == 1.0

def test_cohesion_score_single_node():
    G = nx.Graph()
    G.add_node("a")
    score = cohesion_score(G, ["a"])
    assert score == 1.0

def test_cohesion_score_disconnected():
    G = nx.Graph()
    G.add_nodes_from(["a", "b", "c"])
    score = cohesion_score(G, ["a", "b", "c"])
    assert score == 0.0

def test_cohesion_score_range():
    G = make_graph()
    communities = cluster(G)
    for cid, nodes in communities.items():
        score = cohesion_score(G, nodes)
        assert 0.0 <= score <= 1.0

def test_score_all_keys_match_communities():
    G = make_graph()
    communities = cluster(G)
    scores = score_all(G, communities)
    assert set(scores.keys()) == set(communities.keys())


def test_cluster_does_not_write_to_stdout(capsys):
    """Clustering should not emit ANSI escape codes or other output.

    graspologic's leiden() can emit ANSI escape sequences that break
    PowerShell 5.1's scroll buffer on Windows (issue #19). The output
    suppression in _partition() should prevent any output from leaking.
    """
    G = make_graph()
    cluster(G)
    captured = capsys.readouterr()
    assert captured.out == "", f"cluster() wrote to stdout: {captured.out!r}"


def test_cluster_does_not_write_to_stderr(capsys):
    """Same as above but for stderr — ANSI codes can go to either stream."""
    G = make_graph()
    cluster(G)
    captured = capsys.readouterr()
    # Allow logging output (starts with [graphify]) but no raw ANSI codes
    for line in captured.err.splitlines():
        assert "\x1b" not in line, f"cluster() wrote ANSI to stderr: {line!r}"


def test_remap_communities_to_previous_reuses_old_ids():
    communities = {
        10: ["a", "b", "c"],
        11: ["d", "e"],
    }
    previous = {"a": 5, "b": 5, "c": 5, "d": 1, "e": 1}
    remapped = remap_communities_to_previous(communities, previous)
    assert set(remapped.keys()) == {1, 5}
    assert remapped[5] == ["a", "b", "c"]
    assert remapped[1] == ["d", "e"]


def test_remap_communities_to_previous_assigns_deterministic_new_ids():
    communities = {
        7: ["x", "y", "z"],
        8: ["m"],
    }
    previous = {"a": 3}
    remapped = remap_communities_to_previous(communities, previous)
    assert list(remapped.keys()) == [0, 1]
    assert remapped[0] == ["x", "y", "z"]
    assert remapped[1] == ["m"]


def test_split_community_forwards_resolution_to_partition(monkeypatch):
    from graphify import cluster as cluster_mod

    seen: list[float] = []

    def fake_partition(G, resolution=1.0):
        seen.append(resolution)
        return {n: 0 for n in G.nodes}

    monkeypatch.setattr(cluster_mod, "_partition", fake_partition)
    G = nx.complete_graph(6)
    G = nx.relabel_nodes(G, {i: str(i) for i in G.nodes})
    cluster_mod._split_community(G, list(G.nodes), resolution=3.0)
    assert seen == [3.0]


def test_cluster_forwards_resolution_to_split_passes(monkeypatch):
    from graphify import cluster as cluster_mod

    seen: list[float] = []

    def fake_split(G, nodes, resolution=1.0):
        seen.append(resolution)
        return [sorted(nodes)]

    monkeypatch.setattr(cluster_mod, "_split_community", fake_split)

    # Two 20-node cliques joined by a single edge: 40 nodes total, so max_size is
    # max(10, 40 * 0.25) = 10 and each clique trips the oversized-split path.
    G = nx.Graph()
    for offset in (0, 20):
        clique = [f"n{offset + i}" for i in range(20)]
        G.add_edges_from((a, b) for i, a in enumerate(clique) for b in clique[i + 1:])
    G.add_edge("n0", "n20")

    cluster_mod.cluster(G, resolution=0.5)
    assert seen, "expected the oversized-community split path to run"
    assert set(seen) == {0.5}
