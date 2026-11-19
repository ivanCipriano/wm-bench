"""T1.6 nomi uniformati in ``snake_case`` (PEP 8; ADR-018, PIPELINE di multimark §5.5).

Solo Python (``NOT_APPLICABLE`` sugli altri linguaggi); deterministico (non usa l'RNG). Griglia
``caps ∈ {convert, keep}`` (decisione dell'utente del 19 novembre 2026):

- ``convert``: tutto in ``snake_case``, costanti di modulo comprese (§5.5);
- ``keep``: i nomi **di modulo** in ``ALL_CAPS`` (nucleo senza minuscole e con almeno una
  lettera: ``MOD``, ``N``, ``MAX_N``) restano invariati, come chiede PEP 8 per le costanti; i nomi
  in ``ALL_CAPS`` dentro le funzioni si convertono comunque.

**Candidati.**

- Scope di funzione (lambda comprese): gli stessi di T1.4 (``rename.python_candidates``: variabili
  locali e parametri, con le stesse esclusioni: ``self``, ``cls``, dunder, ``entry_point``, nomi
  in ``global``/``nonlocal``, nomi usati come argomento con nome, funzioni con ``locals``,
  ``vars``, ``eval``, ``exec``).
- Nomi di modulo (scostamento da §5.5, che riprende i candidati di T1.4; ADR-018): i nomi legati
  a livello di modulo solo da nomi (niente ``def``, ``class``, ``import``), con le esclusioni (h)
  e (i) di §3.6.1: nessun nome che compare in un'istruzione ``global`` o ``nonlocal``, in
  ``__all__``, usato come argomento con nome, ``self``, ``cls``, dunder o ``entry_point``; nessun
  nome di modulo se il file usa ``globals``, ``locals``, ``vars``, ``eval`` o ``exec`` (in
  qualunque punto: regola prudente). La rinomina copre tutte le occorrenze, comprese le letture
  dentro funzioni e classi (``ScopeProvider``).
- Variabili delle comprehension e attributi di classe: mai (come T1.4 e §3.6.1).

**Conversione.** ``norm(n)`` di §3.6.2, identica a ``multimark.names.normalize_name``: segmenti
(su ``_``, sui cambi minuscolo→maiuscolo e sulle sequenze di maiuscole seguite da minuscola) in
minuscolo uniti da ``_``. Gli ``_`` iniziali e finali si conservano e si converte solo il nucleo
(``_tmpVal`` → ``_tmp_val``, ``list_`` resta ``list_``). Nomi già in ``snake_case``
(``norm(n) == n``), nuclei vuoti e nuclei con caratteri fuori da ``[A-Za-z0-9_]``: invariati.

**Collisioni.** Un nome originale ha un solo nome nuovo in tutto il file (stessa rinomina in ogni
scope: una rinomina coerente non cambia le relazioni fra i nomi). Se ``norm(n)`` è già una parola
del file (anche in stringhe e commenti, come T1.4), una parola riservata, un builtin o il nome
nuovo di un altro nome, si prova ``norm(n) + "_1"``, poi ``"_2"`` e così via (``n_collisions``).
I nomi si assegnano nell'ordine della prima occorrenza: con ``maxVal`` e ``MaxVal`` il primo
diventa ``max_val``, il secondo ``max_val_1``; con ``N`` e ``n`` (``n`` resta) ``N`` diventa
``n_1``.

**Esiti.** ``APPLIED`` con almeno una rinomina, altrimenti ``UNCHANGED``; ``TOOL_ERROR`` con il
codice originale se l'ingresso o l'uscita non passano ``ast.parse``. ``attack_detail``:
``n_candidates`` (funzione + modulo), ``n_module`` (candidati di modulo), ``n_renamed``,
``n_module_renamed``, ``n_collisions``, ``n_caps_kept`` (nomi di modulo in ``ALL_CAPS`` lasciati
con ``caps=keep`` che sarebbero cambiati), ``n_occurrences``.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.t1.common import identifiers_in, reserved_names
from bench.attacks.t1.rename import Candidate, _python_spans, python_candidates
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.registry import ATTACKS

# §3.6.2: stessa espressione di ``multimark.names._SEGMENT`` (test di confronto con la libreria).
_SEGMENT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+[0-9]*|[0-9]+")
_CORE = re.compile(r"^(_*)(.*?)(_*)$", re.DOTALL)
_ASCII_CORE = re.compile(r"[A-Za-z0-9_]+")
_PY_DYNAMIC_MODULE = re.compile(r"\b(?:globals|locals|vars|eval|exec)\s*\(")
CAPS_MODES = ("convert", "keep")


def norm(name: str) -> str:
    """``norm(n)`` di §3.6.2: segmenti in minuscolo uniti da ``_`` (toglie gli ``_`` ai bordi)."""
    out: list[str] = []
    for part in name.split("_"):
        out.extend(_SEGMENT.findall(part))
    return "_".join(s.lower() for s in out)


def snake_name(name: str) -> str:
    """Nome in ``snake_case`` con gli ``_`` iniziali e finali conservati; il nome stesso se il
    nucleo è vuoto o ha caratteri fuori da ``[A-Za-z0-9_]``."""
    match = _CORE.match(name)
    assert match is not None
    lead, core, trail = match.groups()
    if not core or not _ASCII_CORE.fullmatch(core):
        return name
    new = norm(core)
    return f"{lead}{new}{trail}" if new else name


def is_all_caps(name: str) -> bool:
    """Nucleo con almeno una lettera e nessuna minuscola (``MOD``, ``N``, ``MAX_N``, ``INF2``)."""
    core = name.strip("_")
    return any(c.isalpha() for c in core) and not any(c.islower() for c in core)


def _all_names(tree: ast.Module) -> set[str]:
    """Stringhe di ``__all__`` (assegnato, annotato o esteso a livello di modulo)."""
    out: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            value = getattr(node, "value", None)
            if value is not None:
                for sub in ast.walk(value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        out.add(sub.value)
    return out


def module_candidates(code: str, entry_point: str | None) -> list[Candidate]:
    """Nomi di modulo rinominabili (esclusioni (h) e (i) di §3.6.1, vedi la docstring)."""
    import libcst as cst
    from libcst.metadata import GlobalScope, MetadataWrapper, PositionProvider, ScopeProvider

    if _PY_DYNAMIC_MODULE.search(code):
        return []
    tree = ast.parse(code)
    keyword_args = {kw.arg for kw in ast.walk(tree) if isinstance(kw, ast.keyword) and kw.arg}
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global | ast.Nonlocal):
            declared.update(node.names)
    exported = _all_names(tree)
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

    scope = next((s for s in scopes.values() if isinstance(s, GlobalScope)), None)
    if scope is None:
        return []
    groups: dict[str, list[Any]] = defaultdict(list)
    for assignment in scope.assignments:
        groups[assignment.name].append(assignment)
    out: list[Candidate] = []
    for name, assignments in sorted(groups.items()):
        if (
            name in ("self", "cls")
            or name == entry_point
            or name in declared
            or name in keyword_args
            or name in exported
            or (name.startswith("__") and name.endswith("__"))
        ):
            continue
        spans = _python_spans(assignments, span)
        if spans:
            out.append(Candidate(name, 0, spans))  # funzione 0 = modulo
    return out


def _rewrite(code: str, renames: list[tuple[Candidate, str]]) -> str:
    """Applica le rinomine; uno stesso intervallo con due nomi nuovi diversi è un errore."""
    edits: dict[tuple[int, int], str] = {}
    for cand, new in renames:
        for s in cand.spans:
            if edits.setdefault(s, new) != new:
                raise ValueError(f"span {s} renamed twice ({edits[s]}, {new})")
    ordered = sorted(edits.items(), reverse=True)
    for ((start, end), _), ((after, _end), _t) in zip(ordered[1:], ordered, strict=False):
        if end > after:
            raise ValueError(f"overlapping spans at {start}")
    out = code
    for (start, end), text in ordered:
        out = out[:start] + text + out[end:]
    return out


@ATTACKS.register("T1.6")
class SnakeCaseAttack(Attack):
    """T1.6: variabili locali, parametri e nomi di modulo in ``snake_case``."""

    attack_id: ClassVar[str] = "T1.6"
    group: ClassVar[str] = "T1"
    supported_languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        if self.params.get("caps") not in CAPS_MODES:
            raise ConfigError(f"T1.6: caps must be one of {CAPS_MODES}")

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        entry = ctx.problem.entry_point if ctx.problem is not None else None
        try:
            local = python_candidates(code, entry)
            module = module_candidates(code, entry)
        except SyntaxError as exc:
            return tool_error(code, f"SyntaxError: {exc}", {"n_candidates": 0})
        candidates = sorted([*local, *module], key=lambda c: (c.first, c.function, c.name))
        blocked = identifiers_in(code) | reserved_names(Language.PYTHON)
        given: dict[str, str] = {}  # nome originale -> nome nuovo (uno per file)
        renames: list[tuple[Candidate, str]] = []
        collisions = caps_kept = module_renamed = 0
        for cand in candidates:
            target = snake_name(cand.name)
            if target == cand.name:
                continue
            if cand.function == 0 and self.params["caps"] == "keep" and is_all_caps(cand.name):
                caps_kept += 1
                continue
            if cand.name not in given:
                new, i = target, 0
                while new in blocked or (new.startswith("__") and new.endswith("__")):
                    i += 1
                    new = f"{target}_{i}"
                collisions += i > 0
                blocked.add(new)
                given[cand.name] = new
            renames.append((cand, given[cand.name]))
            module_renamed += cand.function == 0
        detail = {
            "n_candidates": len(candidates),
            "n_module": len(module),
            "n_renamed": len(renames),
            "n_module_renamed": module_renamed,
            "n_collisions": collisions,
            "n_caps_kept": caps_kept,
            "n_occurrences": sum(len(c.spans) for c, _ in renames),
        }
        try:
            new_code = _rewrite(code, renames)
        except ValueError as exc:
            return tool_error(code, str(exc), detail)
        try:
            ast.parse(new_code)
        except SyntaxError as exc:
            return tool_error(code, f"renamed code does not parse: {exc}", detail)
        return outcome(code, new_code, detail)
