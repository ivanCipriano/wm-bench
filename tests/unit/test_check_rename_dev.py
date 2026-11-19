"""``scripts/check_rename_dev.py``: import isolato di ``check_renaming`` dalla copia separata,
conteggi per cella, soglia dell'1% e log che non si sovrascrive (con un ``check_renaming`` finto:
la libreria del submodule, a4e6972, non ha ``rename_check``)."""

from __future__ import annotations

import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from tests.unit.test_inventory_scripts import _load

script = _load("check_rename_dev")

FAKE_CHECK = """
from dataclasses import dataclass, field


@dataclass
class RenameCheck:
    ok: bool
    violations: list = field(default_factory=list)
    n_unpaired_original: int = 0
    n_unpaired_marked: int = 0
    error: object = None


def check_renaming(original, marked):
    if "BUG" in marked:
        return RenameCheck(False, ["original f:x -> marked f:x, f:y"])
    return RenameCheck(True, n_unpaired_marked=marked.count("NEW"))
"""


def _fake_copy(root: Path) -> tuple[Path, str]:
    """Copia finta della libreria in un repository Git con un commit."""
    pkg = root / "multimark"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "rename_check.py").write_text(FAKE_CHECK, encoding="utf-8")
    env = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *env, "commit", "-q", "-m", "x"], check=True)
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()  # fmt: skip
    return root, commit


@dataclass
class Result:
    ok: bool
    violations: list[str] = field(default_factory=list)
    n_unpaired_original: int = 0
    n_unpaired_marked: int = 0
    error: str | None = None


def fake_check(original: str, marked: str) -> Result:
    if marked == "SYNTAX":
        return Result(False, error="SyntaxError")
    if "BUG" in marked:
        return Result(False, [f"v{i}" for i in range(7)], n_unpaired_original=1)
    return Result(True, n_unpaired_marked=1 if "NEW" in marked else 0)


def test_load_checker_is_isolated(tmp_path: Path) -> None:
    sentinel = SimpleNamespace(__file__="elsewhere")
    sys.modules["multimark"] = sentinel  # type: ignore[assignment]
    try:
        repo, commit = _fake_copy(tmp_path / "copy")
        check, got = script.load_checker(repo, commit[:7])
        assert got == commit
        assert check("a", "a BUG").ok is False and check("a", "NEW").n_unpaired_marked == 1
        assert sys.modules["multimark"] is sentinel  # ripristinato
        assert "multimark.rename_check" not in sys.modules
        assert str(repo.resolve()) not in sys.path
        with pytest.raises(RuntimeError, match="expected"):
            script.load_checker(repo, "0000000")
        with pytest.raises(RuntimeError, match="not found"):
            script.load_checker(tmp_path, "")
    finally:
        del sys.modules["multimark"]


def test_cell_stats() -> None:
    marked = pd.DataFrame({
        "sample_id": ["m1", "m2", "m3", "m4", "m5", "m6", "m7"],
        "parent_id": ["b1", "b2", "b3", "b4", "bx", "b6", "b7"],
        "problem_key": ["p1", "p2", "p3", "p4", "p5", "p6", "p7"],
        "code": ["ok", "BUG", "BUG", "NEW", "ok", "  ", "SYNTAX"],
    })  # fmt: skip
    baseline = pd.DataFrame({"sample_id": ["b1", "b2", "b3", "b4", "b6", "b7"],
                             "code": ["o", "o", "o", "o", "o", "o"]})  # fmt: skip
    exec_marked = {"m2": "FAILED", "m3": "PASSED"}
    exec_base = {"b2": "PASSED", "b3": "PASSED"}
    s = script.cell_stats(marked, baseline, exec_marked, exec_base, fake_check, n_cases=1)
    assert (s["samples"], s["checked"], s["skipped"], s["errors"]) == (7, 4, 3, 1)
    assert s["violations"] == 2 and s["rate"] == pytest.approx(0.5)
    assert s["unpaired"] == 3 and s["with_unpaired"] == 3
    assert s["fail_vs_base"] == 1  # m2 fallisce, il genitore passa; m3 passa
    assert [c["sample_id"] for c in s["cases"]] == ["m2"] and s["cases"][0]["fails_vs_base"]
    lines = script.cell_lines("B x", "model", "L1", s)
    assert "50.00%" in lines[0] and "fails tests while parent passes: yes" in lines[1]
    assert lines[-1].strip() == "... 2 more violations"  # 7 violazioni, 5 mostrate
    empty = script.cell_stats(marked.iloc[:0], baseline, {}, {}, fake_check)
    assert empty["samples"] == 0 and math.isnan(empty["rate"])


def test_summary_threshold() -> None:
    ok = script.summary_lines({"mcss x": [("a L1", 0.0), ("a L2", 0.01)]})
    assert "1.00% (a L2)  ok (<= 1%)" in ok[1] and "not redone" in ok[-2]
    above = script.summary_lines({"B y": [("a L1", 0.0101), ("a L2", 0.0)],
                                  "C z": [("a L1", 0.0)]})  # fmt: skip
    assert "ABOVE 1%" in above[1] and "ok (<= 1%)" in above[2]
    assert "at least one cell above 1%" in above[-2]
    missing = script.summary_lines({"D w": [("a L1", 0.0), ("a L2", math.nan)]})
    assert "INCOMPLETE" in missing[1] and "missing a L2" in missing[1]


def test_main_writes_the_log_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench.config import builder

    repo, commit = _fake_copy(tmp_path / "copy")
    cfg = SimpleNamespace(paths=SimpleNamespace(artifacts=tmp_path / "art"), models=["mA", "mB"])
    monkeypatch.setattr(builder, "load_experiment", lambda overrides: cfg)
    marked = pd.DataFrame({"sample_id": ["m1", "m2"], "parent_id": ["b1", "b2"],
                           "problem_key": ["p1", "p2"], "code": ["ok", "BUG"]})  # fmt: skip
    baseline = pd.DataFrame({"sample_id": ["b1", "b2"], "code": ["o", "o"]})

    def read_cell(store: Any, c: Any, model: str, level: str, lang: str, split: str) -> Any:
        if c.label == "D" and level == "L2":
            raise FileNotFoundError("no marked samples")
        return marked, baseline, {"m2": "FAILED"}, {"b2": "PASSED"}

    monkeypatch.setattr(script, "read_cell", read_cell)
    out = tmp_path / "log.txt"
    argv = ["--config", "multimark:cb79b2fcb4e6:B", "--config", "multimark:37400fdcd589:D",
            "--multimark", str(repo), "--expect-commit", commit[:7],
            "--report", str(out)]  # fmt: skip
    assert script.main(argv) == 0
    text = out.read_text(encoding="utf-8")
    assert f"at commit {commit}" in text and "submodule third_party/multimark not touched" in text
    assert text.count("B cb79b2fcb4e6") >= 4 and "50.00%" in text
    summary = text[text.index("## summary") :]
    assert "ABOVE 1%" in summary and "INCOMPLETE" in summary and "missing mA L2" in summary
    assert script.main(argv) == 1  # il log esiste già
    assert script.main([*argv[:-2], "--split", "test"]) == 1
    bad = [*argv[:4], "--multimark", str(repo), "--expect-commit", "0000000"]
    assert script.main(bad) == 1
