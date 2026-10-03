"""Test del campionamento stratificato e della deduplicazione (ADR-004)."""

from __future__ import annotations

import pytest

from bench.data.negatives import (
    StratifiedSampler,
    code_fingerprint,
    deduplicate,
    largest_remainder,
)
from bench.data.sources import Candidate
from bench.domain.errors import DataError


def _cands(locs: list[int], prefix: str = "c") -> list[Candidate]:
    return [
        Candidate(f"{prefix}{i:04d}", f"code {prefix} {i}", loc, f"repo{i % 7}")
        for i, loc in enumerate(locs)
    ]


def test_largest_remainder() -> None:
    assert largest_remainder([1, 1, 1], 10) == [4, 3, 3]
    assert largest_remainder([2, 1], 3) == [2, 1]
    assert largest_remainder([0, 0], 5) == [0, 0]
    assert sum(largest_remainder([3, 5, 7, 11], 1976)) == 1976


def test_proportions_follow_the_reference() -> None:
    reference = [1] * 50 + [10] * 50
    candidates = _cands([1] * 300 + [10] * 300 + [50] * 300)
    result = StratifiedSampler(10).sample(reference, candidates, 100, seed=7)
    locs = [c.loc for c in result.selected]
    assert len(locs) == 100
    assert locs.count(1) == 50 and locs.count(10) == 50
    assert result.moved == 0
    assert result.details["out_of_range"] == 300  # le funzioni da 50 righe sono fuori campo


def test_deterministic_and_order_independent() -> None:
    reference = list(range(1, 31))
    candidates = _cands([i % 40 + 1 for i in range(500)])
    a = StratifiedSampler().sample(reference, candidates, 60, seed=3)
    b = StratifiedSampler().sample(reference, list(reversed(candidates)), 60, seed=3)
    c = StratifiedSampler().sample(reference, candidates, 60, seed=4)
    assert [x.key for x in a.selected] == [x.key for x in b.selected]
    assert [x.key for x in a.selected] != [x.key for x in c.selected]


def test_deficit_taken_from_adjacent_bins_and_recorded() -> None:
    reference = list(range(1, 11))  # 10 gruppi da 1 valore
    candidates = _cands([v for v in range(1, 11) if v != 5 for _ in range(10)])
    result = StratifiedSampler().sample(reference, candidates, 20, seed=1)
    assert len(result.selected) == 20
    assert result.moved == 2
    assert result.available[4] == 0
    assert result.as_dict()["moved_between_bins"] == 2


def test_not_enough_candidates() -> None:
    with pytest.raises(DataError, match="not enough candidates"):
        StratifiedSampler().sample([1, 2, 3], _cands([2, 2]), 5, seed=1)
    with pytest.raises(DataError, match="out of range"):
        StratifiedSampler().sample([1, 2, 3], _cands([100] * 50), 5, seed=1)


def test_zero_requested() -> None:
    assert StratifiedSampler().sample([1, 2], _cands([1]), 0, seed=1).selected == []


def test_deduplicate_against_natives_and_within_pool() -> None:
    a = Candidate("a", "def f():\n    return 1\n", 2, "r")
    b = Candidate("b", "def f():   \n  return 1", 2, "r")  # stesso codice, spazi diversi
    c = Candidate("c", "def g():\n    return 2\n", 2, "r")
    kept, dropped = deduplicate([c, b, a], exclude=[code_fingerprint("def g(): return 2")])
    assert [k.key for k in kept] == ["a"]
    assert dropped == 2
