"""Controllo negativo degli attacchi (D34) su dati finti: punti della griglia con
``negative_control``, celle delle tre fasi, riscrittura della baseline con un attaccante finto,
riferimenti della rilevazione e metriche (FPR sulla baseline riscritta, AUROC fra riscritti)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from bench.attacks.base import grid_attacks, negative_control_attacks
from bench.attacks.llm import Completion
from bench.config.resources import ResourceClass, ResourcePolicy
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Source
from bench.domain.ids import sample_id
from bench.domain.models import CodeSample, Problem, ThresholdSet
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.attack import AttackStage, attacked_subject
from bench.pipeline.stages.attack_control import (
    AttackBaselineStage,
    DetectAttackControlStage,
    MetricsAttackControlStage,
    attacked_baseline_ref,
    control_detection_ref,
)
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.pipeline.stages.watermark import WatermarkStage
from bench.store.refs import ArtifactRef
from tests.conftest import rebuild

METHOD, MODEL, LANG = "acw", "qwen25_coder_7b", "python"
CODES = ["def f(a, b):\n    return a + b\n", ""]


@pytest.fixture
def ccfg(cfg: ExperimentConfig) -> ExperimentConfig:
    return rebuild(
        cfg,
        stage="attack_baseline",
        methods=[METHOD],
        models=[MODEL],
        languages=[LANG],
        levels=["L1"],
        splits=["dev"],
        attacks=["T2.2"],
    )


def _full_rewrite_hash() -> str:
    (h,) = (h for h, a in grid_attacks("T2.2").items() if a.params["variant"] == "full_rewrite")
    return h


def test_only_full_rewrite_has_the_control(ccfg: ExperimentConfig) -> None:
    assert list(negative_control_attacks("T2.2")) == [_full_rewrite_hash()]
    assert negative_control_attacks("T1.4") == {}
    planner = CellPlanner(ccfg)
    assert len(planner.cells_for(AttackStage)) == 2  # entrambe le varianti
    (base,) = planner.cells_for(AttackBaselineStage)
    assert base.method is None and base.attack_params_hash == _full_rewrite_hash()
    for stage in (DetectAttackControlStage, MetricsAttackControlStage):
        (cell,) = planner.cells_for(stage)
        assert cell.method == METHOD and cell.attack_params_hash == _full_rewrite_hash()
    policy = ResourcePolicy(ccfg.resources, {}, ccfg.slurm)
    assert policy.resource_for("attack_baseline", attack="T2.2") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("detect_attack_control", method="acw") is ResourceClass.CPU
    assert policy.resource_for("detect_attack_control", method="stone") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("metrics_attack_control") is ResourceClass.CPU


class FakeLLM:
    """Riscrive ogni codice con un nome di variabile diverso."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, conversations: Sequence[list[dict[str, str]]]) -> list[Completion]:
        self.calls += len(conversations)
        text = "```python\ndef f(a, b):\n    total = a + b\n    return total\n```"
        return [Completion(text, 12, False) for _ in conversations]

    def info(self) -> dict[str, Any]:
        return {"model_id": "fake"}


def _write(facade: BenchmarkFacade, ref: ArtifactRef, obj: Any) -> None:
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    if isinstance(obj, pd.DataFrame):
        facade.store.write_table(ref, obj, manifest)
    else:
        facade.store.write_model(ref, obj, manifest)


def _baseline(facade: BenchmarkFacade) -> list[CodeSample]:
    problem = Problem(
        problem_key="mbpp/1", dataset="mbppplus", level="L1", language=LANG, split="dev",
        prompt_text="", entry_point="f", canonical_solution=None, test_ref=None,
        contamination_risk=False, loc_to_generate=None,
    )  # fmt: skip
    samples = [
        CodeSample(
            sample_id=sample_id(
                source="llm_baseline",
                problem_key="mbpp/1",
                language=LANG,
                model_id=MODEL,
                sample_index=i,
            ),  # fmt: skip
            problem_key="mbpp/1",
            dataset="mbppplus",
            language=LANG,
            level="L1",
            split="dev",
            source=Source.LLM_BASELINE,
            model_id=MODEL,
            method=None,
            config_hash=None,
            key_id=None,
            sample_index=i,
            seed=i,
            raw_output=code,
            code=code,
            extraction_ok=bool(code),
            embed_status=None,
            expected_message=None,
            parent_id=None,
            attack_id=None,
            attack_params_hash=None,
            attack_status=None,
            contamination_risk=False,
        )  # fmt: skip
        for i, code in enumerate(CODES)
    ]
    _write(facade, problems_ref("L1", LANG), pd.DataFrame([problem.model_dump(mode="json")]))
    rows = pd.DataFrame([s.model_dump(mode="json") for s in samples])
    _write(facade, baseline_ref(MODEL, "L1", LANG, "dev"), rows)
    return samples


def test_attack_baseline_rewrites_each_baseline_sample(ccfg: ExperimentConfig) -> None:
    facade = BenchmarkFacade(ccfg)
    parents = _baseline(facade)
    stage = AttackBaselineStage(ccfg)
    (cell,) = CellPlanner(ccfg).cells_for(AttackBaselineStage)
    attack = stage.attack(cell)
    p = attack.params
    llm = FakeLLM()
    stage._llms[(p["attacker"], p["max_new_tokens"], p["batch_size"])] = llm  # type: ignore[assignment]
    ctx = StageContext(ccfg, facade.store, facade.provenance, stage.name, cell)
    stage.run(cell, ctx)
    (ref,) = stage.outputs(cell)
    assert ref == attacked_baseline_ref("T2.2", _full_rewrite_hash(), MODEL, "L1", LANG, "dev")
    table = facade.store.read_table(ref)
    assert len(table) == len(parents)  # I1
    assert list(table["parent_id"]) == [s.sample_id for s in parents]
    assert set(table["source"]) == {"attacked"} and table["method"].isna().all()
    assert list(table["attack_status"]) == ["APPLIED", "UNCHANGED"]  # il codice vuoto resta
    assert "total" in table.loc[0, "code"]
    assert llm.calls == 1
    extra = facade.store.read_manifest(ref).extra  # type: ignore[union-attr]
    assert extra["attacker"] == {"model_id": "fake"} and "prompts_sha256" in extra


def _detections(scores: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "problem_key": [f"mbpp/{i % 2}" for i in range(len(scores))],
            "status": ["OK"] * len(scores),
            "score": scores,
            "embed_status": [None] * len(scores),
            "extra_json": ["{}"] * len(scores),
        }
    )


def test_control_metrics(ccfg: ExperimentConfig) -> None:
    cfg = rebuild(ccfg, stage="metrics_attack_control")
    facade = BenchmarkFacade(cfg)
    h = _full_rewrite_hash()
    cfg_hash = WatermarkStage(cfg).config_hash(METHOD)
    (cell,) = CellPlanner(cfg).cells_for(MetricsAttackControlStage)
    det = DetectAttackControlStage(cfg)
    assert det.cell_subjects(cell) == (f"neg_attacked/T2.2/{h}",)
    assert attacked_baseline_ref("T2.2", h, MODEL, "L1", LANG, "dev") in det.inputs(cell)
    (out,) = det.outputs(cell)
    assert out == control_detection_ref(METHOD, MODEL, cfg_hash, "T2.2", h, "L1", LANG, "dev")
    assert out.path.as_posix() == (
        f"detection/{METHOD}/{MODEL}/{cfg_hash}/neg_attacked/T2.2/{h}/L1_{LANG}_dev.parquet"
    )

    def dref(subject: str) -> ArtifactRef:
        return detection_ref(METHOD, MODEL, cfg_hash, subject, "L1", LANG, "dev")

    _write(facade, dref(attacked_subject("T2.2", h)), _detections([5.0, 5.0, 0.0, 0.0]))
    _write(facade, out, _detections([0.0, 0.0, 5.0, 0.0]))
    _write(facade, dref("neg_llm"), _detections([0.0, 0.0, 0.0, 0.0]))
    thresholds = ThresholdSet(
        method=METHOD, model_id=MODEL, language=LANG, config_hash=cfg_hash, target_fpr=0.01,
        threshold=1.0, achieved_fpr_dev=0.01, n_negatives=100, underpowered=False,
        created_at=datetime.now(UTC), negatives_ref="x", levels=["L1"],
    )  # fmt: skip
    _write(facade, threshold_ref(METHOD, MODEL, LANG, cfg_hash, ["L1"]), thresholds)
    assert facade.run_stage("metrics_attack_control").ran == 1
    stage = MetricsAttackControlStage(cfg)
    (ref,) = stage.outputs(cell)
    assert ref.path.as_posix().startswith(f"metrics/attack_control/T2.2/{h}/{METHOD}/")
    table = facade.store.read_table(ref)
    values = dict(zip(table["name"], table["value"], strict=True))
    assert values["tpr_at_fpr"] == pytest.approx(0.5)
    assert values["fpr_llm_rewritten"] == pytest.approx(0.25)
    assert values["fpr_llm_original"] == pytest.approx(0.0)
    assert values["auroc_rewritten"] == pytest.approx(0.625)
