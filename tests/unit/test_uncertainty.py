"""ClusterBootstrap e Pass@1 (SPEC §13.6)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bench.metrics.uncertainty import ClusterBootstrap, bootstrap_seed, pass_at_1


def _table(rates: dict[str, int], n: int = 6) -> pd.DataFrame:
    rows = []
    for key, c in rates.items():
        rows += [{"problem_key": key, "status": "PASSED" if i < c else "FAILED"} for i in range(n)]
    return pd.DataFrame(rows)


def test_pass_at_1_is_mean_of_c_over_n() -> None:
    value, per_problem = pass_at_1(_table({"a/1": 6, "a/2": 3, "a/3": 0}))
    assert value == pytest.approx(0.5)
    assert per_problem.tolist() == [1.0, 0.5, 0.0]


def test_bootstrap_is_deterministic_and_contains_the_mean() -> None:
    table = _table({f"p/{i}": i % 7 for i in range(60)})
    value, per_problem = pass_at_1(table)
    seed = bootstrap_seed(20261001, "pass_at_1", "cell")
    a = ClusterBootstrap(seed).mean_interval(per_problem)
    b = ClusterBootstrap(seed).mean_interval(per_problem)
    assert a == b
    assert a[0] < value < a[1]
    assert ClusterBootstrap(seed + 1).mean_interval(per_problem) != a


def test_generic_interval_matches_fast_path() -> None:
    table = _table({f"p/{i}": i % 4 for i in range(20)})
    seed = 7
    fast = ClusterBootstrap(seed, n_resamples=200).mean_interval(pass_at_1(table)[1])
    slow = ClusterBootstrap(seed, n_resamples=200).interval(table, lambda t: pass_at_1(t)[0])
    # Stessi indici ricampionati solo se la generazione coincide: basta che siano vicini.
    assert np.allclose(fast, slow, atol=0.08)


def test_degenerate_cases() -> None:
    assert ClusterBootstrap(1).mean_interval(np.array([1.0, 1.0])) == (1.0, 1.0)
    low, high = ClusterBootstrap(1).mean_interval(np.array([]))
    assert np.isnan(low) and np.isnan(high)
    with pytest.raises(ValueError):
        ClusterBootstrap(1, n_resamples=0)
