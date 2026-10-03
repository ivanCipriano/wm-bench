"""Conteggio delle righe di codice (SPEC §10.3, ADR-004).

Una riga conta se contiene almeno un nodo foglia che non sia un commento; le foglie su più
righe (es. stringhe) contano per tutte le righe che coprono. In Python si escludono le
docstring: una stringa che è la prima istruzione di un modulo, di una classe o di una
funzione. Le righe vuote non contengono foglie e quindi non contano.

Regola tarata sui valori del protocollo: HumanEval+ 5,1 ± 4,4 (regione della soluzione
canonica, analizzata insieme al prompt) e MBPP+ 4,0 ± 3,7 (soluzione canonica intera).
"""

from __future__ import annotations

from tree_sitter import Node

from bench.domain.enums import Language
from bench.lang.parsers import COMMENT_TYPES, ParserService, default_parsers

_PY_SCOPES = frozenset({"module", "function_definition", "class_definition"})


def _first_statement_string(block: Node) -> Node | None:
    for child in block.named_children:
        if child.type in COMMENT_TYPES:
            continue
        if (
            child.type == "expression_statement"
            and child.named_child_count == 1
            and child.named_children[0].type in {"string", "concatenated_string"}
        ):
            return child
        return None
    return None


def _python_docstrings(root: Node) -> set[int]:
    """Identificativi dei nodi docstring in un albero Python."""
    found: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _PY_SCOPES:
            block = node if node.type == "module" else node.child_by_field_name("body")
            if block is not None:
                doc = _first_statement_string(block)
                if doc is not None:
                    found.add(doc.id)
        stack.extend(node.children)
    return found


def code_rows(root: Node, lang: Language) -> set[int]:
    """Righe (indice 0) con almeno una foglia di codice nel sottoalbero ``root``."""
    skip = _python_docstrings(root) if Language(lang) is Language.PYTHON else set()
    rows: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.id in skip or node.type in COMMENT_TYPES or node.is_missing:
            continue
        if node.child_count == 0:
            if node.end_byte > node.start_byte:
                rows.update(range(node.start_point[0], node.end_point[0] + 1))
            continue
        stack.extend(node.children)
    return rows


def count_code_lines(
    code: str,
    lang: Language,
    *,
    start_line: int = 0,
    parsers: ParserService | None = None,
) -> int:
    """Numero di righe di codice, contando solo le righe con indice ``>= start_line``.

    Args:
        code: sorgente (per contare solo la parte da generare: prompt + soluzione).
        lang: linguaggio.
        start_line: prima riga da contare (es. numero di righe del prompt).
        parsers: servizio di parsing (default: quello condiviso).
    """
    tree = (parsers or default_parsers()).parse(code, lang)
    return sum(1 for row in code_rows(tree.root_node, lang) if row >= start_line)


def prompt_line_count(prompt: str) -> int:
    """Righe occupate interamente dal prompt: la soluzione inizia da questa riga."""
    return prompt.count("\n")
