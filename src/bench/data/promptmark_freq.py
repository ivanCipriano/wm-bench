"""Liste di frequenza delle iniziali degli identificatori per PromptMark (SPEC §9.4, D8).

PromptMark sceglie la green list fra le iniziali più frequenti degli identificatori di codice umano
e stima il tasso atteso γ dalle stesse frequenze. Il repository le calcola su HumanEval e MBPP
(dati di valutazione); qui si usano funzioni **disgiunte** dai negativi: lo split di training di
CodeSearchNet Python (D8). Solo Python, perché PromptMark si applica solo a Python (audit).

La procedura replica quella degli autori (``scripts/utils/calculate_gamma_for_code.ipynb``):
per ogni programma gli identificatori **unici** di tutte le categorie raccolte da ``CodeNavigator``
(classi, funzioni, variabili pubbliche e non), esclusi builtin e nomi comuni, con la prima lettera
alfabetica in minuscolo; i conteggi si sommano su tutto il corpus. Il formato del file è quello
del repository (``letter_freqs``, ``total_identifiers``).
"""

from __future__ import annotations

import ast
import builtins
import string
import textwrap
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

# Copiato da PromptMark (``shared_utils.COMMON_STD_METHODS``, uguale nel notebook).
COMMON_STD_METHODS = frozenset(
    {
        "self", "re", "cls", "append", "join", "dummy_function", "find", "kwargs",
        "args", "range", "print", "len", "dict", "list", "str", "int", "float",
        "set", "tuple", "os", "np", "subprocess", "now", "today", "timedelta",
        "strptime", "date", "time", "datetime", "logging", "log", "info", "debug",
        "error", "warning", "exception", "lower", "upper", "strip", "split",
        "replace", "endswith", "startswith", "extend", "insert", "remove", "pop",
        "sort", "clear", "keys", "values", "items", "get", "update", "copy",
        "format", "count", "index",
    }
)  # fmt: skip
BUILTIN_NAMES = frozenset(dir(builtins)) | COMMON_STD_METHODS


class LetterFrequencies(BaseModel):
    """Lista di frequenza (formato del repository più la provenienza)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    letter_freqs: dict[str, int]
    total_identifiers: int
    n_programs: int
    n_skipped: int
    source: str


class _Navigator(ast.NodeVisitor):
    """``CodeNavigator`` di PromptMark (solo la raccolta degli identificatori)."""

    def __init__(self) -> None:
        self.public_classes: set[str] = set()
        self.non_public_classes: set[str] = set()
        self.public_funcs: set[str] = set()
        self.non_public_funcs: set[str] = set()
        self.public_vars: set[str] = set()
        self.non_public_vars: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        name = node.name
        if name.startswith("__") and name.endswith("__"):
            pass
        elif name.startswith("_"):
            self.non_public_funcs.add(name)
        else:
            self.public_funcs.add(name)
        for arg in node.args.args:
            if arg.arg not in BUILTIN_NAMES:
                self.non_public_vars.add(arg.arg)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if node.name.startswith("_"):
            self.non_public_classes.add(node.name)
        else:
            self.public_classes.add(node.name)
        self.non_public_vars.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id not in BUILTIN_NAMES:
                if target.id.isupper():
                    self.public_vars.add(target.id)
                else:
                    self.non_public_vars.add(target.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            if node.attr not in BUILTIN_NAMES and node.attr not in COMMON_STD_METHODS:
                self.public_funcs.add(node.attr)
        elif node.attr not in BUILTIN_NAMES and node.attr not in COMMON_STD_METHODS:
            self.non_public_vars.add(node.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id not in BUILTIN_NAMES:
            self.non_public_vars.add(node.id)
        self.generic_visit(node)


def identifiers(code: str) -> set[str] | None:
    """Identificatori validi del programma (``None`` se non si analizza)."""
    try:
        tree = ast.parse(textwrap.dedent(code))
    except (SyntaxError, ValueError):
        return None
    nav = _Navigator()
    nav.visit(tree)
    found = (
        nav.public_classes
        | nav.non_public_classes
        | nav.non_public_funcs
        | nav.non_public_vars
        | nav.public_funcs
        | nav.public_vars
    )
    return {i for i in found if i not in COMMON_STD_METHODS and i not in BUILTIN_NAMES}


def initial(identifier: str) -> str | None:
    """Prima lettera alfabetica in minuscolo, come il notebook degli autori."""
    for char in identifier:
        if char.isalpha():
            return char.lower()
    return None


def letter_frequencies(codes: Iterable[Any], source: str) -> LetterFrequencies:
    """Conteggi delle iniziali sugli identificatori unici di ogni programma del corpus."""
    counts = dict.fromkeys(string.ascii_lowercase, 0)
    total = programs = skipped = 0
    for code in codes:
        if not isinstance(code, str) or not code.strip():
            skipped += 1
            continue
        found = identifiers(code)
        if found is None:
            skipped += 1
            continue
        letters = [c for c in (initial(i) for i in found) if c is not None]
        if not letters:
            continue
        programs += 1
        total += len(letters)
        for letter in letters:
            if letter in counts:
                counts[letter] += 1
    return LetterFrequencies(
        letter_freqs=counts,
        total_identifiers=total,
        n_programs=programs,
        n_skipped=skipped,
        source=source,
    )
