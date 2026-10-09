"""Calibratore delle soglie (SPEC §13.2) e statistiche delle metriche (SPEC §13.3)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bench.metrics.calibration import MIN_SCORE, ThresholdCalibrator, detected
from bench.metrics.stats import auroc, clopper_pearson, ratio_interval, wilson
from bench.metrics.uncertainty import ClusterBootstrap


def test_threshold_is_the_smallest_candidate_within_target() -> None:
    neg = list(range(1000))  # punteggi 0..999 distinti
    cal = ThresholdCalibrator(0.01, 1000).fit(neg)
    assert cal.threshold == 990.0  # 10 negativi ≥ 990 → FPR 0,01
    assert cal.achieved_fpr == pytest.approx(0.01)
    assert not cal.underpowered and cal.n_negatives == 1000


def test_discrete_scores_give_a_lower_achieved_fpr() -> None:
    # Punteggio a tre valori (come ACW): il primo valore sopra il vincolo salta tutta la classe.
    neg = [0.5] * 950 + [0.9] * 45 + [1.0] * 5
    cal = ThresholdCalibrator(0.01, 1000).fit(neg)
    assert cal.threshold == 1.0 and cal.achieved_fpr == pytest.approx(0.005)
    # Se anche il valore massimo supera il target, la soglia è +inf (FPR 0, TPR 0).
    cal = ThresholdCalibrator(0.01, 1000).fit([1.0] * 980 + [0.0] * 20)
    assert cal.threshold == math.inf and cal.achieved_fpr == 0.0


def test_failed_negatives_and_underpowered() -> None:
    cal = ThresholdCalibrator(0.01, 1000).fit([MIN_SCORE] * 50 + list(range(50)))
    assert cal.n_negatives == 100 and cal.underpowered
    assert cal.threshold == 49.0 and cal.achieved_fpr == pytest.approx(0.01)
    with pytest.raises(ValueError, match="NaN"):
        ThresholdCalibrator().fit([0.1, math.nan])
    with pytest.raises(ValueError):
        ThresholdCalibrator().fit([])


def test_detection_rule() -> None:
    assert detected(2.0, "OK", "OK", 2.0)
    assert not detected(1.9, "OK", "OK", 2.0)
    assert not detected(5.0, "OK", "FAILED", 2.0)  # I2
    assert not detected(5.0, "FAILED", "OK", 2.0)
    assert not detected(None, "OK", None, 2.0)
    assert detected(5.0, "OK", None, 2.0)  # negativi: nessuno stato d'inserimento


def test_intervals() -> None:
    low, high = wilson(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-4) and high == pytest.approx(0.5962, abs=1e-4)
    low, high = clopper_pearson(0, 1000)
    assert low == 0.0 and high == pytest.approx(0.003682, abs=1e-5)
    low, high = clopper_pearson(10, 1000)
    assert low < 0.01 < high
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_auroc_treats_failures_as_minimum() -> None:
    pos = np.array([3.0, 4.0, MIN_SCORE])
    neg = np.array([1.0, 2.0])
    assert auroc(pos, neg) == pytest.approx(4 / 6)
    assert auroc(np.array([MIN_SCORE]), np.array([MIN_SCORE])) == pytest.approx(0.5)


def test_ratio_interval_matches_the_cluster_bootstrap() -> None:
    rng = np.random.default_rng(0)
    table = pd.DataFrame(
        {
            "problem_key": np.repeat([f"p{i}" for i in range(30)], 5),
            "hit": rng.integers(0, 2, 150).astype(float),
        }
    )
    fast = ratio_interval(table, "hit", seed=7, n_resamples=500)
    slow = ClusterBootstrap(7, n_resamples=500).interval(table, lambda t: float(t["hit"].mean()))
    assert fast == pytest.approx(slow)


def test_auroc_keeps_infinite_scores_on_top() -> None:
    assert auroc(np.array([math.inf, 5.0]), np.array([1.0, MIN_SCORE])) == 1.0


def test_secondary_scores_replace_the_main_score() -> None:
    """``with_secondary``: punteggio secondario da ``extra``; senza valore → FAILED."""
    import json

    from bench.methods.adapters.promptmark import PromptMarkAdapter
    from bench.methods.adapters.stone import StoneAdapter
    from bench.pipeline.stages.calibrate import negative_scores, with_secondary

    table = pd.DataFrame(
        {
            "status": ["OK", "OK", "FAILED"],
            "score": [9.0, 8.0, None],
            "extra_json": [
                json.dumps({"z_nonsyntax": 1.5}),
                json.dumps({"z_nonsyntax": None}),
                json.dumps({}),
            ],
        }
    )
    stone = StoneAdapter.__new__(StoneAdapter)
    out = with_secondary(table, stone, "z_nonsyntax")
    assert list(out["status"]) == ["OK", "FAILED", "FAILED"]
    assert negative_scores(out) == [1.5, MIN_SCORE, MIN_SCORE]
    assert list(table["score"][:2]) == [9.0, 8.0]  # la tabella originale non cambia
    pm = PromptMarkAdapter.__new__(PromptMarkAdapter)
    assert pm.secondary_score("p_adaptive_paper", {"p_adaptive_paper": 1e-3}) == pytest.approx(3.0)
    assert pm.secondary_score("p_adaptive_paper", {"p_adaptive_paper": 0.0}) == math.inf
    assert pm.secondary_score("p_adaptive_paper", {"p_adaptive_paper": None}) is None
