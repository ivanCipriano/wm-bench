"""Congelamento dei metodi prima della parte di test (ADR-013, M10) su artefatti finti.

Si scrivono a mano la scelta definitiva, la tabella dei candidati e la soglia L1+L2 di STONE
(Python, un modello) e si verificano: il record di congelamento, il rifiuto di selezioni o soglie
provvisorie e di una griglia cambiata, il blocco di ``calibrate`` e ``tune`` su un metodo
congelato (salvo override), la configurazione congelata nelle celle di test e il controllo della
soglia in ``metrics``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import SelectedConfig, ThresholdSet
from bench.hpo.freeze import (
    FINAL_LEVELS,
    FreezeRecord,
    ensure_not_frozen,
    freeze_ref,
    load_freeze,
)
from bench.hpo.grid import load_grid
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.calibrate import CalibrateStage, threshold_ref
from bench.pipeline.stages.metrics import MetricsStage
from bench.pipeline.stages.tune import TuneStage, candidates_ref, selected_ref
from bench.pipeline.stages.watermark import WatermarkStage, make_adapter
from bench.store.refs import ArtifactRef
from tests.conftest import rebuild

METHOD, MODEL, LANG = "stone", "qwen25_coder_7b", "python"


@pytest.fixture
def stone(cfg: ExperimentConfig) -> ExperimentConfig:
    return rebuild(cfg, stage="freeze", methods=[METHOD], models=[MODEL], languages=[LANG])


def _hashes(cfg: ExperimentConfig) -> list[str]:
    adapter = make_adapter(cfg, METHOD)
    return [adapter.config_hash(p.hparams) for p in load_grid(METHOD).candidates()]


def _ctx(facade: BenchmarkFacade, stage: str = "tune") -> StageContext:
    return StageContext(facade.cfg, facade.store, facade.provenance, stage, Cell(method=METHOD))


def _write(facade: BenchmarkFacade, ref: ArtifactRef, obj: Any) -> None:
    ctx = _ctx(facade)
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    if isinstance(obj, pd.DataFrame):
        facade.store.write_table(ref, obj, manifest)
    else:
        facade.store.write_model(ref, obj, manifest)


def write_final(
    facade: BenchmarkFacade,
    *,
    provisional_selection: bool = False,
    provisional_threshold: bool = False,
    candidates: list[str] | None = None,
) -> str:
    """Scelta, candidati e soglia definitivi di STONE; restituisce l'hash scelto."""
    hashes = _hashes(facade.cfg)
    chosen = hashes[3]
    now = datetime.now(UTC)
    levels = ["L1"] if provisional_selection else list(FINAL_LEVELS)
    selected = SelectedConfig(
        method=METHOD,
        model_id=MODEL,
        language=LANG,
        config_hash=chosen,
        hparams={"gamma": 0.25, "delta": 3.0},
        constraint_satisfied=True,
        max_pass1_drop_pp=3.0,
        n_candidates=len(hashes),
        provisional=provisional_selection,
        levels=levels,
        created_at=now,
    )
    _write(facade, selected_ref(METHOD, MODEL, LANG, FINAL_LEVELS), selected)
    table = pd.DataFrame({"config_hash": candidates if candidates is not None else hashes})
    _write(facade, candidates_ref(METHOD, MODEL, LANG, FINAL_LEVELS), table)
    thresholds = ThresholdSet(
        method=METHOD,
        model_id=MODEL,
        language=LANG,
        config_hash=chosen,
        target_fpr=0.01,
        threshold=4.2,
        achieved_fpr_dev=0.009,
        n_negatives=2000,
        underpowered=False,
        created_at=now,
        negatives_ref="x",
        provisional=provisional_threshold,
        levels=["L1"] if provisional_threshold else list(FINAL_LEVELS),
    )
    _write(facade, threshold_ref(METHOD, MODEL, LANG, chosen, FINAL_LEVELS), thresholds)
    return chosen


def test_freeze_record(stone: ExperimentConfig) -> None:
    facade = BenchmarkFacade(stone)
    chosen = write_final(facade)
    assert facade.run_stage("freeze").ran == 1
    record = load_freeze(facade.store, METHOD)
    assert isinstance(record, FreezeRecord)
    assert record.levels == ["L1", "L2"] and record.grid_sha256
    assert sorted(record.candidate_hashes) == sorted(_hashes(stone))
    cell = record.cell(MODEL, LANG)
    assert cell is not None and cell.config_hash == chosen and cell.threshold == 4.2
    thr = threshold_ref(METHOD, MODEL, LANG, chosen, FINAL_LEVELS)
    assert cell.threshold_sha256 == facade.store.read_manifest(thr).data_sha256  # type: ignore[union-attr]
    # Idempotente: il record esiste, la pipeline lo salta; un nuovo congelamento è rifiutato.
    assert facade.run_stage("freeze").ran == 0
    with pytest.raises(ConfigError, match="already frozen"):
        BenchmarkFacade(rebuild(stone, force=True)).run_stage("freeze")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provisional_selection": True}, "selection is provisional"),
        ({"provisional_threshold": True}, "threshold is provisional"),
        ({"candidates": ["abc"]}, "grid changed"),
    ],
)
def test_freeze_refuses_what_is_not_final(
    stone: ExperimentConfig, kwargs: dict[str, Any], message: str
) -> None:
    facade = BenchmarkFacade(stone)
    write_final(facade, **kwargs)
    with pytest.raises(ConfigError, match=message):
        facade.run_stage("freeze")
    assert not facade.store.exists(freeze_ref(METHOD))


@pytest.mark.parametrize(("local_model", "ok"), [("other_model", True), (MODEL, False)])
def test_freeze_expects_the_candidates_of_each_cell(
    stone: ExperimentConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_model: str,
    ok: bool,
) -> None:
    """Con un punto di ``local`` (D31) ogni cella si confronta con i suoi candidati: un punto di
    un'altra cella non manca nella tabella; uno della cella sì. Il record li elenca tutti."""
    import bench.hpo.grid as grid_mod
    import bench.pipeline.stages.freeze as freeze_mod
    import bench.pipeline.stages.tune as tune_mod

    hashes = _hashes(stone)  # griglia del repository, prima della modifica
    grids = tmp_path / "grids"
    grids.mkdir()
    (grids / "stone.yaml").write_text(
        grid_mod.grid_path(METHOD).read_text(encoding="utf-8")
        + "local:\n  - hparams: {gamma: 0.5, delta: 12.0}\n"
        + f"    cells: [{{model_id: {local_model}, language: {LANG}}}]\n",
        encoding="utf-8",
    )

    def fake_grid_path(method: str, root: Path | None = None) -> Path:
        return (root or grids) / f"{method}.yaml"

    for module in (grid_mod, tune_mod, freeze_mod):
        monkeypatch.setattr(module, "grid_path", fake_grid_path)
    facade = BenchmarkFacade(stone)
    write_final(facade, candidates=hashes)
    if not ok:
        with pytest.raises(ConfigError, match="grid changed"):
            facade.run_stage("freeze")
        return
    assert facade.run_stage("freeze").ran == 1
    record = load_freeze(facade.store, METHOD)
    assert record is not None and len(record.candidate_hashes) == len(hashes) + 1


def test_freeze_lists_everything_that_is_missing(stone: ExperimentConfig) -> None:
    with pytest.raises(ConfigError, match="no final selection"):
        BenchmarkFacade(stone).run_stage("freeze")


def test_a_frozen_method_cannot_be_recalibrated(stone: ExperimentConfig) -> None:
    facade = BenchmarkFacade(stone)
    write_final(facade)
    assert ensure_not_frozen(facade.store, METHOD, "calibrate", override=False) is False
    facade.run_stage("freeze")
    with pytest.raises(ConfigError, match="frozen"):
        ensure_not_frozen(facade.store, METHOD, "calibrate", override=False)
    assert ensure_not_frozen(facade.store, METHOD, "calibrate", override=True) is True
    final = rebuild(stone, levels=["L1", "L2"])
    cell = Cell(METHOD, MODEL, LANG)
    for stage in (CalibrateStage(final), TuneStage(final)):
        with pytest.raises(ConfigError, match="frozen"):
            stage.run(cell, _ctx(facade, stage.name))


def test_test_cells_take_the_frozen_configuration(stone: ExperimentConfig) -> None:
    facade = BenchmarkFacade(stone)
    test = rebuild(stone, stage="watermark", levels=["L1"], splits=["dev", "test"])
    with pytest.raises(ConfigError, match="not frozen"):
        CellPlanner(test).cells_for(WatermarkStage)
    chosen = write_final(facade)
    facade.run_stage("freeze")
    cells = CellPlanner(test).cells_for(WatermarkStage)
    by_split = {c.split: c.config_hash for c in cells}
    assert by_split == {"dev": None, "test": chosen}
    # Sweep (hpo.grid) o use_selected=false: nessuna sostituzione.
    sweep = rebuild(test, hpo={**test.hpo.model_dump(), "use_selected": False})
    assert {c.config_hash for c in CellPlanner(sweep).cells_for(WatermarkStage)} == {None}


def test_test_metrics_need_the_frozen_threshold(stone: ExperimentConfig) -> None:
    facade = BenchmarkFacade(stone)
    chosen = write_final(facade)
    facade.run_stage("freeze")
    cfg = rebuild(
        stone,
        stage="metrics",
        detection={**stone.detection.model_dump(), "threshold_levels": ["L1", "L2"]},
    )
    stage = MetricsStage(cfg)
    assert stage.levels() == ["L1", "L2"]
    cell = Cell(METHOD, MODEL, LANG, "L1", "test", config_hash=chosen)
    ref = threshold_ref(METHOD, MODEL, LANG, chosen, FINAL_LEVELS)
    thresholds = facade.store.read_model(ref, ThresholdSet)
    stage._check_test(_ctx(facade, "metrics"), cell, ref, thresholds)  # congelata: va bene
    # Una soglia riscritta dopo il congelamento non è più quella congelata.
    _write(facade, ref, thresholds.model_copy(update={"threshold": 9.9}))
    changed = facade.store.read_model(ref, ThresholdSet)
    with pytest.raises(ConfigError, match="differs from the frozen one"):
        stage._check_test(_ctx(facade, "metrics"), cell, ref, changed)
