"""Funzione d'ingresso conservata, rinominata o mancante (``bench.attacks.interface``), sui casi
visti in T3.1 su STONE (``M11_t3_1_stone_java_regressions.txt``, ``..._cpp_regressions.txt``)."""

from __future__ import annotations

import pytest

from bench.attacks.interface import (
    KEPT,
    MISSING,
    RENAMED,
    entry_status,
    function_names,
    pass_failures,
)
from bench.domain.enums import Language
from bench.domain.models import Problem

JAVA_PROMPT = """import java.util.*;
import java.lang.*;

class Solution {
    /**
    Insert a number 'delimeter' between every two consecutive elements.
     */
    public List<Integer> intersperse(List<Integer> numbers, int delimiter) {
"""
JAVA_BODY = """        List<Integer> result = new ArrayList<>();
        for (int i = 0; i < numbers.size(); i++) {
            result.add(numbers.get(i));
        }
        return result;
    }
}
"""
JAVA_RENAMED_FILE = """import java.util.*;
import java.lang.*;

class Solution {
    public List<Integer> intersperseNumbers(List<Integer> numbers, int delimiter) {
        return numbers;
    }
}
"""
CPP_PROMPT = "#include<stdio.h>\nusing namespace std;\nint sum_to_n(int n){\n"
CPP_CLASS = """#include<iostream>

class SumNumbers {
public:
  int sum(int n) {
    return n;
  }
};

int main() {
  return 0;
}
"""
PY_PROMPT = 'def add(x: int, y: int) -> int:\n    """Add two numbers."""\n'


def problem(lang: Language, prompt: str, entry: str, dataset: str = "humanevalpack") -> Problem:
    return Problem(
        problem_key=f"{dataset}/0",
        dataset=dataset,
        level="L1",
        language=lang,
        split="test",
        prompt_text=prompt,
        entry_point=entry,
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )


JAVA = problem(Language.JAVA, JAVA_PROMPT, "intersperse")
CPP = problem(Language.CPP, CPP_PROMPT, "sum_to_n")
PY = problem(Language.PYTHON, PY_PROMPT, "add", dataset="humanevalplus")


@pytest.mark.parametrize(
    ("prob", "code", "status", "name"),
    [
        (JAVA, JAVA_PROMPT + JAVA_BODY, KEPT, None),  # prompt ripetuto alla lettera
        (JAVA, JAVA_RENAMED_FILE.replace("intersperseNumbers", "intersperse"), KEPT, None),
        (JAVA, JAVA_PROMPT + JAVA_RENAMED_FILE, RENAMED, "intersperseNumbers"),
        (CPP, CPP_PROMPT + "    return n;\n}\n", KEPT, None),
        (CPP, CPP_PROMPT + CPP_CLASS, RENAMED, "sum"),
        (PY, PY_PROMPT + "    return x + y\n", KEPT, None),
        (PY, PY_PROMPT + "def plus(x, y):\n    return x + y\n", RENAMED, "plus"),
        (PY, PY_PROMPT + "x = 1\n", MISSING, None),
    ],
)
def test_entry_status(prob: Problem, code: str, status: str, name: str | None) -> None:
    got = entry_status(code, prob)
    assert (got.status, got.new_name) == (status, name)


def test_mbpp_without_prompt_and_fresh_name() -> None:
    mbpp = problem(Language.PYTHON, "Write a function.", "foo", dataset="mbppplus")
    original = "def helper(x):\n    return x\n\ndef foo(x):\n    return helper(x)\n"
    renamed = "def helper(x):\n    return x\n\ndef bar(x):\n    return helper(x)\n"
    assert entry_status(original, mbpp, original).status == KEPT
    assert entry_status(renamed, mbpp, original).new_name == "bar"  # helper era già nel codice
    assert entry_status("x = 1\n", mbpp, original).status == MISSING


def test_pass_failures_split() -> None:
    rows = [
        {"sample_id": "a", "problem_key": "j", "code": JAVA_PROMPT + JAVA_BODY,
         "attack_status": "APPLIED"},
        {"sample_id": "b", "problem_key": "j", "code": JAVA_PROMPT + JAVA_RENAMED_FILE,
         "attack_status": "APPLIED"},
        {"sample_id": "c", "problem_key": "j", "code": JAVA_PROMPT + JAVA_BODY,
         "attack_status": "APPLIED"},
        {"sample_id": "d", "problem_key": "j", "code": JAVA_PROMPT + JAVA_RENAMED_FILE,
         "attack_status": "TOOL_ERROR"},
    ]  # fmt: skip
    status = {"a": "PASSED", "b": "COMPILE_ERROR", "c": "FAILED", "d": "FAILED"}
    got = pass_failures(rows, status, {"j": JAVA})
    assert got == {"n": 4, "failed": 3, RENAMED: 1, MISSING: 0, "other": 2}


def test_function_names_in_order() -> None:
    assert function_names(CPP_PROMPT + CPP_CLASS, Language.CPP)[:1] == ["sum_to_n"]
    assert function_names(CPP_CLASS, Language.CPP) == ["sum", "main"]
