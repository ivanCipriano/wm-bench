"""ADR-017 §7: le fasi dell'impercettibilità nello sweep dell'HPO. Senza ``hpo.grid`` le celle
sono quelle di prima (le stesse di quando le fasi non partecipavano allo sweep); con
``hpo.grid`` si aggiungono solo i candidati della griglia sulle celle di sviluppo, il default
resta la cella di default (``config_hash = None``) e le celle di test non cambiano."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.builder import load_experiment
from bench.hpo.grid import resolve, sweep_hashes
from bench.methods.base import MethodAdapter
from bench.pipeline.stage import CellPlanner
from bench.pipeline.stages.imperceptibility import (
    AdversarialLogregStage,
    AdversarialUnixcoderStage,
    ImperceptibilityMetricsStage,
    PerplexityStage,
)
from bench.pipeline.stages.watermark import make_adapter

BASE = ["stage=metrics_imperceptibility", "levels=[L1,L2]", "splits=[dev,test]", "paths=local",
        "hpo.use_selected=false"]  # fmt: skip
STAGES = [
    (PerplexityStage, ["execution.sources=[llm_baseline,llm_watermarked,baseline_twin]"]),
    (AdversarialLogregStage, []),
    (AdversarialUnixcoderStage, []),
    (ImperceptibilityMetricsStage, []),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path / "wmb"))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "e" * 40)


def _cells(stage: type, *overrides: str) -> list:  # type: ignore[type-arg]
    return CellPlanner(load_experiment([*BASE, *overrides])).cells_for(stage)


@pytest.mark.parametrize(("stage", "extra"), STAGES)
@pytest.mark.parametrize(
    ("method", "languages"),
    [("multimark", "[python]"), ("stone", "[python,java]"), ("mcgmark", "[python]")],
)
def test_without_grid_nothing_changes(
    stage: type, extra: list[str], method: str, languages: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = [f"methods=[{method}]", f"languages={languages}", *extra]
    now = _cells(stage, *common)
    monkeypatch.setattr(stage, "sweep_roles", ())  # come prima di ADR-017 §7
    before = _cells(stage, *common)
    assert now == before and all(c.config_hash is None for c in now)
    # con hpo.grid ma senza la partecipazione allo sweep: ancora le stesse celle
    assert _cells(stage, *common, f"hpo.grid={method}") == before


@pytest.mark.parametrize(("stage", "extra"), STAGES)
@pytest.mark.parametrize(
    ("method", "languages"),
    [("multimark", "[python]"), ("stone", "[python,java]"), ("mcgmark", "[python]")],
)
def test_grid_adds_only_dev_candidates(
    stage: type, extra: list[str], method: str, languages: str
) -> None:
    common = [f"methods=[{method}]", f"languages={languages}", *extra]
    plain = _cells(stage, *common)
    swept = _cells(stage, *common, f"hpo.grid={method}")
    assert {c for c in swept if c.config_hash is None} <= set(plain)
    assert {c for c in plain if c.split == "test"} <= set(swept)  # test invariato
    assert {c for c in plain if c.method is None} <= set(swept)  # baseline della perplexity
    added = [c for c in swept if c not in plain]
    assert added and all(c.split == "dev" and c.config_hash for c in added)
    if stage is PerplexityStage:
        assert all(c.source == "llm_watermarked" for c in added)
    cfg = load_experiment([*BASE, *common])
    default = resolve(cfg, make_adapter(cfg, method), None)[1]
    assert {c.config_hash for c in added} == set(sweep_hashes(cfg, method, ("candidate",))) - {
        default
    }
    if method == "multimark":  # il default ({}) è nella griglia: resta la cella di default
        assert set(plain) <= set(swept)
