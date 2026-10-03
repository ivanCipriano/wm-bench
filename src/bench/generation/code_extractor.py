"""Estrazione del codice dall'output chat (SPEC §10.6, D4; ADR-006).

La stessa regola vale per la baseline e per tutti i metodi in generazione:

1. primo blocco recintato chiuso con il tag del linguaggio atteso (alias compresi);
   altrimenti il primo blocco chiuso senza tag; altrimenti un blocco aperto e mai chiuso
   (output troncato da ``max_new_tokens``), con il tag atteso o senza tag; altrimenti
   l'intero testo, se non è vuoto e si analizza senza errori;
2. per i dataset a completamento di funzione (HumanEval+, HumanEvalPack), se il codice non
   definisce l'``entry_point``, si antepone il prompt del dataset; per MBPP+ no, perché il
   prompt è solo testo;
3. se non si trova nulla: ``code=""`` ed ``extraction_ok=False``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from tree_sitter import Node

from bench.domain.enums import Language
from bench.domain.models import Problem
from bench.lang.functions import FUNCTION_TYPES
from bench.lang.parsers import ParserService, default_parsers

LANGUAGE_TAGS: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"python", "py", "python3"}),
    Language.JAVA: frozenset({"java"}),
    Language.CPP: frozenset({"cpp", "c++", "cc", "cxx", "hpp"}),
    Language.JAVASCRIPT: frozenset({"javascript", "js", "node", "jsx"}),
}
# Dataset a completamento di funzione: se manca la definizione si antepone il prompt.
PREPEND_PROMPT_DATASETS: frozenset[str] = frozenset({"humanevalplus", "humanevalpack"})

_CLOSED = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.DOTALL)
_OPEN = re.compile(r"```[ \t]*([^\n`]*)\n(.*)\Z", re.DOTALL)


class CodeExtractor(ABC):
    """Strategia di estrazione del codice (SPEC §7.5)."""

    @abstractmethod
    def extract(self, raw_output: str, problem: Problem) -> tuple[str, bool]:
        """Restituisce ``(codice, ok)``."""


def _tag(info: str) -> str:
    return info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""


def _function_name(node: Node, lang: Language) -> str | None:
    """Nome della funzione definita dal nodo (``None`` se anonima)."""
    name = node.child_by_field_name("name")
    if name is not None and name.text is not None:
        return name.text.decode("utf-8", errors="replace")
    if lang is Language.CPP:
        declarator = node.child_by_field_name("declarator")
        while declarator is not None and declarator.type != "function_declarator":
            declarator = declarator.child_by_field_name("declarator")
        inner = declarator.child_by_field_name("declarator") if declarator is not None else None
        if inner is not None and inner.text is not None:
            return inner.text.decode("utf-8", errors="replace").split("::")[-1]
        return None
    parent = node.parent
    if lang is Language.JAVASCRIPT and parent is not None and parent.type == "variable_declarator":
        var = parent.child_by_field_name("name")
        if var is not None and var.text is not None:
            return var.text.decode("utf-8", errors="replace")
    return None


def defines(
    code: str, lang: Language, entry_point: str, parsers: ParserService | None = None
) -> bool:
    """``True`` se il codice definisce una funzione o un metodo chiamato ``entry_point``."""
    root = (parsers or default_parsers()).parse(code, lang).root_node
    types = FUNCTION_TYPES[Language(lang)]
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types and _function_name(node, lang) == entry_point:
            return True
        stack.extend(node.children)
    return False


class FencedCodeExtractor(CodeExtractor):
    """Regola unica di estrazione (D4).

    Args:
        parsers: servizio di parsing.
    """

    def __init__(self, parsers: ParserService | None = None) -> None:
        self.parsers = parsers or default_parsers()

    def select(self, raw_output: str, lang: Language) -> str | None:
        """Passo 1: il testo del codice scelto, oppure ``None``."""
        tags = LANGUAGE_TAGS[Language(lang)]
        closed = [(_tag(m.group(1)), m.group(2)) for m in _CLOSED.finditer(raw_output)]
        for wanted in (lambda t: t in tags, lambda t: t == ""):
            for tag, body in closed:
                if wanted(tag) and body.strip():
                    return body
        # Numero dispari di recinzioni: l'ultima apre un blocco mai chiuso (output troncato).
        if raw_output.count("```") % 2 == 1:
            opened = _OPEN.match(raw_output[raw_output.rfind("```") :])
            tag_ok = opened is not None and _tag(opened.group(1)) in (tags | {""})
            if opened is not None and tag_ok and opened.group(2).strip():
                return opened.group(2)
        text = raw_output.strip()
        if text and "```" not in text and not self.parsers.has_error(text, lang):
            return text + "\n"
        return None

    def extract(self, raw_output: str, problem: Problem) -> tuple[str, bool]:
        """Codice estratto per il problema (SPEC §10.6)."""
        lang = Language(problem.language)
        code = self.select(raw_output or "", lang)
        if code is None:
            return "", False
        if (
            problem.dataset in PREPEND_PROMPT_DATASETS
            and problem.entry_point
            and not defines(code, lang, problem.entry_point, self.parsers)
        ):
            prompt = (
                problem.prompt_text
                if problem.prompt_text.endswith("\n")
                else problem.prompt_text + "\n"
            )
            code = prompt + code
        return code, True
