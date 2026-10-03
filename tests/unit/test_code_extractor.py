"""Test della regola unica di estrazione del codice (SPEC §10.6, D4)."""

from __future__ import annotations

import pytest

from bench.domain.enums import Language
from bench.domain.models import Problem
from bench.generation.code_extractor import FencedCodeExtractor, defines

HE_PROMPT = 'def add(a, b):\n    """Somma."""\n'


def _problem(dataset: str, lang: Language, prompt: str, entry: str) -> Problem:
    return Problem(
        problem_key="humaneval/0",
        dataset=dataset,
        level="L1",  # type: ignore[arg-type]
        language=lang,
        split="dev",  # type: ignore[arg-type]
        prompt_text=prompt,
        entry_point=entry,
        canonical_solution=None,
        test_ref=None,
        contamination_risk=False,
        loc_to_generate=None,
    )


HE = _problem("humanevalplus", Language.PYTHON, HE_PROMPT, "add")
MBPP = _problem("mbppplus", Language.PYTHON, '"""\nWrite add.\n"""\n', "add")
X = FencedCodeExtractor()


def test_tagged_block_wins() -> None:
    raw = "Here:\n```text\nnot code\n```\n```python\ndef add(a, b):\n    return a + b\n```\nBye"
    assert X.extract(raw, HE) == ("def add(a, b):\n    return a + b\n", True)


def test_alias_tag_and_untagged_block() -> None:
    assert X.extract("```py\ndef add(a, b): return a+b\n```", HE)[1]
    code, ok = X.extract("```\ndef add(a, b):\n    return 0\n```", HE)
    assert ok and code.startswith("def add")


def test_other_language_block_is_skipped() -> None:
    raw = "```java\nclass A {}\n```\n```\ndef add(a, b):\n    return 1\n```"
    assert X.extract(raw, HE)[0].startswith("def add")


def test_truncated_block_is_accepted() -> None:
    raw = "Sure:\n```python\ndef add(a, b):\n    return a +"
    code, ok = X.extract(raw, HE)
    assert ok and code.startswith("def add")


def test_plain_text_only_if_valid_code() -> None:
    assert X.extract("def add(a, b):\n    return a + b", HE)[1]
    assert X.extract("I cannot solve this, sorry (", HE) == ("", False)
    assert X.extract("", HE) == ("", False)


def test_prompt_prepended_for_function_completion() -> None:
    raw = "```python\n    return a + b\n```"
    code, ok = X.extract(raw, HE)
    assert ok
    assert code == HE_PROMPT + "    return a + b\n"


def test_no_prepend_for_mbpp() -> None:
    code, ok = X.extract("```python\nresult = 1\n```", MBPP)
    assert ok and code == "result = 1\n"


@pytest.mark.parametrize(
    ("lang", "code", "entry"),
    [
        (Language.PYTHON, "def has_x(a):\n    return a\n", "has_x"),
        (Language.JAVA, "class Solution {\n  public int hasX(int a) { return a; }\n}", "hasX"),
        (
            Language.CPP,
            "#include <vector>\nbool has_x(std::vector<int> v){ return true; }",
            "has_x",
        ),
        (Language.JAVASCRIPT, "const hasX = (a) => {\n  return a;\n};", "hasX"),
        (Language.JAVASCRIPT, "function hasX(a) { return a; }", "hasX"),
    ],
)
def test_defines_entry_point(lang: Language, code: str, entry: str) -> None:
    assert defines(code, lang, entry)
    assert not defines(code, lang, entry + "Other")


def test_hep_java_body_only_gets_prompt() -> None:
    prompt = "import java.util.*;\nclass Solution {\n    public int f(int a) {\n"
    problem = _problem("humanevalpack", Language.JAVA, prompt, "f")
    code, ok = X.extract("```java\n        return a;\n    }\n}\n```", problem)
    assert ok and code.startswith(prompt)
