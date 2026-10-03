"""Parser tree-sitter per i quattro linguaggi del benchmark (SPEC §20, ADR-005).

Le grammatiche sono fissate a versioni compatibili con ``tree-sitter==0.22.3`` (richiesto
da codebleu 0.7.0): ``language()`` restituisce un intero e l'ABI è al più 14.
"""

from __future__ import annotations

import importlib
from functools import cache

from tree_sitter import Language as TSLanguage
from tree_sitter import Node, Parser, Tree

from bench.domain.enums import Language

GRAMMAR_MODULES: dict[Language, str] = {
    Language.PYTHON: "tree_sitter_python",
    Language.JAVA: "tree_sitter_java",
    Language.CPP: "tree_sitter_cpp",
    Language.JAVASCRIPT: "tree_sitter_javascript",
}

# Tipi di nodo dei commenti nelle quattro grammatiche (Java distingue riga e blocco).
COMMENT_TYPES: frozenset[str] = frozenset({"comment", "line_comment", "block_comment"})


@cache
def ts_language(lang: Language) -> TSLanguage:
    """Oggetto ``Language`` di tree-sitter per un linguaggio del benchmark."""
    module = importlib.import_module(GRAMMAR_MODULES[Language(lang)])
    return TSLanguage(module.language())


class ParserService:
    """Parser in cache, uno per linguaggio."""

    def __init__(self) -> None:
        self._parsers: dict[Language, Parser] = {}

    def parser(self, lang: Language) -> Parser:
        """Parser per il linguaggio (creato alla prima richiesta)."""
        lang = Language(lang)
        if lang not in self._parsers:
            self._parsers[lang] = Parser(ts_language(lang))
        return self._parsers[lang]

    def parse(self, code: str, lang: Language) -> Tree:
        """Albero sintattico del codice."""
        return self.parser(lang).parse(code.encode("utf-8"))

    def has_error(self, code: str, lang: Language) -> bool:
        """``True`` se l'albero contiene nodi ``ERROR`` o mancanti."""
        return self.parse(code, lang).root_node.has_error


@cache
def default_parsers() -> ParserService:
    """Servizio di parsing condiviso dal processo."""
    return ParserService()


def iter_nodes(root: Node) -> list[Node]:
    """Tutti i nodi del sottoalbero, in ordine di visita in profondità."""
    out: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.children))
    return out
