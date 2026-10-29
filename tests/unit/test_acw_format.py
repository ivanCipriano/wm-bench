"""ACW al netto della formattazione (M12, ADR-015 §7): normalizzazione del codice, punteggio senza
le regole 44 e 45, celle delle fasi e metriche su dati finti."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from bench.attacks.base import grid_attacks
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.models import ThresholdSet
from bench.metrics.calibration import ThresholdCalibrator
from bench.metrics.imperceptibility import format_free
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner, StageContext
from bench.pipeline.stages.acw_format import (
    AcwFormatMetricsStage,
    format_free_score,
    format_free_view,
)
from bench.pipeline.stages.attack import attacked_subject
from bench.pipeline.stages.calibrate import threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.imperceptibility import (
    AdversarialLogregFormatFreeStage,
    AdversarialUnixcoderFormatFreeStage,
    AdversarialUnixcoderStage,
    adversarial_ref,
)
from bench.pipeline.stages.prepare_data import native_ref
from bench.pipeline.stages.watermark import WatermarkStage
from bench.store.refs import ArtifactRef
from tests.conftest import rebuild

MODEL = "qwen25_coder_7b"


def test_format_free_undoes_rules_44_and_45() -> None:
    acw = 'def f(x):\n\t"""Doc\n\t\triga."""\n\tif x:\n\t\treturn 1\n\n\n\ndef g():\n\treturn 2\n'
    plain = (
        'def f(x):\n    """Doc\n        riga."""\n    if x:\n        return 1\n'
        "\ndef g():\n    return 2\n"
    )
    assert format_free(acw) == plain
    assert format_free(plain) == plain  # idempotente; il codice con spazi non cambia
    assert format_free("a = '\\t'\nb\t= 1\n") == "a = '\\t'\nb\t= 1\n"  # solo l'indentazione


def test_format_free_score_excludes_format_rules() -> None:
    assert (
        format_free_score({"rule_unchanged": {"3": True, "7": False, "44": False, "45": True}})
        == 0.5
    )
    assert format_free_score({"rule_unchanged": {"44": True, "45": True}}) is None
    assert format_free_score({}) is None
    table = pd.DataFrame({
        "status": ["OK", "OK", "FAILED"],
        "score": [1.0, 1.0, None],
        "extra_json": [json.dumps({"rule_unchanged": {"1": True, "45": False}}),
                       json.dumps({"rule_unchanged": {"45": True}}), "{}"],
    })  # fmt: skip
    view = format_free_view(table)
    assert view["score"].tolist()[0] == 1.0 and pd.isna(view["score"].tolist()[1])
    assert view["status"].tolist() == ["OK", "FAILED", "FAILED"]


def test_format_free_stages_only_for_acw() -> None:
    for method, kept in (("acw", True), ("stone", False)):
        cell = Cell(method=method, model_id=MODEL, level="L1", language="python", split="test")
        for stage in (AdversarialLogregFormatFreeStage, AdversarialUnixcoderFormatFreeStage,
                      AcwFormatMetricsStage):  # fmt: skip
            assert (stage.normalize_cell(cell) is not None) is kept
        assert AdversarialUnixcoderStage.normalize_cell(cell) is not None
    assert AdversarialLogregFormatFreeStage.variant == "logreg_fmtfree"
    assert AdversarialUnixcoderFormatFreeStage.format_free


def _write(facade: BenchmarkFacade, ref: ArtifactRef, obj: Any, extra: Any = None) -> None:
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(
        ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None, extra=extra
    )
    if isinstance(obj, pd.DataFrame):
        facade.store.write_table(ref, obj, manifest)
    else:
        facade.store.write_model(ref, obj, manifest)


def _det(prefix: str, rules: list[tuple[bool, bool, bool, bool]]) -> pd.DataFrame:
    """Rilevazioni con l'esito per regola (1, 2, 44, 45); punteggio = frazione invariata."""
    extras = [{"rule_unchanged": dict(zip(("1", "2", "44", "45"), r, strict=True))} for r in rules]
    return pd.DataFrame({
        "sample_id": [f"{prefix}{i}" for i in range(len(rules))],
        "problem_key": [f"p{i % 2}" for i in range(len(rules))],
        "status": ["OK"] * len(rules),
        "score": [sum(r) / 4 for r in rules],
        "embed_status": [None] * len(rules),
        "extra_json": [json.dumps(e) for e in extras],
    })  # fmt: skip


def test_acw_format_metrics(cfg: ExperimentConfig) -> None:
    detection = {**cfg.detection.model_dump(mode="json"), "threshold_levels": ["L1", "L2"]}
    mcfg = rebuild(
        cfg, stage="metrics_acw_format", methods=["acw"], models=[MODEL], languages=["python"],
        levels=["L1"], splits=["dev"], attacks=[], detection=detection,
    )  # fmt: skip
    facade = BenchmarkFacade(mcfg)
    (cell,) = CellPlanner(mcfg).cells_for(AcwFormatMetricsStage)
    h = WatermarkStage(mcfg).config_hash("acw")

    def dref(subject: str, level: str = "L1") -> ArtifactRef:
        return detection_ref("acw", MODEL, h, subject, level, "python", "dev")

    none, mostly = (
        (False,) * 4,
        (True, False, True, True),
    )  # ufficiale 0,75, senza formattazione 0,5
    dev = {"neg_human": _det("n", [none] * 99 + [mostly]), "L2": _det("m", [none] * 99 + [mostly]),
           "integration": _det("i", [none] * 100)}  # fmt: skip
    # neg_human di L1 dev è anche il riferimento di test (parte dev): nativi = solo i suoi id
    _write(facade, dref("neg_human"), dev["neg_human"])
    _write(
        facade,
        native_ref(Language.PYTHON),
        pd.DataFrame({"sample_id": dev["neg_human"]["sample_id"]}),
    )
    _write(facade, dref("neg_human", "L2"), dev["L2"])
    _write(facade, dref("neg_integration", "L2"), dev["integration"])
    positives = [(True,) * 4, mostly, (False, False, True, True), (True, True, False, False)]
    _write(facade, dref("positives"), _det("a", positives))
    ((t11,),) = [list(grid_attacks("T1.1", language="python"))]
    attacked = [
        (True, True, False, False),
        (True, False, False, False),
        none,
        (True, True, False, False),
    ]
    _write(facade, dref(attacked_subject("T1.1", t11)), _det("a", attacked))
    thresholds = ThresholdSet(
        method="acw", model_id=MODEL, language="python", config_hash=h, target_fpr=0.01,
        threshold=0.75, achieved_fpr_dev=0.0067, n_negatives=300, underpowered=True,
        created_at=datetime.now(UTC), negatives_ref="x", levels=["L1", "L2"], provisional=False,
    )  # fmt: skip
    _write(facade, threshold_ref("acw", MODEL, "python", h, ["L1", "L2"]), thresholds)
    oof = pd.DataFrame({"sample_id": ["a", "b", "c", "d"], "problem_key": ["p0", "p0", "p1", "p1"],
                        "label": [1, 0, 1, 0], "score": [0.9, 0.1, 0.8, 0.2]})  # fmt: skip
    for variant in ("logreg_fmtfree", "unixcoder_fmtfree"):
        ref = adversarial_ref(variant, "acw", MODEL, h, "baseline", "L1", "python", "dev")
        _write(facade, ref, oof, extra={"fold_auroc": [1.0, 1.0]})

    assert facade.run_stage("metrics_acw_format").ran == 1
    (ref,) = AcwFormatMetricsStage(mcfg).outputs(cell)
    assert ref.path.as_posix().startswith("imperceptibility/acw_format/acw/")
    table = facade.store.read_table(ref)
    v = dict(zip(table["name"], table["value"], strict=True))
    calibrator = ThresholdCalibrator(0.01, 1000)
    assert v["threshold_official"] == v["threshold_official_recomputed"] == 0.75
    assert v["threshold_format_free"] == calibrator.fit([0.0] * 298 + [0.5, 0.5]).threshold == 0.5
    assert v["n_rules_k1"] == 4 and v["n_format_rules_in_k1"] == 2
    assert v["tpr_at_fpr_official|clean"] == pytest.approx(0.5)  # 1,0 e 0,75
    assert v["tpr_at_fpr_format_free|clean"] == pytest.approx(0.75)  # 1,0, 0,5 e 1,0
    assert v["tpr_at_fpr_official|T1.1"] == pytest.approx(0.0)  # T1.1 toglie 44 e 45
    assert v["tpr_at_fpr_format_free|T1.1"] == pytest.approx(0.75)
    assert v["fpr_human_test_official"] == v["fpr_human_test_format_free"] == pytest.approx(0.01)
    assert v["adversarial_logreg_fmtfree_auroc_oof"] == 1.0
    assert v["adversarial_unixcoder_fmtfree_auroc_fold_mean"] == 1.0
