"""T3.1 ri-marcatura con k2 (ADR-014 §3) su dati finti: griglia e risorse, percorsi della seconda
chiave, riscrittura con il generatore del metodo (finto) e riapplicazione di ACW, metriche della
ri-marcatura (quattro categorie, TPR e FPR con k2, sovrapposizione delle regole di ACW)."""

# ruff: noqa: E501  (dati di prova su una riga)
from __future__ import annotations

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from bench_contracts import WorkerItem, WorkerResult

from bench.attacks.base import AttackContext, MethodSetup, grid_attacks
from bench.attacks.t3.remark import RemarkAttack
from bench.config.resources import ResourceClass, ResourcePolicy
from bench.config.schema import ExperimentConfig
from bench.domain.enums import AttackStatus, Language
from bench.domain.models import Problem, ThresholdSet
from bench.hpo.freeze import FreezeRecord, FrozenCell, data_sha256, freeze_ref
from bench.methods.base import CodeEmbedder
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.remark import MetricsRemarkStage
from bench.pipeline.stages.watermark import WatermarkStage
from bench.store.refs import ArtifactRef
from tests.conftest import rebuild

PY = Language.PYTHON
MODEL = "qwen25_coder_7b"
PROBLEM = Problem(
    problem_key="mbpp/1", dataset="mbppplus", level="L1", language="python", split="test",
    prompt_text="", entry_point="f", canonical_solution=None, test_ref=None,
    contamination_risk=False, loc_to_generate=None,
)  # fmt: skip


def _attack() -> RemarkAttack:
    ((h, attack),) = grid_attacks("T3.1").items()
    assert isinstance(attack, RemarkAttack) and attack.key_id == "k2"
    return attack


def test_grid_resources_and_paths(cfg: ExperimentConfig) -> None:
    attack = _attack()
    assert attack.needs_method and attack.group == "T3"
    policy = ResourcePolicy(cfg.resources, {}, cfg.slurm)
    assert policy.resource_for("attack", method="acw", attack="T3.1") is ResourceClass.CPU
    assert policy.resource_for("attack", method="stone", attack="T3.1") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("detect_k2", method="stone") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("detect_k2", method="promptmark") is ResourceClass.CPU
    assert policy.resource_for("calibrate_k2") is ResourceClass.CPU
    det = detection_ref("stone", MODEL, "abc", "neg_human", "L1", "python", "test", "k2")
    assert (
        det.path.as_posix()
        == "detection/stone/qwen25_coder_7b/abc/k2/neg_human/L1_python_test.parquet"
    )
    k1 = detection_ref("stone", MODEL, "abc", "neg_human", "L1", "python", "test")
    assert (
        k1.path.as_posix() == "detection/stone/qwen25_coder_7b/abc/neg_human/L1_python_test.parquet"
    )
    thr = threshold_ref("stone", MODEL, "python", "abc", ["L1", "L2"], "k2")
    assert thr.path.as_posix() == "thresholds/stone/qwen25_coder_7b/python/L1L2/k2/abc.json"
    assert freeze_ref("stone", "k2").path.as_posix() == "freeze/stone.k2.json"
    assert freeze_ref("stone").path.as_posix() == "freeze/stone.json"


def _parent(code: str, i: int) -> Any:
    return SimpleNamespace(
        sample_id=f"s{i}", problem_key="mbpp/1", code=code, expected_message="0101"
    )


class FakeGenerator:
    """Generatore finto: riscrive, lascia uguale o fallisce secondo il codice."""

    def __init__(self) -> None:
        self.items: list[WorkerItem] = []
        self.key_id = ""

    def supports(self, language: Any) -> bool:
        return True

    def embed_metrics(self, result: Any) -> dict[str, Any]:
        return {"n_sites": 3, "missing": None}

    def rewrite_items(self, items: Sequence[WorkerItem], problems: Any, hp: Any, model: Any,
                      key_id: str, *args: Any) -> Any:  # fmt: skip
        self.items, self.key_id = list(items), key_id
        out = []
        for item in items:
            code = item.prompt_messages[-1]["content"]  # type: ignore[index]
            if "same" in code:
                raw = "```python\ndef same():\n    return 1\n```"
            elif "fail" in code:
                out.append(WorkerResult(item.item_id, 0, "FAILED", None, None, None, None, None))
                continue
            else:
                raw = "```python\ndef f():\n    return 2\n```"
            out.append(WorkerResult(item.item_id, 0, "OK", raw, None, None, None, None))
        return SimpleNamespace(results=out)


def _setup(adapter: Any, tmp_path: Path) -> MethodSetup:
    return MethodSetup(adapter, {}, None, {"temperature": 0.2}, {"mbpp/1": PROBLEM}, tmp_path)


def _ctxs(n: int) -> list[AttackContext]:
    return [AttackContext(rng=random.Random(i), problem=PROBLEM, parsers=None) for i in range(n)]  # type: ignore[arg-type]


def test_regeneration_states(tmp_path: Path) -> None:
    codes = [
        "def f():\n    return 1\n",
        "def same():\n    return 1\n",
        "def fail():\n    pass\n",
        "",
    ]
    parents = [_parent(c, i) for i, c in enumerate(codes)]
    gen = FakeGenerator()
    out = _attack().apply_with_method(parents, PY, _ctxs(len(parents)), _setup(gen, tmp_path))
    assert [o.status for o in out] == [
        AttackStatus.APPLIED, AttackStatus.UNCHANGED, AttackStatus.TOOL_ERROR,
        AttackStatus.UNCHANGED,
    ]  # fmt: skip
    assert out[0].code == "def f():\n    return 2" or "return 2" in out[0].code
    assert out[2].code == codes[2]  # TOOL_ERROR: codice marcato (I1)
    assert out[0].detail["embed_status"] == "OK" and out[0].detail["n_sites"] == 3
    assert gen.key_id == "k2" and len(gen.items) == 3  # il codice vuoto non va al worker
    first = gen.items[0]
    assert first.n == 1 and first.expected_message == "0101"
    assert first.prompt_messages is not None and first.prompt_messages[0]["role"] == "system"
    assert "return 1" in first.prompt_messages[1]["content"]
    again = FakeGenerator()
    _attack().apply_with_method(parents, PY, _ctxs(len(parents)), _setup(again, tmp_path))
    assert [i.seed for i in again.items] == [i.seed for i in gen.items]  # determinismo


class FakeACW(CodeEmbedder):
    """ACW finto: riapplicazione con k2 (regole 2, 3, 4)."""

    def __init__(self) -> None:
        pass

    def supports(self, language: Any) -> bool:
        return True

    def embed_from_code(self, samples: Sequence[Any], hp: Any, model: Any, key_id: str,
                        run_dir: Path, device: str = "cpu") -> Any:  # fmt: skip
        results = []
        for s in samples:
            extra = {"rules": [2, 3, 4], "applicable_rules": [2] if "x" in s.code else []}
            if "x" in s.code:
                results.append(WorkerResult(s.sample_id, None, "OK", None, s.code + "# k2\n",
                                            None, None, None, extra))  # fmt: skip
            else:
                results.append(WorkerResult(s.sample_id, None, "FAILED", None, None, None, None,
                                            None, extra, error="no transformation applied"))  # fmt: skip
        return SimpleNamespace(worker=SimpleNamespace(results=results))


FakeACW.__abstractmethods__ = frozenset()


def test_acw_reapplication(tmp_path: Path) -> None:
    parents = [_parent("x = 1\n", 0), _parent("y = 1\n", 1)]
    out = _attack().apply_with_method(parents, PY, _ctxs(2), _setup(FakeACW(), tmp_path))
    assert [o.status for o in out] == [AttackStatus.APPLIED, AttackStatus.UNCHANGED]
    assert out[0].detail["rules"] == [2, 3, 4] and out[0].detail["applicable_rules"] == [2]


def _write(facade: BenchmarkFacade, ref: ArtifactRef, obj: Any) -> None:
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    if isinstance(obj, pd.DataFrame):
        facade.store.write_table(ref, obj, manifest)
    else:
        facade.store.write_model(ref, obj, manifest)


def _det(scores: list[float], extra: dict[str, Any] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_id": [f"a{i}" for i in range(len(scores))],
            "problem_key": [f"mbpp/{i % 2}" for i in range(len(scores))],
            "status": ["OK"] * len(scores),
            "score": scores,
            "embed_status": [None] * len(scores),
            "extra_json": [json.dumps(extra or {})] * len(scores),
        }
    )


def test_remark_metrics(cfg: ExperimentConfig) -> None:
    method = "acw"
    mcfg = rebuild(
        cfg, stage="metrics_remark", methods=[method], models=[MODEL], languages=["python"],
        levels=["L1"], splits=["dev"], attacks=["T3.1"],
    )  # fmt: skip
    facade = BenchmarkFacade(mcfg)
    (cell,) = CellPlanner(mcfg).cells_for(MetricsRemarkStage)
    h = str(cell.attack_params_hash)
    cfg_hash = WatermarkStage(mcfg).config_hash(method)

    def dref(subject: str, key: str) -> ArtifactRef:
        return detection_ref(method, MODEL, cfg_hash, subject, "L1", "python", "dev", key)

    rules = {"rule_unchanged": {"1": True, "2": True, "3": False}}
    _write(facade, dref(attacked_subject("T3.1", h), "k1"), _det([5.0, 5.0, 0.0, 0.0], rules))
    _write(facade, dref(attacked_subject("T3.1", h), "k2"), _det([5.0, 0.0, 5.0, 0.0]))
    _write(facade, dref("neg_human", "k1"), _det([0.0, 0.0, 0.0, 0.0]))
    _write(facade, dref("neg_human", "k2"), _det([0.0, 5.0, 0.0, 0.0]))
    applied = [[2, 3], [4], [], [2]]
    samples = pd.DataFrame(
        {
            "sample_id": [f"a{i}" for i in range(4)],
            "attack_status": ["APPLIED", "APPLIED", "UNCHANGED", "TOOL_ERROR"],
            "attack_detail": [
                json.dumps({"rules": [2, 3, 4], "applicable_rules": a}) for a in applied
            ],  # fmt: skip
        }
    )
    _write(facade, attacked_ref("T3.1", h, method, MODEL, cfg_hash, "L1", "python", "dev"), samples)
    for key in ("k1", "k2"):
        thresholds = ThresholdSet(
            method=method, model_id=MODEL, language="python", config_hash=cfg_hash,
            target_fpr=0.01, threshold=1.0, achieved_fpr_dev=0.01, n_negatives=100,
            underpowered=False, created_at=datetime.now(UTC), negatives_ref="x",
            levels=["L1", "L2"], provisional=False,
        )  # fmt: skip
        _write(facade, threshold_ref(method, MODEL, "python", cfg_hash, ["L1"], key), thresholds)
    thr2 = threshold_ref(method, MODEL, "python", cfg_hash, ["L1"], "k2")
    record = FreezeRecord(
        method=method, frozen_at=datetime.now(UTC), levels=["L1", "L2"], grid_sha256=None,
        candidate_hashes=[],
        cells=[FrozenCell(model_id=MODEL, language="python", config_hash=cfg_hash,
                          threshold=1.0, threshold_sha256=data_sha256(facade.store, thr2))],
    )  # fmt: skip
    _write(facade, freeze_ref(method, "k2"), record)
    assert facade.run_stage("metrics_remark").ran == 1
    (ref,) = MetricsRemarkStage(mcfg).outputs(cell)
    assert ref.path.as_posix().startswith(f"metrics/remark/T3.1/{h}/{method}/")
    table = facade.store.read_table(ref)
    v = dict(zip(table["name"], table["value"], strict=True))
    for category in ("only_k1", "only_k2", "both", "none"):
        assert v[f"remark_{category}"] == pytest.approx(0.25)
    assert v["tpr_at_fpr_k2"] == pytest.approx(0.5)
    assert v["fpr_human_test_k1"] == pytest.approx(0.0)
    assert v["fpr_human_test_k2"] == pytest.approx(0.25)
    assert v["auroc_k2"] == pytest.approx(0.625)
    assert v["unchanged_fraction"] == pytest.approx(0.25)
    assert v["tool_error_fraction"] == pytest.approx(0.25)
    assert v["threshold_k1"] == v["threshold_k2"] == 1.0
    assert v["acw_rule_overlap"] == 2.0  # {2, 3}
    # regole applicate da k2 nel sottoinsieme di k1: 2, 0, 0, 1
    assert v["acw_overlap_fraction|0"] == pytest.approx(0.5)
    assert v["remark_both|overlap=2"] == pytest.approx(1.0)
    assert v["remark_none|overlap=1"] == pytest.approx(1.0)


def test_detect_k2_subjects_and_frozen_config(cfg: ExperimentConfig) -> None:
    from bench.pipeline.stages.remark import CalibrateK2Stage, DetectK2Stage

    dcfg = rebuild(cfg, stage="detect_k2", methods=["stone"], models=[MODEL],
                   languages=["python"], levels=["L1", "L2"], splits=["dev"])  # fmt: skip
    facade = BenchmarkFacade(dcfg)
    stage = DetectK2Stage(dcfg)
    assert stage.subjects("stone", "L1", "dev") == ("neg_human",)
    assert stage.subjects("stone", "L2", "dev") == ("neg_human", "neg_integration")
    assert stage.subjects("stone", "L1", "test") == ("neg_human",)
    record = FreezeRecord(
        method="stone", frozen_at=datetime.now(UTC), levels=["L1", "L2"], grid_sha256=None,
        candidate_hashes=[],
        cells=[FrozenCell(model_id=MODEL, language="python", config_hash="frozen123")],
    )  # fmt: skip
    _write(facade, freeze_ref("stone"), record)
    cell = Cell(method="stone", model_id=MODEL, level="L2", language="python", split="dev")
    assert stage._frozen(cell).config_hash == "frozen123"
    calib = CalibrateK2Stage(dcfg)
    assert calib.levels() == ["L1", "L2"]
    assert calib._frozen(Cell(method="stone", model_id=MODEL, language="python")).config_hash == (
        "frozen123"
    )
