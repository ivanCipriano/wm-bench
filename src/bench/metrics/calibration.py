"""Calibrazione delle soglie (SPEC §13.2, invariante I3)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MIN_SCORE = -math.inf  # punteggio dei negativi con rilevazione FAILED: mai falso positivo


@dataclass(frozen=True)
class Calibration:
    """Esito della calibrazione: soglia, FPR raggiunto sui negativi, numerosità."""

    threshold: float
    achieved_fpr: float
    n_negatives: int
    underpowered: bool


class ThresholdCalibrator:
    """Soglia = il **più piccolo** candidato (valori distinti dei negativi più ``+inf``) con
    ``mean(neg >= τ) ≤ target_fpr``. Nessuna randomizzazione: con punteggi discreti l'FPR
    raggiunto può essere molto inferiore al target e si riporta così com'è.

    Args:
        target_fpr: FPR obiettivo (protocollo: 0,01).
        min_negatives: sotto questa numerosità la soglia è ``underpowered``.
    """

    def __init__(self, target_fpr: float = 0.01, min_negatives: int = 1000) -> None:
        if not 0.0 < target_fpr < 1.0:
            raise ValueError("target_fpr must be in (0, 1)")
        self.target_fpr = target_fpr
        self.min_negatives = min_negatives

    def fit(self, neg_scores: Sequence[float]) -> Calibration:
        """Soglia dai punteggi dei negativi (``MIN_SCORE`` per i ``FAILED``)."""
        neg = np.asarray(neg_scores, dtype=float)
        if neg.size == 0:
            raise ValueError("no negatives to calibrate on")
        if np.isnan(neg).any():
            raise ValueError("negative scores contain NaN: use MIN_SCORE for FAILED detections")
        candidates = np.append(np.unique(neg), math.inf)
        for tau in candidates:  # in ordine crescente: il primo che rispetta il vincolo
            fpr = float(np.mean(neg >= tau))
            if fpr <= self.target_fpr:
                return Calibration(
                    threshold=float(tau),
                    achieved_fpr=fpr,
                    n_negatives=int(neg.size),
                    underpowered=bool(neg.size < self.min_negatives),
                )
        raise AssertionError("unreachable: +inf always satisfies the constraint")


def detected(score: float | None, status: str, embed_status: str | None, threshold: float) -> bool:
    """Decisione (SPEC §13.2): ``score >= τ`` con rilevazione riuscita e inserimento non FAILED."""
    if status != "OK" or score is None or embed_status == "FAILED":
        return False
    return bool(score >= threshold)
