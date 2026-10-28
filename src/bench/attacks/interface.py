"""Funzione d'ingresso conservata, rinominata o mancante in un campione attaccato (M11; decisione
dell'utente del 28 ottobre 2026 su T3.1).

Le riscritture con LLM (T2.2, T3.1) possono rinominare la funzione del problema, anche se il prompt
lo vieta. La regola D4 (``bench.generation.code_extractor``) allora non trova l'``entry_point`` e,
per i dataset a completamento di funzione, antepone il prompt: il codice finale è prompt +
riscrittura, con la firma originale aperta e il file riscritto dentro il suo corpo. Il campione
non compila (Java, C++) o la funzione d'ingresso contiene solo la docstring (Python).

:func:`entry_status` classifica il codice finale **senza** l'uscita grezza del modello (T2.2 non la
conserva), così la stessa misura vale per T2.2 e T3.1:

- ``kept``: la funzione d'ingresso c'è;
- ``renamed``: manca, e il codice riscritto definisce altre funzioni (il nome nuovo è il primo
  che non compare nel codice di partenza, ``main`` escluso);
- ``missing``: manca e non c'è nessun'altra funzione.

Riconoscere il prompt anteposto (dataset a completamento, codice che inizia con il prompt):

- il resto dopo il prompt non definisce l'``entry_point``;
- Java, C++, JavaScript: il codice ha errori di sintassi (una riscrittura che ripete il prompt alla
  lettera e prosegue con il corpo è invece un programma valido);
- Python: la funzione d'ingresso contiene solo docstring, commenti o ``pass``, e il resto inizia
  in colonna 0 (un file, non un corpo indentato).

È un'euristica: una riscrittura che ripete il prompt, ha un errore di sintassi e definisce funzioni
ausiliarie nel corpo risulterebbe ``renamed``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tree_sitter import Node

from bench.domain.enums import Language
from bench.domain.models import Problem
from bench.generation.code_extractor import PREPEND_PROMPT_DATASETS, _function_name, defines
from bench.lang.functions import FUNCTION_TYPES
from bench.lang.parsers import ParserService, default_parsers

KEPT, RENAMED, MISSING = "kept", "renamed", "missing"
_NOT_NAMES = frozenset({"main"})


@dataclass(frozen=True)
class EntryStatus:
    """Esito per un campione: ``status`` fra ``kept``, ``renamed``, ``missing``; ``new_name`` solo
    per ``renamed``."""

    status: str
    new_name: str | None = None


def function_names(code: str, lang: Language, parsers: ParserService | None = None) -> list[str]:
    """Nomi delle funzioni e dei metodi definiti, in ordine di comparsa (senza ripetizioni)."""
    root = (parsers or default_parsers()).parse(code, lang).root_node
    types = FUNCTION_TYPES[Language(lang)]
    found: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in types:
            name = _function_name(node, lang)
            if name is not None:
                found.append((node.start_byte, name))
        stack.extend(node.children)
    out: list[str] = []
    for _, name in sorted(found):
        if name not in out:
            out.append(name)
    return out


def _python_stub(code: str, entry: str, parsers: ParserService) -> bool:
    """La funzione d'ingresso Python contiene solo docstring, commenti o ``pass``."""
    stack = [parsers.parse(code, Language.PYTHON).root_node]
    while stack:
        node: Node = stack.pop()
        if node.type == "function_definition" and _function_name(node, Language.PYTHON) == entry:
            body = node.child_by_field_name("body")
            if body is None:
                return True
            for child in body.named_children:
                if child.type in ("comment", "pass_statement"):
                    continue
                if child.type == "expression_statement" and all(
                    c.type == "string" for c in child.named_children
                ):
                    continue
                return False
            return True
        stack.extend(node.children)
    return False


def _prompt(problem: Problem) -> str:
    text = problem.prompt_text
    return text if text.endswith("\n") else text + "\n"


def entry_status(
    code: str, problem: Problem, original: str = "", parsers: ParserService | None = None
) -> EntryStatus:
    """Stato della funzione d'ingresso nel codice attaccato ``code`` (``original``: codice di
    partenza, per scegliere il nome nuovo)."""
    entry = problem.entry_point
    if not entry or not code.strip():
        return EntryStatus(KEPT)
    lang = Language(problem.language)
    parsers = parsers or default_parsers()
    prompt = _prompt(problem)
    rewritten = code
    if problem.dataset in PREPEND_PROMPT_DATASETS and code.startswith(prompt):
        rest = code[len(prompt) :]
        if defines(rest, lang, entry, parsers):
            return EntryStatus(KEPT)
        if lang is Language.PYTHON:
            first = next((x for x in rest.splitlines() if x.strip()), "")
            prepended = _python_stub(code, entry, parsers) and not first[:1].isspace()
        else:
            prepended = parsers.has_error(code, lang)
        if not prepended:
            return EntryStatus(KEPT)
        rewritten = rest
    elif defines(code, lang, entry, parsers):
        return EntryStatus(KEPT)
    before = set(function_names(original, lang, parsers)) if original.strip() else set()
    names = [n for n in function_names(rewritten, lang, parsers) if n not in _NOT_NAMES | {entry}]
    if not names:
        return EntryStatus(MISSING)
    fresh = [n for n in names if n not in before]
    return EntryStatus(RENAMED, (fresh or names)[0])


def pass_failures(
    samples: Iterable[Mapping[Any, Any]],
    exec_status: Mapping[str, str],
    problems: Mapping[str, Problem],
    parsers: ParserService | None = None,
) -> dict[str, int]:
    """Campioni attaccati che non passano i test (fallimenti di Pass@1, tutti gli stati
    d'attacco) divisi per stato della funzione d'ingresso: ``renamed``, ``missing`` e ``other``.
    Ogni riga di ``samples`` ha ``sample_id``, ``problem_key``, ``code`` e ``attack_status``; solo
    gli ``APPLIED`` possono aver perso la funzione."""
    parsers = parsers or default_parsers()
    out = {"n": 0, "failed": 0, RENAMED: 0, MISSING: 0, "other": 0}
    for row in samples:
        out["n"] += 1
        if exec_status.get(str(row["sample_id"])) == "PASSED":
            continue
        out["failed"] += 1
        state = KEPT
        if str(row["attack_status"]) == "APPLIED":
            problem = problems[str(row["problem_key"])]
            state = entry_status(str(row["code"] or ""), problem, parsers=parsers).status
        out["other" if state == KEPT else state] += 1
    return out
