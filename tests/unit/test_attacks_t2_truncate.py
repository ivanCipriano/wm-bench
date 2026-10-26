"""T2.4 troncamento (SPEC §12; ADR-014 §2): conteggio delle istruzioni, arrotondamento, corpo
bersaglio (funzione d'ingresso, ``main``, file), stati per campione."""

# ruff: noqa: E501  (programmi di prova su una riga)
from __future__ import annotations

import random

import pytest

from bench.attacks.base import AttackContext, attack_grid, grid_attacks
from bench.attacks.t2.truncate import TruncateAttack
from bench.domain.enums import AttackStatus, Language
from bench.domain.models import Problem
from bench.lang.parsers import default_parsers

PY, JAVA, CPP, JS = Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT
P = default_parsers()


def _attack(p: float) -> TruncateAttack:
    (attack,) = (a for a in grid_attacks("T2.4").values() if a.params["p"] == p)
    assert isinstance(attack, TruncateAttack)
    return attack


def _ctx(entry: str | None) -> AttackContext:
    problem = Problem(
        problem_key="x/1", dataset="humanevalplus" if entry else "codenet", level="L1",
        language="python", split="test", prompt_text="", entry_point=entry,
        canonical_solution=None, test_ref=None, contamination_risk=False, loc_to_generate=None,
    )  # fmt: skip
    return AttackContext(rng=random.Random(0), problem=problem, parsers=P)


def test_grid_and_rounding() -> None:
    assert sorted(p["p"] for p in attack_grid("T2.4").points) == [0.5, 0.75]
    half, three_q = _attack(0.5), _attack(0.75)
    assert [half.keep(n) for n in (1, 2, 3, 4, 5)] == [1, 1, 1, 2, 2]
    assert [three_q.keep(n) for n in (1, 2, 3, 4, 5)] == [1, 1, 2, 3, 3]
    assert not three_q.requires_gpu


@pytest.mark.parametrize(
    ("lang", "code", "kept", "gone"),
    [
        (PY, 'def f(a):\n    """doc"""\n    s = 0\n    # c\n    s += a\n    s *= 2\n    return s\n',
         ['"""doc"""', "s = 0", "s += a"], ["s *= 2", "return s"]),
        (JAVA, "class S {\n  int f(int a) {\n    int s = 0;\n    s += a;\n    s *= 2;\n    return s;\n  }\n}\n",
         ["s += a;", "  }\n}"], ["s *= 2", "return s"]),
        (CPP, "int f(int a) {\n    int s = 0;\n    s += a;\n    s *= 2;\n    return s;\n}\n",
         ["s += a;\n}"], ["return s"]),
        (JS, "const f = (a) => {\n  let s = 0;\n  s += a;\n  s *= 2;\n  return s;\n};\n",
         ["s += a;\n};"], ["return s"]),
    ],
)  # fmt: skip
def test_entry_body_prefix(lang: Language, code: str, kept: list[str], gone: list[str]) -> None:
    out = _attack(0.5).safe_apply(code, lang, _ctx("f"))
    assert out.status is AttackStatus.APPLIED
    assert out.detail["n"] == 4 and out.detail["kept"] == 2 and out.detail["target"] == "entry"
    assert all(k in out.code for k in kept) and not any(g in out.code for g in gone)
    assert out.detail["parses"]


def test_codenet_main_and_top_level() -> None:
    cpp = "#include <iostream>\nint main() {\n    int n;\n    std::cin >> n;\n    n *= 2;\n    std::cout << n;\n    return 0;\n}\n"
    out = _attack(0.5).safe_apply(cpp, CPP, _ctx(None))
    assert out.detail["target"] == "main" and out.detail["n"] == 5 and out.detail["kept"] == 2
    script = "import sys\ndef g(x):\n    return x\nn = int(sys.stdin.readline())\nm = g(n)\nprint(m)\nprint(m + 1)\n"
    out = _attack(0.5).safe_apply(script, PY, _ctx(None))
    assert out.detail["target"] == "top_level" and out.detail["n"] == 4
    assert "import sys" in out.code and "def g" in out.code and "print" not in out.code


def test_sample_level_states() -> None:
    attack = _attack(0.5)
    one = attack.safe_apply("def f(a):\n    return a\n", PY, _ctx("f"))
    assert one.status is AttackStatus.UNCHANGED and one.detail["n"] == 1
    missing = attack.safe_apply("def g(a):\n    return a\n", PY, _ctx("f"))
    assert missing.status is AttackStatus.NOT_APPLICABLE
    assert missing.detail["reason"] == "entry_not_found" and missing.code.startswith("def g")
    empty = attack.safe_apply("", PY, _ctx("f"))
    assert (
        empty.status is AttackStatus.NOT_APPLICABLE
        and empty.detail["reason"] == "extraction_failed"
    )
    no_main = attack.safe_apply("int g() { return 1; }\n", CPP, _ctx(None))
    assert no_main.detail["reason"] == "main_not_found"
    arrow = attack.safe_apply("const f = (a) => a + 1;\n", JS, _ctx("f"))
    assert arrow.status is AttackStatus.UNCHANGED
