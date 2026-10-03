"""Test di parser, conteggio delle righe ed estrazione di funzioni (SPEC §10.3)."""

from __future__ import annotations

import pytest

from bench.domain.enums import Language
from bench.lang.functions import (
    extract_functions,
    function_loc,
    parse_standalone,
    standalone_function_loc,
)
from bench.lang.line_counter import count_code_lines, prompt_line_count
from bench.lang.parsers import default_parsers


def test_python_comments_blank_lines_and_docstrings() -> None:
    code = '''"""Docstring del modulo."""
import os  # commento in coda

# commento intero


def f(x):
    """Docstring
    su più righe."""
    s = """stringa
non docstring"""
    return x


class A:
    """Doc della classe."""

    y = 1
'''
    # Righe di codice: import, def, s = (2 righe), return, class, y = 1.
    assert count_code_lines(code, Language.PYTHON) == 7


def test_python_region_to_generate() -> None:
    prompt = 'def add(a, b):\n    """Somma."""\n'
    solution = "    # commento\n    c = a + b\n\n    return c\n"
    assert prompt_line_count(prompt) == 2
    assert count_code_lines(prompt + solution, Language.PYTHON, start_line=2) == 2


@pytest.mark.parametrize(
    ("lang", "code", "expected"),
    [
        (
            Language.JAVA,
            "class A {\n  // c\n  /* b\n  */\n  int f() {\n\n    return 1;\n  }\n}\n",
            5,
        ),
        (Language.CPP, "#include <x>\n// c\nint f() {\n  /* b */\n  return 1;\n}\n", 4),
        (Language.JAVASCRIPT, "// c\nconst f = (x) => {\n\n  return x; // t\n};\n", 3),
    ],
)
def test_other_languages(lang: Language, code: str, expected: int) -> None:
    assert count_code_lines(code, lang) == expected


def test_function_loc_ignores_boilerplate() -> None:
    java = (
        "import java.util.*;\n\nclass Solution {\n"
        "    public int f(int x) {\n        return x + 1;\n    }\n}\n"
    )
    assert function_loc(java, Language.JAVA) == 3
    py = "from typing import List\n\n\ndef f(x):\n    '''doc'''\n    return x\n"
    assert function_loc(py, Language.PYTHON) == 2
    assert function_loc("x = 1\ny = 2\n", Language.PYTHON) == 2  # nessuna funzione: tutto il file


def test_javascript_arrow_functions_count_as_functions() -> None:
    code = "const add = (a, b) => {\n  return a + b;\n};\nconsole.log(add(1, 2));\n"
    assert function_loc(code, Language.JAVASCRIPT) == 3


def test_standalone_java_method_needs_wrapper() -> None:
    method = "public int f(int x) {\n    return x;\n}"
    assert parse_standalone(method, Language.JAVA) is not None
    assert standalone_function_loc(method, Language.JAVA) == 3
    assert standalone_function_loc("public int f(int x) {", Language.JAVA) is None


def test_extract_cpp_functions_skips_broken_ones() -> None:
    source = (
        "#include <cstdio>\n"
        "int a(int x) {\n  return x;\n}\n"
        "struct S {\n  int m() const { return 1; }\n};\n"
        "int broken( {\n"
    )
    found = extract_functions(source, Language.CPP)
    assert [f.code.split("(")[0] for f in found] == ["int a", "int m"]
    assert found[0].start_line == 1
    assert found[0].loc == 3


def test_parser_service_is_shared_and_detects_errors() -> None:
    parsers = default_parsers()
    assert parsers is default_parsers()
    assert parsers.has_error("def f(:\n", Language.PYTHON)
    assert not parsers.has_error("def f():\n    pass\n", Language.PYTHON)
