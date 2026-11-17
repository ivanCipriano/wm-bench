"""``scripts/report_metrics.py``: filtro ``--config``, soglia teorica fissa ``score >= 2`` per
``mcss`` e ``multimark`` (pipeline di multimark v1.3.0) e punti dello sweep senza metriche."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

rm = _load("report_metrics")
STEM = "L1_python_dev"


def _det(root: Path, meth: str, cfg: str, subject: str, scores: list[float | None],
         status: list[str] | None = None, embed: list[str] | None = None) -> None:  # fmt: skip
    path = root / "detection" / meth / "m" / cfg / subject / f"{STEM}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame({"score": scores, "status": status or ["OK"] * len(scores)})
    if embed is not None:
        table["embed_status"] = embed
    table.to_parquet(path)


def _metrics(root: Path, meth: str, cfg: str) -> None:
    path = root / "metrics_L1L2" / meth / "m" / cfg / f"{STEM}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "name": ["tpr_at_fpr"],
            "value": [0.5],
            "ci_low": [0.4],
            "ci_high": [0.6],
            "n": [4],
            "ci_method": ["cluster_bootstrap"],
        }  # fmt: skip
    ).to_parquet(path)


def _threshold(root: Path, meth: str, cfg: str, value: float) -> None:
    path = root / "thresholds" / meth / "m" / "python" / "L1L2" / f"{cfg}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"threshold": value, "achieved_fpr_dev": 0.01, "n_negatives": 10, "levels": ["L1", "L2"]}
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for cfg in ("old", "new"):
        _metrics(tmp_path, "multimark", cfg)
        _threshold(tmp_path, "multimark", cfg, 1.26)
    # positivi: 2.5 sì, 2.0 sì (>=), 1.9 no, 3.0 con inserimento FAILED no, None no
    _det(tmp_path, "multimark", "new", "positives", [2.5, 2.0, 1.9, 3.0, None],
         embed=["OK", "OK", "OK", "FAILED", "OK"])  # fmt: skip
    _det(tmp_path, "multimark", "new", "neg_human", [0.1, 2.2, 1.0, 5.0],
         status=["OK", "OK", "OK", "FAILED"])  # fmt: skip
    _det(tmp_path, "multimark", "new", "neg_llm", [0.0, 0.3])
    # punto dello sweep: rilevazione e soglia, niente metriche
    _det(tmp_path, "multimark", "sweep", "positives", [1.5, 2.5])
    _det(tmp_path, "multimark", "sweep", "neg_human", [1.5, 0.0])
    _det(tmp_path, "multimark", "sweep", "neg_llm", [0.1])
    _threshold(tmp_path, "multimark", "sweep", 1.2)
    _metrics(tmp_path, "stone", "s1")
    return tmp_path


def _run(root: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> str:
    rm.main(["--artifacts", str(root), "--final", *extra])
    return capsys.readouterr().out


def test_theoretical_threshold_rows(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(root, capsys, "--config", "new")
    assert "config old" not in out and "config new" in out
    assert "theoretical threshold score >= 2 (p <= 0.01, fixed; pipeline v1.3.0):" in out
    lines = {x.split()[0]: x for x in out.splitlines() if x.startswith("    ")}
    assert lines["tpr"].split()[1:2] == ["0.4000"] and " 2/5 " in lines["tpr"]
    assert " 1/4 " in lines["fpr_human"]  # 5.0 con rilevazione FAILED non conta
    assert lines["fpr_llm"].split()[1] == "0.0000" and " 0/2 " in lines["fpr_llm"]
    assert "clopper_pearson" in lines["tpr"]
    assert "tpr_at_fpr" in out  # le metriche della cella restano


def test_sweep_points_and_other_methods(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(root, capsys, "--config", "new", "sweep", "s1")
    assert "config sweep; no metrics: sweep point, rates from the detection)" in out
    sweep = out[out.index("config sweep") :]
    assert "calibrated threshold 1.2:" in sweep and "threshold 1.2  achieved FPR" in sweep
    # a 1.2: tpr 2/2, fpr_human 1/2; a 2: tpr 1/2, fpr_human 0/2
    assert sweep.count(" 2/2 ") == 1 and sweep.count(" 1/2 ") == 2 and " 0/2 " in sweep
    end = out.index("##### multimark m python (L1 dev, config sweep")
    stone = out[out.index("##### stone") : end]
    assert "theoretical" not in stone
    # senza --config: tutte le celle con metriche, nessun punto dello sweep
    every = _run(root, capsys)
    assert "config old" in every and "config new" in every and "config sweep" not in every
