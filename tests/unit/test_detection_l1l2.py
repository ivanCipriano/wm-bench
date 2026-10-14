"""Rilevazione e calibrazione definitiva su L1+L2 (M9, ADR-011) su dati finti.

MCGMark con lo shim ``echo``, mini CodeNet di ``test_prepare_data_l2``: soggetto
``neg_integration`` nelle sole celle L2 dev, soglia L1+L2 su un percorso proprio con l'FPR di
sviluppo per parte, nativi di L1 senza l'integrazione di L1, soglia provvisoria invariata.
"""

from __future__ import annotations

import pytest

import bench.pipeline.stages.generate_baseline as gb
import bench.pipeline.stages.watermark as wm
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.models import ThresholdSet
from bench.methods.base import MethodAdapter
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stages.calibrate import CalibrateStage, levels_tag, threshold_ref
from bench.pipeline.stages.detect import DetectStage, detection_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.pipeline.stages.prepare_data import native_ref, pooled_integration_ref
from bench.pipeline.stages.tune import candidates_ref, selected_ref
from tests.conftest import rebuild
from tests.unit.test_generate_baseline import FakeBackend
from tests.unit.test_prepare_data_l2 import N_DEV, run_l2  # noqa: F401  (fixture)
from tests.unit.test_watermark import MODEL, echo_client

METHOD = "mcgmark"


@pytest.fixture
def chain(
    run_l2: tuple[BenchmarkFacade, ExperimentConfig],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> BenchmarkFacade:
    _, base = run_l2
    tcfg = rebuild(
        base,
        methods=[METHOD],
        models=[MODEL],
        languages=["python"],
        levels=["L1", "L2"],
        splits=["dev"],
    )
    FakeBackend.calls, FakeBackend.fail_after = [], None
    monkeypatch.setattr(gb, "BACKEND_FACTORY", FakeBackend)
    monkeypatch.setattr(wm, "WORKER_FACTORY", echo_client)
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)
    facade = BenchmarkFacade(tcfg)
    for stage in ("generate_baseline", "watermark", "generate_baseline_twin", "detect"):
        facade.run_stage(stage)
    return facade


def test_pooled_integration_only_in_the_l2_dev_cells(chain: BenchmarkFacade) -> None:
    stage = DetectStage(chain.cfg)
    assert "neg_integration" not in stage.subjects(METHOD, "L1", "dev")
    assert "neg_integration" not in stage.subjects(METHOD, "L2", "test")
    assert "neg_integration" in stage.subjects(METHOD, "L2", "dev")
    cfg_hash = stage.config_hash(METHOD)
    ref = detection_ref(METHOD, MODEL, cfg_hash, "neg_integration", "L2", "python", "dev")
    pooled = chain.store.read_table(pooled_integration_ref(Language.PYTHON))
    scored = chain.store.read_table(ref)
    assert len(scored) == len(pooled)  # I1
    assert set(scored["sample_id"]) == set(pooled["sample_id"])


def test_final_thresholds_on_l1_l2(chain: BenchmarkFacade) -> None:
    assert chain.run_stage("calibrate").ran == 1
    cfg_hash = DetectStage(chain.cfg).config_hash(METHOD)
    ref = threshold_ref(METHOD, MODEL, "python", cfg_hash, ["L1", "L2"])
    assert ref.path.as_posix() == f"thresholds/{METHOD}/{MODEL}/python/L1L2/{cfg_hash}.json"
    thresholds = chain.store.read_model(ref, ThresholdSet)
    assert not thresholds.provisional and thresholds.levels == ["L1", "L2"]

    # Parti: nativi L1 dev (senza l'integrazione di L1), nativi L2 dev, integrazione combinata.
    l1 = chain.store.read_table(native_ref(Language.PYTHON))
    pooled = chain.store.read_table(pooled_integration_ref(Language.PYTHON))
    extra = chain.store.read_manifest(ref).extra  # type: ignore[union-attr]
    parts = extra["fpr_dev_by_part"]
    assert set(parts) == {"L1_native", "L2_native", "integration_L1L2"}
    assert parts["L1_native"]["n_negatives"] == int((l1["split"] == "dev").sum())
    assert parts["L2_native"]["n_negatives"] == N_DEV * 5
    assert parts["integration_L1L2"]["n_negatives"] == len(pooled)
    assert thresholds.n_negatives == sum(p["n_negatives"] for p in parts.values())
    hits = sum(p["false_positives"] for p in parts.values())
    assert hits / thresholds.n_negatives == pytest.approx(thresholds.achieved_fpr_dev)

    # La soglia provvisoria (solo L1) ha il suo percorso e resta quella della M7/M8.
    only_l1 = rebuild(chain.cfg, levels=["L1"])
    assert BenchmarkFacade(only_l1).run_stage("calibrate").ran == 1
    provisional = chain.store.read_model(
        threshold_ref(METHOD, MODEL, "python", cfg_hash), ThresholdSet
    )
    assert provisional.provisional and provisional.levels == ["L1"]
    assert chain.store.exists(ref)  # la definitiva non viene toccata
    stage = CalibrateStage(only_l1)
    assert set(stage._parts(next(iter(_cells(only_l1))))) == {"L1"}


def _cells(cfg: ExperimentConfig) -> list:  # type: ignore[type-arg]
    from bench.pipeline.stage import CellPlanner

    return CellPlanner(cfg).cells_for(CalibrateStage)


def test_level_tags_of_the_output_paths() -> None:
    assert levels_tag(["L1"]) is None and levels_tag(["L2", "L1"]) == "L1L2"
    final = ["L1", "L2"]
    assert selected_ref("stone", MODEL, "cpp").path.as_posix() == (
        f"hpo/selected/stone/{MODEL}/cpp.json"
    )
    assert selected_ref("stone", MODEL, "cpp", final).path.as_posix() == (
        f"hpo/selected_L1L2/stone/{MODEL}/cpp.json"
    )
    assert candidates_ref("stone", MODEL, "cpp", final).path.as_posix() == (
        f"hpo/candidates_L1L2/stone/{MODEL}/cpp.parquet"
    )
    assert metrics_ref("stone", MODEL, "h", "L2", "cpp", "test", final).path.as_posix() == (
        f"metrics_L1L2/stone/{MODEL}/h/L2_cpp_test.parquet"
    )
    assert metrics_ref("stone", MODEL, "h", "L1", "cpp", "dev").path.as_posix() == (
        f"metrics/stone/{MODEL}/h/L1_cpp_dev.parquet"
    )
