"""ADR-017 §5: ``MetricsStage`` nello sweep dell'HPO. Senza ``hpo.grid`` le celle sono quelle di
prima; con ``hpo.grid`` si aggiungono solo i candidati della griglia richiesta, il punto di
default resta la cella di default (``config_hash = None``: stessa chiave, stessi semi), e le celle
di test non si espandono. ADR-017 §6: lo stesso per le celle con attacco di sviluppo nelle fasi
``attack``, ``execute`` (``source=attacked``), ``detect`` e ``metrics``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.builder import load_experiment
from bench.methods.base import MethodAdapter
from bench.pipeline.stage import CellPlanner
from bench.pipeline.stages.attack import AttackStage
from bench.pipeline.stages.detect import DetectStage
from bench.pipeline.stages.execute import ExecuteStage
from bench.pipeline.stages.metrics import MetricsStage

BASE = ["stage=metrics", "levels=[L1,L2]", "splits=[dev,test]", "paths=local"]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path / "wmb"))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "e" * 40)


POSTHOC = ("mcss", "multimark")  # parte di test protetta dal congelamento (P6, ADR-019)


def _cells(stage: type, *overrides: str) -> list:  # type: ignore[type-arg]
    """Celle della fase; con un post-hoc del registro solo dev: le loro celle di test senza
    congelamento sono rifiutate (test_posthoc_test_cells_are_refused)."""
    methods = next((o for o in overrides if o.startswith("methods=")), "")
    split = ["splits=[dev]"] if any(m in methods for m in POSTHOC) else []
    cfg = load_experiment([*BASE, "hpo.use_selected=false", *split, *overrides])
    return CellPlanner(cfg).cells_for(stage)


@pytest.mark.parametrize(
    ("method", "languages"),
    [("multimark", "[python]"), ("stone", "[python,java]"), ("mcgmark", "[python]")],
)
def test_metrics_sweep_adds_only_candidates(method: str, languages: str) -> None:
    common = [f"methods=[{method}]", f"languages={languages}"]
    plain = _cells(MetricsStage, *common)
    swept = _cells(MetricsStage, *common, f"hpo.grid={method}")
    assert all(c.config_hash is None for c in plain)
    # celle senza hash: solo quelle di prima (il default se è un punto della griglia, il test)
    assert {c for c in swept if c.config_hash is None} <= set(plain)
    assert {c for c in plain if c.split == "test"} <= set(swept)
    extra = [c for c in swept if c not in plain]
    assert extra and all(c.config_hash and c.split == "dev" and c.attack_id is None for c in extra)
    from bench.hpo.grid import resolve, sweep_hashes
    from bench.pipeline.stages.watermark import make_adapter

    cfg = load_experiment([*BASE, *common])
    default = resolve(cfg, make_adapter(cfg, method), None)[1]
    if default in sweep_hashes(cfg, method, ("candidate",)):  # resta la cella di default
        assert set(plain) <= set(swept)
    hashes = {c.config_hash for c in extra}
    assert default not in hashes
    assert hashes == set(sweep_hashes(cfg, method, ("candidate",))) - {default}


def _n_points(method: str = "multimark") -> int:
    """Punti della griglia ``configs/hpo/<metodo>.yaml`` (default compreso)."""
    from bench.hpo.grid import load_grid

    return len(load_grid(method).points)


def test_existing_stages_and_plain_runs_are_unchanged() -> None:
    """Senza ``hpo.grid`` nessuna fase cambia; ``execute`` nello sweep resta come prima
    (il default con l'hash esplicito, sui campioni marcati: Pass@1 dei punti di sviluppo)."""
    common = ["methods=[mcss,multimark,stone]", "languages=[python]"]
    for stage in (MetricsStage, ExecuteStage):
        assert all(c.config_hash is None for c in _cells(stage, *common))
    ex = _cells(ExecuteStage, "methods=[multimark]", "languages=[python]", "splits=[dev]",
                "execution.sources=[llm_watermarked]", "hpo.grid=multimark")  # fmt: skip
    n = _n_points()
    assert len(ex) == 4 * n and len({c.config_hash for c in ex}) == n  # 2 modelli × 2 livelli
    assert all(c.source == "llm_watermarked" and c.config_hash for c in ex)


ATTACK_STAGES = [
    (AttackStage, []),
    (ExecuteStage, ["execution.sources=[attacked]"]),
    (DetectStage, []),
    (MetricsStage, []),
]


@pytest.mark.parametrize(("stage", "extra"), ATTACK_STAGES)
@pytest.mark.parametrize(
    ("method", "languages"),
    [("multimark", "[python]"), ("stone", "[python,java]"), ("mcgmark", "[python]")],
)
def test_attacked_sweep_adds_only_dev_candidates(
    stage: type, extra: list[str], method: str, languages: str
) -> None:
    """ADR-017 §6: con ``hpo.grid`` le celle attaccate di sviluppo si espandono per candidato; le
    celle senza hash sono un sottoinsieme di quelle senza griglia (il default, se è un punto, e il
    test restano le celle di prima: stessa chiave, stessi semi, stessi percorsi)."""
    common = [f"methods=[{method}]", f"languages={languages}", "attacks=[T1.5,T2.4]", *extra]
    plain = _cells(stage, *common)
    swept = _cells(stage, *common, f"hpo.grid={method}")
    assert plain and all(c.config_hash is None and c.attack_id for c in plain)
    assert {c for c in swept if c.config_hash is None} <= set(plain)
    assert {c for c in plain if c.split == "test"} <= set(swept)
    added = [c for c in swept if c not in plain]
    assert added and all(c.config_hash and c.split == "dev" and c.attack_id for c in added)
    from bench.hpo.grid import resolve, sweep_hashes
    from bench.pipeline.stages.watermark import make_adapter

    cfg = load_experiment([*BASE, *common])
    default = resolve(cfg, make_adapter(cfg, method), None)[1]
    if default in sweep_hashes(cfg, method, ("candidate",)):
        assert set(plain) <= set(swept)
    assert {c.config_hash for c in added} == set(sweep_hashes(cfg, method, ("candidate",))) - {
        default
    }
    # ogni candidato ha le stesse celle (attacco, parametri, livello, linguaggio) del default
    dev = {(c.model_id, c.level, c.language, c.attack_id, c.attack_params_hash)
           for c in plain if c.split == "dev"}  # fmt: skip
    for h in {c.config_hash for c in added}:
        got = {(c.model_id, c.level, c.language, c.attack_id, c.attack_params_hash)
               for c in added if c.config_hash == h}  # fmt: skip
        assert got <= dev
        if method == "multimark":
            assert got == dev


def test_sweep_without_attacks_is_unchanged_in_every_stage() -> None:
    """Senza attacchi lo sweep di ``attack``, ``execute`` e ``detect`` resta quello di prima:
    nessuna cella per ``attack``; ``execute`` e ``detect`` con il default a hash esplicito."""
    common = ["methods=[multimark]", "languages=[python]", "splits=[dev]", "hpo.grid=multimark"]
    assert _cells(AttackStage, *common) == []
    det = _cells(DetectStage, *common)
    n = 4 * _n_points()  # 2 modelli × 2 livelli × punti della griglia
    assert len(det) == n and all(c.config_hash and c.attack_id is None for c in det)
    ex = _cells(ExecuteStage, *common, "execution.sources=[llm_watermarked,attacked]")
    assert len(ex) == n and all(c.source == "llm_watermarked" for c in ex)


@pytest.mark.parametrize("stage", [MetricsStage, ExecuteStage, AttackStage, DetectStage])
def test_posthoc_test_cells_are_refused(stage: type) -> None:
    """P6 (ADR-019): le celle di test di mcss e multimark senza congelamento, o con la griglia,
    non si pianificano (prima erano le celle di default senza hash); stone resta com'era."""
    from bench.domain.errors import ConfigError

    for method in ("mcss", "multimark"):
        common = [f"methods=[{method}]", "languages=[python]", "attacks=[T1.5]",
                  "execution.sources=[llm_watermarked,attacked]"]  # fmt: skip
        for more in ([], [f"hpo.grid={method}"]):
            cfg = load_experiment([*BASE, "hpo.use_selected=false", *common, *more])
            with pytest.raises(ConfigError, match="P6 freeze check"):
                CellPlanner(cfg).cells_for(stage)
    stone = _cells(stage, "methods=[stone]", "languages=[python]", "attacks=[T1.5]",
                   "execution.sources=[llm_watermarked,attacked]")  # fmt: skip
    assert any(c.split == "test" and c.config_hash is None for c in stone)
