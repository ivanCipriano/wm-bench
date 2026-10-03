"""Regressione di tree-sitter e codebleu nell'ambiente bench-core (ADR-005).

``pip check`` non rileva l'incompatibilità tra ``tree-sitter==0.22.3`` e grammatiche con
ABI 15 o con ``language()`` che restituisce un capsule: questo test sì. Va lanciato anche
sul cluster dopo ogni modifica dell'ambiente.
"""

from __future__ import annotations

import importlib.metadata
import re

import pytest
from codebleu import calc_codebleu

from bench.domain.enums import Language
from bench.lang.parsers import default_parsers, ts_language
from bench.store.manifest import PARSER_PACKAGES, parser_versions
from tests.conftest import REPO_ROOT

SNIPPETS: dict[Language, tuple[str, str]] = {
    Language.PYTHON: (
        "def add(a, b):\n    return a + b\n",
        "def plus(x, y):\n    return x + y\n",
    ),
    Language.JAVA: (
        "class A {\n  int add(int a, int b) { return a + b; }\n}\n",
        "class B {\n  int plus(int x, int y) { return x + y; }\n}\n",
    ),
    Language.CPP: (
        "#include <cstdio>\nint add(int a, int b) { return a + b; }\n",
        "#include <cstdio>\nint plus(int x, int y) { return x + y; }\n",
    ),
    Language.JAVASCRIPT: (
        "function add(a, b) { return a + b; }\n",
        "const plus = (x, y) => { return x + y; };\n",
    ),
}


def _pinned_versions() -> dict[str, str]:
    text = (REPO_ROOT / "environment-bench-core.yml").read_text(encoding="utf-8")
    return dict(re.findall(r"-\s*(tree-sitter[a-z-]*)==([0-9.]+)", text))


def test_installed_parsers_match_the_pinned_versions() -> None:
    pinned = _pinned_versions()
    assert set(pinned) == set(PARSER_PACKAGES)
    assert parser_versions() == pinned
    assert importlib.metadata.version("codebleu") == "0.7.0"


@pytest.mark.parametrize("lang", list(SNIPPETS))
def test_grammar_loads_with_supported_abi(lang: Language) -> None:
    assert ts_language(lang).version <= 14


@pytest.mark.parametrize("lang", list(SNIPPETS))
def test_valid_snippets_parse_without_errors(lang: Language) -> None:
    for code in SNIPPETS[lang]:
        assert not default_parsers().has_error(code, lang)


@pytest.mark.parametrize("lang", list(SNIPPETS))
def test_codebleu_runs(lang: Language) -> None:
    reference, prediction = SNIPPETS[lang]
    result = calc_codebleu([reference], [prediction], lang=str(lang))
    assert 0.0 < result["codebleu"] <= 1.0
    # syntax_match e dataflow_match usano tree-sitter: con grammatiche incompatibili falliscono.
    assert result["syntax_match_score"] > 0.0
