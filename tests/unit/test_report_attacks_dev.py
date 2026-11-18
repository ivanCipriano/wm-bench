"""``scripts/report_attacks_dev.py``: ordine dei punti (17 scelti, poi gli altri) e misure per
fascia di siti (soglia calibrata, soglia teorica, ``bit_accuracy``, attribuzione sbagliata)."""

from __future__ import annotations

import json
import math
from typing import Any, ClassVar

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("report_attacks_dev")


def test_points_chosen_first_then_the_rest_of_the_full_grids() -> None:
    points = script.attack_points("python")
    assert len(points) == 23
    chosen = [p for p in points if p.chosen is not None]
    assert [p.chosen for p in chosen] == list(range(17))
    assert points[:17] == chosen
    assert points[0].label == "T1.4 name_kind=random ratio=0.5"
    others = {p.label for p in points[17:]}
    assert others == {
        "T1.4 name_kind=random ratio=0.25", "T1.4 name_kind=random ratio=0.75",
        "T1.4 name_kind=natural ratio=0.25", "T1.4 name_kind=natural ratio=0.75",
        "T2.1 b=2", "T2.1 b=3",
    }  # fmt: skip
    assert len({p.params_hash for p in points}) == 23


class FakeAdapter:
    secondary_scores: ClassVar[dict[str, str]] = {"nreg16": "x"}

    @staticmethod
    def secondary_score(name: str, extra: dict[str, Any]) -> float | None:
        return extra.get(f"score_{name}")


def test_band_stats_by_sites() -> None:
    rows = [  # (n_sites, score, status, bits, best, expected)
        (10, 3.0, "OK", 24, "a", "a"),
        (10, 1.5, "OK", 12, "b", "a"),
        (40, 2.5, "OK", 24, "c", "c"),
        (40, None, "FAILED", None, None, "c"),
        (0, 0.1, "OK", 0, None, "d"),
    ]
    table = pd.DataFrame({
        "n_sites": [r[0] for r in rows],
        "score": [r[1] for r in rows],
        "status": [r[2] for r in rows],
        "embed_status": ["OK"] * len(rows),
        "bits_correct": [r[3] for r in rows],
        "extra_json": [json.dumps({"score_nreg16": r[1], **({"best_nreg16": r[4]} if r[4] else {})})
                       for r in rows],
        "expected": [r[5] for r in rows],
    })  # fmt: skip
    cap = script._capacity()
    stats = script.band_stats(table, 24, 2.8, FakeAdapter(), {"nreg16": 1.0}, cap.SITE_BANDS)
    assert stats["all"]["n"] == 5
    assert stats["all"]["tpr_cal"] == pytest.approx(1 / 5)
    assert stats["all"]["tpr_2"] == pytest.approx(2 / 5)
    assert stats["8-12"]["tpr_2"] == pytest.approx(1 / 2)
    assert stats["8-12"]["bit_acc"] == pytest.approx((1 + 0.5) / 2)
    assert stats["8-12"]["wa_nreg16"] == pytest.approx(1 / 2)  # "b" invece di "a"
    assert stats["32-55"]["bit_acc"] == pytest.approx(1 / 2)  # FAILED = 0 bit
    assert stats["32-55"]["wa_nreg16"] == 0.0
    assert stats["n_sites=0/none"]["n"] == 1
    assert math.isnan(stats["n_sites=0/none"]["wa_nreg16"])  # nessun rilevato con best
    assert stats["1-7"]["n"] == 0


def test_config_parse_and_refusals(tmp_path: Any) -> None:
    c = script.Config.parse("multimark:59a04735824c:base")
    assert (c.method, c.config_hash, c.label, c.name) == (
        "multimark",
        "59a04735824c",
        "base",
        "base 59a04735824c",
    )
    assert script.Config.parse("mcss:1d0c64c5124e").label == "mcss"
    existing = tmp_path / "log.txt"
    existing.write_text("x", encoding="utf-8")
    assert script.main(["--config", "mcss:h", "--report", str(existing)]) == 1
    assert existing.read_text(encoding="utf-8") == "x"
    assert script.main(["--config", "mcss:h", "--split", "test"]) == 1
