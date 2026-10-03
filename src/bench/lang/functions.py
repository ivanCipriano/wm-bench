"""Estrazione di funzioni e righe di codice delle funzioni (ADR-004).

Serve a:

- estrarre funzioni candidate dai file di The Stack (C++);
- misurare la lunghezza dei negativi in modo omogeneo (``function_loc``): si contano solo
  le righe di codice dentro le funzioni più esterne, così import, ``class Solution`` o
  ``#include`` delle soluzioni canoniche non pesano rispetto alle funzioni isolate di
  CodeSearchNet e The Stack.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from bench.domain.enums import Language
from bench.lang.line_counter import code_rows
from bench.lang.parsers import ParserService, default_parsers

FUNCTION_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"function_definition"}),
    Language.JAVA: frozenset({"method_declaration", "constructor_declaration"}),
    Language.CPP: frozenset({"function_definition"}),
    Language.JAVASCRIPT: frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "function",
            "arrow_function",
            "method_definition",
        }
    ),
}

# Involucri provati in ordine per analizzare una funzione isolata senza errori.
_WRAPPERS: dict[Language, tuple[tuple[str, str], ...]] = {
    Language.PYTHON: (("", ""),),
    Language.JAVA: (("", ""), ("class __Wrapper__ {\n", "\n}")),
    Language.CPP: (("", ""), ("struct __Wrapper__ {\n", "\n};")),
    Language.JAVASCRIPT: (("", ""), ("(", ")"), ("class __Wrapper__ {\n", "\n}")),
}


@dataclass(frozen=True)
class ExtractedFunction:
    """Funzione estratta da un sorgente."""

    code: str
    start_line: int
    loc: int


def outermost_functions(root: Node, lang: Language) -> list[Node]:
    """Nodi funzione non contenuti in altre funzioni."""
    types = FUNCTION_TYPES[Language(lang)]
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            found.append(node)
            continue
        stack.extend(reversed(node.children))
    return found


def function_loc(code: str, lang: Language, parsers: ParserService | None = None) -> int:
    """Righe di codice dentro le funzioni più esterne (tutto il file se non ce ne sono)."""
    root = (parsers or default_parsers()).parse(code, lang).root_node
    functions = outermost_functions(root, lang)
    if not functions:
        return len(code_rows(root, lang))
    rows: set[int] = set()
    for fn in functions:
        rows |= code_rows(fn, lang)
    return len(rows)


def parse_standalone(
    code: str, lang: Language, parsers: ParserService | None = None
) -> tuple[str, str] | None:
    """Involucro (prefisso, suffisso) con cui la funzione isolata si analizza senza errori.

    Returns:
        L'involucro, oppure ``None`` se nessun involucro elimina gli errori.
    """
    service = parsers or default_parsers()
    for prefix, suffix in _WRAPPERS[Language(lang)]:
        if not service.has_error(prefix + code + suffix, lang):
            return prefix, suffix
    return None


def standalone_function_loc(
    code: str, lang: Language, parsers: ParserService | None = None
) -> int | None:
    """``function_loc`` di una funzione isolata (``None`` se non si analizza senza errori)."""
    wrapper = parse_standalone(code, lang, parsers)
    if wrapper is None:
        return None
    return function_loc(wrapper[0] + code + wrapper[1], lang, parsers)


def extract_functions(
    source: str, lang: Language, parsers: ParserService | None = None
) -> list[ExtractedFunction]:
    """Funzioni più esterne di un sorgente, escluse quelle con errori di sintassi."""
    root = (parsers or default_parsers()).parse(source, lang).root_node
    data = source.encode("utf-8")
    out: list[ExtractedFunction] = []
    for fn in outermost_functions(root, lang):
        if fn.has_error:
            continue
        text = data[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace")
        out.append(ExtractedFunction(text, fn.start_point[0], len(code_rows(fn, lang))))
    return out
