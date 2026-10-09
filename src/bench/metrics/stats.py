"""Statistiche delle metriche di rilevabilità (SPEC §13.3, §13.6)."""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.stats import beta, norm
from sklearn.metrics import roc_auc_score

FloatArray = npt.NDArray[np.float64]


def wilson(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Intervallo di Wilson per una proporzione."""
    if n == 0:
        return (math.nan, math.nan)
    z = float(norm.ppf(1 - alpha / 2))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def clopper_pearson(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Intervallo esatto di Clopper-Pearson (usato per l'FPR)."""
    if n == 0:
        return (math.nan, math.nan)
    low = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, n - successes + 1))
    high = 1.0 if successes == n else float(beta.ppf(1 - alpha / 2, successes + 1, n - successes))
    return (low, high)


def finite_scores(pos: FloatArray, neg: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Per l'AUROC: ``-inf`` (rilevazione o inserimento falliti) sotto ogni punteggio finito,
    ``+inf`` (es. p = 0) sopra; l'ordine, l'unica cosa che conta, è conservato."""
    both = np.concatenate([pos, neg])
    finite = both[np.isfinite(both)]
    floor = (finite.min() - 1.0) if finite.size else 0.0
    ceil = (finite.max() + 1.0) if finite.size else 1.0

    def clip(x: FloatArray) -> FloatArray:
        return np.where(x == -np.inf, floor, np.where(x == np.inf, ceil, x))

    return clip(pos), clip(neg)


def auroc(pos: FloatArray, neg: FloatArray) -> float:
    """AUROC positivi contro negativi (``sklearn.metrics.roc_auc_score``)."""
    if pos.size == 0 or neg.size == 0:
        return math.nan
    p, n = finite_scores(pos, neg)
    labels = np.concatenate([np.ones(p.size), np.zeros(n.size)])
    return float(roc_auc_score(labels, np.concatenate([p, n])))


def auroc_interval(
    positives: pd.DataFrame,
    neg: FloatArray,
    seed: int,
    n_resamples: int,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """IC dell'AUROC: bootstrap per problema sui positivi (colonne ``problem_key``, ``score``) e
    ricampionamento indipendente dei negativi (SPEC §13.3)."""
    if positives.empty or neg.size == 0:
        return (math.nan, math.nan)
    groups = [g["score"].to_numpy(dtype=float) for _, g in positives.groupby("problem_key")]
    rng = np.random.default_rng(seed)
    values = np.empty(n_resamples)
    for b in range(n_resamples):
        picks = rng.integers(0, len(groups), len(groups))
        pos_b = np.concatenate([groups[i] for i in picks])
        neg_b = neg[rng.integers(0, neg.size, neg.size)]
        values[b] = auroc(pos_b, neg_b)
    low, high = np.nanpercentile(values, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(low), float(high)


def ratio_interval(
    table: pd.DataFrame, column: str, seed: int, n_resamples: int, alpha: float = 0.05
) -> tuple[float, float]:
    """IC di una media sui campioni (es. TPR) con bootstrap per problema (SPEC §13.6).

    Equivale a ``ClusterBootstrap.interval`` con la media come statistica, ma lavora su somme e
    conteggi per problema invece di concatenare tabelle a ogni ricampionamento.
    """
    grouped = table.groupby("problem_key", sort=True)[column]
    sums = grouped.sum().to_numpy(dtype=float)
    counts = grouped.count().to_numpy(dtype=float)
    if sums.size == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, sums.size, (n_resamples, sums.size))
    values = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    low, high = np.percentile(values, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(low), float(high)
