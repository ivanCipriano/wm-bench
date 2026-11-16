"""Intervalli bootstrap nel controllo di retrocompatibilità (``scripts/check_metrics_compat.py``).

Sulla catena finta delle fixture gli intervalli bootstrap sono degeneri (pochi problemi, valori
uguali: ``0–0``, ``1–1``): non dipendono dal seme, e un seme sbagliato passerebbe inosservato. Qui
gli intervalli si rendono **dipendenti dal seme** con uno scarto ``(seme mod 997 + 1) · 1e-6``
sugli estremi (bootstrap per problema, ``ratio_interval``, ``auroc_interval``), sia quando la
catena scrive i parquet sia quando lo script li ricalcola. Il calcolo degli intervalli del
benchmark non cambia: lo scarto esiste solo in questo test.

- Con la cella del manifest (comportamento corretto) tutte le righe coincidono a 1e-9, estremi
  compresi.
- Con la cella ricostruita dal percorso (``config_hash`` del percorso al posto di ``None``: il
  primo tentativo sul cluster del 16 novembre 2026) i valori puntuali coincidono e gli estremi
  no, esattamente il sintomo osservato.
- Un manifest mancante è un errore della cella, non un confronto.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

import bench.pipeline.stages.metrics as metrics_mod
from bench.methods.base import MethodAdapter
from bench.metrics.uncertainty import ClusterBootstrap
from tests.compat_metrics import compute
from tests.unit.test_inventory_scripts import _load


def _shift(seed: int) -> float:
    return ((seed % 997) + 1) * 1e-6


def _tag(interval: tuple[float, float], seed: int) -> tuple[float, float]:
    low, high = interval
    return (low + _shift(seed), high + _shift(seed))


class TaggedBootstrap(ClusterBootstrap):
    """``ClusterBootstrap`` con gli estremi spostati in funzione del seme."""

    def interval(self, *args: Any, **kwargs: Any) -> tuple[float, float]:
        return _tag(super().interval(*args, **kwargs), self.seed)

    def mean_interval(self, *args: Any, **kwargs: Any) -> tuple[float, float]:
        return _tag(super().mean_interval(*args, **kwargs), self.seed)


def _seed_dependent(mp: pytest.MonkeyPatch) -> None:
    ratio, auroc = metrics_mod.ratio_interval, metrics_mod.auroc_interval

    def ratio_tagged(table: pd.DataFrame, column: str, seed: int, *a: Any, **k: Any) -> Any:
        return _tag(ratio(table, column, seed, *a, **k), seed)

    def auroc_tagged(pos: Any, neg: Any, seed: int, *a: Any, **k: Any) -> Any:
        return _tag(auroc(pos, neg, seed, *a, **k), seed)

    mp.setattr(metrics_mod, "ClusterBootstrap", TaggedBootstrap)
    mp.setattr(metrics_mod, "ratio_interval", ratio_tagged)
    mp.setattr(metrics_mod, "auroc_interval", auroc_tagged)


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Catena finta scritta con intervalli dipendenti dal seme."""
    root = tmp_path_factory.mktemp("compat_seeds") / "wmb"
    with pytest.MonkeyPatch.context() as mp:
        _seed_dependent(mp)
        compute(root)
    return root


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, chain: Path) -> ModuleType:
    _seed_dependent(monkeypatch)
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(chain))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    return _load("check_metrics_compat")


def _bootstrap_rows(root: Path) -> int:
    n = 0
    for path in (root / "artifacts").glob("metrics*/**/*.parquet"):
        t = pd.read_parquet(path)
        n += int(t["ci_method"].astype(str).str.contains("bootstrap").sum())
    return n


def test_manifest_cell_reproduces_the_intervals(script: ModuleType, chain: Path) -> None:
    assert _bootstrap_rows(chain) > 0
    report = chain.parent / "compat_manifest.txt"
    assert script.main(["--report", str(report), "paths=local"]) == 0
    text = report.read_text(encoding="utf-8")
    assert "outcome: PASS" in text and "[DIFF]" not in text and "[ERROR]" not in text
    assert "cells from the parquet manifests" in text


def test_default_cells_were_written_without_config_hash(script: ModuleType, chain: Path) -> None:
    """Il caso che ha fatto fallire il primo controllo: hash nel percorso, ``None`` nella cella."""
    art = chain / "artifacts"
    plain = sorted(p for p in (art / "metrics").rglob("*.parquet") if "attacked" not in p.parts)
    assert plain
    for path in plain:
        info = script.parse_path(path.relative_to(art))
        cell = script.manifest_cell(path, info, 1000)
        assert cell.config_hash is None and info["config_hash"]


def test_cell_from_the_path_moves_only_the_intervals(
    script: ModuleType, chain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.pipeline.stage import Cell

    def from_path(path: Path, info: dict[str, Any], n_bootstrap: int) -> Cell:
        return Cell(**{k: info[k] for k in (*script.CELL_FIELDS, "config_hash")})

    monkeypatch.setattr(script, "manifest_cell", from_path)
    report = chain.parent / "compat_path.txt"
    assert script.main(["--report", str(report), "paths=local"]) == 1
    text = report.read_text(encoding="utf-8")
    diffs = [line.strip() for line in text.splitlines() if line.startswith("    ")]
    assert diffs, text
    assert all(".ci_low:" in d or ".ci_high:" in d for d in diffs), diffs
    assert "[DIFF] metrics/mcgmark/" in text and "[DIFF] metrics/acw/" in text


def test_missing_manifest_is_an_error(script: ModuleType, chain: Path, tmp_path: Path) -> None:
    copy = tmp_path / "wmb"
    shutil.copytree(chain, copy)
    victim = sorted((copy / "artifacts" / "metrics" / "stone").rglob("*.parquet"))[0]
    victim.with_name(victim.name + ".manifest.json").unlink()
    report = tmp_path / "compat_missing.txt"
    argv = ["--methods", "stone", "--report", str(report), "paths=local"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WMB_LOCAL_ROOT", str(copy))
        assert script.main(argv) == 1
    text = report.read_text(encoding="utf-8")
    assert "[ERROR]" in text and "no usable manifest" in text
