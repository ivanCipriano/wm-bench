"""Runner della sandbox (``containers/runner/wmb_runner.py``) eseguito sull'host.

I casi generici usano Python come "compilatore" ed "eseguibile" (portabile ovunque);
Java viene provato davvero solo se ``javac`` è disponibile.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from bench.domain.enums import ExecStatus, Language
from bench.execution import harness
from tests.conftest import REPO_ROOT
from tests.datafix import _jsonl


def _load_runner() -> ModuleType:
    path = REPO_ROOT / "containers" / "runner" / "wmb_runner.py"
    spec = importlib.util.spec_from_file_location("wmb_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()
PY = sys.executable


def _job(
    rule: str, compile_cmd: list[str] | None, run_cmd: list[str] | None, timeout: float = 20
) -> dict:  # type: ignore[type-arg]
    return {
        "rule": rule,
        "compile_cmd": compile_cmd,
        "run_cmd": run_cmd,
        "timeout_s": timeout,
        "compile_timeout_s": 30,
        "mem_mb": None,
        "stderr_tail_chars": 500,
    }


def _run(job: dict, program: str, tmp_path: Path) -> dict:  # type: ignore[type-arg]
    return runner.run_program(job, {"id": "0", "program": program}, tmp_path)  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("rule", "code", "out", "err", "expected"),
    [
        ("cpp", 0, "", "", ExecStatus.PASSED),
        ("cpp", 134, "", "a.out: test.cpp:5: Assertion `x' failed.", ExecStatus.FAILED),
        ("cpp", 139, "", "Segmentation fault", ExecStatus.RUNTIME_ERROR),
        ("java", 0, "", "", ExecStatus.PASSED),
        ("java", 1, "", "Exception in thread main java.lang.AssertionError", ExecStatus.FAILED),
        ("java", 1, "", "java.lang.NullPointerException", ExecStatus.RUNTIME_ERROR),
        ("javascript", 0, "", "", ExecStatus.PASSED),
        ("javascript", 0, "", "Assertion failed", ExecStatus.FAILED),
        ("javascript", 0, "debug output", "", ExecStatus.FAILED),
        ("javascript", 1, "", "SyntaxError: Unexpected token", ExecStatus.SYNTAX_ERROR),
        ("javascript", 1, "", "ReferenceError: x is not defined", ExecStatus.RUNTIME_ERROR),
        ("javascript", 1, "", "", ExecStatus.PASSED),  # stessa regola dell'harness
        ("cpp", None, "", "", ExecStatus.TIMEOUT),
    ],
)
def test_classify(rule: str, code: int | None, out: str, err: str, expected: ExecStatus) -> None:
    assert runner.classify(rule, code, out, err) == expected.value


def test_run_program_statuses(tmp_path: Path) -> None:
    js = _job("javascript", None, [PY, "test.js"])
    assert _run(js, "x = 1\n", tmp_path)["status"] == "PASSED"
    failed = _run(js, "import sys\nsys.stderr.write('Assertion failed')\n", tmp_path)
    assert failed["status"] == "FAILED" and "Assertion failed" in failed["stderr_tail"]
    printed = _run(js, "print('hello')\n", tmp_path)
    assert printed["status"] == "FAILED" and printed["stderr_tail"].startswith("stdout not empty")
    slow = _run(
        _job("javascript", None, [PY, "test.js"], timeout=0.5),
        "import time\ntime.sleep(10)\n",
        tmp_path,
    )
    assert slow["status"] == "TIMEOUT"
    broken = _job(
        "java",
        [PY, "-c", "import sys; sys.stderr.write('error: x'); sys.exit(1)"],
        [PY, "-c", "pass"],
    )
    result = _run(broken, "class Main {}", tmp_path)
    assert result["status"] == "COMPILE_ERROR" and "error: x" in result["stderr_tail"]
    syntax = _job("syntax_python", [PY, "-m", "py_compile", "main.py"], None)
    assert _run(syntax, "def f(:\n", tmp_path)["status"] == "SYNTAX_ERROR"
    assert _run(syntax, "def f():\n    return 1\n", tmp_path)["status"] == "PASSED"
    assert list(tmp_path.iterdir()) == []  # le cartelle dei campioni vengono rimosse


def test_main_writes_result_json(tmp_path: Path) -> None:
    job = _job("javascript", None, [PY, "test.js"])
    job.update(
        {
            "mode": "samples",
            "kind": "program",
            "samples": [
                {"id": "0", "program": "x = 1\n"},
                {"id": "1", "program": "print(2)\n"},
            ],
        }
    )
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert runner.main(["wmb_runner.py", str(tmp_path / "job.json")]) == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in result["results"]] == ["PASSED", "FAILED"]
    assert "python" in result["versions"]


@pytest.mark.skipif(shutil.which("javac") is None, reason="javac not available")
def test_real_java_canonical_and_wrong_answer(tmp_path: Path) -> None:
    rows = [r for r in _jsonl("humanevalpack.jsonl") if r["dir"] == "java"]
    job = _job("java", harness.COMPILE_CMD[Language.JAVA], harness.RUN_CMD[Language.JAVA])
    for row in rows[:2]:
        canonical = harness.check_program(
            row["prompt"] + row["canonical_solution"], row["test"], Language.JAVA
        )
        assert _run(job, canonical, tmp_path)["status"] == "PASSED", row["task_id"]
    # Java/0 (hasCloseElements) che restituisce sempre false: compila ma i test falliscono.
    wrong = harness.check_program(
        rows[0]["prompt"] + "        return false;\n    }\n}", rows[0]["test"], Language.JAVA
    )
    assert _run(job, wrong, tmp_path)["status"] == "FAILED"
