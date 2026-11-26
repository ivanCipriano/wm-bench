"""``scripts/check_attacks.py`` e ``scripts/check_m12.py`` per i metodi post-hoc (P6): punti scelti
per livello e metodi multi-bit; per i cinque metodi nulla cambia. Inoltre
``scripts/set_aside_t3_1_posthoc.py`` (T3.1 dei post-hoc rifatto dopo la correzione del 26
novembre 2026)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.hpo.freeze import FreezeRecord, FrozenCell
from bench.store.artifact_store import ArtifactStore
from tests.unit.test_inventory_scripts import _load

MODEL = "qwen25_coder_7b"

attacks = _load("check_attacks")
m12 = _load("check_m12")


def test_chosen_points_by_level() -> None:
    from bench.chains import POSTHOC_POINTS

    t14 = POSTHOC_POINTS["L2"]["T1.4"]
    for method in ("sweet", "acw", "stone", "promptmark", "mcgmark"):  # i cinque: tutti i punti
        assert attacks.chosen_point(method, "L1", "T1.4", "anything")
    assert attacks.chosen_point("mcss", "L1", "T1.4", "anything")  # L1: suite completa
    assert attacks.chosen_point("multimark", "L2", "T1.4", t14[0])
    assert not attacks.chosen_point("multimark", "L2", "T1.4", "not-a-chosen-point")
    assert attacks.chosen_point("multimark", "L2", "T2.5", "any-point")  # tutti i punti
    assert attacks.chosen_point("mcss", "L3", "T1.4", POSTHOC_POINTS["L3"]["T1.4"][0])
    assert not attacks.chosen_point("mcss", "L3", "T1.4", t14[1])
    assert attacks.chosen_point("mcss", "L3", "T2.4k", "any-point")


def test_m12_multibit_methods() -> None:
    assert set(m12.MULTIBIT) == {"mcgmark", "mcss", "multimark"}
    assert m12.POSTHOC == ("mcss", "multimark") == attacks.POSTHOC


def test_set_aside_t3_1_posthoc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Artefatti T3.1 delle due configurazioni di multimark: prova a vuoto per default, poi
    rinomina con il suffisso; si ferma se una destinazione esiste (nessuna sovrascrittura)."""
    aside = _load("set_aside_t3_1_posthoc")
    record = FreezeRecord(
        method="multimark", frozen_at=datetime(2026, 11, 24, tzinfo=UTC), levels=["L1", "L2"],
        grid_sha256=None, candidate_hashes=[],
        cells=[FrozenCell(model_id=MODEL, language="python", label=label, config_hash=h)
               for label, h in (("prudent", "e52b040770df"), ("high_capacity", "3fed1f1c1e24"))],
    )  # fmt: skip
    monkeypatch.setattr(aside, "load_freeze", lambda store, method: record)
    store = ArtifactStore(tmp_path)
    refs = aside.t3_1_refs(store, "multimark")
    assert len(refs) == 12 and len({r.path for _, r in refs}) == 12
    kinds = [r.path.parts[0] for _, r in refs[:6]]
    assert kinds[0] == "attacked" and kinds[1] == "execution"
    assert sum("k2" in str(r.path) for _, r in refs[:6]) == 1  # rilevazione con k2
    assert all("0b0f5086dab1" in str(r.path) for _, r in refs)
    first = refs[0][1]
    for path in (store.path_of(first), store.manifest_of(first)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    fake_cfg = SimpleNamespace(paths=SimpleNamespace(artifacts=tmp_path))
    monkeypatch.setattr(aside, "load_experiment", lambda overrides: fake_cfg)
    monkeypatch.setattr(sys, "argv", ["set_aside", "--methods", "multimark"])
    assert aside.main() == 0 and store.path_of(first).is_file()  # default: stampa soltanto
    monkeypatch.setattr(sys, "argv", ["set_aside", "--methods", "multimark", "--apply"])
    assert aside.main() == 0 and not store.path_of(first).exists()
    moved = store.path_of(first).with_name(store.path_of(first).name + aside.SUFFIX)
    assert moved.read_text(encoding="utf-8") == "x"
    manifest = store.manifest_of(first)
    assert manifest.with_name(manifest.name + aside.SUFFIX).is_file()
    store.path_of(first).write_text("y", encoding="utf-8")  # di nuovo: la destinazione esiste
    assert aside.main() == 1 and store.path_of(first).read_text(encoding="utf-8") == "y"
    assert moved.read_text(encoding="utf-8") == "x"
