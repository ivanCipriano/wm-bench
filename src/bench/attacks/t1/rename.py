"""T1.4 rinomina di variabili locali e parametri (SPEC §12; ADR-012).

Griglia ``ratio ∈ {0.25, 0.5, 0.75, 1.0} × name_kind ∈ {random, natural}``. Un **candidato** è una
coppia (funzione, nome) di un parametro o di una variabile locale; se ne rinominano
``max(1, round(ratio × candidati))``, scelti con il seme dell'attacco. Tutte le occorrenze del nome
nella funzione cambiano insieme. Nomi nuovi: ``random`` = lettera + 7 caratteri alfanumerici
minuscoli; ``natural`` = dalla raccolta di nomi del linguaggio (``attack_pools``). Mai collisioni
con parole riservate, builtin o parole già presenti nel file.

**Python** (``libcst`` con ``ScopeProvider``): gruppi di assegnazioni dello stesso nome in uno scope
di funzione, fatti solo di nomi e parametri (niente ``import``, ``def``, ``class``). Esclusi:
``self``, ``cls``, nomi in ``global``/``nonlocal``, nomi usati come argomento con nome in una
chiamata (``f(n=...)``), funzioni che usano ``locals``, ``vars``, ``eval`` o ``exec``.

**Java, C++, JavaScript** (tree-sitter, analisi limitata a parametri e dichiarazioni locali di ogni
funzione più esterna): mai campi, membri, globali, nomi di funzioni o classi, ``entry_point``. Un
nome è escluso anche se, nella funzione, compare in una posizione che non è una variabile (membro,
nome qualificato, etichetta) o come funzione chiamata: l'analisi resta conservativa.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from tree_sitter import Node

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.t1.common import NameFactory, byte_to_char, identifiers_in, reserved_names
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.lang.functions import outermost_functions
from bench.lang.parsers import iter_nodes
from bench.registry import ATTACKS

_PY_DYNAMIC_CALL = re.compile(r"\b(?:locals|vars|eval|exec)\s*\(")


@dataclass
class Candidate:
    """Nome rinominabile in una funzione: intervalli (in caratteri) da riscrivere."""

    name: str
    function: int
    spans: list[tuple[int, int]] = field(default_factory=list)
    shorthand: list[tuple[int, int]] = field(default_factory=list)  # JS: {x} -> {x: nuovo}

    @property
    def first(self) -> int:
        return min(s for s, _ in [*self.spans, *self.shorthand])


# ---------------------------------------------------------------------- Python (libcst)
def _python_spans(
    assignments: list[Any], span: Callable[[Any], tuple[int, int]]
) -> list[tuple[int, int]] | None:
    """Intervalli di un gruppo di assegnazioni e dei loro usi; ``None`` se il gruppo contiene
    altro che nomi e parametri (``import``, ``def``, ``class``, nomi in stringhe)."""
    import libcst as cst

    spans: list[tuple[int, int]] = []
    for assignment in assignments:
        node = getattr(assignment, "node", None)
        if isinstance(node, cst.Param):
            spans.append(span(node.name))
        elif isinstance(node, cst.Name):
            spans.append(span(node))
        else:
            return None
        for access in assignment.references:
            if not isinstance(access.node, cst.Name):
                return None
            spans.append(span(access.node))
    return sorted(set(spans))


def python_candidates(code: str, entry_point: str | None) -> list[Candidate]:
    """Gruppi rinominabili di uno scope di funzione."""
    import libcst as cst
    from libcst.metadata import FunctionScope, MetadataWrapper, PositionProvider, ScopeProvider

    tree = ast.parse(code)
    keyword_args = {kw.arg for kw in ast.walk(tree) if isinstance(kw, ast.keyword) and kw.arg}
    declared_outer: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global | ast.Nonlocal):
            declared_outer.update(node.names)
    wrapper = MetadataWrapper(cst.parse_module(code))
    scopes = wrapper.resolve(ScopeProvider)
    positions = wrapper.resolve(PositionProvider)
    line_starts = [0]
    for line in code.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def span(node: Any) -> tuple[int, int]:
        rng = positions[node]
        return (
            line_starts[rng.start.line - 1] + rng.start.column,
            line_starts[rng.end.line - 1] + rng.end.column,
        )

    seen: set[int] = set()
    out: list[Candidate] = []
    function_index = 0
    for scope in scopes.values():
        if not isinstance(scope, FunctionScope) or id(scope) in seen:
            continue
        seen.add(id(scope))
        function_index += 1
        # Funzioni con locals/vars/eval/exec (anche annidate): nessuna rinomina.
        if _PY_DYNAMIC_CALL.search(wrapper.module.code_for_node(scope.node)):
            continue
        groups: dict[str, list[Any]] = defaultdict(list)
        for assignment in scope.assignments:
            groups[assignment.name].append(assignment)
        for name, assignments in sorted(groups.items()):
            if (
                name in ("self", "cls")
                or name == entry_point
                or name in declared_outer
                or name in keyword_args
                or (name.startswith("__") and name.endswith("__"))
            ):
                continue
            spans = _python_spans(assignments, span)
            if spans:
                out.append(Candidate(name, function_index, spans))
    return sorted(out, key=lambda c: (c.first, c.name))


# ---------------------------------------------------------------------- tree-sitter
_TS_IDENT = "identifier"


def _cpp_declarator_names(node: Node | None) -> list[Node]:
    """Identificatori dichiarati da un dichiaratore C++ (puntatori, riferimenti, array)."""
    out: list[Node] = []
    while node is not None:
        if node.type == "identifier":
            return [node]
        if node.type == "structured_binding_declarator":
            return [c for c in node.named_children if c.type == "identifier"]
        if node.type == "function_declarator":
            return []  # dichiarazione di funzione, non una variabile
        nxt = node.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in node.named_children if c.type.endswith("declarator")), None)
            if nxt is None:
                nxt = next((c for c in node.named_children if c.type == "identifier"), None)
        node = nxt
    return out


def _js_pattern_names(node: Node | None) -> list[Node]:
    """Identificatori legati da un pattern JavaScript (parametri, destrutturazioni)."""
    if node is None:
        return []
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        return [node]
    if node.type == "pair_pattern":
        return _js_pattern_names(node.child_by_field_name("value"))
    if node.type in ("assignment_pattern", "object_assignment_pattern"):
        return _js_pattern_names(node.child_by_field_name("left"))
    if node.type in ("object_pattern", "array_pattern", "rest_pattern", "formal_parameters"):
        return [n for c in node.named_children for n in _js_pattern_names(c)]
    return []


_JAVA_NAMED = frozenset(
    {"formal_parameter", "catch_formal_parameter", "resource", "enhanced_for_statement"}
)


def _java_declared(node: Node) -> list[Node]:
    t = node.type
    parent = node.parent.type if node.parent is not None else ""
    if t in _JAVA_NAMED or (
        t == "variable_declarator" and parent in ("local_variable_declaration", "spread_parameter")
    ):
        name = node.child_by_field_name("name")
        return [name] if name is not None and name.type == _TS_IDENT else []
    if t == "lambda_expression":
        params = node.child_by_field_name("parameters")
        if params is not None and params.type == _TS_IDENT:
            return [params]
        if params is not None and params.type == "inferred_parameters":
            return [c for c in params.named_children if c.type == _TS_IDENT]
    return []


def _cpp_declared(node: Node) -> list[Node]:
    if node.type in ("parameter_declaration", "optional_parameter_declaration", "for_range_loop"):
        return _cpp_declarator_names(node.child_by_field_name("declarator"))
    if node.type == "declaration":
        return [
            n
            for decl in node.children_by_field_name("declarator")
            for n in _cpp_declarator_names(decl)
        ]
    return []


def _js_declared(node: Node) -> list[Node]:
    t = node.type
    if t == "formal_parameters":
        return _js_pattern_names(node)
    if t == "variable_declarator":
        return _js_pattern_names(node.child_by_field_name("name"))
    if t in ("arrow_function", "catch_clause"):
        return _js_pattern_names(node.child_by_field_name("parameter"))
    if t == "for_in_statement" and node.child_by_field_name("kind") is not None:
        return _js_pattern_names(node.child_by_field_name("left"))
    return []


_DECLARED = {Language.JAVA: _java_declared, Language.CPP: _cpp_declared,
             Language.JAVASCRIPT: _js_declared}  # fmt: skip


def _declared(fn: Node, lang: Language) -> list[Node]:
    """Identificatori dichiarati come parametri o variabili locali dentro una funzione."""
    collect = _DECLARED[Language(lang)]
    return [n for node in iter_nodes(fn) for n in collect(node)]


# Genitori di un identificatore che non è un uso della variabile (membro, nome qualificato,
# etichetta, dichiarazione di funzione o classe).
_BLOCKED_PARENTS = {
    Language.JAVA: frozenset({
        "method_reference", "scoped_identifier", "labeled_statement", "break_statement",
        "continue_statement", "marker_annotation", "annotation", "method_declaration",
        "constructor_declaration", "class_declaration", "switch_label", "enum_constant",
    }),
    Language.CPP: frozenset({
        "qualified_identifier", "template_function", "labeled_statement", "goto_statement",
        "function_declarator",
    }),
    Language.JAVASCRIPT: frozenset({
        "function_declaration", "class_declaration", "labeled_statement", "break_statement",
        "continue_statement", "generator_function_declaration", "function_expression", "class",
    }),
}  # fmt: skip
# (genitore, campo): il nome del membro o della funzione chiamata.
_BLOCKED_FIELDS = {
    Language.JAVA: (("field_access", "field"), ("method_invocation", "name")),
    Language.CPP: (("call_expression", "function"),),
    Language.JAVASCRIPT: (("call_expression", "function"),),
}


def _blocked_position(node: Node, lang: Language) -> bool:
    """Occorrenza di un identificatore che non è un uso della variabile (membro, nome
    qualificato, etichetta, funzione chiamata)."""
    parent = node.parent
    if parent is None:
        return False
    if any(
        parent.type == ptype and parent.child_by_field_name(fname) == node
        for ptype, fname in _BLOCKED_FIELDS[lang]
    ):
        return True
    return parent.type in _BLOCKED_PARENTS[lang]


_OCCURRENCE_TYPES = frozenset(
    {"identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern"}
)


def ts_candidates(
    code: str, lang: Language, parsers: Any, entry_point: str | None
) -> list[Candidate]:
    """Candidati delle funzioni più esterne (Java, C++, JavaScript)."""
    root = parsers.parse(code, lang).root_node
    to_char = byte_to_char(code)
    functions = outermost_functions(root, lang)
    inside: set[int] = set()
    for fn in functions:
        inside.update(n.id for n in iter_nodes(fn))
    # Nomi visibili fuori dalle funzioni (campi, globali, classi, import, macro): mai rinominati.
    outer = {
        n.text.decode("utf-8")
        for n in iter_nodes(root)
        if n.id not in inside and n.child_count == 0 and n.text is not None
    }
    outer |= {
        name.text.decode("utf-8")
        for fn in functions
        for name in [fn.child_by_field_name("name")]
        if name is not None and name.text is not None
    }
    reserved = reserved_names(lang) | outer | ({entry_point} if entry_point else set())
    out: list[Candidate] = []
    for index, fn in enumerate(functions):
        declared = {n.text.decode("utf-8") for n in _declared(fn, lang) if n.text is not None}
        occurrences: dict[str, list[Node]] = defaultdict(list)
        blocked: set[str] = set()
        for node in iter_nodes(fn):
            if node.type not in _OCCURRENCE_TYPES or node.text is None:
                continue
            name = node.text.decode("utf-8")
            if name not in declared:
                continue
            if node.type == "identifier" and _blocked_position(node, lang):
                blocked.add(name)
            occurrences[name].append(node)
        for name in sorted(declared - blocked - reserved):
            cand = Candidate(name, index)
            for node in occurrences[name]:
                span = (to_char[node.start_byte], to_char[node.end_byte])
                if node.type == "identifier":
                    cand.spans.append(span)
                else:
                    cand.shorthand.append(span)
            if cand.spans or cand.shorthand:
                out.append(cand)
    return sorted(out, key=lambda c: (c.first, c.name))


def rewrite(code: str, renames: list[tuple[Candidate, str]]) -> str:
    """Applica le rinomine (dal fondo del codice verso l'inizio)."""
    edits: list[tuple[int, int, str]] = []
    for cand, new in renames:
        edits.extend((s, e, new) for s, e in cand.spans)
        edits.extend((s, e, f"{cand.name}: {new}") for s, e in cand.shorthand)
    out = code
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]
    return out


@ATTACKS.register("T1.4")
class RenameAttack(Attack):
    """T1.4: rinomina di una frazione ``ratio`` dei candidati con nomi ``name_kind``."""

    attack_id: ClassVar[str] = "T1.4"
    group: ClassVar[str] = "T1"
    needs_pools: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        if not 0 < float(self.params.get("ratio", 0)) <= 1:
            raise ConfigError("T1.4: ratio must be in (0, 1]")
        if self.params.get("name_kind") not in ("random", "natural"):
            raise ConfigError("T1.4: name_kind must be random or natural")

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        lang = Language(language)
        entry = ctx.problem.entry_point if ctx.problem is not None else None
        kind = str(self.params["name_kind"])
        if kind == "natural" and not ctx.pools.names:
            raise ConfigError("T1.4 natural: empty name pool (run stage attack_pools)")
        had_error = ctx.parsers.has_error(code, lang)
        if lang is Language.PYTHON:
            try:
                candidates = python_candidates(code, entry)
            except SyntaxError as exc:
                return tool_error(code, f"SyntaxError: {exc}", {"n_candidates": 0})
        else:
            candidates = ts_candidates(code, lang, ctx.parsers, entry)
        n = len(candidates)
        k = max(1, round(float(self.params["ratio"]) * n)) if n else 0
        chosen = sorted(ctx.rng.sample(range(n), k))
        names = NameFactory(ctx.rng, lang, identifiers_in(code), ctx.pools.names)
        renames = [(candidates[i], names.new(kind)) for i in chosen]
        new = rewrite(code, renames)
        detail = {
            "n_candidates": n,
            "n_renamed": k,
            "n_occurrences": sum(len(c.spans) + len(c.shorthand) for c, _ in renames),
            "natural_fallbacks": names.fallbacks,
        }
        if lang is Language.PYTHON:
            try:
                ast.parse(new)
            except SyntaxError as exc:
                return tool_error(code, f"renamed code does not parse: {exc}", detail)
        elif not had_error and ctx.parsers.has_error(new, lang):
            return tool_error(code, "renamed code does not parse", detail)
        return outcome(code, new, detail)
