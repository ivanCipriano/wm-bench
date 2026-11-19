"""``scripts/report_snake_dev.py``: punti di T1.6, misure di ``attack_detail`` e spostamento del
punteggio (la lettura degli artefatti è nello smoke test di ``test_posthoc_smoke.py``)."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("report_snake_dev")


def test_points_of_t16_in_separate_rows() -> None:
    points = script.rad.attack_points("python", (script.ATTACK,))
    assert [p.label for p in points] == ["T1.6 caps=convert", "T1.6 caps=keep"]
    assert len({p.params_hash for p in points}) == 2


def test_detail_stats() -> None:
    def detail(**kw: int) -> str:
        return json.dumps(kw)

    samples = pd.DataFrame({
        "attack_status": ["APPLIED", "UNCHANGED", "TOOL_ERROR", "NOT_APPLICABLE"],
        "attack_detail": [detail(n_candidates=4, n_renamed=3, n_caps_kept=1, n_collisions=1),
                          detail(n_candidates=2, n_renamed=0), "", detail(n_candidates=9)],
    })  # fmt: skip
    got = script.detail_stats(samples)
    assert got["n"] == 3  # NOT_APPLICABLE fuori
    assert got["changed"] == pytest.approx(1 / 3)
    assert got["n_renamed"] == pytest.approx(1.0) and got["n_caps_kept"] == pytest.approx(1 / 3)
    assert got["n_collisions"] == pytest.approx(1 / 3)
    assert got["ren_cand"] == pytest.approx(3 / 6)
    empty = script.detail_stats(samples.iloc[3:])
    assert empty["n"] == 0 and math.isnan(empty["changed"])


def test_score_shift_pairs_children_with_parents() -> None:
    clean = pd.DataFrame({"sample_id": ["p1", "p2", "p3"], "score": [4.0, 2.0, float("nan")]})
    attacked = pd.DataFrame({"sample_id": ["c1", "c2", "c3", "c4"],
                             "score": [3.0, 2.0, 5.0, 1.0]})  # fmt: skip
    parents = {"c1": "p1", "c2": "p2", "c3": "p3"}  # c3: genitore senza punteggio, c4: nessuno
    assert script.score_shift(clean, attacked, parents) == pytest.approx(5 / 6 - 1)
    assert math.isnan(script.score_shift(clean, attacked, {}))
