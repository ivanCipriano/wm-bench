"""ADR-017 §5: ``MetricsStage`` nello sweep dell'HPO. Senza ``hpo.grid`` le celle sono quelle di
prima; con ``hpo.grid`` si aggiungono solo i candidati della griglia richiesta, il punto di
default resta la cella di default (``config_hash = None``: stessa chiave, stessi semi), e le celle
di test e quelle sotto attacco non si espandono."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.builder import load_experiment
from bench.methods.base import MethodAdapter
from bench.pipeline.stage import CellPlanner
from bench.pipeline.stages.execute import ExecuteStage
from bench.pipeline.stages.metrics import MetricsStage

BASE = ["stage=metrics", "levels=[L1,L2]", "splits=[dev,test]", "paths=local"]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path / "wmb"))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "e" * 40)


def _cells(stage: type, *overrides: str) -> list:  # type: ignore[type-arg]
    cfg = load_experiment([*BASE, "hpo.use_selected=false", *overrides])
    return CellPlanner(cfg).cells_for(stage)


@pytest.mark.parametrize(
    ("method", "languages"),
    [("multimark", "[python]"), ("stone", "[python,java]"), ("mcgmark", "[python]")],
)
def test_metrics_sweep_adds_only_candidates(method: str, languages: str) -> None:
    common = [f"methods=[{method}]", f"languages={languages}"]
    attacked = [*common, "attacks=[T1.5]"]  # celle sotto attacco: nessuna espansione
    assert _cells(MetricsStage, *attacked, f"hpo.grid={method}") == _cells(MetricsStage, *attacked)
    plain = _cells(MetricsStage, *common)
    swept = _cells(MetricsStage, *common, f"hpo.grid={method}")
    assert all(c.config_hash is None for c in plain)
    # celle senza hash: solo quelle di prima (il default se è un punto della griglia, il test)
    assert {c for c in swept if c.config_hash is None} <= set(plain)
    assert {c for c in plain if c.split == "test"} <= set(swept)
    if method == "multimark":  # il default ({}) è nella griglia: resta la cella di default
        assert set(plain) <= set(swept)
    extra = [c for c in swept if c not in plain]
    assert extra and all(c.config_hash and c.split == "dev" and c.attack_id is None for c in extra)
    from bench.hpo.grid import resolve, sweep_hashes
    from bench.pipeline.stages.watermark import make_adapter

    cfg = load_experiment([*BASE, *common])
    default = resolve(cfg, make_adapter(cfg, method), None)[1]
    hashes = {c.config_hash for c in extra}
    assert default not in hashes
    assert hashes == set(sweep_hashes(cfg, method, ("candidate",))) - {default}


def test_existing_stages_and_plain_runs_are_unchanged() -> None:
    """Senza ``hpo.grid`` nessuna fase cambia; ``execute`` nello sweep resta come prima
    (il default con l'hash esplicito, sui campioni marcati: Pass@1 dei due punti)."""
    common = ["methods=[mcss,multimark,stone]", "languages=[python]"]
    for stage in (MetricsStage, ExecuteStage):
        assert all(c.config_hash is None for c in _cells(stage, *common))
    ex = _cells(ExecuteStage, "methods=[multimark]", "languages=[python]", "splits=[dev]",
                "execution.sources=[llm_watermarked]", "hpo.grid=multimark")  # fmt: skip
    assert len(ex) == 12 and len({c.config_hash for c in ex}) == 3
    assert all(c.source == "llm_watermarked" and c.config_hash for c in ex)
