"""T1.1, T1.2 e ``ToolRunner`` (M10, ADR-012).

In locale girano ruff e black (sono in ``bench-core``) e i test del ``ToolRunner`` con un "tool"
Python finto: lotti, divisione a metà quando il lotto fallisce, timeout, layout Maven con un package
per file. I test dei tool non Python (google-java-format, OpenRewrite, clang, prettier, eslint) si
saltano se il tool non è installato (``scripts/setup_tools.sh``): girano sul cluster.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

from bench.attacks.base import AttackContext, make_attack
from bench.attacks.tools import ToolRunner, _flat, _maven_layout, _ToolSpec
from bench.config.builder import repo_root
from bench.domain.enums import AttackStatus, Language
from bench.lang.parsers import default_parsers
from tests.unit.test_attacks_t1 import SAMPLES


@pytest.fixture
def runner(tmp_path: Path) -> ToolRunner:
    return ToolRunner(repo_root() / "tools", tmp_path / "tmp", chunk_size=3)


def _ctxs(runner: ToolRunner, n: int) -> list[AttackContext]:
    return [
        AttackContext(rng=random.Random(i), problem=None, parsers=default_parsers(), tools=runner)
        for i in range(n)
    ]


def test_black_formats_python_and_reports_errors(runner: ToolRunner) -> None:
    codes = ["def f( x ):\n  return x+1\n", "x = 1\n", "def f(:\n"]
    out = make_attack("T1.1", {}).apply_many(codes, Language.PYTHON, _ctxs(runner, 3))
    assert out[0].status is AttackStatus.APPLIED
    assert out[0].code == "def f(x):\n    return x + 1\n"
    assert out[1].status is AttackStatus.UNCHANGED
    assert out[2].status is AttackStatus.TOOL_ERROR and out[2].code == codes[2]


def test_ruff_fixes_python_in_batches(runner: ToolRunner) -> None:
    codes = ["import os\n\n\ndef f(x):\n    return x\n"] * 4 + ["def f(x):\n    return x\n"]
    out = make_attack("T1.2", {}).apply_many(codes, Language.PYTHON, _ctxs(runner, len(codes)))
    assert [o.status for o in out[:4]] == [AttackStatus.APPLIED] * 4
    assert "import os" not in out[0].code  # F401 (correzione sicura)
    assert out[4].status is AttackStatus.UNCHANGED
    assert out[0].detail == {"tool": "ruff"}


def test_failing_batch_is_split_until_the_bad_file(runner: ToolRunner) -> None:
    script = (
        "import sys, pathlib\n"
        "files = [pathlib.Path(f) for f in sys.argv[1:]]\n"
        "if any('BAD' in f.read_text() for f in files): sys.exit(3)\n"
        "for f in files: f.write_text(f.read_text().upper())\n"
    )
    spec = _ToolSpec(
        "fake", _flat(".txt"), lambda root, files: [sys.executable, "-c", script, *map(str, files)]
    )
    results = runner._run_chunk(spec, ["a", "b", "BAD", "c", "d"])
    assert [r.code for r in results] == ["A", "B", None, "C", "D"]
    assert results[2].error is not None and "exit 3" in results[2].error


def test_error_keeps_diagnostics_printed_on_stdout(runner: ToolRunner) -> None:
    """Come clang-tidy: le diagnosi su stdout, il solo riepilogo su stderr."""
    script = (
        "import sys\n"
        "print('a.cpp:1:1: warning: something [readability-x]')\n"
        'print("f00000.cpp:3:5: error: variable length array of non-POD element type")\n'
        "sys.stderr.write('1 warning and 1 error generated.\\n'); sys.exit(1)\n"
    )
    spec = _ToolSpec("fake", _flat(".cpp"), lambda root, files: [sys.executable, "-c", script])
    (result,) = runner._run_chunk(spec, ["int main() {}"])
    assert result.code is None and result.error is not None
    assert result.error.startswith("exit 1: f00000.cpp:3:5: error: variable length array")
    assert "1 warning and 1 error generated." in result.error
    assert "warning: something" not in result.error


def test_tool_timeout_becomes_an_error(tmp_path: Path) -> None:
    runner = ToolRunner(repo_root() / "tools", tmp_path, timeout_s=0.5, per_file_s=0.0)
    spec = _ToolSpec(
        "slow",
        _flat(".txt"),
        lambda root, files: [sys.executable, "-c", "import time; time.sleep(5)"],
    )
    (result,) = runner._run_chunk(spec, ["a"])
    assert result.code is None and "timeout" in str(result.error)


def test_maven_layout_one_package_per_file(tmp_path: Path) -> None:
    layout = _maven_layout()
    original = "package foo.bar;\n\nimport java.util.*;\n\npublic class Main { }\n"
    path = layout.write(tmp_path, 7, original)
    assert path.relative_to(tmp_path).as_posix() == "src/main/java/p00007/Main.java"
    assert path.read_text(encoding="utf-8").startswith("package p00007;\n\nimport java.util.*;")
    assert layout.read(path, original) == original
    plain = "class Solution { }\n"
    path = layout.write(tmp_path, 8, plain)
    assert path.name == "Solution.java"
    assert layout.read(path, plain) == plain


EXTERNAL = [
    ("T1.1", "java", "google-java-format"),
    ("T1.1", "cpp", "clang-format"),
    ("T1.1", "javascript", "prettier"),
    ("T1.2", "java", "openrewrite"),
    ("T1.2", "cpp", "clang-tidy"),
    ("T1.2", "javascript", "eslint"),
]


@pytest.mark.parametrize(("attack_id", "language", "tool"), EXTERNAL)
def test_external_tools(runner: ToolRunner, attack_id: str, language: str, tool: str) -> None:
    if not runner.available(tool):
        pytest.skip(f"{tool} not installed (scripts/setup_tools.sh)")
    codes = [SAMPLES[language], SAMPLES[language] + "\n"]
    out = make_attack(attack_id, {}).apply_many(codes, Language(language), _ctxs(runner, 2))
    for o in out:
        assert o.status in (AttackStatus.APPLIED, AttackStatus.UNCHANGED), o.detail
        assert not default_parsers().has_error(o.code, Language(language))
    assert out[0].status is AttackStatus.APPLIED  # i campioni non sono nello stile del tool
    assert runner.version(tool)
