"""T2.4 troncamento (SPEC §12; ADR-014 §2; decisioni dell'utente del 26 ottobre 2026).

Griglia ``p ∈ {0.75, 0.5}``: si tengono le prime ``max(1, ⌊p·n⌋)`` **istruzioni** del corpo
bersaglio e si tolgono le altre (prefisso puro, senza aggiungere nulla: il codice può non
compilare o fallire i test, come prevede lo SPEC).

**Corpo bersaglio:**

- con la funzione d'ingresso del problema (``entry_point``, L1): il corpo di quella funzione (la
  prima definita con quel nome; in JavaScript anche ``const f = (…) => {…}``);
- senza funzione d'ingresso (programmi completi di CodeNet, L2): il corpo di ``main`` se c'è una
  funzione con quel nome (Java, C++ e, quando c'è, gli altri linguaggi); altrimenti le istruzioni di
  **primo livello del file**, escluse le definizioni di funzioni e le importazioni (tipicamente gli
  script Python e JavaScript).

**Istruzione:** figlio diretto con nome del corpo (blocco della funzione o radice del file),
esclusi i commenti e, in Python, la docstring (resta). In un corpo di funzione si toglie il
tratto dalla fine dell'ultima istruzione tenuta alla fine dell'ultima istruzione (commenti
compresi, graffa di chiusura esclusa); a livello di file si tolgono le singole istruzioni
contate oltre la soglia, lasciando importazioni e definizioni dove sono.

**Stati:** ``UNCHANGED`` se si tengono tutte le istruzioni (es. corpo di una sola istruzione);
``NOT_APPLICABLE`` per campione quando l'attacco non si può applicare (codice vuoto per
estrazione fallita, funzione d'ingresso o ``main`` non trovati): il campione resta nelle metriche
con il codice originale (I1) e il motivo nel dettaglio (``reason``). Il dettaglio registra
``n``, ``kept``, ``removed``, ``target`` e se il risultato si analizza ancora (``parses``).

La variante in ``k`` funzioni (codice a livello di file, ClassEval e CodeNet multi-funzione) è
rinviata alla Milestone 13 con la definizione già fissata in ADR-014 §2.3.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from tree_sitter import Node

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.generation.code_extractor import _function_name
from bench.lang.functions import FUNCTION_TYPES
from bench.registry import ATTACKS

PY, JAVA, CPP, JS = Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT
BLOCKS = frozenset({"block", "compound_statement", "statement_block"})
COMMENTS = frozenset({"comment", "line_comment", "block_comment"})
# A livello di file: importazioni e definizioni di funzioni non sono istruzioni contate.
IMPORTS = {
    PY: frozenset({"import_statement", "import_from_statement", "future_import_statement"}),
    JS: frozenset({"import_statement"}),
    JAVA: frozenset({"import_declaration", "package_declaration"}),
    CPP: frozenset({"preproc_include", "using_declaration"}),
}
DEFINITIONS = {
    PY: frozenset({"function_definition"}),
    JS: frozenset({"function_declaration", "generator_function_declaration"}),
    JAVA: frozenset({"method_declaration"}),
    CPP: frozenset({"function_definition"}),
}


def _text(src: bytes, node: Node) -> bytes:
    return src[node.start_byte : node.end_byte]


def _functions(root: Node, lang: Language) -> list[Node]:
    """Funzioni del sorgente in ordine di posizione (anche annidate)."""
    types = FUNCTION_TYPES[lang]
    out: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            out.append(node)
        stack.extend(reversed(node.children))
    return out


def _named_function(root: Node, lang: Language, name: str) -> Node | None:
    return next((f for f in _functions(root, lang) if _function_name(f, lang) == name), None)


def _is_docstring(node: Node) -> bool:
    return (
        node.type == "expression_statement"
        and len(node.named_children) == 1
        and node.named_children[0].type in ("string", "concatenated_string")
    )


def _is_require(node: Node) -> bool:
    """JavaScript: ``const x = require(...)`` (importazione CommonJS)."""
    if node.type not in ("lexical_declaration", "variable_declaration"):
        return False
    values = [d.child_by_field_name("value") for d in node.named_children]
    return bool(values) and all(
        v is not None
        and v.type == "call_expression"
        and (v.child_by_field_name("function") or v).text == b"require"
        for v in values
    )


def _is_function_value(node: Node) -> bool:
    """JavaScript: ``const f = (...) => {...}`` è una definizione di funzione."""
    if node.type not in ("lexical_declaration", "variable_declaration"):
        return False
    values = [d.child_by_field_name("value") for d in node.named_children]
    return bool(values) and all(
        v is not None and v.type in ("arrow_function", "function_expression", "function")
        for v in values
    )


def _top_level(root: Node, lang: Language) -> list[Node]:
    """Istruzioni contate a livello di file (esclusi commenti, importazioni e definizioni)."""
    out = []
    for node in root.named_children:
        if node.type in COMMENTS or node.type in IMPORTS[lang] or node.type in DEFINITIONS[lang]:
            continue
        if lang is PY and node.type == "decorated_definition":
            continue
        if lang is JS and (_is_require(node) or _is_function_value(node)):
            continue
        if lang is PY and _is_docstring(node) and node == root.named_children[0]:
            continue
        out.append(node)
    return out


def _body_statements(body: Node, lang: Language) -> list[Node]:
    statements = [c for c in body.named_children if c.type not in COMMENTS]
    if lang is PY and statements and _is_docstring(statements[0]):
        statements = statements[1:]
    return statements


def _line_start(src: bytes, offset: int) -> int:
    """Inizio della riga se prima di ``offset`` ci sono solo spazi, altrimenti ``offset``."""
    start = src.rfind(b"\n", 0, offset) + 1
    return start if not src[start:offset].strip() else offset


def _line_end(src: bytes, offset: int) -> int:
    """Fine della riga (a capo compreso) se dopo ``offset`` ci sono solo spazi."""
    end = src.find(b"\n", offset)
    end = len(src) if end < 0 else end
    return end + 1 if not src[offset:end].strip() and end < len(src) else offset


@ATTACKS.register("T2.4")
class TruncateAttack(Attack):
    """T2.4: prefisso del ``p`` delle istruzioni del corpo bersaglio."""

    attack_id: ClassVar[str] = "T2.4"
    group: ClassVar[str] = "T2"

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        p = self.params.get("p")
        if not isinstance(p, int | float) or not 0 < p < 1:
            raise ConfigError(f"T2.4: p must be in (0, 1), not {p!r}")

    def keep(self, n: int) -> int:
        """Istruzioni da tenere: ``max(1, ⌊p·n⌋)``."""
        return max(1, math.floor(float(self.params["p"]) * n))

    def safe_apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        if self.supports(language) and not code.strip():
            return AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {"reason": "extraction_failed"})
        return super().safe_apply(code, language, ctx)

    def target(self, root: Node, lang: Language, ctx: AttackContext) -> tuple[str, Node] | str:
        """(tipo di bersaglio, nodo del corpo o radice) oppure il motivo per cui non c'è."""
        entry = ctx.problem.entry_point if ctx.problem is not None else None
        if entry:
            fn = _named_function(root, lang, entry)
            body = fn.child_by_field_name("body") if fn is not None else None
            return ("entry", body) if body is not None else "entry_not_found"
        fn = _named_function(root, lang, "main")
        body = fn.child_by_field_name("body") if fn is not None else None
        if body is not None:
            return "main", body
        if lang in (JAVA, CPP):
            return "main_not_found"
        return "top_level", root

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        lang = Language(language)
        tree = ctx.parsers.parse(code, lang)
        found = self.target(tree.root_node, lang, ctx)
        if isinstance(found, str):
            return AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {"reason": found})
        kind, body = found
        src = code.encode("utf-8")
        if kind == "top_level":
            statements = _top_level(body, lang)
        elif body.type in BLOCKS:
            statements = _body_statements(body, lang)
        else:
            statements = [body]  # funzione freccia con un'espressione: una sola istruzione
        n = len(statements)
        keep = self.keep(n) if n else 0
        detail: dict[str, Any] = {"p": self.params["p"], "target": kind, "n": n, "kept": keep}
        if keep >= n:
            return outcome(code, code, {**detail, "removed": 0})
        dropped = statements[keep:]
        if kind == "top_level":
            spans = [(_line_start(src, s.start_byte), _line_end(src, s.end_byte)) for s in dropped]
        else:
            # dalla fine dell'ultima istruzione tenuta: nessuna riga vuota al posto del taglio
            spans = [(statements[keep - 1].end_byte, statements[-1].end_byte)]
        new = src
        for start, end in sorted(spans, reverse=True):
            new = new[:start] + new[end:]
        text = new.decode("utf-8")
        detail.update(
            removed=n - keep,
            parses=not ctx.parsers.has_error(text, lang) or tree.root_node.has_error,
        )
        return outcome(code, text, detail)
