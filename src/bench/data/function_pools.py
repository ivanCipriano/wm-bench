"""Raccolta di funzioni Python per l'attacco T2.5 di diluizione (ADR-018 §4; PIPELINE di
multimark §5.5).

Fonte: funzioni di CodeSearchNet **train** Python (``whole_func_string``), riferimento ausiliario,
mai dati di valutazione. Le righe si leggono in ordine casuale fissato dal seme
``derive_seed(global_seed, "function_pool", "python")`` (§5.5) e si tengono le prime
``MAX_FUNCTIONS`` (5.000) che passano tutti i filtri, nell'ordine (conteggio dopo ciascuno):

1. ``generated``: riga più lunga di 200 caratteri o marcatori di codice generato (gli stessi
   filtri delle raccolte di ADR-012, ``bench.data.attack_pools``);
2. ``parse``: ``ast.parse`` e ``compile`` falliscono sul testo senza rientro comune
   (``compile`` scarta anche i ``nonlocal`` senza funzione che li racchiude, che a livello di
   modulo non si compilano);
3. ``single_function``: il testo non è una sola definizione di funzione (``def`` o
   ``async def``);
4. ``loc``: righe di codice fuori da 3–40 (righe non vuote e non di solo commento, docstring
   comprese);
5. ``import``: ``import`` o ``from ... import`` dentro la funzione;
6. ``decorator``: la funzione ha decoratori;
7. ``recursive``: il corpo usa il nome della funzione;
8. ``global``: istruzione ``global`` nella funzione;
9. ``definition_time``: annotazioni (parametri o ritorno) o valori di default che non sono
   letterali (``ast.literal_eval``). Non è in §5.5: annotazioni e default si valutano quando la
   ``def`` viene eseguita, quindi un nome non definito nel file ospite (``np.ndarray``,
   ``DEFAULT_TIMEOUT``) farebbe fallire il programma attaccato al caricamento, e le canoniche di
   L1 non resterebbero verdi (proprietà di §5.5);
10. ``duplicate``: stesso testo di una funzione già tenuta.

Ogni funzione tenuta ha ``function_id`` (ordine di accettazione), ``name``, ``code`` (testo senza
rientro comune, fine riga Unix, senza righe vuote finali), ``n_loc`` e ``repository_name``.
"""

from __future__ import annotations

import ast
import textwrap
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from bench.data.attack_pools import _GENERATED, MAX_LINE_CHARS, _filter_table
from bench.data.loaders.base import normalize_newlines

MIN_LOC, MAX_LOC = 3, 40
MAX_FUNCTIONS = 5_000
FUNCTION_FILTERS = (
    "generated", "parse", "single_function", "loc", "import", "decorator", "recursive", "global",
    "definition_time", "duplicate",
)  # fmt: skip
COLUMNS = ("function_id", "name", "code", "n_loc", "repository_name")


def lines_of_code(code: str) -> int:
    """Righe non vuote e non di solo commento (le docstring contano)."""
    return sum(1 for line in code.splitlines() if line.strip() and not line.strip().startswith("#"))


def _literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _evaluated_at_definition(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Annotazioni o default non letterali: si valutano quando la ``def`` viene eseguita."""
    args = fn.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    every += [a for a in (args.vararg, args.kwarg) if a is not None]
    if fn.returns is not None or any(a.annotation is not None for a in every):
        return True
    defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    return not all(_literal(d) for d in defaults)


def clean_function(raw: str) -> str:
    """Testo della funzione senza rientro comune, fine riga Unix, senza spazi finali di riga e
    senza righe vuote alla fine."""
    text = textwrap.dedent(normalize_newlines(raw))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n") + "\n"


def drop_reason(code: str) -> tuple[str | None, str, int]:
    """(primo filtro che scarta la funzione o ``None``, nome, righe di codice); ``code`` è il
    testo già pulito (:func:`clean_function`). Il filtro ``duplicate`` è del costruttore."""
    if max((len(line) for line in code.splitlines()), default=0) > MAX_LINE_CHARS or (
        _GENERATED.search(code)
    ):
        return "generated", "", 0
    try:
        tree = ast.parse(code)
        compile(tree, "<function_pool>", "exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return "parse", "", 0
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef | ast.AsyncFunctionDef):
        return "single_function", "", 0
    fn = tree.body[0]
    n_loc = lines_of_code(code)
    nodes = list(ast.walk(fn))
    checks = (
        ("loc", not MIN_LOC <= n_loc <= MAX_LOC),
        ("import", any(isinstance(n, ast.Import | ast.ImportFrom) for n in nodes)),
        ("decorator", bool(fn.decorator_list)),
        ("recursive", any(isinstance(n, ast.Name) and n.id == fn.name for n in nodes)),
        ("global", any(isinstance(n, ast.Global) for n in nodes)),
        ("definition_time", _evaluated_at_definition(fn)),
    )
    reason = next((name for name, failed in checks if failed), None)
    return reason, fn.name, n_loc


@dataclass
class FunctionPoolBuilder:
    """Accumula le funzioni che passano i filtri, con i conteggi."""

    max_functions: int = MAX_FUNCTIONS
    rows: list[dict[str, Any]] = field(default_factory=list)
    drops: Counter[str] = field(default_factory=Counter)
    seen: set[str] = field(default_factory=set)
    scanned: int = 0

    @property
    def full(self) -> bool:
        return len(self.rows) >= self.max_functions

    def add(self, raw: str, repository: str) -> bool:
        """Esamina una funzione; ``True`` se entra nella raccolta."""
        self.scanned += 1
        code = clean_function(raw)
        reason, name, n_loc = drop_reason(code)
        if reason is None and code in self.seen:
            reason = "duplicate"
        if reason is not None:
            self.drops[reason] += 1
            return False
        self.seen.add(code)
        self.rows.append({"function_id": len(self.rows), "name": name, "code": code,
                          "n_loc": n_loc, "repository_name": repository})  # fmt: skip
        return True

    def add_all(self, functions: Iterable[tuple[str, str]]) -> None:
        """Esamina ``(codice, repository)`` in ordine finché la raccolta non è piena."""
        for raw, repository in functions:
            if self.full:
                break
            self.add(raw, repository)

    def stats(self) -> dict[str, Any]:
        """Conteggi dopo ogni filtro sulle funzioni esaminate."""
        table = _filter_table(self.scanned, self.drops, FUNCTION_FILTERS)
        return table | {
            "kept": len(self.rows),
            "max_functions": self.max_functions,
            "loc_range": [MIN_LOC, MAX_LOC],
            "repositories": len({r["repository_name"] for r in self.rows}),
        }
