"""Integrazione dei negativi umani con campionamento stratificato (SPEC §10.4, ADR-004).

Decisioni dell'utente (3 ottobre 2026):

- la distribuzione di riferimento è quella delle righe dei negativi umani **nativi** della
  stessa parte (sviluppo o test) e dello stesso linguaggio;
- i candidati si scelgono per decili di quella distribuzione; se un decile non ha
  abbastanza candidati si prende dai decili adiacenti e lo spostamento si registra;
- il seme deriva da ``derive_seed`` (SPEC §18).

Le righe di riferimento e dei candidati si contano allo stesso modo (``function_loc``).
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import xxhash

from bench.data.sources import Candidate
from bench.domain.errors import DataError


def code_fingerprint(code: str) -> str:
    """Impronta del codice indipendente da spazi e a capo (per la deduplicazione)."""
    return xxhash.xxh3_64_hexdigest(" ".join(code.split()).encode("utf-8"))


def deduplicate(
    candidates: Iterable[Candidate], exclude: Iterable[str] = ()
) -> tuple[list[Candidate], int]:
    """Toglie i duplicati (stessa impronta) e i candidati uguali a codice da escludere.

    A parità di impronta resta il candidato con la chiave minore (deterministico).

    Returns:
        I candidati rimasti, ordinati per chiave, e quanti ne sono stati tolti.
    """
    seen = set(exclude)
    kept: list[Candidate] = []
    dropped = 0
    for cand in sorted(candidates, key=lambda c: c.key):
        fp = code_fingerprint(cand.code)
        if fp in seen:
            dropped += 1
            continue
        seen.add(fp)
        kept.append(cand)
    return kept, dropped


def largest_remainder(weights: Sequence[float], n: int) -> list[int]:
    """Ripartisce ``n`` unità in proporzione ai pesi (metodo dei resti maggiori).

    A parità di resto vince l'indice minore.
    """
    total = float(sum(weights))
    if n <= 0 or total <= 0:
        return [0] * len(weights)
    exact = [w * n / total for w in weights]
    base = [int(x) for x in exact]
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - base[i]), i))
    for i in order[: n - sum(base)]:
        base[i] += 1
    return base


@dataclass
class SampleResult:
    """Esito del campionamento."""

    selected: list[Candidate]
    moved: int
    edges: list[float]
    targets: list[int]
    available: list[int]
    taken: list[int]
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Diagnostica per il manifest."""
        return {
            "selected": len(self.selected),
            "moved_between_bins": self.moved,
            "bin_upper_edges": self.edges,  # l'ultimo è il massimo del riferimento
            "bin_targets": self.targets,
            "bin_available": self.available,
            "bin_taken": self.taken,
            **self.details,
        }


class StratifiedSampler:
    """Campionamento stratificato per quantili delle righe di un riferimento.

    I gruppi sono delimitati dai quantili del riferimento e, agli estremi, dal suo minimo e
    dal suo massimo: un candidato fuori da ``[min, max]`` non appartiene a nessun quantile
    e non viene scelto (contato in ``out_of_range``).

    Args:
        n_bins: numero di quantili (10 = decili). Soglie ripetute (valori discreti) si
            fondono, quindi i gruppi effettivi possono essere meno di ``n_bins``.
    """

    def __init__(self, n_bins: int = 10) -> None:
        self.n_bins = n_bins

    def edges(self, reference: Sequence[int]) -> list[float]:
        """Soglie superiori dei gruppi; l'ultima è il massimo del riferimento."""
        if not reference:
            raise DataError("stratified sampling needs a non-empty reference")
        qs = [i / self.n_bins for i in range(1, self.n_bins)]
        inner = {float(x) for x in np.quantile(np.asarray(reference, dtype=float), qs)}
        return sorted(inner | {float(max(reference))})

    @staticmethod
    def bin_of(edges: Sequence[float], low: float, loc: float) -> int | None:
        """Gruppo di un valore (``edges[i-1] < loc <= edges[i]``), ``None`` se fuori campo."""
        if loc < low or loc > edges[-1]:
            return None
        return bisect.bisect_left(edges, loc)

    def sample(
        self, reference: Sequence[int], candidates: Sequence[Candidate], n: int, seed: int
    ) -> SampleResult:
        """Sceglie ``n`` candidati con la distribuzione di righe del riferimento.

        Raises:
            DataError: se i candidati nel campo del riferimento sono meno di ``n``.
        """
        edges = self.edges(reference)
        low = float(min(reference))
        n_groups = len(edges)
        if n <= 0:
            return SampleResult([], 0, edges, [0] * n_groups, [0] * n_groups, [0] * n_groups)

        ref_counts = [0] * n_groups
        for loc in reference:
            group = self.bin_of(edges, low, loc)
            assert group is not None  # il riferimento è nel proprio campo per costruzione
            ref_counts[group] += 1
        targets = largest_remainder(ref_counts, n)

        rng = random.Random(seed)
        pools: list[list[Candidate]] = [[] for _ in range(n_groups)]
        out_of_range = 0
        for cand in sorted(candidates, key=lambda c: c.key):
            group = self.bin_of(edges, low, cand.loc)
            if group is None:
                out_of_range += 1
            else:
                pools[group].append(cand)
        in_range = sum(len(p) for p in pools)
        if in_range < n:
            raise DataError(
                f"not enough candidates in the reference range [{low:g}, {edges[-1]:g}]: "
                f"need {n}, have {in_range} ({out_of_range} out of range)"
            )
        for pool in pools:
            rng.shuffle(pool)
        available = [len(p) for p in pools]

        taken = [min(t, len(p)) for t, p in zip(targets, pools, strict=True)]
        selected = [c for p, k in zip(pools, taken, strict=True) for c in p[:k]]
        cursor = list(taken)
        moved = 0
        for group in range(n_groups):
            deficit = targets[group] - taken[group]
            while deficit > 0:
                donor = self._nearest_donor(group, cursor, pools)
                if donor is None:  # impossibile: in_range >= n
                    raise DataError("stratified sampling: no donor group left")
                selected.append(pools[donor][cursor[donor]])
                cursor[donor] += 1
                moved += 1
                deficit -= 1
        selected.sort(key=lambda c: c.key)
        details = {"reference_range": [low, edges[-1]], "out_of_range": out_of_range}
        return SampleResult(selected, moved, edges, targets, available, taken, details)

    @staticmethod
    def _nearest_donor(group: int, cursor: list[int], pools: list[list[Candidate]]) -> int | None:
        """Gruppo adiacente più vicino con candidati residui (a parità, quello inferiore)."""
        for distance in range(1, len(pools)):
            for other in (group - distance, group + distance):
                if 0 <= other < len(pools) and cursor[other] < len(pools[other]):
                    return other
        return None
