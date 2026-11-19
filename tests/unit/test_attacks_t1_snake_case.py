"""T1.6 nomi in ``snake_case`` (ADR-018): conversione, collisioni, candidati, esiti, semantica.

Si verifica anche che l'aggiunta non cambi nulla degli attacchi esistenti: gli
``attack_params_hash`` di tutte le altre griglie sono quelli di prima (le celle già calcolate
si ritrovano con le stesse chiavi; per esempio quelli di S nel log ``P2_round3.txt``).
"""

from __future__ import annotations

import contextlib
import io
import random
import sys
from typing import Any

import pytest

from bench.attacks.base import AttackContext, attack_grid, grid_attacks, make_attack
from bench.attacks.t1.snake_case import is_all_caps, norm, snake_name
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.lang.parsers import default_parsers
from tests.unit.test_inventory_scripts import LIBRARY, needs_library

# attack_params_hash su Python delle griglie già presenti prima di T1.6 (non devono cambiare)
EXISTING_HASHES = {
    "T1.1": ["64c5926a3776"],
    "T1.2": ["099e4625be84"],
    "T1.3": ["9c5c9c818076", "cf94035eb2cc"],
    "T1.4": ["0ad38d8e9413", "0e5ca5d90163", "23f6be2801c3", "2980f994225b", "2a4beca18363",
             "90a2b392574c", "a6134274dc08", "e2770ff72bae"],  # fmt: skip
    "T1.5": ["22e881a50348", "705d07d343b2", "cf35cfccc99a"],
    "T2.1": ["3abebad80f94", "7c2ef655fc0c", "ccf8c478420f", "f39d410e0723"],
    "T2.2": ["1d0f993b3c76", "4cbc6372efdb"],
    "T2.4": ["214ddb2cb794", "3ce510a0199e"],
    "T2.4k": ["1504a65ed908", "1bca60e72e7d", "30c93a7dcf56", "4507c4ca4936", "5fe2d9e7cffc",
              "8719411a1283", "ab2b269ef1e9", "bb568508cfd6", "caddd8b29336", "ea8e987332ea"],
    "T3.1": ["0b0f5086dab1"],
    "T4.1": ["47969295f471", "fe396d74371a"],
    "T4.2": ["4e309c07bb95"],
}  # fmt: skip

NAMES = [
    "totalCount", "TotalCount", "TOTAL_COUNT", "total_count", "HTTPResponse", "item2Count",
    "x", "N", "MOD", "maxN", "getHTTPResponseCode", "a1B2", "x__y", "ABC123def", "IOError2",
    "camelCaseXML", "snake_with_Mixed", "A", "aB", "already_snake_2",
]  # fmt: skip

PROGRAM = """MOD = 10**9 + 7
maxVal = 3
N = 5
n = N + 1


class Box:
    sizeX = maxVal

    def area(self, sideLen):
        return sideLen * self.sizeX


def solve(itemCount, MaxVal=2):
    totalSum = 0
    TotalSum = 1
    for idx in range(itemCount):
        totalSum += idx * maxVal + MOD
    f = lambda xY: xY + 1
    def inner():
        return totalSum + N
    return totalSum + TotalSum + inner() + f(MaxVal)


print(solve(N), [aB for aB in range(n)], Box().area(N), Box.sizeX)
"""

CONVERT = """mod = 10**9 + 7
max_val = 3
n_1 = 5
n = n_1 + 1


class Box:
    sizeX = max_val

    def area(self, side_len):
        return side_len * self.sizeX


def solve(item_count, max_val_1=2):
    total_sum = 0
    total_sum_1 = 1
    for idx in range(item_count):
        total_sum += idx * max_val + mod
    f = lambda x_y: x_y + 1
    def inner():
        return total_sum + n_1
    return total_sum + total_sum_1 + inner() + f(max_val_1)


print(solve(n_1), [aB for aB in range(n)], Box().area(n_1), Box.sizeX)
"""


def _ctx(seed: int = 0, entry: str | None = None) -> AttackContext:
    problem = None
    if entry is not None:
        problem = Problem(
            problem_key="humaneval/0", dataset="humanevalplus", level="L1", language="python",
            split="dev", prompt_text="", entry_point=entry, canonical_solution=None,
            test_ref=None, contamination_risk=False, loc_to_generate=None,
        )  # fmt: skip
    return AttackContext(rng=random.Random(seed), problem=problem, parsers=default_parsers())


def _apply(code: str, caps: str = "convert", **kw: Any) -> Any:
    return make_attack("T1.6", {"caps": caps}).safe_apply(code, Language.PYTHON, _ctx(**kw))


def _run(code: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<t16>", "exec"), {"__name__": "__main__"})
    return out.getvalue()


# ---------------------------------------------------------------------- conversione
@needs_library
def test_norm_matches_the_library() -> None:
    """``norm`` è ``multimark.names.normalize_name`` (§3.6.2), sugli stessi casi."""
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark.names import normalize_name
    finally:
        sys.path.remove(str(LIBRARY))
    assert [norm(n) for n in NAMES] == [normalize_name(n) for n in NAMES]


def test_norm_and_snake_name() -> None:
    assert norm("totalCount") == norm("TotalCount") == norm("TOTAL_COUNT") == "total_count"
    assert norm("HTTPResponse") == "http_response"
    assert snake_name("_tmpVal") == "_tmp_val"
    assert snake_name("__privVal") == "__priv_val"
    assert snake_name("list_") == "list_"
    assert snake_name("fooBar_") == "foo_bar_"
    assert snake_name("_") == "_"
    assert snake_name("già") == "già"  # caratteri fuori da [A-Za-z0-9_]: invariato
    assert snake_name("already_snake") == "already_snake"
    assert snake_name("x__y") == "x_y"


def test_all_caps() -> None:
    assert all(is_all_caps(n) for n in ("MOD", "N", "MAX_N", "INF2", "_PRIVATE"))
    assert not any(is_all_caps(n) for n in ("maxN", "Mod", "n", "_1", "X_y"))


# ---------------------------------------------------------------------- rinomine
def test_convert_renames_locals_parameters_and_module_names() -> None:
    out = _apply(PROGRAM)
    assert out.status is AttackStatus.APPLIED
    assert out.code == CONVERT
    assert out.detail == {
        "n_candidates": 12, "n_module": 4, "n_renamed": 9, "n_module_renamed": 3,
        "n_collisions": 3, "n_caps_kept": 0, "n_occurrences": 24,
    }  # fmt: skip
    assert _run(out.code) == _run(PROGRAM)


def test_keep_leaves_module_constants_in_all_caps() -> None:
    out = _apply(PROGRAM, caps="keep")
    assert out.status is AttackStatus.APPLIED
    lines = out.code.splitlines()
    assert lines[:4] == ["MOD = 10**9 + 7", "max_val = 3", "N = 5", "n = N + 1"]
    assert "total_sum + N" in out.code and "idx * max_val + MOD" in out.code
    assert out.detail["n_caps_kept"] == 2 and out.detail["n_module_renamed"] == 1
    assert _run(out.code) == _run(PROGRAM)
    # dentro le funzioni ALL_CAPS si converte comunque
    inside = _apply("def f(K):\n    LIMIT_X = K + 1\n    return LIMIT_X\n", caps="keep")
    assert inside.code == "def f(k):\n    limit_x = k + 1\n    return limit_x\n"
    assert inside.detail["n_caps_kept"] == 0


def test_collisions() -> None:
    out = _apply("def f(maxVal):\n    MaxVal = 1\n    max_val_1 = 2\n    return maxVal + MaxVal\n")
    # max_val libero per il primo; max_val_1 già nel file: il secondo prende max_val_2
    assert out.code == (
        "def f(max_val):\n    max_val_2 = 1\n    max_val_1 = 2\n    return max_val + max_val_2\n"
    )
    assert out.detail["n_collisions"] == 1
    reserved = _apply(
        "def f():\n    If = 1\n    Input = 2\n    LIST = 3\n    return If, Input, LIST\n"
    )
    assert reserved.code == (
        "def f():\n    if_1 = 1\n    input_1 = 2\n    list_1 = 3\n"
        "    return if_1, input_1, list_1\n"
    )
    words = _apply('def f():\n    fooBar = 1\n    return fooBar, "foo_bar"\n')
    assert "foo_bar_1 = 1" in words.code  # una parola del file, anche in una stringa


def test_same_name_in_two_functions_gets_the_same_new_name() -> None:
    code = "def f():\n    myVal = 1\n    return myVal\n\n\ndef g(myVal):\n    return myVal\n"
    out = _apply(code)
    assert out.code == code.replace("myVal", "my_val")


def test_exclusions() -> None:
    cases = [
        '__all__ = ["fooBar"]\nfooBar = 1\n',  # in __all__
        "fooBar = 1\n\ndef f():\n    global fooBar\n    fooBar = 2\n",  # global
        "def f(fooBar):\n    return fooBar\n\nf(fooBar=1)\n",  # argomento con nome
        "def f():\n    aB = 1\n    return eval('aB')\n",  # funzione con eval
        "fooBar = 1\nprint(globals()['fooBar'])\n",  # modulo con globals
        "print([aB for aB in range(3)])\n",  # comprehension
        "class A:\n    sizeX = 1\n",  # attributo di classe
        "import os.path as osPath\nfrom math import gcd as myGcd\n",  # import
        "def myFunc():\n    pass\n\nclass MyClass:\n    pass\n",  # def e class
        "def f(self, cls):\n    return self, cls\n",
        "__myDunder__ = 1\n",
        "x = 1\n\ndef f(y):\n    return x.fooBar + y\n",  # attributo
    ]
    for code in cases:
        out = _apply(code)
        assert out.status is AttackStatus.UNCHANGED, (code, out.code)
        assert out.code == code
    entry = _apply("hasCloseElements = 1\n", entry="hasCloseElements")
    assert entry.status is AttackStatus.UNCHANGED


def test_module_names_read_in_functions_and_classes() -> None:
    code = (
        "totalCount = 0\nstepSize = 2\n\n\ndef bump():\n    return totalCount + stepSize\n\n\n"
        "class C:\n    base = stepSize\n\n\nfor rowIdx in range(3):\n    totalCount += rowIdx\n"
        "print(bump(), C.base, totalCount)\n"
    )
    out = _apply(code)
    assert "totalCount" not in out.code and "stepSize" not in out.code
    assert "rowIdx" not in out.code and "return total_count + step_size" in out.code
    assert out.detail["n_module"] == out.detail["n_module_renamed"] == 3
    assert _run(out.code) == _run(code)


def test_unchanged_not_applicable_tool_error_and_determinism() -> None:
    snake = "def f(total_count, n):\n    my_val = total_count + n\n    return my_val\n"
    out = _apply(snake)
    assert out.status is AttackStatus.UNCHANGED and out.detail["n_renamed"] == 0
    java = make_attack("T1.6", {"caps": "convert"}).safe_apply(
        "class A { int fooBar; }", Language.JAVA, _ctx()
    )
    assert java.status is AttackStatus.NOT_APPLICABLE
    broken = _apply("def f(:\n    pass\n")
    assert broken.status is AttackStatus.TOOL_ERROR and broken.code == "def f(:\n    pass\n"
    assert {_apply(PROGRAM, seed=s).code for s in range(5)} == {CONVERT}


# ---------------------------------------------------------------------- griglia
def test_grid_and_parameters() -> None:
    grid = attack_grid("T1.6")
    assert [p["caps"] for p in grid.points] == ["convert", "keep"]
    hashes = grid_attacks("T1.6", language="python")
    assert len(hashes) == 2 and all(len(h) == 12 for h in hashes)
    with pytest.raises(ConfigError):
        make_attack("T1.6", {"caps": "lower"})


def test_existing_attack_hashes_are_unchanged() -> None:
    for attack_id, want in EXISTING_HASHES.items():
        assert sorted(grid_attacks(attack_id, language="python")) == want, attack_id
