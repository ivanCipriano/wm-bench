"""Metriche del Livello 3 (ADR-016): rilevazione per dataset, Pass@1 solo su ClassEval, validità
del codice di CodeSearchNet e The Stack."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.metrics.uncertainty import ClusterBootstrap
from bench.pipeline.stage import Cell
from bench.pipeline.stages.metrics import MetricsStage, _ts_valid

CELL = Cell(method="stone", model_id="m", level="L3", language="python", split="test")


class Collector:
    def __init__(self) -> None:
        self.values: dict[str, tuple[float, int]] = {}

    def __call__(self, name: str, value: float, ci: Any, how: str, n: int) -> None:
        self.values[name] = (value, n)


def _boot(name: str) -> ClusterBootstrap:
    return ClusterBootstrap(1, n_resamples=50)


def _exec(rows: list[tuple[str, str, str, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["problem_key", "dataset", "status", "stderr_tail"])


def test_tree_sitter_validity_view() -> None:
    table = _exec([
        ("a", "codesearchnet", "COMPILE_ERROR", "Main.java: error"),
        ("b", "codesearchnet", "SYNTAX_ERROR", "tree-sitter: ERROR node"),
        ("c", "codesearchnet", "EXTRACTION_FAILED", None),
        ("d", "codesearchnet", "PASSED", None),
    ])  # fmt: skip
    assert _ts_valid(table)["status"].tolist() == ["PASSED", "FAILED", "FAILED", "PASSED"]


def test_l3_functional_splits_classeval_from_untested(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = MetricsStage(cfg)
    marked = _exec([
        ("classeval/ClassEval_0", "classeval", "PASSED", None),
        ("classeval/ClassEval_17", "classeval", "FAILED", None),
        ("csn/python/x", "codesearchnet", "SYNTAX_ERROR", "tree-sitter: ERROR node"),
        ("csn/python/y", "codesearchnet", "SYNTAX_ERROR", "main.py: invalid syntax"),
    ])  # fmt: skip
    reference = _exec([
        ("classeval/ClassEval_0", "classeval", "PASSED", None),
        ("csn/python/x", "codesearchnet", "PASSED", None),
        ("csn/python/y", "codesearchnet", "PASSED", None),
    ])  # fmt: skip
    monkeypatch.setattr(stage, "_read_exec", lambda ctx, ref: reference)
    add = Collector()
    tested = stage._l3_functional(None, CELL, {"exec_baseline": None}, add, _boot, marked,  # type: ignore[arg-type]
                                  "exec_baseline")  # fmt: skip
    assert set(tested["dataset"]) == {"classeval"}
    # tree-sitter: x non valido, y valido (l'errore è di py_compile) -> 0,5; riferimento 1,0
    assert add.values["tree_sitter_valid"][0] == pytest.approx(0.5)
    assert add.values["tree_sitter_valid_baseline"][0] == pytest.approx(1.0)
    assert add.values["delta_tree_sitter_valid_pp"][0] == pytest.approx(50.0)
    # py_compile (controllo aggiuntivo affidabile per Python): nessuno passa
    assert add.values["syntax_check"][0] == pytest.approx(0.0)
    assert "compile_info" not in add.values and "pass_at_1" not in add.values
    java = Cell(method="stone", model_id="m", level="L3", language="java", split="test")
    add = Collector()
    stage._l3_functional(None, java, {"exec_baseline": None}, add, _boot, marked, "exec_baseline")  # type: ignore[arg-type]
    assert "compile_info" in add.values and "syntax_check" not in add.values


def test_per_dataset_detection(cfg: ExperimentConfig) -> None:
    stage = MetricsStage(cfg)
    positives = pd.DataFrame({
        "problem_key": ["c0", "c1", "s0", "s1"],
        "dataset": ["classeval", "classeval", "codesearchnet", "codesearchnet"],
        "status": ["OK"] * 4, "score": [5.0, 1.0, 5.0, 5.0], "embed_status": ["OK"] * 4,
    })  # fmt: skip
    negatives = pd.DataFrame({
        "problem_key": ["n0", "n1", "n2"],
        "dataset": ["classeval", "codesearchnet", "codesearchnet"],
        "status": ["OK"] * 3, "score": [0.0, 0.0, 9.0], "embed_status": [None] * 3,
    })  # fmt: skip
    add = Collector()
    stage._per_dataset(CELL, add, positives, negatives, 2.0)
    assert add.values["tpr_at_fpr|classeval"] == (pytest.approx(0.5), 2)
    assert add.values["tpr_at_fpr|codesearchnet"] == (pytest.approx(1.0), 2)
    assert add.values["fpr_human|codesearchnet"] == (pytest.approx(0.5), 2)
    assert add.values["fpr_human|classeval"] == (pytest.approx(0.0), 1)
    assert "auroc|classeval" in add.values and "auroc|codesearchnet" in add.values
