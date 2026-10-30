"""T2.4 in k funzioni (SPEC §12; ADR-014 §2.3; TODO 32; decisioni dell'utente del 26 e 30 ottobre
2026).

Si applica al codice a livello di file con **almeno 2 funzioni**: le classi di ClassEval (L3) e i
programmi CodeNet di L2 test. Le funzioni sono quelle più esterne del file (in ClassEval i metodi
della classe), ``main`` e ``__init__`` comprese come tutte le altre. Si tengono ``k`` funzioni
estratte con l'RNG dell'attacco (``sample_rng``, SPEC §18) e si tolgono le altre, con i loro
decoratori (Python), la dichiarazione ``template`` (C++) o la dichiarazione ``const f = ...``
(JavaScript); tutto il resto del file resta. In ClassEval resta così la classe con i soli k metodi.

Griglia ``k ∈ {1, …, 10}`` (10 = massimo di funzioni di una classe di ClassEval): la curva in k va
da 1 al totale; con ``k ≥ n`` il campione resta com'è (``UNCHANGED``). Un file con meno di 2
funzioni, o con codice vuoto, è ``NOT_APPLICABLE`` (motivo nel dettaglio). Il dettaglio registra
``n``, ``k``, ``kept`` (nomi tenuti), ``removed`` e se il risultato si analizza ancora.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tree_sitter import Node

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.generation.code_extractor import _function_name
from bench.lang.functions import outermost_functions
from bench.registry import ATTACKS

MIN_FUNCTIONS = 2


def removal_anchor(fn: Node, lang: Language) -> Node:
    """Nodo da togliere insieme alla funzione (decoratori, ``template``, ``const f = ...``)."""
    node = fn
    parent = node.parent
    if lang is Language.PYTHON and parent is not None and parent.type == "decorated_definition":
        return parent
    if lang is Language.CPP and parent is not None and parent.type == "template_declaration":
        return parent
    if lang is Language.JAVASCRIPT:
        while parent is not None and parent.type in ("variable_declarator", "lexical_declaration",
                                                     "variable_declaration"):  # fmt: skip
            node, parent = parent, parent.parent
    return node


def _line_span(src: bytes, start: int, end: int) -> tuple[int, int]:
    """Estende il tratto alle righe intere se prima e dopo ci sono solo spazi."""
    line_start = src.rfind(b"\n", 0, start) + 1
    if not src[line_start:start].strip():
        start = line_start
    line_end = src.find(b"\n", end)
    line_end = len(src) if line_end < 0 else line_end
    if not src[end:line_end].strip():
        end = min(len(src), line_end + 1)
    return start, end


@ATTACKS.register("T2.4k")
class TruncateKAttack(Attack):
    """T2.4 in k: tiene k funzioni del file, tolte le altre."""

    attack_id: ClassVar[str] = "T2.4k"
    group: ClassVar[str] = "T2"

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        k = self.params.get("k")
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ConfigError(f"T2.4k: k must be a positive integer, not {k!r}")

    def safe_apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        if self.supports(language) and not code.strip():
            return AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {"reason": "extraction_failed"})
        return super().safe_apply(code, language, ctx)

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        lang = Language(language)
        tree = ctx.parsers.parse(code, lang)
        functions = outermost_functions(tree.root_node, lang)
        functions.sort(key=lambda f: f.start_byte)
        n, k = len(functions), int(self.params["k"])
        detail: dict[str, Any] = {"k": k, "n": n}
        if n < MIN_FUNCTIONS:
            return AttackOutcome(
                code, AttackStatus.NOT_APPLICABLE, {**detail, "reason": "single_function"}
            )
        names = [_function_name(f, lang) or f"<{f.start_point[0]}>" for f in functions]
        if k >= n:
            return outcome(code, code, {**detail, "kept": names, "removed": 0})
        keep = sorted(ctx.rng.sample(range(n), k))
        src = code.encode("utf-8")
        spans = []
        for i, fn in enumerate(functions):
            if i not in keep:
                anchor = removal_anchor(fn, lang)
                spans.append(_line_span(src, anchor.start_byte, anchor.end_byte))
        new = src
        for start, end in sorted(spans, reverse=True):
            new = new[:start] + new[end:]
        text = new.decode("utf-8")
        detail.update(
            kept=[names[i] for i in keep],
            removed=n - k,
            parses=not ctx.parsers.has_error(text, lang) or tree.root_node.has_error,
        )
        return outcome(code, text, detail)
