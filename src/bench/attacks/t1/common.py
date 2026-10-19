"""Strumenti comuni degli attacchi T1: punti fra istruzioni, nomi riservati, nomi nuovi.

**Punti fra istruzioni** (T1.3(b), T1.5): un punto è l'inizio di un'istruzione figlia diretta di
un blocco dentro una funzione (``block`` di Python e Java, ``constructor_body``,
``compound_statement`` di C++, ``statement_block`` di JavaScript), quando l'istruzione apre la
sua riga (prima ci sono solo spazi). Lì si inserisce una riga nuova con la stessa indentazione:
mai dentro una stringa su più righe, mai dentro un'espressione, mai fra ``case`` di uno
``switch`` senza graffe (non sono blocchi).
"""

from __future__ import annotations

import builtins
import keyword
import random
import re
import string
from dataclasses import dataclass
from functools import cache

from tree_sitter import Node

from bench.domain.enums import Language
from bench.lang.functions import FUNCTION_TYPES
from bench.lang.parsers import ParserService

BLOCK_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"block"}),
    Language.JAVA: frozenset({"block", "constructor_body"}),
    Language.CPP: frozenset({"compound_statement"}),
    Language.JAVASCRIPT: frozenset({"statement_block"}),
}
# Funzioni e lambda: i blocchi dentro questi nodi sono "dentro una funzione".
FUNCTION_LIKE: dict[Language, frozenset[str]] = {
    Language.PYTHON: FUNCTION_TYPES[Language.PYTHON] | {"lambda"},
    Language.JAVA: FUNCTION_TYPES[Language.JAVA] | {"lambda_expression"},
    Language.CPP: FUNCTION_TYPES[Language.CPP] | {"lambda_expression"},
    Language.JAVASCRIPT: FUNCTION_TYPES[Language.JAVASCRIPT],
}
LINE_COMMENT: dict[Language, str] = {
    Language.PYTHON: "#",
    Language.JAVA: "//",
    Language.CPP: "//",
    Language.JAVASCRIPT: "//",
}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")

# ---------------------------------------------------------------------- nomi riservati
_JAVA_RESERVED = frozenset(
    """abstract assert boolean break byte case catch char class const continue default do double
    else enum extends final finally float for goto if implements import instanceof int interface
    long native new package private protected public return short static strictfp super switch
    synchronized this throw throws transient try void volatile while true false null var record
    yield sealed permits non-sealed when module open exports requires
    String Object Math System Integer Long Double Float Boolean Character Byte Short Arrays List
    ArrayList Map HashMap Set HashSet Collections Optional Stream Collectors Iterator Comparator
    StringBuilder Exception RuntimeException Error Thread Number Void Iterable Objects""".split()
)
_CPP_RESERVED = frozenset(
    """alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t
    char16_t char32_t class compl concept const consteval constexpr constinit const_cast continue
    co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit
    export extern false float for friend goto if inline int long mutable namespace new noexcept
    not not_eq nullptr operator or or_eq private protected public register reinterpret_cast
    requires return short signed sizeof static static_assert static_cast struct switch template
    this thread_local throw true try typedef typeid typename union unsigned using virtual void
    volatile wchar_t while xor xor_eq final override main std NULL EOF assert errno stdin stdout
    stderr size_t string vector map set pair abs min max swap sort printf scanf cout cin cerr endl
    INT_MAX INT_MIN LLONG_MAX LLONG_MIN UINT_MAX FLT_MAX DBL_MAX""".split()
)
_JS_RESERVED = frozenset(
    """break case catch class const continue debugger default delete do else export extends false
    finally for function if import in instanceof let new null return super switch this throw true
    try typeof var void while with yield await async static enum implements interface package
    private protected public of get set undefined NaN Infinity arguments eval globalThis console
    require module exports process Buffer Math Object Array String Number Boolean Symbol BigInt
    JSON Promise Map Set WeakMap WeakSet Date RegExp Error TypeError RangeError parseInt
    parseFloat isNaN isFinite setTimeout setInterval clearTimeout clearInterval""".split()
)


@cache
def reserved_names(language: Language) -> frozenset[str]:
    """Parole chiave, builtin e nomi globali comuni da non usare come nomi nuovi."""
    lang = Language(language)
    if lang is Language.PYTHON:
        return frozenset(keyword.kwlist) | frozenset(keyword.softkwlist) | frozenset(dir(builtins))
    return {
        Language.JAVA: _JAVA_RESERVED,
        Language.CPP: _CPP_RESERVED,
        Language.JAVASCRIPT: _JS_RESERVED,
    }[lang]


def identifiers_in(code: str) -> set[str]:
    """Tutte le parole che potrebbero essere identificatori (anche in stringhe e commenti:
    insieme volutamente largo per evitare collisioni)."""
    return set(_IDENT.findall(code))


class NameFactory:
    """Nomi nuovi senza collisioni con parole riservate, nomi del file e nomi già dati.

    Args:
        rng: generatore del campione.
        language: linguaggio (parole riservate).
        taken: nomi già presenti nel codice.
        pool: nomi naturali (per ``natural``); vuoto = solo nomi casuali.
    """

    def __init__(
        self,
        rng: random.Random,
        language: Language,
        taken: set[str],
        pool: tuple[str, ...] = (),
    ) -> None:
        self.rng = rng
        self.blocked = set(taken) | reserved_names(language)
        self.pool = pool
        self.fallbacks = 0

    def _free(self, name: str) -> bool:
        return name not in self.blocked and not (name.startswith("__") and name.endswith("__"))

    def random(self) -> str:
        """Lettera minuscola più 7 caratteri alfanumerici minuscoli."""
        alphabet = string.ascii_lowercase + string.digits
        while True:
            name = self.rng.choice(string.ascii_lowercase) + "".join(
                self.rng.choice(alphabet) for _ in range(7)
            )
            if self._free(name):
                self.blocked.add(name)
                return name

    def natural(self, tries: int = 50) -> str:
        """Un nome del pool non ancora usato; dopo ``tries`` tentativi, un nome casuale."""
        for _ in range(tries if self.pool else 0):
            name = self.rng.choice(self.pool)
            if self._free(name):
                self.blocked.add(name)
                return name
        self.fallbacks += 1
        return self.random()

    def new(self, kind: str) -> str:
        return self.natural() if kind == "natural" else self.random()


# ---------------------------------------------------------------------- punti fra istruzioni
@dataclass(frozen=True)
class Site:
    """Punto prima di un'istruzione: offset (in caratteri) di inizio riga e indentazione."""

    line_start: int
    indent: str
    statement: Node
    block: Node
    function_body: bool  # il blocco è il corpo di una funzione (non un blocco annidato)
    first: bool  # prima istruzione del blocco


def byte_to_char(code: str) -> list[int]:
    """Tabella offset in byte UTF-8 → offset in caratteri (lunghezza ``len(bytes) + 1``)."""
    table: list[int] = []
    for i, ch in enumerate(code):
        table.extend([i] * len(ch.encode("utf-8")))
    table.append(len(code))
    return table


def _inside_function(node: Node, language: Language) -> bool:
    kinds = FUNCTION_LIKE[language]
    cur = node.parent
    while cur is not None:
        if cur.type in kinds:
            return True
        cur = cur.parent
    return False


def statement_sites(code: str, language: Language, parsers: ParserService) -> list[Site]:
    """Punti prima delle istruzioni dei blocchi dentro funzioni, in ordine di posizione."""
    lang = Language(language)
    root = parsers.parse(code, lang).root_node
    to_char = byte_to_char(code)
    blocks = BLOCK_TYPES[lang]
    sites: list[Site] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type not in blocks or not _inside_function(node, lang):
            continue
        statements = [
            c
            for c in node.named_children
            if c.type not in ("comment", "line_comment", "block_comment")
        ]
        is_body = node.parent is not None and node.parent.type in FUNCTION_LIKE[lang]
        for i, stmt in enumerate(statements):
            start = to_char[stmt.start_byte]
            line_start = code.rfind("\n", 0, start) + 1
            prefix = code[line_start:start]
            if prefix.strip():
                continue  # l'istruzione non apre la riga
            sites.append(Site(line_start, prefix, stmt, node, is_body, i == 0))
    return sorted(sites, key=lambda s: s.line_start)


def insert_line(code: str, site: Site, text: str) -> str:
    """Inserisce ``text`` su una riga nuova prima dell'istruzione del punto."""
    return code[: site.line_start] + site.indent + text + "\n" + code[site.line_start :]
