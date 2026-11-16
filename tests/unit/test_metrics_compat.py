"""Retrocompatibilità delle metriche dei metodi esistenti (ADR-017; PIPELINE §5.1–§5.4).

Le metriche di MCGMark (senza attacco e sotto T1.5), STONE e ACW calcolate dal codice attuale sulla
catena finta di ``tests/compat_metrics.py`` devono coincidere, riga per riga e a tolleranza 1e-9,
con quelle registrate dal codice di prima delle modifiche comuni
(``tests/fixtures/compat/metrics_before_p2.json``). Sono ammesse solo metriche **nuove** e additive
(``NEW_METRICS``; nelle celle con attacco anche la capacità dei metodi multi-bit, §5.3): nessuna
metrica esistente cambia valore, intervallo, ``n`` o metodo dell'IC, e nessuna sparisce.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from tests.compat_metrics import GOLDEN, compute

TOL = 1e-9
# Metriche additive delle modifiche comuni (§5.2, §5.3): nuove righe, nessuna riga vecchia toccata.
NEW_METRICS = re.compile(r"^(message_accuracy_detected|wrong_attribution_rate_\w+)$")
# Capacità sugli attaccati (§5.3): righe nuove solo nelle celle con attacco.
NEW_ATTACKED = re.compile(r"^(message_accuracy|bit_accuracy|embedding_success_rate)$")


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return (math.isnan(a) and math.isnan(b)) or abs(a - b) <= TOL
    return bool(a == b)


@pytest.fixture(scope="module")
def chain_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("compat") / "wmb"


@pytest.fixture(scope="module")
def current(chain_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    return compute(chain_root)


def test_golden_was_recorded_before_the_common_changes() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["src_dirty"] is False
    methods = {cell.split("/")[1] for cell in golden["cells"] if not cell.startswith("metrics/at")}
    assert methods == {"mcgmark", "stone", "acw"}
    assert any(cell.startswith("metrics/attacked/T1.5/") for cell in golden["cells"])


def test_attacked_mcgmark_gets_the_capacity(current: dict[str, dict[str, dict[str, Any]]]) -> None:
    """§5.3: la capacità si calcola anche sugli attaccati di un metodo multi-bit."""
    cells = [c for c in current if c.startswith("metrics/attacked/T1.5/")]
    assert len(cells) == 3
    for cell in cells:
        assert {"message_accuracy", "bit_accuracy", "embedding_success_rate"} <= set(current[cell])


def test_existing_metrics_are_unchanged(current: dict[str, dict[str, dict[str, Any]]]) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["cells"]
    assert set(current) == set(golden)  # stesse celle, stessi percorsi (config_hash invariati)
    for cell, rows in golden.items():
        now = current[cell]
        assert set(rows) <= set(now), f"{cell}: metrics disappeared {set(rows) - set(now)}"
        added = set(now) - set(rows)
        attacked = cell.startswith("metrics/attacked/")
        allowed = [NEW_METRICS, NEW_ATTACKED] if attacked else [NEW_METRICS]
        assert all(any(r.match(n) for r in allowed) for n in added), f"{cell}: unexpected {added}"
        for name, fields in rows.items():
            for key, value in fields.items():
                assert _same(now[name][key], value), (cell, name, key, now[name][key], value)


def test_cluster_script_recomputes_the_cells(
    current: dict[str, dict[str, dict[str, Any]]], chain_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``scripts/check_metrics_compat.py`` (il controllo sulle celle reali del cluster) sulla
    catena finta: ricalcola ogni cella senza scrivere artefatti e trova tutte le righe uguali."""
    from bench.methods.base import MethodAdapter
    from tests.unit.test_inventory_scripts import _load

    script = _load("check_metrics_compat")
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(chain_root))
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    before = {p: p.stat().st_mtime_ns for p in chain_root.rglob("*.parquet")}
    report = chain_root.parent / "compat.txt"
    assert script.main(["--report", str(report), "paths=local"]) == 0
    text = report.read_text(encoding="utf-8")
    assert "outcome: PASS" in text and f"cells compared: {len(current)}" in text
    assert text.count("[OK") == len(current) and "[DIFF]" not in text
    assert {p: p.stat().st_mtime_ns for p in chain_root.rglob("*.parquet")} == before
    assert script.main(["--report", str(report), "paths=local"]) == 1  # il log esiste già
