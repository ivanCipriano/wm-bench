"""T2.5 diluizione: funzioni umane aggiunte al file (ADR-018 §4; PIPELINE di multimark §5.5).

Solo Python (``NOT_APPLICABLE`` sugli altri linguaggi), livello di file. Per ogni campione si
estraggono ``n`` funzioni distinte dalla raccolta della fase ``function_pools`` con l'RNG
dell'attacco (``sample_rng``, SPEC §18) e si aggiungono a livello di modulo; non vengono chiamate.

**Posizione** (``position``):

- ``before``: dopo la parte iniziale del file fatta di docstring del modulo e ``import``
  (``from __future__`` compreso), quindi prima della prima istruzione che non è un ``import``
  (al più la prima definizione); senza ``import`` né docstring, in testa al file;
- ``after``: in coda al file;
- ``around``: le prime ``ceil(n/2)`` prima, le altre dopo.

Ogni funzione aggiunta è separata dal resto da due righe vuote (PEP 8).

**Collisioni.** Se il nome di una funzione aggiunta è già una parola del file (anche in stringhe e
commenti, come T1.4 e T1.6), una parola riservata o un builtin, oppure il nome di un'altra
funzione aggiunta, la ``def`` si rinomina in ``<nome>_ext<k>`` con il primo ``k = 1, 2, …``
libero. Basta rinominare la ``def``: le funzioni della raccolta non sono ricorsive. Con i
builtin si evita che una funzione aggiunta prima del codice ne cambi il comportamento (per
esempio una ``def sorted`` in testa al file).

**Esiti.** ``APPLIED``; codice vuoto ``NOT_APPLICABLE`` (§5.5); ``TOOL_ERROR`` con il codice
originale se l'ingresso o l'uscita non passano ``ast.parse``. ``attack_detail``: ``n_inserted``,
``names`` (nomi delle funzioni aggiunte, dopo le rinomine), ``position``, ``added_loc`` (righe di
codice aggiunte, come ``n_loc`` della raccolta), ``function_ids`` (indici nella raccolta),
``n_renamed`` (``def`` rinominate per collisione).

Griglia (decisione G di §7 e dell'utente del 23 novembre 2026): ``(n, position)`` ∈
``{(1, before), (2, around), (4, around)}``; (2, around) è la cella di §6.3 e §6.4.
"""

from __future__ import annotations

import ast
import math
import re
from typing import Any, ClassVar

from bench.attacks.base import Attack, AttackContext, AttackOutcome, tool_error
from bench.attacks.t1.common import identifiers_in, reserved_names
from bench.data.function_pools import lines_of_code
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.registry import ATTACKS

POSITIONS = ("before", "after", "around")
_DEF_NAME = re.compile(r"^(\s*(?:async\s+)?def\s+)([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def insertion_line(tree: ast.Module) -> int:
    """Numero di righe del file prima del punto ``before``: fine della parte iniziale fatta di
    docstring del modulo e ``import`` (0 = in testa)."""
    end = 0
    for i, stmt in enumerate(tree.body):
        docstring = (
            i == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
        if not (docstring or isinstance(stmt, ast.Import | ast.ImportFrom)):
            break
        end = stmt.end_lineno or stmt.lineno
    return end


def rename_def(code: str, new: str) -> str:
    """Rinomina la ``def`` più esterna della funzione (la prima)."""
    return _DEF_NAME.sub(lambda m: m.group(1) + new, code, count=1)


def _block(functions: list[str]) -> str:
    return "\n\n".join(f.rstrip("\n") + "\n" for f in functions)


def dilute(code: str, functions: list[str], position: str, tree: ast.Module) -> str:
    """Codice con ``functions`` inserite in ``position`` (vedi la docstring del modulo)."""
    n_before = {"before": len(functions), "after": 0}.get(position, math.ceil(len(functions) / 2))
    head, tail = functions[:n_before], functions[n_before:]
    lines = code.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    cut = insertion_line(tree)
    top, rest = "".join(lines[:cut]), "".join(lines[cut:])
    out = top
    if head:
        if top.strip():
            out = top.rstrip("\n") + "\n\n\n"
        out += _block(head)
        out += "\n\n" + rest.lstrip("\n") if rest.strip() else rest.lstrip("\n")
    else:
        out += rest
    if tail:
        out = out.rstrip("\n") + "\n\n\n" + _block(tail)
    return out


@ATTACKS.register("T2.5")
class DilutionAttack(Attack):
    """T2.5: ``n`` funzioni umane aggiunte a livello di modulo, mai chiamate."""

    attack_id: ClassVar[str] = "T2.5"
    group: ClassVar[str] = "T2"
    supported_languages: ClassVar[frozenset[Language]] = frozenset({Language.PYTHON})
    needs_function_pool: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        n, position = self.params.get("n"), self.params.get("position")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ConfigError(f"T2.5: n must be a positive integer, got {n!r}")
        if position not in POSITIONS:
            raise ConfigError(f"T2.5: position must be one of {POSITIONS}, got {position!r}")

    def safe_apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        if self.supports(language) and not code.strip():
            return AttackOutcome(code, AttackStatus.NOT_APPLICABLE, {"reason": "empty_code"})
        return super().safe_apply(code, language, ctx)

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        pool = ctx.pools.functions
        n, position = int(self.params["n"]), str(self.params["position"])
        if len(pool) < n:
            raise ConfigError(f"T2.5: {len(pool)} functions in the pool (run stage function_pools)")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return tool_error(code, f"SyntaxError: {exc}", {"n_inserted": 0})
        ids = ctx.rng.sample(range(len(pool)), n)
        blocked = identifiers_in(code) | reserved_names(Language.PYTHON)
        chosen: list[str] = []
        names: list[str] = []
        renamed = 0
        for i in ids:
            fn = pool[i]
            name = ast.parse(fn).body[0].name  # type: ignore[attr-defined]
            new, k = name, 0
            while new in blocked:
                k += 1
                new = f"{name}_ext{k}"
            if k:
                fn = rename_def(fn, new)
                renamed += 1
            blocked.add(new)
            names.append(new)
            chosen.append(fn)
        detail = {
            "n_inserted": n,
            "names": names,
            "position": position,
            "added_loc": sum(lines_of_code(f) for f in chosen),
            "function_ids": ids,
            "n_renamed": renamed,
        }
        new_code = dilute(code, chosen, position, tree)
        try:
            ast.parse(new_code)
        except SyntaxError as exc:
            return tool_error(code, f"diluted code does not parse: {exc}", detail)
        return AttackOutcome(new_code, AttackStatus.APPLIED, detail)
