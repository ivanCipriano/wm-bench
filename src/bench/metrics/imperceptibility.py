"""Misure d'impercettibilità accoppiate: CodeBLEU e perplexity (SPEC §13.4; ADR-015; D9 e D37).

Decisioni dell'utente del 28 ottobre 2026 (estensione di D9, D37):

- **CodeBLEU, misura principale per tutti i metodi:** ogni campione marcato contro **ciascuno** dei
  campioni di riferimento dello stesso problema, poi la media (riferimento: la baseline; per
  MCGMark la gemella). Niente riferimenti multipli (gonfiano il punteggio). **Variabilità
  naturale:** lo stesso calcolo fra i campioni del riferimento dello stesso problema (ognuno contro
  gli altri); si riporta anche la differenza principale − naturale, per problema;
- **CodeBLEU secondaria:** per STONE e SWEET il confronto per indice (stesso seme, SPEC); per ACW
  il confronto con il campione da cui deriva (``parent_id``);
- **ΔNLL e ΔPPL, misura principale per tutti i metodi:** per problema, media dei marcati − media
  del riferimento (NLL per token e perplexity = exp(NLL) del singolo campione); media e mediana sui
  problemi. Secondaria per STONE e SWEET (indice) e per ACW (esatta): media delle differenze per
  coppia. Per ACW, senza campioni vuoti, la differenza delle medie per problema coincide con la
  media delle differenze esatte (cambia solo l'intervallo);
- **codice vuoto** (estrazione fallita, stringa vuota): escluso da tutte le misure (dalle medie per
  problema; nelle coppie cade la coppia); i campioni con inserimento ``FAILED`` o ``PARTIAL`` con
  codice restano.

Tutte le tabelle hanno almeno ``sample_id``, ``problem_key`` e ``code``; le misure per problema
restituiscono una serie indicizzata da ``problem_key`` (ordinata), da passare al bootstrap per
problema.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

MIN_WITHIN = 2  # campioni del riferimento per la variabilità naturale
CODEBLEU_LANG = {"python": "python", "java": "java", "cpp": "cpp", "javascript": "javascript"}


def is_empty(code: Any) -> bool:
    """Codice davvero vuoto: assente o solo spazi."""
    return code is None or (isinstance(code, float) and np.isnan(code)) or not str(code).strip()


def non_empty(table: pd.DataFrame) -> pd.DataFrame:
    """Righe con codice non vuoto."""
    return table[[not is_empty(c) for c in table["code"]]]


def empty_counts(table: pd.DataFrame) -> tuple[int, int]:
    """(campioni vuoti, campioni) di una tabella."""
    return sum(is_empty(c) for c in table["code"]), len(table)


def codebleu_score(reference: str, hypothesis: str, language: str) -> float:
    """CodeBLEU (``codebleu==0.7.0``) di ``hypothesis`` rispetto a un solo riferimento."""
    if reference == hypothesis:
        return 1.0
    from codebleu import calc_codebleu

    lang = CODEBLEU_LANG[language]
    return float(calc_codebleu([reference], [hypothesis], lang=lang)["codebleu"])


Scorer = Callable[[str, str], float]


def _groups(table: pd.DataFrame) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for row in non_empty(table).itertuples(index=False):
        out.setdefault(str(row.problem_key), []).append((str(row.sample_id), str(row.code)))
    return out


def cross_mean(marked: pd.DataFrame, reference: pd.DataFrame, score: Scorer) -> pd.Series:
    """Per problema: media, sui campioni marcati, della media dei punteggi contro ciascun campione
    del riferimento dello stesso problema (problemi con almeno un campione per parte)."""
    refs = _groups(reference)
    values: dict[str, float] = {}
    for problem, samples in sorted(_groups(marked).items()):
        others = refs.get(problem)
        if not others:
            continue
        per_sample = [np.mean([score(r, code) for _, r in others]) for _, code in samples]
        values[problem] = float(np.mean(per_sample))
    return pd.Series(values, dtype=float).sort_index()


def within_mean(reference: pd.DataFrame, score: Scorer) -> pd.Series:
    """Variabilità naturale per problema: ogni campione del riferimento contro gli altri campioni
    dello stesso problema, media per campione e poi per problema (almeno 2 campioni)."""
    values: dict[str, float] = {}
    for problem, samples in sorted(_groups(reference).items()):
        if len(samples) < MIN_WITHIN:
            continue
        per_sample = [
            np.mean([score(r, code) for j, (_, r) in enumerate(samples) if j != i])
            for i, (_, code) in enumerate(samples)
        ]
        values[problem] = float(np.mean(per_sample))
    return pd.Series(values, dtype=float).sort_index()


def paired(marked: pd.DataFrame, reference: pd.DataFrame, on: tuple[str, str]) -> pd.DataFrame:
    """Coppie (marcato, riferimento) senza codice vuoto: ``on`` = (colonna del marcato, colonna
    del riferimento), es. ``("sample_index", "sample_index")`` con ``problem_key`` o
    ``("parent_id", "sample_id")`` per ACW. Colonne ``problem_key``, ``*_m`` e ``*_r``."""
    left, right = on
    m = non_empty(marked)
    r = non_empty(reference)
    keys_m = ["problem_key", left] if left != "parent_id" else [left]
    keys_r = ["problem_key", right] if right != "sample_id" else [right]
    out = m.merge(r, left_on=keys_m, right_on=keys_r, suffixes=("_m", "_r"))
    if "problem_key" not in out.columns:
        out["problem_key"] = out["problem_key_m"]
    return out


def difference_of_means(marked: pd.DataFrame, reference: pd.DataFrame, column: str) -> pd.Series:
    """Per problema: media di ``column`` dei marcati − media del riferimento (problemi con
    entrambe le parti; codice vuoto escluso)."""
    a = non_empty(marked).groupby("problem_key")[column].mean()
    b = non_empty(reference).groupby("problem_key")[column].mean()
    common = a.index.intersection(b.index)
    return (a.loc[common] - b.loc[common]).astype(float).sort_index()


def per_problem_of_pairs(pairs: pd.DataFrame, values: pd.Series) -> pd.Series:
    """Media per problema di un valore per coppia (per il bootstrap per problema)."""
    return values.groupby(pairs["problem_key"].to_numpy()).mean().astype(float).sort_index()


def bootstrap_stat(
    per_problem: pd.Series, statistic: Callable[[Any], float], seed: int, n: int
) -> tuple[float, tuple[float, float]]:
    """Statistica sui valori per problema e intervallo percentile 95% del bootstrap per problema."""
    values = per_problem.to_numpy(dtype=float)
    if values.size == 0:
        return float("nan"), (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, values.size, (n, values.size))
    stats = np.array([statistic(values[p]) for p in picks])
    low, high = np.percentile(stats, [2.5, 97.5])
    return float(statistic(values)), (float(low), float(high))
