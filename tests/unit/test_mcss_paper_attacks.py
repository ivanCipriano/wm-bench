"""``scripts/mcss_paper_attacks.py`` (PIPELINE_metodo.md di multimark §4.4 punto 3, criterio (a)
di P2): profili uguali a quelli degli adapter, riepilogo con ``--attack none`` su una selezione
sintetica, criterio, intervalli di Wilson, log mai sovrascritti, libreria allineata al submodule.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

from bench.config.schema import ExperimentConfig
from bench.methods.adapters.multimark import PosthocMultibitAdapter
from bench.methods.base import MethodAdapter
from bench.metrics.stats import wilson
from bench.pipeline.stages.watermark import make_adapter
from tests.unit.test_inventory_scripts import LIBRARY, _load, needs_library

pytestmark = needs_library


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load("mcss_paper_attacks")


def _synthetic(seed: int, n_statements: int, n_functions: int = 1) -> str:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark.synthetic import generate
    finally:
        sys.path.remove(str(LIBRARY))
    return str(generate(seed, n_statements, n_functions))


def _selection(path: Path, codes: list[str]) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for i, code in enumerate(codes):
            fh.write(
                json.dumps({"sample_id": f"s{i}", "problem_key": f"p{i}", "code": code}) + "\n"
            )
    return path


def _run(script: ModuleType, sel: Path, tmp_path: Path, *extra: str) -> tuple[int, Path, Path]:
    report, records = tmp_path / "report.txt", tmp_path / "records.jsonl"
    argv = ["--selection", str(sel), "--report", str(report), "--records", str(records),
            "--library", str(LIBRARY), *extra]  # fmt: skip
    return script.main(argv), report, records


@pytest.mark.parametrize("method", ["mcss", "multimark"])
def test_profiles_are_the_adapter_profiles(
    script: ModuleType, cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)
    script.import_library(LIBRARY)
    profile = script.profiles()[method]
    adapter = make_adapter(cfg, method)
    assert isinstance(adapter, PosthocMultibitAdapter)
    native = adapter.to_native_hparams({})
    for key, value in native.items():
        if key != "registry_seed":  # le esche non servono: registro N_reg = 1
            assert getattr(profile, key) == value, key


def test_attack_none_on_long_files_passes(script: ModuleType, tmp_path: Path) -> None:
    codes = [_synthetic(i, 34, 1 + i % 2) for i in range(4)]
    code, report, records = _run(script, _selection(tmp_path / "sel.jsonl", codes), tmp_path)
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "attack = none" in text and "(4 files, sha256 " in text
    assert text.count("): PASS") == 2 and "): FAIL" not in text
    assert "message_accuracy (decoded == m, all cases): 4/4 = 1.0000 [Wilson 95%:" in text
    rows = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 8 and {r["method"] for r in rows} == {"mcss", "multimark"}
    by = {(r["sample_id"], r["method"]): r for r in rows}
    for i in range(4):  # stesso messaggio per i due profili (confronto appaiato)
        assert by[(f"s{i}", "mcss")]["expected_message"] == by[(f"s{i}", "multimark")][
            "expected_message"]  # fmt: skip
    assert all(r["covered"] for r in rows if r["method"] == "mcss")  # legame posizionale
    assert "paired outcome (mcss ok, multimark ok): (True, True): 4" in text
    # i log non si sovrascrivono
    assert _run(script, tmp_path / "sel.jsonl", tmp_path)[0] == 1


def test_short_and_empty_files_are_counted(script: ModuleType, tmp_path: Path) -> None:
    codes = [_synthetic(1, 34), _synthetic(2, 4), "def f():\n    return 1\n"]
    sel = _selection(tmp_path / "sel.jsonl", codes)
    code, report, records = _run(script, sel, tmp_path, "--methods", "mcss", "--keys", "2")
    assert code == 0
    rows = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6  # 3 file x 2 chiavi
    assert {r["key_index"] for r in rows} == {0, 1}
    empty = [r for r in rows if r["sample_id"] == "s2"]
    assert all(r["embed_status"] == "FAILED" and r["embed_reason"] == "no_sites" for r in empty)
    assert all(not r["decoded_ok"] and r["bits_correct"] == 0 for r in empty)
    text = report.read_text(encoding="utf-8")
    assert "FAILED reasons: {'no_sites': 2}" in text
    assert "criterion (a) of P2 (message_accuracy = 1.00 on all files): FAIL" in text
    assert "paired outcome" not in text  # un solo profilo


def test_stale_library_and_bad_selection_are_refused(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sel = _selection(tmp_path / "sel.jsonl", [_synthetic(1, 34)])
    monkeypatch.setattr(script, "submodule_commit", lambda: "0" * 40)
    code, report, _ = _run(script, sel, tmp_path)
    assert code == 1 and not report.exists()
    monkeypatch.undo()
    dup = tmp_path / "dup.jsonl"
    dup.write_text(sel.read_text(encoding="utf-8") * 2, encoding="utf-8")
    assert _run(script, dup, tmp_path)[0] == 1


def test_wilson_matches_the_benchmark(script: ModuleType) -> None:
    for s, n in ((0, 10), (3, 10), (10, 10), (169, 169), (1, 2)):
        assert script.wilson(s, n) == pytest.approx(wilson(s, n), abs=1e-12)
    assert all(math.isnan(x) for x in script.wilson(0, 0))


def test_posthoc_examples_report(tmp_path: Path) -> None:
    """``scripts/posthoc_examples.py``: esempi prima/dopo (``docs/reports/P2_esempi_bench.md``)."""
    examples = _load("posthoc_examples")
    out = tmp_path / "examples.md"
    fixture = LIBRARY.parent.parent / "tests" / "fixtures" / "tiny" / "humanevalplus.jsonl"
    argv = ["--input", str(fixture), "--ids", "HumanEval/2", "--synthetic", "3:8:2",
            "--library", str(LIBRARY), "--out", str(out)]  # fmt: skip
    assert examples.main(argv) == 0
    text = out.read_text(encoding="utf-8")
    assert "## Esempio 1: `HumanEval/2`" in text and "## Esempio 2: `synthetic-3`" in text
    assert text.count("#### `mcss`") == 2 and text.count("#### `multimark`") == 2
    assert "`FAILED` (`no_sites`)" in text  # multimark senza siti su HumanEval/2
    assert "`ok`, 0 posizioni cancellate, uguale al messaggio" in text  # mcss, N >= B
    assert "**no**" not in text and "**messaggio sbagliato**" not in text  # senza attacchi
    assert examples.main(argv) == 1  # il file esiste già
    assert examples.main(["--ids", "x", "--out", str(tmp_path / "x.md")]) == 1
