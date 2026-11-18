"""``scripts/report_round3.py``: punti di S, correzione di Šidák della curva in K, controllo con
le esche e somma delle celle per il criterio di v1.3.3."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("report_round3")


def test_s_points_in_the_order_of_v133() -> None:
    points = script.s_points("python")
    assert [p.label for p in points] == [
        "T1.4 name_kind=natural ratio=1.0", "T1.4 name_kind=random ratio=0.5", "T1.5 k=5",
        "T2.1 b=all", "T2.4 p=0.5", "T4.1 rename=True",
    ]  # fmt: skip
    assert len({p.params_hash for p in points}) == len(points)


def test_sidak_score() -> None:
    assert script.sidak(3.0, 1) == 3.0
    # p = 0,01 e K = 16: 1 - 0,99^16
    assert script.sidak(2.0, 16) == pytest.approx(-math.log10(1 - 0.99**16))
    # p piccolo: score - log10 K
    assert script.sidak(20.0, 2**24) == pytest.approx(20.0 - math.log10(2**24), abs=1e-6)
    assert script.sidak(400.0, 2**20) == pytest.approx(400.0 - math.log10(2**20))
    assert script.sidak(0.0, 16) == pytest.approx(0.0)
    assert math.isnan(script.sidak(math.nan, 16))
    scores = [script.sidak(5.0, k) for k in script.K_VALUES]
    assert scores == sorted(scores, reverse=True)


def test_curve_stats_and_decoy_control() -> None:
    rows = [  # (n_sites, score, status, best16, expected)
        (10, 3.0, "OK", "a", "a"),  # K=1 e K=16 (3 - 1,2 = 1,8 < 2: no a K=16)
        (10, 9.0, "OK", "b", "b"),  # rilevato fino a K = 2^24 (9 - 7,2 = 1,8: no a 2^24)
        (40, 30.0, "OK", "x", "c"),  # punteggio alto ma l'esca vince: no nel controllo
        (40, None, "FAILED", None, "c"),
        (0, 0.1, "OK", None, "d"),
    ]
    table = pd.DataFrame({
        "n_sites": [r[0] for r in rows],
        "score": [r[1] for r in rows],
        "status": [r[2] for r in rows],
        "embed_status": ["OK"] * len(rows),
        "extra_json": [json.dumps({"score_nreg16": r[1], "best_nreg16": r[3],
                                   "score_nreg1024": r[1], "best_nreg1024": r[3]})
                       for r in rows],
        "expected": [r[4] for r in rows],
    })  # fmt: skip
    cap = script.rad._capacity()
    stats = script.curve_stats(table, cap, cap.SITE_BANDS)
    assert stats["all"]["n"] == 5
    assert stats["all"]["K1"] == pytest.approx(3 / 5)
    assert stats["all"]["K16"] == pytest.approx(2 / 5)
    assert stats["all"]["K16777216"] == pytest.approx(1 / 5)
    assert stats["all"]["dec16"] == pytest.approx(1 / 5)  # solo il secondo
    assert stats["all"]["K1024"] == pytest.approx(2 / 5) and "dec1024" in stats["all"]
    assert stats["8-12"]["K1"] == 1.0 and stats["8-12"]["K16"] == 0.5
    assert stats["n_sites=0/none"]["n"] == 1 and stats["n_sites=0/none"]["K1"] == 0.0


def test_pooled_rate_and_missing_cells() -> None:
    p = script.Pooled()
    p.add(0.5, 10)
    p.add(1.0, 30)
    p.add(math.nan, 5)
    assert p.rate == pytest.approx(35 / 40)
    p.missing.append("M L2")
    assert math.isnan(p.rate)


def test_refuses_test_split_and_existing_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert script.main(["--config", "mcss:abc", "--split", "test"]) == 1
    log = tmp_path / "x.txt"
    log.write_text("x", encoding="utf-8")
    assert script.main(["--config", "mcss:abc", "--report", str(log)]) == 1
    assert script.main(["--config", "mcss:abc", "--curve", "C"]) == 1
