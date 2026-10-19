"""Attacchi T1 (M10, ADR-012): proprietà di SPEC §21.3 sui quattro linguaggi.

Determinismo dato il seme, ``UNCHANGED`` quando il codice non cambia, T1.3 (nessun commento
nelle stringhe, densità 1/3, docstring intatte), T1.4 (mai ``entry_point``, campi, globali o
membri; nessuna collisione; stessa semantica in Python), T1.5 (mai stdout; stessa semantica in
Python). I tool esterni (T1.1, T1.2) sono in ``test_attack_tools.py``.
"""

from __future__ import annotations

import contextlib
import io
import random
import re
from typing import Any

import pytest

from bench.attacks.base import (
    AttackContext,
    AttackPools,
    attack_grid,
    grid_attacks,
    make_attack,
    sample_rng,
)
from bench.attacks.t1.common import identifiers_in
from bench.domain.enums import AttackStatus, Language
from bench.domain.models import Problem
from bench.lang.parsers import default_parsers

POOLS = AttackPools(
    comments=("check the bounds", "handle the empty case", "compute the result"),
    names=("count", "total", "index", "value", "result", "items", "acc"),
)

PY = '''from typing import List

LIMIT = 10


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """Check closeness.

    # not a comment, inside the docstring
    """
    # loop over pairs
    text = """multi
line string"""
    for idx, elem in enumerate(numbers):
        for idx2, elem2 in enumerate(numbers):
            if idx != idx2:
                distance = abs(elem - elem2)  # diff
                if distance < threshold:
                    return True
    total = len(numbers) * 2 + LIMIT
    return False
'''

JAVA = """import java.util.*;

class Solution {
    // field
    private int count = 3;
    public boolean hasCloseElements(List<Double> numbers, double threshold) {
        /* scan */
        String s = "// not a comment";
        for (int i = 0; i < numbers.size(); i++) {
            for (int j = i + 1; j < numbers.size(); j++) {
                double distance = Math.abs(numbers.get(i) - numbers.get(j));
                if (distance < threshold) return true; // early
            }
        }
        int total = count + i2(3) * 2;
        this.count = total;
        return false;
    }
    private int i2(int x) { return x; }
}
"""

CPP = """#include <vector>
#include <math.h>
using namespace std;
int LIMIT = 3;
bool has_close_elements(vector<float> numbers, float threshold){
    // scan pairs
    const char* s = "/* not a comment */";
    int i, j;
    for (i=0;i<numbers.size();i++)
    for (j=i+1;j<numbers.size();j++)
    if (abs(numbers[i]-numbers[j])<threshold)
    return true;
    int total = i * 2 + j + LIMIT;
    return false;
}
"""

JS = """const LIMIT = 3;
const hasCloseElements = (numbers, threshold) => {
  // scan
  const s = `// not a comment`;
  for (let i = 0; i < numbers.length; i++) {
    for (let j = 0; j < numbers.length; j++) {
      if (i != j) {
        let distance = Math.abs(numbers[i] - numbers[j]);
        if (distance < threshold) { return true; }
      }
    }
  }
  const {a, b} = {a: 1, b: threshold + LIMIT};
  const obj = {a};
  return obj.a > b;
}
"""

SAMPLES = {"python": PY, "java": JAVA, "cpp": CPP, "javascript": JS}
ENTRY = {
    "python": "has_close_elements",
    "java": "hasCloseElements",
    "cpp": "has_close_elements",
    "javascript": "hasCloseElements",
}
CONFIGS = [
    ("T1.3", {"mode": "remove"}),
    ("T1.3", {"mode": "insert", "density": 0.333}),
    ("T1.4", {"ratio": 1.0, "name_kind": "random"}),
    ("T1.4", {"ratio": 0.5, "name_kind": "natural"}),
    ("T1.5", {"k": 1}),
    ("T1.5", {"k": 5}),
]


def _problem(language: str) -> Problem:
    return Problem(
        problem_key="humaneval/0",
        dataset="humanevalplus" if language == "python" else "humanevalpack",
        level="L1",
        language=language,
        split="test",
        prompt_text="",
        entry_point=ENTRY[language],
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )


def _ctx(language: str, seed: int = 7) -> AttackContext:
    return AttackContext(
        rng=random.Random(seed),
        problem=_problem(language),
        parsers=default_parsers(),
        pools=POOLS,
        sample_id="s",
    )


def _apply(attack_id: str, params: dict[str, Any], language: str, seed: int = 7) -> Any:
    attack = make_attack(attack_id, params)
    return attack.safe_apply(SAMPLES[language], Language(language), _ctx(language, seed))


@pytest.mark.parametrize("language", list(SAMPLES))
@pytest.mark.parametrize(("attack_id", "params"), CONFIGS)
def test_applied_parses_and_is_deterministic(
    attack_id: str, params: dict[str, Any], language: str
) -> None:
    first = _apply(attack_id, params, language)
    assert first.status is AttackStatus.APPLIED, first.detail
    assert not default_parsers().has_error(first.code, Language(language))
    assert _apply(attack_id, params, language) == first
    # Un seme diverso sceglie di solito punti o nomi diversi.
    seeds = {_apply(attack_id, params, language, seed).code for seed in range(6)}
    if (attack_id, params.get("ratio")) != ("T1.4", 1.0) and params.get("mode") != "remove":
        assert len(seeds) > 1


def test_sample_rng_follows_spec_seed() -> None:
    a = sample_rng(20261001, "T1.4", "abc", "sid").random()
    assert a == sample_rng(20261001, "T1.4", "abc", "sid").random()
    assert a != sample_rng(20261001, "T1.4", "abd", "sid").random()


@pytest.mark.parametrize("language", list(SAMPLES))
def test_unchanged_when_nothing_to_do(language: str) -> None:
    code = {
        "python": "x = 1\n",
        "java": "class A { int x; }\n",
        "cpp": "int x = 1;\n",
        "javascript": "var x = 1;\n",
    }[language]
    for attack_id, params in [("T1.3", {"mode": "remove"}), ("T1.4", {"ratio": 1.0,
                              "name_kind": "random"}), ("T1.5", {"k": 3})]:  # fmt: skip
        out = make_attack(attack_id, params).safe_apply(code, Language(language), _ctx(language))
        assert out.status is AttackStatus.UNCHANGED, (attack_id, out)
        assert out.code == code
    empty = make_attack("T1.5", {"k": 1}).safe_apply("  \n", Language(language), _ctx(language))
    assert empty.status is AttackStatus.UNCHANGED


# ---------------------------------------------------------------------- T1.3
@pytest.mark.parametrize("language", list(SAMPLES))
def test_comments_removed_strings_kept(language: str) -> None:
    out = _apply("T1.3", {"mode": "remove"}, language)
    removed = {
        "python": ["# loop over pairs", "# diff"],
        "java": ["// field", "/* scan */", "// early"],
        "cpp": ["// scan pairs"],
        "javascript": ["// scan\n"],
    }[language]
    for comment in removed:
        assert comment in SAMPLES[language]
        assert comment not in out.code
    assert out.detail["n_removed"] == len(removed)
    for kept in ("not a comment", "multi\nline string"):
        if kept in SAMPLES[language]:
            assert kept in out.code
    if language == "python":
        assert '"""Check closeness.' in out.code  # docstring intatta (SPEC §12)


@pytest.mark.parametrize("language", list(SAMPLES))
def test_comment_insertion_density_and_syntax(language: str) -> None:
    out = _apply("T1.3", {"mode": "insert", "density": 0.333}, language)
    detail = out.detail
    assert detail["n_comments"] == min(detail["n_wanted"], detail["n_sites"])
    assert detail["n_wanted"] == max(1, round(0.333 * detail["n_code_lines"]))
    prefix = "#" if language == "python" else "//"
    added = [
        line.strip()
        for line in out.code.splitlines()
        if line.strip() in {f"{prefix} {c}" for c in POOLS.comments}
    ]
    assert len(added) == detail["n_comments"]
    assert 0 < detail["added_token_fraction"] < 1
    # Le stringhe su più righe non cambiano.
    if language == "python":
        assert 'text = """multi\nline string"""' in out.code


# ---------------------------------------------------------------------- T1.4
@pytest.mark.parametrize("language", list(SAMPLES))
def test_rename_protects_entry_point_fields_globals_members(language: str) -> None:
    out = _apply("T1.4", {"ratio": 1.0, "name_kind": "random"}, language)
    code = out.code
    assert ENTRY[language] in code
    protected = {
        "python": ["LIMIT", "List", "enumerate", "abs", "len"],
        "java": ["private int count = 3;", "this.count", ".size()", "Math.abs", "i2(3)"],
        "cpp": ["int LIMIT = 3;", "+ LIMIT", ".size()", "abs("],
        "javascript": ["const LIMIT = 3;", "+ LIMIT", ".length", "Math.abs", ".a >"],
    }[language]
    for name in protected:
        assert name in code, name
    assert out.detail["n_renamed"] == out.detail["n_candidates"] > 0
    # Nessun nome nuovo coincide con una parola del codice originale.
    new_names = identifiers_in(code) - identifiers_in(SAMPLES[language])
    assert len(new_names) >= out.detail["n_renamed"]


def test_rename_javascript_shorthand_keeps_property_names() -> None:
    out = _apply("T1.4", {"ratio": 1.0, "name_kind": "random"}, "javascript")
    assert re.search(r"const \{a: \w+, b: \w+\} = \{a: 1, b: \w+ \+ LIMIT\};", out.code)
    assert re.search(r"const \w+ = \{a: \w+\};", out.code)


def test_rename_python_semantics_and_keyword_arguments() -> None:
    code = (
        "def f(n, step):\n"
        "    acc = 0\n"
        "    for i in range(n):\n"
        "        acc += i * step\n"
        "    return g(value=acc)\n"
        "\n"
        "def g(value):\n"
        "    return value + 1\n"
    )
    out = make_attack("T1.4", {"ratio": 1.0, "name_kind": "random"}).safe_apply(
        code, Language.PYTHON, _ctx("python")
    )
    assert out.status is AttackStatus.APPLIED
    assert "def g(value):" in out.code  # usato come argomento con nome: escluso
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    exec(code, before)
    exec(out.code, after)
    assert before["f"](5, 3) == after["f"](5, 3)


# ---------------------------------------------------------------------- T1.5
def test_local_edits_python_no_stdout_same_result() -> None:
    code = (
        "def f(xs, k):\n"
        "    total = 0\n"
        "    for x in xs:\n"
        "        total = total + x * k\n"
        "    result = total - k\n"
        "    return result\n"
    )
    attack = make_attack("T1.5", {"k": 5})
    for seed in range(20):
        out = attack.safe_apply(code, Language.PYTHON, _ctx("python", seed))
        assert out.status is AttackStatus.APPLIED
        assert out.detail["n_edits"] == 5
        ns: dict[str, Any] = {}
        exec(out.code, ns)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = ns["f"]([1, 2, 3], 2)
        assert value == 10
        assert stdout.getvalue() == ""


@pytest.mark.parametrize("language", ["java", "cpp", "javascript"])
def test_local_edits_never_write_stdout(language: str) -> None:
    forbidden = {"java": "System.out", "cpp": "cout", "javascript": "console.log"}[language]
    for seed in range(10):
        out = _apply("T1.5", {"k": 5}, language, seed)
        assert forbidden not in out.code
        assert not default_parsers().has_error(out.code, Language(language))


def test_local_edits_skip_unsafe_points() -> None:
    java = (
        "class A extends B {\n"
        "    A(int x) {\n"
        "        super(x);\n"
        "        final int c = x + 1;\n"
        "    }\n"
        "}\n"
    )
    for seed in range(10):
        out = make_attack("T1.5", {"k": 3}).safe_apply(java, Language.JAVA, _ctx("java", seed))
        lines = [line.strip() for line in out.code.splitlines()]
        assert lines.index("super(x);") == lines.index("A(int x) {") + 1  # nulla prima di super
        assert "final int c = x + 1;" in lines  # dichiarazioni costanti: nessuna estrazione
    py = 'def f(x):\n    """Doc."""\n    return x + 1\n'
    for seed in range(10):
        out = make_attack("T1.5", {"k": 1}).safe_apply(py, Language.PYTHON, _ctx("python", seed))
        assert out.code.splitlines()[1] == '    """Doc."""'


# ---------------------------------------------------------------------- griglie
def test_attack_grids() -> None:
    sizes = {a: len(attack_grid(a).points) for a in ("T1.1", "T1.2", "T1.3", "T1.4", "T1.5")}
    assert sizes == {"T1.1": 1, "T1.2": 1, "T1.3": 2, "T1.4": 8, "T1.5": 3}
    hashes = grid_attacks("T1.4")
    assert len(hashes) == 8 and all(len(h) == 12 for h in hashes)
    density = [a.params for a in grid_attacks("T1.3").values() if a.params["mode"] == "insert"]
    assert density == [{"mode": "insert", "density": 0.333}]


RECURSIVE = {
    "python": "def fib(n):\n    if n < 2:\n        return n\n    return fib(n - 1) + fib(n - 2)\n",
    "java": (
        "class S {\n    int fib(int n) {\n        if (n < 2) return n;\n"
        "        return fib(n - 1) + this.fib(n - 2);\n    }\n}\n"
    ),
    "cpp": "int fib(int n) {\n    if (n < 2) return n;\n    return fib(n - 1) + fib(n - 2);\n}\n",
}


@pytest.mark.parametrize("language", list(RECURSIVE))
def test_local_edits_no_log_in_recursive_functions(language: str) -> None:
    from bench.attacks.t1.common import statement_sites
    from bench.attacks.t1.local_edits import log_sites

    code = RECURSIVE[language]
    sites = statement_sites(code, Language(language), default_parsers())
    assert log_sites(sites, Language(language), exclude_recursive=False)
    assert log_sites(sites, Language(language)) == []
    marker = {"python": "stderr", "java": "System.err", "cpp": "cerr"}[language]
    for seed in range(10):
        out = make_attack("T1.5", {"k": 5}).safe_apply(
            code, Language(language), _ctx(language, seed)
        )
        assert marker not in out.code
        assert out.detail["n_stderr_log"] == 0


def test_local_edits_never_log_in_javascript() -> None:
    for seed in range(20):
        out = _apply("T1.5", {"k": 5}, "javascript", seed)
        assert "console.error" not in out.code
        assert out.detail["n_stderr_log"] == 0
        assert out.detail["n_edits"] == 5  # k invariato: gli altri due tipi
