"""T4.1 minificazione e T4.2 bundling (M11, ADR-014 §4).

In locale gira la minificazione Python (python-minifier è in ``bench-core``); terser ed esbuild
(``tools/js``, con Node) si saltano se non sono installati: girano sul cluster.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from bench.attacks.base import AttackContext, grid_attacks, make_attack
from bench.attacks.t4.minify import npm_packages
from bench.attacks.tools import ToolRunner
from bench.config.builder import repo_root
from bench.domain.enums import AttackStatus, Language
from bench.lang.parsers import default_parsers

PY = '''from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if two numbers are closer than threshold. """
    for idx, elem in enumerate(numbers):
        for idx2, elem2 in enumerate(numbers):
            if idx != idx2:
                distance = abs(elem - elem2)
                if distance < threshold:
                    return True
    return False
'''
JS = """const sumSquares = (lst) => {
  // somma dei quadrati
  let total = 0;
  for (let i = 0; i < lst.length; i++) {
    total += lst[i] * lst[i];
  }
  return total;
};
"""


@pytest.fixture
def runner(tmp_path: Path) -> ToolRunner:
    return ToolRunner(repo_root() / "tools", tmp_path / "tmp", chunk_size=3)


def _ctxs(runner: ToolRunner, n: int) -> list[AttackContext]:
    return [
        AttackContext(rng=random.Random(i), problem=None, parsers=default_parsers(), tools=runner)
        for i in range(n)
    ]


def _entry(code: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(code)) if isinstance(n, ast.FunctionDef))


def test_grids() -> None:
    assert sorted(a.params["rename"] for a in grid_attacks("T4.1").values()) == [False, True]
    assert [a.params for a in grid_attacks("T4.2").values()] == [{}]


@pytest.mark.parametrize("rename", [False, True])
def test_python_minify_keeps_entry_point_and_parameters(runner: ToolRunner, rename: bool) -> None:
    attack = make_attack("T4.1", {"rename": rename})
    out = attack.apply_many([PY], Language.PYTHON, _ctxs(runner, 1))
    assert out[0].status is AttackStatus.APPLIED
    assert out[0].detail == {"tool": "python-minifier"}
    before, after = _entry(PY), _entry(out[0].code)
    assert after.name == "has_close_elements"
    assert [a.arg for a in after.args.args] == [a.arg for a in before.args.args]
    assert ast.get_docstring(after) is not None  # docstring conservata (default)
    assert ("distance" in out[0].code) is not rename  # locali rinominate solo con rename
    namespace: dict[str, object] = {}
    exec(compile(out[0].code, "<t4>", "exec"), namespace)  # codice del test
    fn = namespace["has_close_elements"]
    assert callable(fn)
    assert fn([1.0, 2.0, 3.9], 0.3) is False
    assert fn(numbers=[1.0, 1.1], threshold=0.3) is True  # parametri per nome


def test_python_minify_tool_error_keeps_code(runner: ToolRunner) -> None:
    bad = "def f(:\n"
    out = make_attack("T4.1", {"rename": True}).apply_many([bad], Language.PYTHON, _ctxs(runner, 1))
    assert out[0].status is AttackStatus.TOOL_ERROR and out[0].code == bad


@pytest.mark.parametrize(
    ("attack_id", "language"),
    [("T4.1", Language.JAVA), ("T4.1", Language.CPP), ("T4.2", Language.PYTHON)],
)
def test_not_applicable_languages(runner: ToolRunner, attack_id: str, language: Language) -> None:
    params = {"rename": False} if attack_id == "T4.1" else {}
    out = make_attack(attack_id, params).apply_many(["x"], language, _ctxs(runner, 1))
    assert out[0].status is AttackStatus.NOT_APPLICABLE and out[0].code == "x"


@pytest.mark.parametrize("rename", [False, True])
def test_terser_keeps_top_level_const_name(runner: ToolRunner, rename: bool) -> None:
    if not runner.available("terser"):
        pytest.skip("terser not installed (tools/js, node)")
    attack = make_attack("T4.1", {"rename": rename})
    out = attack.apply_many([JS, "const f = (;"], Language.JAVASCRIPT, _ctxs(runner, 2))
    assert out[0].status is AttackStatus.APPLIED
    assert "sumSquares" in out[0].code and "somma" not in out[0].code
    assert out[1].status is AttackStatus.TOOL_ERROR


def test_esbuild_bundle_keeps_function(runner: ToolRunner) -> None:
    if not runner.available("esbuild"):
        pytest.skip("esbuild not installed (tools/js, node)")
    out = make_attack("T4.2", {}).apply_many([JS, JS], Language.JAVASCRIPT, _ctxs(runner, 2))
    assert out[0].status is AttackStatus.APPLIED
    assert "sumSquares" in out[0].code  # niente tree-shaking
    assert out[0].code == out[1].code  # il nome del file nel lotto non entra nel bundle


def test_npm_packages_ignores_relative_node_and_builtin_modules() -> None:
    code = (
        "const md5 = require('js-md5');\nconst fs = require(\"fs\");\n"
        "const p = require('node:path');\nimport y from './a';\n"
        "import { a } from '@scope/pkg/sub';\nimport 'lodash/fp';\n"
        "const c = await import('chalk');\n"
    )
    assert npm_packages(code, frozenset({"fs", "path"})) == [
        "js-md5",
        "@scope/pkg",
        "lodash",
        "chalk",
    ]
