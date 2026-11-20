"""``scripts/compare_thresholds.py``: negativi e soglie identici fra il vecchio e il nuovo hash di
mcss, altrimenti uscita 1 (con uno store finto)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from bench.domain.models import ThresholdSet
from tests.unit.test_inventory_scripts import _load

script = _load("compare_thresholds")


def _det(scores: list[float], extra: dict[str, Any] | None = None) -> pd.DataFrame:
    payload = json.dumps({"nreg16": 1.0, "elapsed_s": 0.1, **(extra or {})})
    return pd.DataFrame({
        "sample_id": [f"s{i}" for i in range(len(scores))],
        "status": ["OK"] * len(scores),
        "score": scores,
        "native_decision": [None] * len(scores),
        "decoded_message": ["0101"] * len(scores),
        "bits_correct": [None] * len(scores),
        "extra_json": [payload] * len(scores),
    })  # fmt: skip


def _threshold(h: str, value: float = 2.5, secondary: float = 3.0) -> ThresholdSet:
    return ThresholdSet(
        method="mcss", model_id="m", language="python", config_hash=h, target_fpr=0.01,
        threshold=value, achieved_fpr_dev=0.009, n_negatives=500, underpowered=False,
        created_at=datetime.now(UTC), negatives_ref=f"x/{h}", levels=["L1", "L2"],
        secondary={"nreg16": {"threshold": secondary, "achieved_fpr_dev": 0.01}},
    )  # fmt: skip


def test_compare_detections() -> None:
    a = _det([1.0, math.nan, 0.5])
    assert script.compare_detections(a, _det([1.0, math.nan, 0.5])) == []
    # elapsed diverso: ignorato
    b = a.assign(extra_json=[json.dumps({"nreg16": 1.0, "elapsed_s": 9.0})] * 3)
    assert script.compare_detections(a, b) == []
    assert script.compare_detections(a, _det([1.0, math.nan, 0.6]))[0].startswith("s2: score")
    assert script.compare_detections(a, _det([1.0, math.nan, 0.5], {"nreg16": 2.0})) != []
    assert "different samples" in script.compare_detections(a, _det([1.0, 2.0]))[0]


def test_compare_threshold_sets() -> None:
    assert script.compare_thresholds(_threshold("old"), _threshold("new")) == []
    diff = script.compare_thresholds(_threshold("old"), _threshold("new", 2.6))
    assert diff[0].startswith("threshold")
    assert script.compare_thresholds(_threshold("old"), _threshold("new", secondary=3.1)) != []
    inf = _threshold("old", math.inf)
    assert script.compare_thresholds(inf, _threshold("new", math.inf)) == []


class FakeStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def exists(self, ref: Any) -> bool:
        return str(ref.path) in self.data

    def read_table(self, ref: Any) -> pd.DataFrame:
        return self.data[str(ref.path)]

    def read_model(self, ref: Any, cls: Any) -> Any:
        return self.data[str(ref.path)]


def _store(new_score: float, new_threshold: float) -> dict[str, Any]:
    from bench.pipeline.stages.calibrate import threshold_ref
    from bench.pipeline.stages.detect import detection_ref

    data: dict[str, Any] = {}
    for h, score, tau in (("old", 0.5, 2.5), ("new", new_score, new_threshold)):
        for subject, level in script.SUBJECTS:
            ref = detection_ref("mcss", "m", h, subject, level, "python", "dev")
            data[str(ref.path)] = _det([1.0, score])
        data[str(threshold_ref("mcss", "m", "python", h, ["L1", "L2"]).path)] = _threshold(h, tau)
    return data


@pytest.mark.parametrize(("score", "tau", "code"), [(0.5, 2.5, 0), (0.7, 2.5, 1), (0.5, 2.4, 1)])
def test_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], score: float,
              tau: float, code: int) -> None:  # fmt: skip
    import bench.store.artifact_store as store_module
    from bench.config import builder

    cfg = SimpleNamespace(paths=SimpleNamespace(artifacts=Path(".")), models=["m"])
    monkeypatch.setattr(builder, "load_experiment", lambda overrides: cfg)
    data = _store(score, tau)
    monkeypatch.setattr(store_module, "ArtifactStore", lambda root: FakeStore(data))
    assert script.main(["--old", "old", "--new", "new"]) == code
    out = capsys.readouterr()
    if code == 0:
        assert "[OK] negatives and thresholds identical" in out.out
        assert out.out.count("[SAME]") == len(script.SUBJECTS) + 1
    else:
        assert "[DIFF]" in out.out and "[STOP]" in out.err
