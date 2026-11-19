"""``scripts/report_imperceptibility_dev.py``: Levenshtein bit-parallelo, identificatori cambiati,
misure su tutti i campioni e sui soli cambiati, AUROC sui soli cambiati, rifiuti."""

from __future__ import annotations

import math
import random
from pathlib import Path

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("report_imperceptibility_dev")


def _naive(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def test_levenshtein_matches_dynamic_programming() -> None:
    rng = random.Random(7)
    for _ in range(400):
        a = "".join(rng.choice("ab_x(") for _ in range(rng.randint(0, 90)))
        b = "".join(rng.choice("ab_y(") for _ in range(rng.randint(0, 90)))
        assert script.levenshtein(a, b) == _naive(a, b), (a, b)
    assert script.levenshtein("kitten", "sitting") == 3
    long_a = "x = 1\n" * 300
    assert script.levenshtein(long_a, long_a.replace("x", "yy", 1)) == 2


ORIG = "def f(a, b):\n    total = a + b\n    return total\n"
RENAMED = "def f(a, b):\n    acc = a + b\n    return acc\n"


def test_identifiers_and_edit_table() -> None:
    assert script.identifiers(ORIG, "python") == {"f", "a", "b", "total"}
    base = pd.DataFrame({"sample_id": ["p1", "p2", "p3"], "code": [ORIG, ORIG, ""]})
    marked = pd.DataFrame({
        "sample_id": ["m1", "m2", "m3"], "parent_id": ["p1", "p2", "p3"],
        "code": [RENAMED, ORIG, "x = 1\n"],  # m2: inserimento fallito; m3: originale vuoto
    })  # fmt: skip
    t = script.edit_table(marked, base, "python")
    assert list(t["sample_id"]) == ["m1", "m2"]
    m1 = t.iloc[0]
    assert bool(m1["changed"]) and m1["ident_abs"] == 1 and m1["ident_frac"] == pytest.approx(0.25)
    assert m1["lev"] == script.levenshtein(ORIG, RENAMED)
    assert m1["lev_norm"] == pytest.approx(m1["lev"] / len(ORIG))
    assert t.iloc[1][["ident_abs", "lev", "lev_norm", "ident_frac"]].tolist() == [0, 0, 0, 0]
    s = script.edit_summary(t)
    assert s["all"]["n"] == 2 and s["changed"]["n"] == 1
    assert s["all"]["ident_abs"] == pytest.approx(0.5) and s["changed"]["ident_abs"] == 1
    assert s["changed"]["ident_frac"] == pytest.approx(0.25)


def test_changed_auroc_uses_only_changed_positives() -> None:
    oof = pd.DataFrame({
        "sample_id": ["m1", "m2", "b1", "b2"], "label": [1, 1, 0, 0],
        "score": [0.9, 0.1, 0.2, 0.3],
    })  # fmt: skip
    assert script.changed_auroc(oof, {"m1"}) == pytest.approx(1.0)
    assert script.changed_auroc(oof, {"m1", "m2"}) == pytest.approx(0.5)
    assert math.isnan(script.changed_auroc(oof, set()))


def test_refusals(tmp_path: Path) -> None:
    assert script.main(["--config", "mcss:abc", "--split", "test"]) == 1
    log = tmp_path / "x.txt"
    log.write_text("x", encoding="utf-8")
    assert script.main(["--config", "multimark:abc", "--report", str(log)]) == 1
    assert script.main(["--config", "mcss:abc:mcss"]) == 1  # manca la base
