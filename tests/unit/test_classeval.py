"""ClassEval (M13, ADR-016): loader sui dati del submodule, prompt, executor e script dei test."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.codenet_exclusions import load_excluded
from bench.data.loaders.classeval import KEY_PREFIX, ClassEvalLoader
from bench.domain.enums import Language, Level
from bench.execution.executors import (
    CLASSEVAL_CHECK,
    EXECUTORS,
    ClassEvalExecutor,
    SampleToRun,
    add_static_statement,
    classeval_program,
    executor_name,
)
from bench.execution.sandbox import LocalSandbox
from bench.generation.prompt_builder import PromptBuilder
from tests.conftest import REPO_ROOT

PIPELINE = REPO_ROOT / "third_party" / "ClassEval" / "classeval_evaluation" / "test_pipeline.py"


@pytest.fixture
def loader(cfg: ExperimentConfig) -> ClassEvalLoader:
    return ClassEvalLoader(cfg.datasets["classeval"])


def test_loader_has_the_94_classes_without_the_excluded_ones(loader: ClassEvalLoader) -> None:
    problems = loader.load_problems(Language.PYTHON)
    excluded = load_excluded(loader.spec.file("excluded"), prefix=KEY_PREFIX)
    assert len(loader.all_rows) == 100 and len(problems) == 94 and len(excluded) == 6
    assert not {p.problem_key for p in problems} & set(excluded)
    rows = {f"classeval/{r['task_id']}": r for r in loader.all_rows}
    for p in problems:
        row = rows[p.problem_key]
        assert p.level is Level.L3 and p.split is None and not p.contamination_risk
        assert p.prompt_text == row["skeleton"] and p.entry_point == row["class_name"]
        assert p.test_ref == row["task_id"] and p.canonical_solution == row["solution_code"]
        assert p.loc_to_generate is not None and p.loc_to_generate >= 0
    negatives = loader.load_human_negatives(Language.PYTHON)
    assert [n.problem_key for n in negatives] == [p.problem_key for p in problems]
    assert all(n.code == p.canonical_solution for n, p in zip(negatives, problems, strict=True))
    assert "Words2NumbersTestMain" in loader.tests()["ClassEval_97"]["test_classes"]
    assert all(c == c.strip() for t in loader.tests().values() for c in t["test_classes"])


def test_prompt_is_the_skeleton_in_the_template(
    cfg: ExperimentConfig, loader: ClassEvalLoader
) -> None:
    problem = loader.load_problems(Language.PYTHON)[0]
    text = PromptBuilder.from_config(cfg.prompt).render_user(problem)
    assert text.startswith(f"Complete the class `{problem.entry_point}` in the following Python")
    assert "```python\n" + problem.prompt_text in text and text.endswith("```")


def _original_add_static_statement() -> Any:
    """``AutoTest.add_static_statement`` dal sorgente del submodule (senza importarlo)."""
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AutoTest")
    wanted = {"get_leading_spaces", "add_static_statement"}
    module = ast.Module(
        body=[ast.ClassDef(name="T", bases=[], keywords=[], decorator_list=[],
                           body=[f for f in cls.body
                                 if isinstance(f, ast.FunctionDef) and f.name in wanted])],
        type_ignores=[],
    )  # fmt: skip
    namespace: dict[str, Any] = {}
    exec(compile(ast.fix_missing_locations(module), str(PIPELINE), "exec"), namespace)
    return namespace["T"]()


@pytest.mark.parametrize(
    "code",
    [
        "class A:\n    def f(x):\n        return x\n    def g(self):\n        pass\n",
        "class A:\n    @staticmethod\n    def f(x):\n        return x\n",
        "class A:\n    @classmethod\n    def f(cls, x):\n        return x\n",
        "def top(x):\n    return x\nclass B:\n        def deep(x):\n            pass\n",
    ],
)
def test_add_static_statement_matches_classeval(code: str) -> None:
    assert add_static_statement(code) == _original_add_static_statement().add_static_statement(code)


def _check(tmp_path: Path, program: str, classes: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "main.py").write_text(program, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CLASSEVAL_CHECK), "main", classes, "5"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip


TESTS = (
    "import unittest\n"
    "class ATestF(unittest.TestCase):\n"
    "    def test_f(self):\n        self.assertEqual(A().f(2), 4)\n"
    "class ATestG(unittest.TestCase):\n"
    "    def test_g(self):\n        self.assertEqual(A.g(), 1)\n"
)


@pytest.mark.parametrize(
    ("code", "classes", "returncode", "assertion"),
    [
        ("class A:\n    def f(self, x):\n        return 2 * x\n    def g():\n        return 1\n",
         "ATestF,ATestG", 0, False),
        ("class A:\n    def f(self, x):\n        return x\n    def g():\n        return 1\n",
         "ATestF,ATestG", 1, True),
        ("class A:\n    def f(self, x):\n        return 2 * x\n", "ATestF,ATestMissing", 1, True),
        ("import not_a_module\n", "ATestF", 1, False),
    ],
)  # fmt: skip
def test_classeval_check_statuses(
    tmp_path: Path, code: str, classes: str, returncode: int, assertion: bool
) -> None:
    program = classeval_program(code, ["import math"], TESTS)
    result = _check(tmp_path, program, classes)
    assert result.returncode == returncode
    assert ("Assertion" in result.stderr) is assertion


def test_classeval_executor_job(
    cfg: ExperimentConfig, loader: ClassEvalLoader, tmp_path: Path
) -> None:
    assert EXECUTORS.get(executor_name("classeval")) is ClassEvalExecutor
    executor = ClassEvalExecutor(cfg, LocalSandbox(), Language.PYTHON, tmp_path)
    problem = next(p for p in loader.load_problems(Language.PYTHON) if p.test_ref == "ClassEval_0")
    entry = loader.tests()["ClassEval_0"]
    job, files = executor.job(problem, [SampleToRun("a", problem.problem_key, 0, "class X: pass")])
    assert job["rule"] == "syntax_python"
    assert job["compile_cmd"] == ["python3", "-m", "py_compile", "main.py"]
    assert job["run_cmd"] == ["python3", "../classeval_check.py", "main",
                              ",".join(entry["test_classes"]), "5"]  # fmt: skip
    assert job["timeout_s"] == 5 * len(entry["test_classes"]) + 10
    program = job["samples"][0]["program"]
    assert program == "\n".join(entry["import_statement"]) + "\nclass X: pass\n" + entry["test"]
    assert files == {"classeval_check.py": CLASSEVAL_CHECK.read_bytes()}
    assert executor.canonical(problem) == problem.canonical_solution
    with pytest.raises(Exception, match="Python only"):
        ClassEvalExecutor(cfg, LocalSandbox(), Language.JAVA, tmp_path)


def test_canonical_of_a_stdlib_class_passes(loader: ClassEvalLoader, tmp_path: Path) -> None:
    problem = next(p for p in loader.load_problems(Language.PYTHON) if p.test_ref == "ClassEval_0")
    entry = loader.tests()["ClassEval_0"]
    program = classeval_program(
        str(problem.canonical_solution), entry["import_statement"], entry["test"]
    )
    result = _check(tmp_path, program, ",".join(entry["test_classes"]))
    assert result.returncode == 0, result.stderr[-500:]


def test_test_classes_without_tests_are_dropped(loader: ClassEvalLoader) -> None:
    """ClassEval_69: ``TestPDFHandler`` ha solo ``setUpClass``/``tearDownClass`` (ADR-016)."""
    listed = next(r for r in loader.all_rows if r["task_id"] == "ClassEval_69")["test_classes"]
    kept = loader.tests()["ClassEval_69"]["test_classes"]
    assert "TestPDFHandler" in listed and "TestPDFHandler" not in kept and len(kept) == 3
    tests = loader.tests()
    listed_by_task = {r["task_id"]: [c.strip() for c in r["test_classes"]] for r in loader.all_rows}
    changed = [t for t, names in listed_by_task.items() if names != tests[t]["test_classes"]]
    assert changed == ["ClassEval_69"]


def test_pass1_exclusions_filter_only_executions(cfg: ExperimentConfig) -> None:
    """D41: 17, 48 e 58 fuori da Pass@1 e ΔPass@1 (le esecuzioni lette da ``metrics``)."""
    import pandas as pd

    from bench.pipeline.facade import BenchmarkFacade
    from bench.pipeline.stage import Cell, StageContext
    from bench.pipeline.stages.metrics import MetricsStage
    from bench.store.refs import ArtifactRef

    stage = MetricsStage(cfg)
    assert set(stage.pass1_excluded()) == {
        "classeval/ClassEval_17", "classeval/ClassEval_48", "classeval/ClassEval_58",
    }  # fmt: skip
    facade = BenchmarkFacade(cfg)
    ref = ArtifactRef.of("executions", "executions/x.parquet")
    table = pd.DataFrame({"problem_key": ["classeval/ClassEval_17", "classeval/ClassEval_0"],
                          "status": ["FAILED", "PASSED"]})  # fmt: skip
    ctx = StageContext(facade.cfg, facade.store, facade.provenance, "test", Cell())
    manifest = ctx.make_manifest(ref, n_rows_in=None, n_rows_out=None, n_rows_expected=None)
    facade.store.write_table(ref, table, manifest)
    assert stage._read_exec(ctx, ref)["problem_key"].tolist() == ["classeval/ClassEval_0"]
