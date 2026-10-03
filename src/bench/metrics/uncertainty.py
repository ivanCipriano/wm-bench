"""Intervalli di confidenza (SPEC §13.6, invariante I7).

``ClusterBootstrap`` ricampiona i ``problem_key`` con reinserimento: i campioni dello stesso
problema non sono indipendenti, quindi l'unità di ricampionamento è il problema.
In Milestone 4 serve per Pass@1; le altre metriche arrivano in Milestone 7.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pandas as pd
from bench_contracts import derive_seed


class UncertaintyEstimator(ABC):
    """Stima di un intervallo di confidenza per una statistica di una tabella."""

    @abstractmethod
    def interval(
        self, table: pd.DataFrame, statistic: Callable[[pd.DataFrame], float], alpha: float
    ) -> tuple[float, float]:
        """Estremi dell'intervallo al livello ``1 - alpha``."""


class ClusterBootstrap(UncertaintyEstimator):
    """Bootstrap per problema con intervallo percentile.

    Args:
        seed: seme del generatore, da ``bootstrap_seed``.
        n_resamples: numero di ricampionamenti (SPEC: 1.000).
        cluster_column: colonna che identifica il problema.
    """

    def __init__(
        self, seed: int, n_resamples: int = 1000, cluster_column: str = "problem_key"
    ) -> None:
        if n_resamples <= 0:
            raise ValueError("n_resamples must be positive")
        self.seed = seed
        self.n_resamples = n_resamples
        self.cluster_column = cluster_column

    def interval(
        self, table: pd.DataFrame, statistic: Callable[[pd.DataFrame], float], alpha: float = 0.05
    ) -> tuple[float, float]:
        """Intervallo di una statistica qualunque (ricalcolata su ogni ricampionamento)."""
        groups = {k: g for k, g in table.groupby(self.cluster_column, sort=True)}
        keys = list(groups)
        if not keys:
            return (float("nan"), float("nan"))
        rng = np.random.default_rng(self.seed)
        values = np.empty(self.n_resamples)
        for b in range(self.n_resamples):
            picks = rng.integers(0, len(keys), len(keys))
            values[b] = statistic(pd.concat([groups[keys[i]] for i in picks], ignore_index=True))
        low, high = np.percentile(values, [100 * alpha / 2, 100 * (1 - alpha / 2)])
        return float(low), float(high)

    def mean_interval(
        self, per_cluster: npt.NDArray[np.float64], alpha: float = 0.05
    ) -> tuple[float, float]:
        """Intervallo della media di un valore per problema (es. Pass@1 = media dei c/n)."""
        per_cluster = np.asarray(per_cluster, dtype=float)
        if per_cluster.size == 0:
            return (float("nan"), float("nan"))
        rng = np.random.default_rng(self.seed)
        picks = rng.integers(0, per_cluster.size, (self.n_resamples, per_cluster.size))
        means = per_cluster[picks].mean(axis=1)
        low, high = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
        return float(low), float(high)


def bootstrap_seed(global_seed: int, metric: str, cell_key: str) -> int:
    """Seme del bootstrap (SPEC §18): ``(global_seed, "bootstrap", metrica, cella)``."""
    return derive_seed(global_seed, "bootstrap", metric, cell_key)


def pass_at_1(table: pd.DataFrame) -> tuple[float, npt.NDArray[np.float64]]:
    """Pass@1 stimatore non distorto con N campioni per problema: media dei ``c/n``.

    ``table`` ha una riga per campione con ``problem_key`` e ``status``; restituisce il valore
    e il vettore ``c/n`` per problema (in ordine di ``problem_key``), da passare al bootstrap.
    """
    passed = (table["status"] == "PASSED").groupby(table["problem_key"], sort=True).mean()
    per_problem = passed.to_numpy(dtype=float)
    return (float(per_problem.mean()) if per_problem.size else float("nan")), per_problem
