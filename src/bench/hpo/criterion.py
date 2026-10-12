"""Criterio di selezione dell'HPO (SPEC §14.4): massimo TPR con un vincolo su ΔPass@1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from bench.domain.errors import ConfigError


@dataclass(frozen=True)
class Candidate:
    """Una configurazione valutata sulla parte di sviluppo (SPEC §14.3).

    ``pass1_drop_pp`` = (Pass@1 della baseline − Pass@1 dei marcati) × 100: positivo = perdita.
    """

    config_hash: str
    hparams: dict[str, Any]
    n_positives: int
    n_detected: int
    tpr_dev: float
    pass1_dev: float
    pass1_baseline_dev: float
    pass1_drop_pp: float


@dataclass(frozen=True)
class Selection:
    """Configurazione scelta, con l'esito del vincolo."""

    candidate: Candidate
    constraint_satisfied: bool
    n_admissible: int


class ConstrainedTprCriterion:
    """Ammissibili = ``pass1_drop_pp ≤ max``.

    - Se ce ne sono: massimo ``tpr_dev``; a parità di TPR minimo ``pass1_drop_pp``; a ulteriore
      parità ``config_hash`` lessicograficamente minore (deterministico).
    - Altrimenti: minimo ``pass1_drop_pp`` (a parità, ``config_hash`` minore) e
      ``constraint_satisfied = False``: il caso va segnalato nel report.

    Args:
        max_pass1_drop_pp: perdita massima di Pass@1 in punti percentuali (``hpo``, provvisoria).
    """

    def __init__(self, max_pass1_drop_pp: float) -> None:
        if max_pass1_drop_pp < 0:
            raise ConfigError("max_pass1_drop_pp must be >= 0")
        self.max_pass1_drop_pp = max_pass1_drop_pp

    def admissible(self, candidate: Candidate) -> bool:
        return candidate.pass1_drop_pp <= self.max_pass1_drop_pp

    def select(self, candidates: list[Candidate]) -> Selection:
        """Configurazione scelta fra i candidati.

        Raises:
            ConfigError: nessun candidato, o valori mancanti (NaN).
        """
        if not candidates:
            raise ConfigError("no HPO candidates")
        bad = [
            c.config_hash
            for c in candidates
            if math.isnan(c.tpr_dev) or math.isnan(c.pass1_drop_pp)
        ]
        if bad:
            raise ConfigError(f"HPO candidates with missing TPR or Pass@1: {bad}")
        ok = [c for c in candidates if self.admissible(c)]
        if ok:
            best = min(ok, key=lambda c: (-c.tpr_dev, c.pass1_drop_pp, c.config_hash))
            return Selection(best, True, len(ok))
        best = min(candidates, key=lambda c: (c.pass1_drop_pp, c.config_hash))
        return Selection(best, False, 0)
