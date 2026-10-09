"""Catena della M7 su dati finti: execute (marcati e gemella) → detect → calibrate → metrics.

MCGMark con lo shim ``echo`` (punteggio = lunghezza del codice) e la sandbox finta di
``test_execute``: si verificano celle, I1, soggetti, soglie provvisorie, metriche con IC (I7) e il
rifiuto delle metriche sul test con soglie provvisorie.
"""

from __future__ import annotations

import pandas as pd
import pytest

import bench.pipeline.stages.generate_baseline as gb
import bench.pipeline.stages.watermark as wm
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import ThresholdSet
from bench.execution import sandbox as sb
from bench.methods.base import MethodAdapter
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.calibrate import CalibrateStage, threshold_ref
from bench.pipeline.stages.detect import DetectStage, detection_ref
from bench.pipeline.stages.execute import ExecuteStage, execution_ref
from bench.pipeline.stages.metrics import MetricsStage, metrics_ref
from bench.pipeline.stages.prepare_data import integration_ref, native_ref
from tests.conftest import rebuild
from tests.datafix import build_tiny_datasets, tiny_config
from tests.unit.test_execute import FakeSandbox
from tests.unit.test_generate_baseline import FakeBackend
from tests.unit.test_watermark import MODEL, echo_client

METHOD = "mcgmark"


@pytest.fixture
def facade(cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(tcfg, methods=[METHOD], models=[MODEL], languages=["python"], splits=["dev"])
    sources = ["canonical", "llm_baseline", "llm_watermarked", "baseline_twin"]
    tcfg = rebuild(tcfg, execution={**tcfg.execution.model_dump(), "sources": sources})
    FakeBackend.calls, FakeBackend.fail_after = [], None
    FakeSandbox.jobs, FakeSandbox.files = [], []
    FakeSandbox.fail_after, FakeSandbox.broken = None, False
    monkeypatch.setattr(gb, "BACKEND_FACTORY", FakeBackend)
    monkeypatch.setattr(wm, "WORKER_FACTORY", echo_client)
    monkeypatch.setattr(sb, "make_sandbox", lambda config: FakeSandbox())
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    facade = BenchmarkFacade(tcfg)
    for stage in (
        "generate_baseline",
        "watermark",
        "generate_baseline_twin",
        "evalplus_groundtruth",
        "execute",
    ):
        facade.run_stage(stage)
    return facade


def test_execute_cells_include_the_method_sources(facade: BenchmarkFacade) -> None:
    cells = CellPlanner(facade.cfg).cells_for(ExecuteStage)
    by_source = {c.source: c for c in cells}
    assert set(by_source) == {"canonical", "llm_baseline", "llm_watermarked", "baseline_twin"}
    assert by_source["llm_baseline"].method is None and by_source["canonical"].model_id is None
    stage = ExecuteStage(facade.cfg)
    cfg_hash = stage.config_hash(by_source["llm_watermarked"])
    assert execution_ref(by_source["llm_watermarked"], cfg_hash).path.as_posix() == (
        f"execution/llm_watermarked/{METHOD}/{MODEL}/{cfg_hash}/L1_python_dev.parquet"
    )
    marked = facade.store.read_table(execution_ref(by_source["llm_watermarked"], cfg_hash))
    samples = facade.store.read_table(
        wm.watermarked_ref(METHOD, MODEL, cfg_hash, "L1", "python", "dev")
    )
    assert len(marked) == len(samples)  # I1
    twin = facade.store.read_table(execution_ref(by_source["baseline_twin"], cfg_hash))
    assert len(twin) == len(samples)
    # Un metodo senza gemella non ha la cella baseline_twin.
    no_twin = rebuild(facade.cfg, methods=["sweet"])
    assert {c.source for c in CellPlanner(no_twin).cells_for(ExecuteStage)} == {
        "canonical",
        "llm_baseline",
        "llm_watermarked",
    }


def test_detect_calibrate_metrics(facade: BenchmarkFacade) -> None:
    for stage in ("detect", "calibrate", "metrics"):
        report = facade.run_stage(stage)
        assert report.ran == 1, stage
    cfg_hash = DetectStage(facade.cfg).config_hash(METHOD)

    # detect: un artefatto per soggetto, una riga per codice (I1).
    def det(subject: str) -> pd.DataFrame:
        ref = detection_ref(METHOD, MODEL, cfg_hash, subject, "L1", "python", "dev")
        return facade.store.read_table(ref)

    positives = det("positives")
    samples = facade.store.read_table(
        wm.watermarked_ref(METHOD, MODEL, cfg_hash, "L1", "python", "dev")
    )
    assert len(positives) == len(samples)
    assert set(positives["sample_id"]) == set(samples["sample_id"])
    native = facade.store.read_table(native_ref(Language.PYTHON))
    integration = facade.store.read_table(integration_ref(Language.PYTHON, "dev"))
    n_human = int((native["split"] == "dev").sum()) + int((integration["split"] == "dev").sum())
    assert len(det("neg_human")) == n_human
    baseline = facade.store.read_table(gb.baseline_ref(MODEL, "L1", "python", "dev"))
    assert len(det("neg_llm")) == len(baseline)
    assert len(det("neg_llm_twin")) == len(baseline)
    assert set(positives["status"]) == {"OK"}
    # Il messaggio atteso dei negativi è derivato dagli identificativi (SPEC §9.5).
    assert positives["key_id"].eq(wm.KEY_ID).all()

    # calibrate: soglia provvisoria (solo L1), sottodimensionata sui dati finti.
    thresholds = facade.store.read_model(
        threshold_ref(METHOD, MODEL, "python", cfg_hash), ThresholdSet
    )
    assert thresholds.provisional and thresholds.levels == ["L1"]
    assert thresholds.underpowered and thresholds.n_negatives == n_human
    assert thresholds.achieved_fpr_dev <= facade.cfg.detection.target_fpr

    # metrics: tutte le metriche previste, ciascuna con il suo intervallo (I7).
    table = facade.store.read_table(metrics_ref(METHOD, MODEL, cfg_hash, "L1", "python", "dev"))
    names = set(table["name"])
    assert names == {
        "tpr_at_fpr",
        "fpr_human",
        "fpr_llm",
        "fpr_llm_baseline",
        "auroc",
        "pass_at_1",
        "pass_at_1_baseline",
        "delta_pass_at_1_pp",
        "delta_pass_at_1_pp_baseline",
        "message_accuracy",
        "bit_accuracy",
        "embedding_success_rate",
    }
    assert table["ci_low"].notna().all() and table["ci_high"].notna().all()
    assert (table["ci_low"] <= table["value"] + 1e-9).all()
    assert (table["value"] <= table["ci_high"] + 1e-9).all()
    values = dict(zip(table["name"], table["value"], strict=True))
    assert values["fpr_human"] == pytest.approx(thresholds.achieved_fpr_dev)
    manifest = facade.store.read_manifest(
        metrics_ref(METHOD, MODEL, cfg_hash, "L1", "python", "dev")
    )
    assert manifest is not None and manifest.extra["provisional_thresholds"] is True


def test_metrics_refuse_the_test_split_with_provisional_thresholds(
    facade: BenchmarkFacade,
) -> None:
    facade.run_stage("detect")
    facade.run_stage("calibrate")
    cell = Cell(method=METHOD, model_id=MODEL, level="L1", language="python", split="test")
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "metrics", cell)
    with pytest.raises(ConfigError, match="provisional"):
        MetricsStage(facade.cfg).run(cell, ctx)
    # Con l'opzione dello smoke test il controllo è superato (poi mancano gli input del test).
    smoke = rebuild(
        facade.cfg,
        detection={**facade.cfg.detection.model_dump(), "allow_provisional_test": True},
    )
    with pytest.raises(Exception) as info:
        MetricsStage(smoke).run(
            cell, StageContext(smoke, facade.store, facade.provenance, "metrics", cell)
        )
    assert "provisional" not in str(info.value)


def test_calibrate_cells(facade: BenchmarkFacade) -> None:
    cells = CellPlanner(facade.cfg).cells_for(CalibrateStage)
    assert [c.key() for c in cells] == [
        Cell(method=METHOD, model_id=MODEL, language="python").key()
    ]


def test_conditional_preservation_of_acw() -> None:
    """P(corretto dopo | corretto prima) sui soli campioni della baseline corretti (Wilson)."""
    rows: list[tuple[str, float, tuple[float, float], str, int]] = []
    watermarked = pd.DataFrame({"sample_id": ["m1", "m2", "m3"], "parent_id": ["b1", "b2", "b3"]})
    marked_exec = pd.DataFrame(
        {"sample_id": ["m1", "m2", "m3"], "status": ["PASSED", "FAILED", "PASSED"]}
    )
    baseline_exec = pd.DataFrame(
        {"sample_id": ["b1", "b2", "b3"], "status": ["PASSED", "PASSED", "FAILED"]}
    )
    MetricsStage._conditional_preservation(
        lambda *a: rows.append(a), watermarked, marked_exec, baseline_exec
    )
    name, value, (low, high), how, n = rows[0]
    assert name == "conditional_preservation" and value == 0.5 and n == 2 and how == "wilson"
    assert 0.0 < low < 0.5 < high < 1.0


def test_unsupported_language_gives_not_applicable_metrics(facade: BenchmarkFacade) -> None:
    """MCGMark su JavaScript: positivi NOT_APPLICABLE, nessuna metrica, manifest marcato."""
    js = rebuild(facade.cfg, languages=["javascript"])
    for stage in ("generate_baseline", "watermark", "generate_baseline_twin", "detect"):
        BenchmarkFacade(js).run_stage(stage)
    BenchmarkFacade(js).run_stage("calibrate")
    cfg_hash = DetectStage(js).config_hash(METHOD)
    positives = facade.store.read_table(
        detection_ref(METHOD, MODEL, cfg_hash, "positives", "L1", "javascript", "dev")
    )
    assert set(positives["status"]) == {"NOT_APPLICABLE"}
    manifest = facade.store.read_manifest(threshold_ref(METHOD, MODEL, "javascript", cfg_hash))
    assert manifest is not None and manifest.extra["not_applicable"] is True
    cell = Cell(method=METHOD, model_id=MODEL, level="L1", language="javascript", split="dev")
    MetricsStage(js).run(cell, StageContext(js, facade.store, facade.provenance, "metrics", cell))
    ref = metrics_ref(METHOD, MODEL, cfg_hash, "L1", "javascript", "dev")
    assert facade.store.read_table(ref).empty
    manifest = facade.store.read_manifest(ref)
    assert manifest is not None and manifest.extra["not_applicable"] is True
