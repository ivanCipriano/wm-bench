"""T2.4 in k funzioni (ADR-014 §2.3): griglia, funzioni tenute, ``main`` e ``__init__`` come le
altre, decoratori e ``template`` tolti con la funzione, stati per campione."""

from __future__ import annotations

import random

import pytest

from bench.attacks.base import AttackContext, attack_grid, grid_attacks
from bench.attacks.t2.truncate_k import TruncateKAttack
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.lang.parsers import default_parsers

P = default_parsers()
CLASS = (
    "import math\n\n\nclass Shape:\n"
    "    def __init__(self, w):\n        self.w = w\n\n"
    "    @staticmethod\n    def unit():\n        return 1\n\n"
    "    def area(self):\n        return self.w * self.w\n\n"
    "    def side(self):\n        return math.sqrt(self.area())\n"
)
CPP = (
    "#include <cstdio>\nusing namespace std;\n\n"
    "template <typename T>\nT twice(T x) {\n    return 2 * x;\n}\n\n"
    "int helper(int a) {\n    return a + 1;\n}\n\n"
    'int main() {\n    printf("%d", helper(twice(1)));\n    return 0;\n}\n'
)


def _attack(k: int) -> TruncateKAttack:
    (attack,) = (a for a in grid_attacks("T2.4k").values() if a.params["k"] == k)
    assert isinstance(attack, TruncateKAttack)
    return attack


def _ctx(seed: int = 0) -> AttackContext:
    return AttackContext(rng=random.Random(seed), problem=None, parsers=P)


def test_grid() -> None:
    assert sorted(p["k"] for p in attack_grid("T2.4k").points) == list(range(1, 11))
    with pytest.raises(ConfigError):
        TruncateKAttack({"k": 0})


@pytest.mark.parametrize("seed", range(5))
def test_classeval_keeps_k_methods(seed: int) -> None:
    out = _attack(2).safe_apply(CLASS, Language.PYTHON, _ctx(seed))
    assert out.status is AttackStatus.APPLIED
    assert out.detail["n"] == 4 and out.detail["removed"] == 2 and out.detail["parses"]
    kept = out.detail["kept"]
    assert len(kept) == 2 and set(kept) <= {"__init__", "unit", "area", "side"}
    for name in {"__init__", "unit", "area", "side"} - set(kept):
        assert f"def {name}(" not in out.code
    assert "class Shape:" in out.code and "import math" in out.code
    assert ("@staticmethod" in out.code) == ("unit" in kept)  # il decoratore va via con la funzione


def test_cpp_main_and_template_count_like_the_others() -> None:
    out = _attack(1).safe_apply(CPP, Language.CPP, _ctx(3))
    assert out.detail["n"] == 3 and len(out.detail["kept"]) == 1
    if "twice" not in out.detail["kept"]:
        assert "template" not in out.code
    assert "#include <cstdio>" in out.code


def test_states() -> None:
    whole = _attack(10).safe_apply(CLASS, Language.PYTHON, _ctx())
    assert whole.status is AttackStatus.UNCHANGED and whole.code == CLASS
    single = _attack(1).safe_apply("def f():\n    return 1\n", Language.PYTHON, _ctx())
    assert single.status is AttackStatus.NOT_APPLICABLE
    assert single.detail["reason"] == "single_function"
    empty = _attack(1).safe_apply("", Language.PYTHON, _ctx())
    assert empty.status is AttackStatus.NOT_APPLICABLE
