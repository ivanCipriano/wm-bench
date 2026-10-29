"""Prompt delle funzioni del Livello 3 senza test (CodeSearchNet, The Stack C++; ADR-016 §2).

Decisioni dell'utente del 29 ottobre 2026:

- **CodeSearchNet** (Python, Java, JavaScript): prompt = **firma + docstring**. Python: il codice
  fino alla fine della docstring (prima istruzione del corpo). Java e JavaScript: il commento di
  documentazione (quello del sorgente se ``whole_func_string`` lo contiene, altrimenti ricostruito
  da ``func_documentation_string`` come ``/** ... */``) + la firma fino alla graffa del corpo;
- **The Stack C++**: prompt = **commento di documentazione + firma**: il blocco di commenti
  contiguo subito sopra la funzione (o sopra il suo ``template``), filtrato con gli stessi criteri
  delle raccolte degli attacchi (ADR-012, ``bench.data.attack_pools``): niente licenze o copyright,
  niente commenti generati, niente direttive di tool, niente codice commentato, solo inglese.

Il modello genera il corpo; ``loc_to_generate`` sono le righe di codice del corpo (SPEC §10.3).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter import Node

from bench.data.attack_pools import (
    _DIRECTIVE,
    _GENERATED,
    _LICENSE,
    MAX_LINE_CHARS,
    _is_code,
    _non_english,
)
from bench.domain.enums import Language
from bench.generation.code_extractor import _function_name
from bench.lang.functions import outermost_functions, parse_standalone
from bench.lang.line_counter import code_rows
from bench.lang.parsers import ParserService

DOC_MIN_CHARS, DOC_MAX_CHARS = 10, 2000
DOC_FILTERS = ("length", "license", "generated", "directive", "code", "non_english")
_DOC_MARKERS = re.compile(r"^\s*(/\*+!?|//+!?|\*+/|\*(?!/))\s?|\s*\*+/\s*$")
DOXYGEN_STARTS = ("/**", "/*!", "///", "//!")


@dataclass(frozen=True)
class PromptedFunction:
    """Funzione con il suo prompt.

    ``code`` è la funzione umana completa (canonica), ``prompt`` la parte data al modello,
    ``entry_point`` il nome della funzione e ``loc_to_generate`` le righe di codice del corpo.
    """

    code: str
    prompt: str
    entry_point: str
    loc_to_generate: int
    doc_kind: str = "docstring"
    start_line: int = 0  # riga della funzione nel file (chiave di The Stack, come i negativi)


def clean_doc(raw: str) -> str:
    """Testo di un commento di documentazione senza marcatori, su una riga."""
    lines = [_DOC_MARKERS.sub("", line).strip() for line in raw.split("\n")]
    return " ".join(" ".join(x for x in lines if x).split())


def doc_drop_reason(raw: str) -> str | None:
    """Primo filtro che scarta un commento di documentazione (``None`` se resta).

    Stessi criteri delle raccolte degli attacchi (ADR-012) sul testo pulito dell'intero blocco;
    la lunghezza ammessa (10-2.000 caratteri) è quella di un commento di più righe, non di una.
    """
    text = clean_doc(raw)
    checks: tuple[tuple[str, Callable[[str], bool]], ...] = (
        ("length", lambda t: not DOC_MIN_CHARS <= len(t) <= DOC_MAX_CHARS),
        ("license", lambda t: bool(_LICENSE.search(t))),
        ("generated", lambda t: bool(_GENERATED.search(t))),
        ("directive", lambda t: bool(_DIRECTIVE.search(t))),
        ("code", _is_code),
        ("non_english", _non_english),
    )
    return next((name for name, check in checks if check(text)), None)


def _body_loc(fn: Node, lang: Language) -> int:
    """Righe di codice del corpo (senza la riga della firma e la docstring Python)."""
    body = fn.child_by_field_name("body")
    if body is None:
        return 0
    rows = code_rows(body, lang)
    rows.discard(fn.start_point[0])
    return len(rows)


def _python_docstring(body: Node) -> Node | None:
    first = next((c for c in body.named_children if c.type != "comment"), None)
    if first is None or first.type != "expression_statement":
        return None
    if not first.named_children or first.named_children[0].type != "string":
        return None
    return first


def _first_function(root: Node, lang: Language) -> Node | None:
    functions = outermost_functions(root, lang)
    return functions[0] if functions else None


def python_prompt(code: str, parsers: ParserService) -> PromptedFunction | str:
    """Firma + docstring di una funzione Python (o il motivo dello scarto)."""
    root = parsers.parse(code, Language.PYTHON).root_node
    fn = _first_function(root, Language.PYTHON)
    if fn is None or root.has_error:
        return "parse_error"
    name = _function_name(fn, Language.PYTHON)
    body = fn.child_by_field_name("body")
    if name is None or body is None:
        return "no_name"
    doc = _python_docstring(body)
    if doc is None:
        return "no_doc_in_code"
    data = code.encode("utf-8")
    prompt = data[: doc.end_byte].decode("utf-8") + "\n"
    rows = code_rows(body, Language.PYTHON) - set(range(doc.end_point[0] + 1))
    if not rows:
        return "empty_body"
    return PromptedFunction(code, prompt, name, len(rows))


def javadoc(text: str) -> str:
    """Commento ``/** ... */`` da ``func_documentation_string``."""
    lines = [line.rstrip() for line in text.strip().split("\n")]
    return "/**\n" + "".join(f" * {line}".rstrip() + "\n" for line in lines) + " */\n"


def brace_prompt(
    code: str, lang: Language, documentation: str, parsers: ParserService
) -> PromptedFunction | str:
    """Commento di documentazione + firma fino alla graffa del corpo (Java, JavaScript)."""
    wrapper = parse_standalone(code, lang, parsers)
    if wrapper is None:
        return "parse_error"
    prefix = wrapper[0].encode("utf-8")
    source = (wrapper[0] + code + wrapper[1]).encode("utf-8")
    root = parsers.parse(source.decode("utf-8"), lang).root_node
    fn = _first_function(root, lang)
    if fn is None:
        return "parse_error"
    name = _function_name(fn, lang)
    body = fn.child_by_field_name("body")
    if name is None or body is None or body.type not in ("block", "statement_block"):
        return "no_name"
    loc = _body_loc(fn, lang)
    if loc == 0:
        return "empty_body"
    start, brace = fn.start_byte - len(prefix), body.start_byte - len(prefix)
    data = code.encode("utf-8")
    head = data[:start].decode("utf-8")
    in_source = head.strip().startswith(("/*", "//"))
    doc = head.strip() + "\n" if in_source else javadoc(documentation)
    # Riga della firma con la sua indentazione originale, fino alla graffa compresa.
    line_start = data.rfind(b"\n", 0, start) + 1
    indent = data[line_start:start].decode("utf-8") if not data[line_start:start].strip() else ""
    signature = indent + data[start:brace].decode("utf-8").rstrip() + " {\n"
    return PromptedFunction(code, doc + signature, name, loc, "source" if in_source else "built")


def preceding_comments(anchor: Node) -> list[Node]:
    """Commenti contigui subito sopra ``anchor`` (nessuna riga vuota in mezzo)."""
    out: list[Node] = []
    node, row = anchor.prev_sibling, anchor.start_point[0]
    while node is not None and node.type == "comment" and node.end_point[0] >= row - 1:
        out.append(node)
        row = node.start_point[0]
        node = node.prev_sibling
    return out[::-1]


def cpp_prompts(
    source: str, parsers: ParserService
) -> tuple[list[PromptedFunction], dict[str, int]]:
    """Funzioni C++ di un file con commento di documentazione e conteggi dei filtri."""
    counts: dict[str, int] = {}

    def drop(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    root = parsers.parse(source, Language.CPP).root_node
    data = source.encode("utf-8")
    out: list[PromptedFunction] = []
    for fn in outermost_functions(root, Language.CPP):
        counts["functions"] = counts.get("functions", 0) + 1
        if fn.has_error:
            drop("parse_error")
            continue
        anchor = (
            fn.parent if fn.parent is not None and fn.parent.type == "template_declaration" else fn
        )
        comments = preceding_comments(anchor)
        if not comments:
            drop("no_doc")
            continue
        doc_raw = data[comments[0].start_byte : comments[-1].end_byte].decode("utf-8", "replace")
        code = data[anchor.start_byte : fn.end_byte].decode("utf-8", "replace")
        if max((len(x) for x in code.split("\n")), default=0) > MAX_LINE_CHARS or _GENERATED.search(
            code
        ):
            drop("generated_or_minified")
            continue
        name = _function_name(fn, Language.CPP)
        body = fn.child_by_field_name("body")
        if name is None or body is None:
            drop("no_name")
            continue
        loc = _body_loc(fn, Language.CPP)
        if loc == 0:
            drop("empty_body")
            continue
        reason = doc_drop_reason(doc_raw)
        if reason is not None:
            drop(f"doc_{reason}")
            continue
        signature = data[anchor.start_byte : body.start_byte].decode("utf-8", "replace").rstrip()
        prompt = doc_raw.rstrip() + "\n" + signature + " {\n"
        kind = "doxygen" if doc_raw.lstrip().startswith(DOXYGEN_STARTS) else "plain"
        out.append(
            PromptedFunction(
                doc_raw.rstrip() + "\n" + code, prompt, name, loc, kind, fn.start_point[0]
            )
        )
        counts["kept"] = counts.get("kept", 0) + 1
    return out, counts
