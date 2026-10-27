"""T2.1 refactoring a regole (SPEC §12, §21.3; ADR-014, D35): griglia, definizione di ``b``,
catalogo AST con le sue restrizioni, regole Ruff, sovrapposizione con ACW, determinismo e
conservazione della semantica (Python, eseguito prima e dopo l'attacco)."""

# ruff: noqa: E501  (programmi di prova su una riga)
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from bench.attacks.base import AttackContext, attack_grid, grid_attacks
from bench.attacks.t2.refactor import RefactorAttack, apply_ast_type, ast_applicable
from bench.attacks.tools import ToolRunner
from bench.domain.enums import AttackStatus, Language
from bench.lang.parsers import default_parsers

PY, JAVA, CPP, JS = Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT
P = default_parsers()


def _attack(b: object) -> RefactorAttack:
    (attack,) = (a for a in grid_attacks("T2.1").values() if a.params["b"] == b)
    assert isinstance(attack, RefactorAttack)
    return attack


def _ctx(tmp_path: Path, seed: int = 0) -> AttackContext:
    tools = ToolRunner(Path("tools"), tmp_path)
    return AttackContext(rng=random.Random(seed), problem=None, parsers=P, tools=tools)


def _apply(attack: RefactorAttack, code: str, lang: Language, tmp_path: Path, seed: int = 0) -> Any:
    return attack.apply_many([code], lang, [_ctx(tmp_path, seed)])[0]


def test_grid_and_ruff_rules() -> None:
    grid = attack_grid("T2.1")
    assert sorted(str(p["b"]) for p in grid.points) == ["1", "2", "3", "all"]
    attack = _attack("all")
    assert attack.params["ruff_version"] == "0.7.4"
    assert "PERF401" in attack.ruff_codes() and "SIM108" in attack.ruff_codes()
    assert not attack.requires_gpu and attack.needs_tools


def test_t2_1_runs_on_cpu() -> None:
    from bench.config.builder import load_experiment
    from bench.config.resources import ResourceClass, ResourcePolicy

    cfg = load_experiment(["stage=attack"])
    policy = ResourcePolicy(cfg.resources, {}, cfg.slurm)
    assert policy.resource_for("attack", attack="T2.1") is ResourceClass.CPU
    assert policy.resource_for("attack", attack="T2.2") is ResourceClass.GPU_NVIDIA
    assert policy.resource_for("attack", attack="T3.1") is ResourceClass.GPU_NVIDIA


@pytest.mark.parametrize(
    ("lang", "code", "type_name", "expected"),
    [
        (JAVA, "class S{void f(){ while (a < b) { b--; } }}", "while_to_for", "for (; a < b; )"),
        (CPP, "void f(){ if (x) y(); else z(); }", "if_else_inversion",
         "if (!(x)) { z(); } else { y(); }"),
        (JS, "function f(){ x += g(); }", "compound_assignment", "x = x + (g());"),
        (PY, "def f(a):\n    return a * 2\n", "commutative_swap", "return 2 * a"),
        (JAVA, "class S{boolean f(int a,int b){ return a <= b; }}", "comparison_flip", "b >= a"),
    ],
)  # fmt: skip
def test_ast_rules(lang: Language, code: str, type_name: str, expected: str) -> None:
    result = apply_ast_type(code, lang, type_name, P, None)
    assert result is not None
    new, points, _ = result
    assert points == 1 and expected in new


@pytest.mark.parametrize(
    ("lang", "code", "absent"),
    [
        (PY, "def f(n):\n    for i in range(n):\n        if i:\n            continue\n", "for_to_while"),
        (PY, "def f(n):\n    for i in range(n):\n        pass\n    return i\n", "for_to_while"),
        (PY, "def f(a):\n    if a:\n        x = 1\n    elif a:\n        x = 2\n    else:\n        x = 3\n",
         "if_else_inversion"),
        (PY, "def f(l):\n    l += [1]\n    l *= 2\n    l |= {1}\n", "compound_assignment"),
        (JAVA, "class S{short b; void f(){ short c = 1; c += 1; int k = 0; k += 1.5; b += 1; }}",
         "compound_assignment"),
        (JAVA, "class S{void f(int n){ for(int i=0;i<n;i++){ if(i==1) continue; } }}", "for_to_while"),
        (CPP, "void f(int n){ if (int y = n) {} else {} }", "if_else_inversion"),
        (JS, "function f(n){ for(let i=0;i<n;i++){ g(() => i); } }", "for_to_while"),
        (JS, "function f(){ x ??= 1; }", "compound_assignment"),
        (PY, "def f(a):\n    return g(a) * 2\n", "commutative_swap"),
    ],
)  # fmt: skip
def test_restrictions(lang: Language, code: str, absent: str) -> None:
    assert absent not in ast_applicable(code, lang, P)


def test_b_and_unchanged(tmp_path: Path) -> None:
    code = "class S{int f(int a,int n){ while (a < n) { a += 1; } if (a == n) return 1; else return 2; }}"
    one = _apply(_attack(1), code, JAVA, tmp_path)
    assert one.status is AttackStatus.APPLIED and one.detail["n_types"] == 1
    assert one.detail["n_types_applicable"] == len(ast_applicable(code, JAVA, P))
    full = _apply(_attack("all"), code, JAVA, tmp_path)
    assert full.detail["n_types"] == full.detail["n_types_applicable"] >= 4
    assert _apply(_attack(1), code, JAVA, tmp_path) == one  # determinismo
    nothing = _apply(_attack("all"), "class S{void f(){ g(); }}", JAVA, tmp_path)
    assert nothing.status is AttackStatus.UNCHANGED and nothing.detail["n_types"] == 0
    empty = _attack(2).apply_many([""], JAVA, [_ctx(tmp_path)])[0]
    assert empty.status is AttackStatus.UNCHANGED


def test_python_ruff_types_and_acw_overlap(tmp_path: Path) -> None:
    code = (
        "def f(xs, n):\n    out = []\n    for x in xs:\n        out.append(x * 2)\n"
        "    if n < 3:\n        return out\n    else:\n        return []\n"
    )
    out = _apply(_attack("all"), code, PY, tmp_path)
    assert out.status is AttackStatus.APPLIED
    applied = out.detail["applied"]
    assert "ruff:list-comprehension" in applied and "ruff:remove-unnecessary-else" in applied
    assert out.detail["acw_equivalent"]["ruff:list-comprehension"] == [4, 29]
    assert {1, 4, 29, 37, 38} <= set(out.detail["acw_equivalent_rules"])


PROGRAMS = [
    "def f(xs, n):\n    total = 0\n    for i in range(0, len(xs), 2):\n        if xs[i] > n:\n"
    "            total += 1\n        else:\n            total -= 1\n    return total\n",
    "def f(xs, n):\n    out = []\n    for x in xs:\n        if x != n:\n            out.append(x * 3)\n"
    "    return out\n",
    "def f(xs, n):\n    for x in xs:\n        if x > n:\n            return True\n    return False\n",
    "def f(xs, n):\n    best = None\n    for i in range(len(xs) - 1, -1, -1):\n"
    "        if best is None or xs[i] < best:\n            best = xs[i]\n    return best\n",
    "def f(xs, n):\n    s = 1\n    for i in range(1, n):\n        s **= 1\n        s //= 1\n"
    "        s = s + i if i % 2 == 0 else s\n    return s\n",
]


@pytest.mark.parametrize("program", PROGRAMS)
def test_python_semantics_preserved(program: str, tmp_path: Path) -> None:
    attacked = _apply(_attack("all"), program, PY, tmp_path).code
    rng = random.Random(7)
    env_a: dict[str, Any] = {}
    env_b: dict[str, Any] = {}
    exec(program, env_a)
    exec(attacked, env_b)
    for _ in range(50):
        xs = [rng.randint(-5, 5) for _ in range(rng.randint(0, 8))]
        n = rng.randint(-3, 6)
        assert env_a["f"](list(xs), n) == env_b["f"](list(xs), n), attacked


CPP_STRING = (
    "#include <string>\nusing namespace std;\n"
    "string f(string s, int k, long long w) {\n"
    '    string r = ""; std::string t; auto c = 1; int a = 0; double d = 0; int *p = &a;\n'
    "    for (char &x : s) { r += 'a' + k; t += x; c += 1; a += k; d *= 2; w -= 1; x += 1; p += 1; }\n"
    "    return r;\n}\n"
)


def test_cpp_compound_assignment_arithmetic_only() -> None:
    """Catalogo C++ versione 2: solo variabili di tipo aritmetico (humaneval/89 e /11 di STONE:
    ``r += 'a' + k`` su ``std::string`` espanso non compila)."""
    result = apply_ast_type(CPP_STRING, CPP, "compound_assignment", P, None, version=2)
    assert result is not None
    new, points, _ = result
    assert points == 3  # a, d, w
    assert "a = a + (k)" in new and "d = d * (2)" in new and "w = w - (1)" in new
    assert "r += 'a' + k" in new and "t += x" in new and "c += 1" in new and "x += 1" in new
    assert "p += 1" in new
    old = apply_ast_type(CPP_STRING, CPP, "compound_assignment", P, None, version=1)
    assert old is not None and old[1] == 8  # versione 1: tutte (comportamento del primo lancio)


def test_catalog_version_changes_only_cpp_hash() -> None:
    attack = _attack("all")
    assert attack.catalog_version(CPP) == 2 and attack.catalog_version(PY) == 1
    hashes = {lang: attack.params_hash(lang) for lang in (PY, JAVA, CPP, JS)}
    assert hashes[PY] == hashes[JAVA] == hashes[JS] == attack.params_hash()
    assert hashes[CPP] != hashes[PY]
    assert attack.params_hash(CPP) in grid_attacks("T2.1", language=CPP)
    assert attack.params_hash(CPP) not in grid_attacks("T2.1", language=PY)


def test_runs_with_cpp_catalog_version(tmp_path: Path) -> None:
    out = _apply(_attack("all"), CPP_STRING, CPP, tmp_path)
    assert out.status is AttackStatus.APPLIED
    assert "r += 'a' + k" in out.code and out.detail["points"]["compound_assignment"] == 3
