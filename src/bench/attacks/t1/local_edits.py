"""T1.5 modifiche localizzate senza effetto sul comportamento (SPEC §12; ADR-012).

Griglia ``k ∈ {1, 3, 5}``. Per ognuna delle ``k`` modifiche si sceglie con il seme dell'attacco un
tipo fra quelli che hanno punti disponibili, poi un punto (``statement_sites``); dopo ogni modifica
il codice si rianalizza. Tipi:

- ``unused_var``: variabile temporanea inutilizzata (``x = 0`` / ``int x = 0;`` /
  ``[[maybe_unused]] int x = 0;`` / ``let x = 0;``);
- ``stderr_log``: log su **stderr**, mai su stdout (CodeNet confronta stdout):
  ``print(..., file=__import__("sys").stderr)`` / ``System.err.println`` / ``std::cerr`` (con
  ``#include <iostream>`` se manca) / ``console.error``; solo nel corpo diretto delle funzioni,
  non dentro cicli o rami;
- ``extract``: un'espressione binaria senza effetti collaterali, presa da un'istruzione semplice
  (espressione, ``return``, dichiarazione con un solo dichiaratore) che non contiene chiamate,
  ``new``, incrementi o assegnazioni annidate, si sposta in una temporanea prima dell'istruzione
  (``t = (...)`` / ``var`` / ``auto`` / ``const``). Mai dal lato destro di ``&&``, ``||``, ``and``,
  ``or``, ``??`` né dai rami di un'espressione condizionale (sarebbero valutati sempre), mai da
  argomenti di template, mai espressioni di soli letterali; in C++ mai ``<<`` e ``>>`` (possono
  essere operatori di stream).

Punti esclusi per tutti i tipi: prima di ``super(...)``/``this(...)`` in un costruttore Java,
prima della docstring di Python e prima di una direttiva JavaScript (``"use strict"``).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar

from tree_sitter import Node

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.t1.common import (
    NameFactory,
    Site,
    byte_to_char,
    identifiers_in,
    insert_line,
    statement_sites,
)
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.lang.parsers import iter_nodes
from bench.registry import ATTACKS

EDIT_TYPES = ("unused_var", "stderr_log", "extract")

_BINARY = {
    Language.PYTHON: frozenset({"binary_operator", "comparison_operator", "boolean_operator"}),
    Language.JAVA: frozenset({"binary_expression"}),
    Language.CPP: frozenset({"binary_expression"}),
    Language.JAVASCRIPT: frozenset({"binary_expression"}),
}
_SIMPLE_STATEMENTS = {
    Language.PYTHON: frozenset({"expression_statement", "return_statement"}),
    Language.JAVA: frozenset(
        {"expression_statement", "return_statement", "local_variable_declaration"}
    ),
    Language.CPP: frozenset({"expression_statement", "return_statement", "declaration"}),
    Language.JAVASCRIPT: frozenset(
        {"expression_statement", "return_statement", "lexical_declaration", "variable_declaration"}
    ),
}
# Nodi con effetti collaterali o con uno scope proprio: l'istruzione non è candidata.
_FORBIDDEN = {
    Language.PYTHON: frozenset({
        "call", "lambda", "list_comprehension", "set_comprehension", "dictionary_comprehension",
        "generator_expression", "named_expression", "await", "yield", "augmented_assignment",
    }),
    Language.JAVA: frozenset({
        "method_invocation", "object_creation_expression", "array_creation_expression",
        "update_expression", "lambda_expression", "switch_expression", "method_reference",
        "instanceof_expression",  # un pattern lega una variabile allo scope della condizione
    }),
    Language.CPP: frozenset({
        "call_expression", "new_expression", "delete_expression", "update_expression",
        "lambda_expression", "co_await_expression", "template_argument_list", "sizeof_expression",
        "decltype", "comma_expression", "user_defined_literal",
    }),
    Language.JAVASCRIPT: frozenset({
        "call_expression", "new_expression", "update_expression", "arrow_function", "function",
        "function_expression", "await_expression", "yield_expression", "template_string",
        "augmented_assignment_expression", "sequence_expression",
    }),
}  # fmt: skip
# Dichiarazioni costanti o statiche: il valore può servire come espressione costante (array,
# template, case) o essere calcolato una volta sola.
_CONSTANT_WORDS = frozenset({"const", "constexpr", "constinit", "consteval", "static", "final"})
_ASSIGN = frozenset({"assignment", "assignment_expression", "augmented_assignment",
                     "augmented_assignment_expression"})  # fmt: skip
_SHORT_CIRCUIT = frozenset({"&&", "||", "??", "and", "or"})
_CONDITIONAL = frozenset({"conditional_expression", "ternary_expression"})
_LITERALS = frozenset({
    "integer", "float", "string", "true", "false", "none", "decimal_integer_literal",
    "decimal_floating_point_literal", "hex_integer_literal", "character_literal",
    "string_literal", "number_literal", "char_literal", "raw_string_literal", "number",
    "null_literal", "null", "nullptr", "string_fragment", "escape_sequence", "string_content",
})  # fmt: skip


def _operator(node: Node) -> str:
    op = node.child_by_field_name("operator")
    if op is not None and op.text is not None:
        return op.text.decode("utf-8")
    # Python boolean_operator/comparison: il token fra gli operandi.
    tokens = [c.type for c in node.children if not c.is_named]
    return tokens[0] if tokens else ""


def _excluded_site(site: Site, lang: Language) -> bool:
    stmt = site.statement
    if lang is Language.JAVA and stmt.type == "explicit_constructor_invocation":
        return True
    if site.first and site.function_body and stmt.type == "expression_statement":
        first = stmt.named_children[0] if stmt.named_children else None
        if first is not None and first.type == "string":  # docstring o direttiva JS
            return True
    return False


def _statement_ok(stmt: Node, lang: Language) -> bool:
    if stmt.type not in _SIMPLE_STATEMENTS[lang]:
        return False
    if stmt.type in ("local_variable_declaration", "declaration", "lexical_declaration",
                     "variable_declaration"):  # fmt: skip
        declarators = [c for c in stmt.named_children if c.type in (
            "variable_declarator", "init_declarator", "identifier")]  # fmt: skip
        if len(declarators) != 1:
            return False
        qualifiers = [
            c for c in iter_nodes(stmt)
            if c.type in ("type_qualifier", "storage_class_specifier", "modifiers")
        ]  # fmt: skip
        if any(
            word in _CONSTANT_WORDS
            for q in qualifiers
            for word in (q.text or b"").decode("utf-8").split()
        ):
            return False
    top_assign = None
    if stmt.type == "expression_statement" and stmt.named_children:
        first = stmt.named_children[0]
        if first.type in _ASSIGN:
            top_assign = first
    for node in iter_nodes(stmt):
        if node.type in _FORBIDDEN[lang]:
            return False
        if node.type in _ASSIGN and node is not top_assign and node != top_assign:
            return False
    return True


def _excluded_expr(node: Node, stmt: Node, lang: Language) -> bool:
    """Sottoespressioni valutate solo a volte o in posizione non di valore."""
    child = node
    cur = node.parent
    while cur is not None and cur != stmt:
        if cur.type in _BINARY[lang] and _operator(cur) in _SHORT_CIRCUIT:
            right = cur.child_by_field_name("right") or cur.named_children[-1]
            if right == child:
                return True
        if cur.type in _CONDITIONAL:
            cond = cur.child_by_field_name("condition")
            if cond is None and cur.named_children:
                # Python: "a if c else b" -> [a, c, b]; il valore valutato sempre è c.
                cond = cur.named_children[1] if lang is Language.PYTHON else cur.named_children[0]
            if child != cond:
                return True
        if cur.type in _ASSIGN and cur.child_by_field_name("left") == child:
            return True  # lato sinistro di un'assegnazione
        if (
            lang is Language.CPP
            and cur.type in ("init_declarator",)
            and (cur.child_by_field_name("declarator") == child)
        ):
            return True
        child = cur
        cur = cur.parent
    return False


def _expr_ok(node: Node, lang: Language) -> bool:
    leaves = [n for n in iter_nodes(node) if n.child_count == 0 and n.is_named]
    if not any(n.type == "identifier" for n in leaves):
        return False  # solo letterali (in Java può essere un'espressione costante)
    return not (
        lang is Language.CPP
        and any(
            _operator(n) in ("<<", ">>") for n in iter_nodes(node) if n.type == "binary_expression"
        )
    )


def _extract_candidates(site: Site, lang: Language) -> list[Node]:
    stmt = site.statement
    if not _statement_ok(stmt, lang):
        return []
    return [
        n
        for n in iter_nodes(stmt)
        if n.type in _BINARY[lang] and _expr_ok(n, lang) and not _excluded_expr(n, stmt, lang)
    ]


def _unused_var(lang: Language, name: str) -> str:
    return {
        Language.PYTHON: f"{name} = 0",
        Language.JAVA: f"int {name} = 0;",
        Language.CPP: f"[[maybe_unused]] int {name} = 0;",
        Language.JAVASCRIPT: f"let {name} = 0;",
    }[lang]


def _log(lang: Language, message: str) -> str:
    return {
        Language.PYTHON: f'print("{message}", file=__import__("sys").stderr)',
        Language.JAVA: f'System.err.println("{message}");',
        Language.CPP: f'std::cerr << "{message}" << std::endl;',
        Language.JAVASCRIPT: f'console.error("{message}");',
    }[lang]


def _temp(lang: Language, name: str, expr: str) -> str:
    return {
        Language.PYTHON: f"{name} = ({expr})",
        Language.JAVA: f"var {name} = ({expr});",
        Language.CPP: f"auto {name} = ({expr});",
        Language.JAVASCRIPT: f"const {name} = ({expr});",
    }[lang]


def _ensure_iostream(code: str) -> str:
    if "<iostream>" in code or "<bits/stdc++.h>" in code:
        return code
    return "#include <iostream>\n" + code


@ATTACKS.register("T1.5")
class LocalEditsAttack(Attack):
    """T1.5: ``k`` modifiche localizzate."""

    attack_id: ClassVar[str] = "T1.5"
    group: ClassVar[str] = "T1"

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        if int(self.params.get("k", 0)) < 1:
            raise ConfigError("T1.5: k must be >= 1")

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        lang = Language(language)
        had_error = ctx.parsers.has_error(code, lang)
        names = NameFactory(ctx.rng, lang, identifiers_in(code))
        new = code
        edits: list[str] = []
        for _ in range(int(self.params["k"])):
            sites = [
                s for s in statement_sites(new, lang, ctx.parsers) if not _excluded_site(s, lang)
            ]
            top = [s for s in sites if s.function_body]
            extract = [(s, e) for s in sites for e in _extract_candidates(s, lang)]
            options: dict[str, Callable[[str], str]] = {}
            if sites:
                options["unused_var"] = partial(self._add_unused, lang=lang, ctx=ctx, sites=sites,
                                                names=names)  # fmt: skip
            if top:
                options["stderr_log"] = partial(self._add_log, lang=lang, ctx=ctx, sites=top)
            if extract:
                options["extract"] = partial(self._add_extract, lang=lang, ctx=ctx,
                                             candidates=extract, names=names)  # fmt: skip
            if not options:
                break
            kind = ctx.rng.choice(sorted(options))
            new = options[kind](new)
            edits.append(kind)
        detail = {
            "k": int(self.params["k"]),
            "n_edits": len(edits),
            "edits": edits,
            **{f"n_{t}": edits.count(t) for t in EDIT_TYPES},
        }
        if not had_error and ctx.parsers.has_error(new, lang):
            return tool_error(code, "the edited code no longer parses", detail)
        return outcome(code, new, detail)

    @staticmethod
    def _add_unused(
        code: str, lang: Language, ctx: AttackContext, sites: list[Site], names: NameFactory
    ) -> str:
        site = ctx.rng.choice(sites)
        return insert_line(code, site, _unused_var(lang, names.random()))

    @staticmethod
    def _add_log(code: str, lang: Language, ctx: AttackContext, sites: list[Site]) -> str:
        site = ctx.rng.choice(sites)
        message = f"trace {ctx.rng.randrange(16**6):06x}"
        new = insert_line(code, site, _log(lang, message))
        return _ensure_iostream(new) if lang is Language.CPP else new

    @staticmethod
    def _add_extract(
        code: str,
        lang: Language,
        ctx: AttackContext,
        candidates: list[tuple[Site, Node]],
        names: NameFactory,
    ) -> str:
        site, expr = ctx.rng.choice(candidates)
        to_char = byte_to_char(code)
        start, end = to_char[expr.start_byte], to_char[expr.end_byte]
        name = names.random()
        replaced = code[:start] + name + code[end:]
        return insert_line(replaced, site, _temp(lang, name, code[start:end]))
