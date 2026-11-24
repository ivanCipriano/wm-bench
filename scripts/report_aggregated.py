"""Rilevazione aggregata su k campioni dei metodi post-hoc multi-bit (§5.10 della PIPELINE di
multimark; decisioni dell'utente del 24 novembre 2026, v1.3.16 punto 7).

Per ``mcss`` e ``multimark`` il punteggio è una funzione dei voti salvati in ``extra``
(``agreement`` A con il messaggio atteso del campione, cioè registro ``N_reg = 1``, e ``n_votes``
N, §3.10.4), quindi si **somma** su k campioni senza rilanciare niente::

    score_k = -log10 P[Bin(ΣN, 1/2) >= ΣA]

(esatto con ``math.comb`` come la libreria; con pesi non interi l'approssimazione normale della
libreria). Un campione con rilevazione non riuscita porta 0 voti; un gruppo senza voti non è mai
rilevato (``MIN_SCORE``, come i ``FAILED`` della calibrazione). I positivi con inserimento
``FAILED`` sono esclusi (I2).

**Gruppi.** Per ``k ∈ {1, 2, 4, 8, 16}`` si estraggono 1.000 gruppi di k campioni da **problemi
distinti** (k problemi senza reinserimento, poi un campione a caso di ciascuno) con il seme
``derive_seed(global_seed, "aggregate", metodo, livello, str(k))`` (livello ``L1L2`` per i negativi
di sviluppo): a parità di metodo, livello e k le configurazioni e le condizioni (senza attacco e con
attacco) usano le stesse estrazioni di indici. Se i problemi sono meno di k, quel k non si calcola.

**Fasi** (sottocomandi; tutto da tabelle ``detection`` già calcolate):

- ``verify`` (solo ``split=dev``, prima del congelamento): (1) a k = 1 il punteggio ricalcolato da
  (A, N) coincide con lo ``score`` salvato riga per riga (scarto < 1e-9); (2) metà dei problemi
  dei negativi di sviluppo per calibrare τ_k, l'altra metà per misurare l'FPR, e viceversa
  (metà fissata da ``derive_seed(global_seed, "aggregate-half", problem_key) % 2``): l'FPR deve
  restare sotto obiettivo + 2σ, con σ² = t(1−t)(1/n_cal + 1/n_out) (errore della soglia stimata
  su una metà più quello della misura sull'altra; controllo informativo, WARN: i punteggi sono
  discreti e la soglia all'1% da 1.000 gruppi è rumorosa; l'FPR della metà di misura si stampa
  sempre anche per livello, nativi L1, nativi L2 e integrazione, anche in ``calibrate``);
  (3) τ_k confrontata con la soglia teorica ``score_k >= 2``; (4) TPR dei positivi di dev non
  decrescente in k (oltre 2σ della differenza), con righe, problemi distinti, punteggio medio
  per k e il calo più grande con il 2σ per n = gruppi e per n = problemi; (5) due esecuzioni
  danno le stesse soglie (determinismo). Bloccanti: (1), (5) e (4) **senza attacco**; (4) con un
  attacco è un avviso (modifica fatta dopo il FAIL di mcss sotto T2.5 in
  ``P6_aggregated_dev.txt``, decisione dell'utente; soglie e risultati invariati). Gli intervalli
  di Wilson per livello hanno anche la versione con n = problemi distinti. Non scrive soglie;
- ``calibrate`` (solo dev, metodo non ancora congelato): τ_k all'FPR obiettivo sui gruppi dei
  negativi umani di sviluppo L1+L2 (le stesse parti di ``calibrate``: nativi di L1, nativi di L2,
  integrazione combinata), regola di :class:`ThresholdCalibrator`. Scrive
  ``aggregated/<metodo>/<modello>/<cfg>/L1L2_python.json`` sotto gli artefatti (mai sovrascritto);
  il congelamento ne registra lo sha256;
- ``evaluate``: TPR@τ_k dei positivi (senza attacco e con gli attacchi T1.4 natural r = 1,0,
  T2.1 b = all, T1.6 convert, T2.5 (2, around)) e FPR dei negativi umani della stessa parte, con
  intervalli di Wilson e **bootstrap per gruppo** (ricampionamento dei 1.000 gruppi, percentili
  2,5–97,5). Sul test legge solo soglie congelate (sha256 del record ``freeze/<metodo>.json``);
  per ClassEval (L3, nessuna parte di sviluppo) si usano le τ_k di L1+L2 dev, dichiarato nel
  report. La τ_k è unica (L1+L2 dev, nessuna soglia per livello): l'FPR si riporta **per
  livello** (``neg_human`` del livello), con Wilson per gruppi, Wilson per problemi distinti e
  bootstrap, in una tabella a parte del report. Scrive ``aggregated_<livello>.csv`` in ``--out``.

Per MCGMark non si calcola: il suo punteggio non è additivo per campione.

Uso (bench-core, in un job o con srun: legge solo tabelle; minuti)::

    python scripts/report_aggregated.py verify --report /tmp/P6_aggregated_dev_v2.txt
    python scripts/report_aggregated.py calibrate --report /tmp/P6_aggregated_calibrate.txt
    python scripts/report_aggregated.py evaluate --split test --levels L1 L2 L3 \\
        --out $WMB/artifacts/tables --report /tmp/P6_aggregated_test.txt

Le configurazioni sono quelle del registro ``configs/freeze/posthoc.yaml`` (``--config
metodo:hash:etichetta`` per altre, solo con ``verify``). I report non si sovrascrivono.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]

KS: tuple[int, ...] = (1, 2, 4, 8, 16)
N_GROUPS = 1000
N_BOOT = 1000
THEORETICAL = 2.0  # soglia teorica score >= 2 (§3.10.3, metrica secondaria)
MIN_SCORE = -math.inf
IDENTITY_TOL = 1e-9
LANGUAGE = "python"
DEV_LEVEL = "L1L2"
# Celle attaccate dell'aggregata (v1.3.16 punto 7): (attacco, parametri contenuti nel punto).
ATTACKS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("T1.4", {"ratio": 1.0, "name_kind": "natural"}),
    ("T2.1", {"b": "all"}),
    ("T1.6", {"caps": "convert"}),
    ("T2.5", {"n": 2, "position": "around"}),
)


@dataclass(frozen=True)
class Config:
    """Configurazione da aggregare: metodo, hash ed etichetta."""

    method: str
    config_hash: str
    label: str

    @classmethod
    def parse(cls, text: str) -> Config:
        parts = text.split(":")
        if len(parts) != 3 or not all(parts):  # noqa: PLR2004
            raise argparse.ArgumentTypeError(f"expected method:hash:label, got {text!r}")
        return cls(*parts)


# --------------------------------------------------------------------------- punteggio
def binom_score(n: float, a: float) -> float:
    """``-log10 P[Bin(n, 1/2) >= a]`` come la libreria (``multimark.detect``): esatto con pesi
    interi, approssimazione normale altrimenti; ``MIN_SCORE`` senza voti."""
    if n <= 0:
        return MIN_SCORE
    if float(a).is_integer() and float(n).is_integer():
        ni, ai = int(n), int(a)
        if ai <= 0:
            return 0.0
        if ai > ni:
            return math.inf
        exact = sum(math.comb(ni, j) for j in range(ai, ni + 1))
        return -(math.log10(exact) - ni * math.log10(2))
    z = (2 * a - n) / math.sqrt(n)
    tail = 0.5 * math.erfc(z / math.sqrt(2))
    return -math.log10(tail) if tail > 0 else math.inf


def votes(table: pd.DataFrame, positives: bool) -> pd.DataFrame:
    """``problem_key``, A e N per riga; rilevazione non riuscita = 0 voti; i positivi con
    inserimento ``FAILED`` sono esclusi (I2)."""
    t = table
    if positives and "embed_status" in t.columns:
        t = t[t["embed_status"].astype(str) != "FAILED"]
    a_list, n_list = [], []
    for status, raw in zip(t["status"], t["extra_json"], strict=True):
        extra = json.loads(raw) if isinstance(raw, str) and raw else {}
        ok = status == "OK" and extra.get("n_votes") is not None
        a_list.append(float(extra.get("agreement") or 0) if ok else 0.0)
        n_list.append(float(extra.get("n_votes") or 0) if ok else 0.0)
    out = pd.DataFrame({"problem_key": t["problem_key"].astype(str).to_numpy(),
                        "A": a_list, "N": n_list})  # fmt: skip
    if "part" in t.columns:  # livello dei negativi di sviluppo (dev_negatives)
        out["part"] = t["part"].astype(str).to_numpy()
    return out


def identity(table: pd.DataFrame) -> dict[str, float]:
    """Righe ``OK`` in cui il punteggio da (A, N) differisce dallo ``score`` salvato."""
    n = bad = 0
    worst: float = 0.0
    for status, score, raw in zip(table["status"], table["score"], table["extra_json"],
                                  strict=True):  # fmt: skip
        if status != "OK" or score is None or (isinstance(score, float) and math.isnan(score)):
            continue
        extra = json.loads(raw) if isinstance(raw, str) and raw else {}
        if extra.get("n_votes") is None or extra.get("agreement") is None:
            bad += 1
            n += 1
            continue
        got = binom_score(float(extra["n_votes"]), float(extra["agreement"]))
        diff = 0.0 if got == float(score) else abs(got - float(score))
        n += 1
        if diff > IDENTITY_TOL:
            bad += 1
        worst = max(worst, diff)
    return {"n": n, "mismatch": bad, "max_diff": worst}


# --------------------------------------------------------------------------- gruppi
def seed_for(global_seed: int, method: str, level: str, k: int) -> int:
    from bench.domain.ids import derive_seed

    return int(derive_seed(global_seed, "aggregate", method, level, str(k)))


def groups(pool: pd.DataFrame, k: int, n_groups: int, seed: int) -> FloatArray | None:
    """Somme (ΣA, ΣN) di ``n_groups`` gruppi di k campioni da problemi distinti; ``None`` se i
    problemi sono meno di k. Estrazione deterministica e indipendente dall'ordine delle righe."""
    pool = pool.sort_values(["problem_key", "A", "N"], kind="mergesort").reset_index(drop=True)
    keys = sorted(pool["problem_key"].unique())
    if len(keys) < k or k < 1:
        return None
    rows = {key: pool.index[pool["problem_key"] == key].to_numpy() for key in keys}
    a, n = pool["A"].to_numpy(dtype=float), pool["N"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    out = np.zeros((n_groups, 2))
    for g in range(n_groups):
        chosen = rng.choice(len(keys), size=k, replace=False)
        idx = [rows[keys[c]][rng.integers(len(rows[keys[c]]))] for c in chosen]
        out[g] = (a[idx].sum(), n[idx].sum())
    return out


def scores(sums: FloatArray) -> FloatArray:
    return np.array([binom_score(n, a) for a, n in sums], dtype=float)


def calibrate(neg_scores: FloatArray, target_fpr: float) -> dict[str, float]:
    """τ_k con la regola della calibrazione (il più piccolo candidato con FPR ≤ obiettivo)."""
    from bench.metrics.calibration import ThresholdCalibrator

    fit = ThresholdCalibrator(target_fpr=target_fpr, min_negatives=1).fit(neg_scores.tolist())
    return {"threshold": fit.threshold, "achieved_fpr": fit.achieved_fpr,
            "n_groups": fit.n_negatives}  # fmt: skip


def rate(hits: FloatArray, boot_seed: int, n_boot: int = N_BOOT) -> dict[str, float]:
    """Proporzione con Wilson e bootstrap per gruppo (percentili 2,5 e 97,5)."""
    from bench.metrics.stats import wilson

    n = int(hits.size)
    if n == 0:
        return {"value": math.nan, "n": 0, "wilson_low": math.nan, "wilson_high": math.nan,
                "boot_low": math.nan, "boot_high": math.nan}  # fmt: skip
    k = int(hits.sum())
    lo, hi = wilson(k, n)
    rng = np.random.default_rng(boot_seed)
    boots = hits[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    return {"value": k / n, "n": n, "wilson_low": lo, "wilson_high": hi,
            "boot_low": float(np.percentile(boots, 2.5)),
            "boot_high": float(np.percentile(boots, 97.5))}  # fmt: skip


def boot_seed(global_seed: int, *parts: str) -> int:
    from bench.domain.ids import derive_seed

    return int(derive_seed(global_seed, "aggregate-boot", *parts))


def half_limit(target: float, n_cal: int, n_out: int) -> float:
    """FPR massimo ammesso sulla metà di misura: obiettivo + 2σ, con σ² = t(1−t)(1/n_cal +
    1/n_out) (la soglia stimata su una metà ha il suo errore di campionamento, che si somma a
    quello della misura sull'altra; la sola Wilson della misura sarebbe troppo stretta)."""
    return target + 2 * math.sqrt(target * (1 - target) * (1 / max(1, n_cal) + 1 / max(1, n_out)))


def decreases(before: float, after: float, n: int = N_GROUPS) -> bool:
    """TPR che cala da un k al successivo oltre 2σ della differenza (gruppi indipendenti)."""
    sigma = math.sqrt((before * (1 - before) + after * (1 - after)) / n)
    return after < before - max(2 * sigma, 1e-9)


def half_of(global_seed: int, key: str) -> int:
    from bench.domain.ids import derive_seed

    return int(derive_seed(global_seed, "aggregate-half", key)) % 2


def threshold_for(neg: pd.DataFrame, k: int, seed: int, target: float) -> dict[str, float] | None:
    sums = groups(neg, k, N_GROUPS, seed)
    if sums is None:
        return None
    s = scores(sums)
    out = calibrate(s, target)
    out["theoretical_fpr"] = float(np.mean(s >= THEORETICAL))
    out["n_problems"] = int(neg["problem_key"].nunique())
    out["n_rows"] = len(neg)
    return out


def group_scores(pool: pd.DataFrame, k: int, seed: int) -> FloatArray | None:
    sums = groups(pool, k, N_GROUPS, seed)
    return None if sums is None else scores(sums)


def hits_at(pool: pd.DataFrame, k: int, seed: int, tau: float) -> FloatArray | None:
    s = group_scores(pool, k, seed)
    return None if s is None else (s >= tau).astype(float)


def mean_score(s: FloatArray) -> str:
    """Media dei punteggi aggregati dei gruppi con voti (i gruppi senza voti a parte)."""
    finite = s[np.isfinite(s)]
    text = f"{finite.mean():.3f}" if finite.size else "nan"
    return text + (f"({s.size - finite.size} no votes)" if finite.size < s.size else "")


def problem_wilson(rate_value: float, n_problems: int) -> tuple[float, float]:
    """Wilson con n = problemi distinti: i 1.000 gruppi riusano gli stessi problemi, quindi la
    Wilson con n = gruppi è troppo stretta quando i problemi sono pochi (v1.3.16, dichiarato)."""
    from bench.metrics.stats import wilson

    return wilson(round(rate_value * n_problems), n_problems)


def worst_drop(vals: Sequence[float], n_groups: int, n_problems: int) -> tuple[float, float, float]:
    """Calo più grande fra k consecutivi e il suo 2σ con n = gruppi e con n = problemi."""
    best = (0.0, 0.0, 0.0)
    finite = [v for v in vals if not math.isnan(v)]
    for a, b in itertools.pairwise(finite):
        var = a * (1 - a) + b * (1 - b)
        if a - b > best[0]:
            best = (a - b, 2 * math.sqrt(var / n_groups), 2 * math.sqrt(var / max(1, n_problems)))
    return best


# --------------------------------------------------------------------------- dati
def _det(store: Any, c: Config, model: str, subject: str, level: str, split: str) -> Any:
    from bench.pipeline.stages.detect import detection_ref

    ref = detection_ref(c.method, model, c.config_hash, subject, level, LANGUAGE, split)
    return store.read_table(ref) if store.exists(ref) else None


def dev_negatives(store: Any, c: Config, model: str) -> pd.DataFrame | None:
    """Negativi umani di sviluppo L1+L2 con le parti di ``calibrate``: nativi di L1 (senza
    l'integrazione di L1), nativi di L2, integrazione combinata L1+L2."""
    from bench.domain.enums import Language
    from bench.pipeline.stages.prepare_data import native_ref

    l1 = _det(store, c, model, "neg_human", "L1", "dev")
    l2 = _det(store, c, model, "neg_human", "L2", "dev")
    integ = _det(store, c, model, "neg_integration", "L2", "dev")
    natives_ref = native_ref(Language(LANGUAGE))
    if l1 is None or l2 is None or integ is None or not store.exists(natives_ref):
        return None
    natives = set(store.read_table(natives_ref)["sample_id"])
    l1 = l1[l1["sample_id"].isin(natives)]
    parts = [l1.assign(part="L1"), l2.assign(part="L2"), integ.assign(part="integration_L1L2")]
    out: pd.DataFrame = pd.concat(parts, ignore_index=True)
    return out


def attack_subjects() -> list[tuple[str, str]]:
    """(etichetta, soggetto) delle celle attaccate dell'aggregata."""
    from bench.attacks.base import grid_attacks
    from bench.pipeline.stages.attack import attacked_subject

    out = []
    for attack_id, want in ATTACKS:
        for h, attack in grid_attacks(attack_id, language=LANGUAGE).items():
            params = dict(getattr(attack, "params", {}))
            if all(params.get(k) == v for k, v in want.items()):
                label = attack_id + " " + ",".join(f"{k}={v}" for k, v in want.items())
                out.append((label, attacked_subject(attack_id, h)))
    return out


Subjects = Sequence[tuple[str, str]]


def conditions(
    store: Any, c: Config, model: str, level: str, split: str, subjects: Subjects
) -> list[tuple[str, pd.DataFrame | None]]:
    """(condizione, positivi) senza attacco e con gli attacchi dell'aggregata."""
    out = [("no attack", _det(store, c, model, "positives", level, split))]
    out += [(label, _det(store, c, model, subj, level, split)) for label, subj in subjects]
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- fasi
class Aggregated:
    """Letture e calcoli comuni alle tre fasi."""

    def __init__(self, store: Any, global_seed: int, target_fpr: float,
                 configs: Sequence[Config], models: Sequence[str]) -> None:  # fmt: skip
        self.store, self.seed, self.target = store, global_seed, target_fpr
        self.configs, self.models = list(configs), list(models)
        self.out: list[str] = []
        self.warnings = self.comparisons = self.tpr_warnings = 0

    def w(self, line: str = "") -> None:
        self.out.append(line)

    def thresholds(self, neg: pd.DataFrame, method: str) -> dict[int, dict[str, float]]:
        found = {}
        for k in KS:
            t = threshold_for(neg, k, seed_for(self.seed, method, DEV_LEVEL, k), self.target)
            if t is not None:
                found[k] = t
        return found

    # ---- verify
    def verify(self) -> bool:
        ok = True
        subjects = attack_subjects()
        self.w(f"# rilevazione aggregata, verifica su dev (§5.10, v1.3.16): k {list(KS)}, "
               f"{N_GROUPS} gruppi da problemi distinti, FPR obiettivo {self.target}")  # fmt: skip
        for c in self.configs:
            for model in self.models:
                name = f"{c.label} ({c.method} {c.config_hash}) {model}"
                neg = dev_negatives(self.store, c, model)
                if neg is None:
                    self.w(f"## {name}: MISSING dev negatives (neg_human L1/L2, neg_integration)")
                    ok = False
                    continue
                ok &= self._verify_identity(name, c, model, neg)
                above, total = self._verify_halves(name, c, neg)
                self.warnings += above
                self.comparisons += total
                full = self.thresholds(votes(neg, positives=False), c.method)
                again = self.thresholds(votes(neg, positives=False), c.method)
                same = json.dumps(full, sort_keys=True) == json.dumps(again, sort_keys=True)
                self.w(f"## {name}: determinism (two runs, same thresholds): "
                       f"{'OK' if same else 'FAIL'}")  # fmt: skip
                ok &= same
                self.w(f"## {name}: τ_k on all dev negatives vs theoretical score >= "
                       f"{THEORETICAL:g}")  # fmt: skip
                self.w(f"  {'k':>3}{'τ_k':>10}{'FPR dev':>10}{'FPR at 2':>10}{'problems':>10}")
                for k, t in full.items():
                    self.w(f"  {k:>3}{t['threshold']:>10.3f}{t['achieved_fpr']:>10.4f}"
                           f"{t['theoretical_fpr']:>10.4f}{t['n_problems']:>10}")  # fmt: skip
                ok &= self._verify_tpr(name, c, model, full, subjects)
        self.w("")
        self.w(f"## split halves: FPR above the limit in {self.warnings} of {self.comparisons} "
               "comparisons (informative: the scores are discrete and τ_k from 1,000 groups of "
               "one half is noisy; about 5% of the comparisons are expected above a "
               "2σ limit)")  # fmt: skip
        self.w(f"## TPR decreasing in k on attacked rows: {self.tpr_warnings} (WARN, not "
               "blocking: change made after the FAIL of P6_aggregated_dev.txt, thresholds and "
               "results unchanged)")  # fmt: skip
        self.w(f"## result: {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} (blocking: "
               "k = 1 identity, determinism, TPR not decreasing in k beyond 2σ without "
               "attack)")  # fmt: skip
        return ok

    def _verify_identity(self, name: str, c: Config, model: str, neg: pd.DataFrame) -> bool:
        tables = [("dev negatives", neg)]
        for lv in ("L1", "L2"):
            t = _det(self.store, c, model, "positives", lv, "dev")
            if t is not None:
                tables.append((f"positives {lv}", t))
        good = True
        for what, t in tables:
            r = identity(t)
            state = "OK" if r["mismatch"] == 0 else "FAIL"
            good &= state == "OK"
            self.w(f"## {name}: k = 1 identity on {what}: {int(r['n'])} rows, "
                   f"{int(r['mismatch'])} mismatches, max |Δ| {r['max_diff']:.2e} "
                   f"{state}")  # fmt: skip
        return good

    def _verify_halves(self, name: str, c: Config, neg: pd.DataFrame) -> tuple[int, int]:
        from bench.metrics.stats import wilson

        pool = votes(neg, positives=False)
        half = np.array([half_of(self.seed, k) for k in pool["problem_key"]])
        parts = (pool[half == 0], pool[half == 1])
        self.w(f"## {name}: split halves of the dev negatives by problem "
               f"({parts[0]['problem_key'].nunique()} / {parts[1]['problem_key'].nunique()} "
               "problems): τ_k on one half, FPR on the other; limit = target + 2σ with the noise "
               "of the calibration and of the measurement, σ² = t(1-t)(1/n_cal + 1/n_out), "
               "n = min(groups, rows of the half); per level: Wilson with n = groups (too "
               "narrow: the groups reuse the same problems) and with n = distinct problems "
               "(conservative)")  # fmt: skip
        self.w(f"  {'k':>3}{'cal':>5}{'τ_k':>10}{'FPR other':>11}{'Wilson':>20}{'limit':>9}"
               f"{'check':>8}")  # fmt: skip
        self.w(f"  {'':>8}{'level':>18}{'FPR':>11}{'Wilson n=groups':>20}"
               f"{'Wilson n=problems':>20}")  # fmt: skip
        above = total = 0
        for k in KS:
            seed = seed_for(self.seed, c.method, DEV_LEVEL, k)
            for i, (cal, other) in enumerate((parts, parts[::-1])):
                t = threshold_for(cal, k, seed, self.target)
                hits = hits_at(other, k, seed, t["threshold"]) if t else None
                if t is None or hits is None:
                    self.w(f"  {k:>3}{'AB'[i]:>5}  not enough problems")
                    continue
                fp = int(hits.sum())
                lo, hi = wilson(fp, hits.size)
                limit = half_limit(self.target, min(N_GROUPS, len(cal)), min(N_GROUPS, len(other)))
                passed = fp / hits.size <= limit
                above += 0 if passed else 1
                total += 1
                self.w(f"  {k:>3}{'AB'[i]:>5}{t['threshold']:>10.3f}{fp / hits.size:>11.4f}"
                       f"{f'[{lo:.4f}, {hi:.4f}]':>20}{limit:>9.4f}"
                       f"{'OK' if passed else 'WARN':>8}")  # fmt: skip
                self._half_levels(other, k, seed, t["threshold"])
        return above, total

    def _half_levels(self, other: pd.DataFrame, k: int, seed: int, tau: float) -> None:
        """FPR della metà di misura per livello (nativi L1, nativi L2, integrazione), con i
        gruppi estratti dentro il livello e la stessa τ_k; sempre nel log (v1.3.16)."""
        from bench.metrics.stats import wilson

        if "part" not in other.columns:
            return
        for part in sorted(set(other["part"])):
            sub = other[other["part"] == part]
            hits = hits_at(sub, k, seed, tau)
            if hits is None:
                self.w(f"  {'':>8}{part:>18}  not enough problems "
                       f"({sub['problem_key'].nunique()})")  # fmt: skip
                continue
            fp = int(hits.sum())
            lo, hi = wilson(fp, hits.size)
            n_problems = int(sub["problem_key"].nunique())
            plo, phi = problem_wilson(fp / hits.size, n_problems)
            self.w(f"  {'':>8}{part:>18}{fp / hits.size:>11.4f}{f'[{lo:.4f}, {hi:.4f}]':>20}"
                   f"{f'[{plo:.4f}, {phi:.4f}]':>20}  rows {len(sub)}, "
                   f"problems {n_problems}")  # fmt: skip

    def _verify_tpr(self, name: str, c: Config, model: str, full: dict[int, dict[str, float]],
                    subjects: Subjects) -> bool:  # fmt: skip
        """TPR non decrescente in k: bloccante senza attacco, avviso (WARN) con attacco.

        Con un attacco che toglie quasi tutto il segnale (mcss sotto T2.5, TPR ≈ 0,07) non c'è
        niente da accumulare e, con ~50 problemi su L2, i 1.000 gruppi a k grande riusano gli
        stessi problemi: il 2σ con n = gruppi è sottostimato. Modifica fatta dopo il FAIL di
        ``P6_aggregated_dev.txt`` (decisione dell'utente, v1.3.16); non cambia soglie né
        risultati. Sotto ogni riga: righe, problemi distinti, punteggio medio per k e il calo
        più grande con il 2σ per n = gruppi e per n = problemi."""
        good = True
        self.w(f"## {name}: TPR of the dev positives at τ_k (must not decrease in k; blocking "
               "without attack, WARN with an attack)")  # fmt: skip
        for level in ("L1", "L2"):
            for label, table in conditions(self.store, c, model, level, "dev", subjects):
                if table is None:
                    self.w(f"  {level} {label:<34} no detection")
                    continue
                pool = votes(table, positives=True)
                n_problems = int(pool["problem_key"].nunique())
                vals, means = [], []
                for k, t in full.items():
                    s = group_scores(pool, k, seed_for(self.seed, c.method, level, k))
                    vals.append(math.nan if s is None else float((s >= t["threshold"]).mean()))
                    means.append("-" if s is None else mean_score(s))
                finite = [v for v in vals if not math.isnan(v)]
                mono = all(not decreases(a, b) for a, b in itertools.pairwise(finite))
                if label == "no attack":
                    good &= mono
                    state = "OK" if mono else "FAIL (decreasing)"
                else:
                    self.tpr_warnings += 0 if mono else 1
                    state = "OK" if mono else "WARN (decreasing, attacked: not blocking)"
                cells = "".join(f"{v:>8.3f}" for v in vals)
                self.w(f"  {level} {label:<34}{cells}  {state}")
                drop, s_groups, s_problems = worst_drop(vals, N_GROUPS, n_problems)
                self.w(f"  {'':>4}rows {len(pool)}, problems {n_problems}; mean score_k "
                       f"{' '.join(means)}; largest drop {drop:.3f}, 2σ groups {s_groups:.3f}, "
                       f"2σ problems {s_problems:.3f}")  # fmt: skip
        return good

    # ---- calibrate
    def calibrate_all(self, commit: str | None) -> list[tuple[Path, dict[str, Any]]]:
        from bench.hpo.posthoc_freeze import aggregated_path

        out = []
        for c in self.configs:
            for model in self.models:
                neg = dev_negatives(self.store, c, model)
                if neg is None:
                    raise SystemExit(f"{c.label} {model}: missing dev negatives")
                found = self.thresholds(votes(neg, positives=False), c.method)
                above, total = self._verify_halves(f"{c.label} ({c.method} {c.config_hash}) "
                                                   f"{model}", c, neg)  # fmt: skip
                self.w(f"## split halves: FPR above the limit in {above} of {total} comparisons "
                       "(informative, not blocking)")  # fmt: skip
                data = {
                    "method": c.method, "label": c.label, "config_hash": c.config_hash,
                    "model_id": model, "language": LANGUAGE, "levels": ["L1", "L2"],
                    "target_fpr": self.target, "n_groups": N_GROUPS, "ks": list(KS),
                    "seed": "derive_seed(global_seed, 'aggregate', method, 'L1L2', str(k))",
                    "negatives": "L1_native + L2_native + integration_L1L2 (dev)",
                    "wm_bench_commit": commit,
                    "created_at": datetime.now(UTC).isoformat(),
                    "k": {str(k): v for k, v in found.items()},
                }  # fmt: skip
                path = self.store.root / aggregated_path(c.method, model, c.config_hash)
                out.append((path, data))
                self.w(f"## {c.label} {model}: " + ", ".join(
                    f"k={k} τ={v['threshold']:.3f} (FPR {v['achieved_fpr']:.4f})"
                    for k, v in found.items()))  # fmt: skip
        return out

    # ---- evaluate
    def evaluate(
        self, levels: Sequence[str], split: str, thresholds: dict[tuple[str, str, str], Any]
    ) -> pd.DataFrame:
        rows = []
        subjects = attack_subjects()
        for c in self.configs:
            for model in self.models:
                thr = thresholds[(c.method, model, c.config_hash)]["k"]
                for level in levels:
                    neg = _det(self.store, c, model, "neg_human", level, split)
                    neg_pool = votes(neg, positives=False) if neg is not None else None
                    for label, table in conditions(self.store, c, model, level, split, subjects):
                        if table is None:
                            continue
                        pool = votes(table, positives=True)
                        for k_text, t in thr.items():
                            k, tau = int(k_text), float(t["threshold"])
                            seed = seed_for(self.seed, c.method, level, k)
                            hits = hits_at(pool, k, seed, tau)
                            if hits is None:
                                continue
                            tpr = rate(hits, boot_seed(self.seed, c.method, c.config_hash, model,
                                                       level, label, str(k)))  # fmt: skip
                            fp = (hits_at(neg_pool, k, seed, tau)
                                  if neg_pool is not None else None)  # fmt: skip
                            fpr = rate(fp if fp is not None else np.array([]),
                                       boot_seed(self.seed, c.method, c.config_hash, model, level,
                                                 "neg_human", str(k)))  # fmt: skip
                            n_pos = int(pool["problem_key"].nunique())
                            n_neg = (int(neg_pool["problem_key"].nunique())
                                     if neg_pool is not None else 0)  # fmt: skip
                            tlo, thi = problem_wilson(tpr["value"], n_pos)
                            flo, fhi = (problem_wilson(fpr["value"], n_neg) if n_neg
                                        else (math.nan, math.nan))  # fmt: skip
                            rows.append({
                                "method": c.method, "label": c.label, "config_hash": c.config_hash,
                                "model_id": model, "level": level, "split": split,
                                "condition": label, "k": k, "threshold": tau,
                                "threshold_from": "L1L2 dev",
                                "tpr": tpr["value"], "n_groups": tpr["n"],
                                "tpr_wilson_low": tpr["wilson_low"],
                                "tpr_wilson_high": tpr["wilson_high"],
                                "tpr_boot_low": tpr["boot_low"], "tpr_boot_high": tpr["boot_high"],
                                "fpr": fpr["value"], "fpr_wilson_low": fpr["wilson_low"],
                                "fpr_wilson_high": fpr["wilson_high"],
                                "fpr_boot_low": fpr["boot_low"], "fpr_boot_high": fpr["boot_high"],
                                "n_problems": n_pos,
                                "tpr_wilson_low_problems": tlo, "tpr_wilson_high_problems": thi,
                                "fpr_negatives": f"neg_human {level} {split}",
                                "n_neg_rows": 0 if neg_pool is None else len(neg_pool),
                                "n_neg_problems": n_neg,
                                "fpr_wilson_low_problems": flo, "fpr_wilson_high_problems": fhi,
                            })  # fmt: skip
        return pd.DataFrame(rows)


def evaluate_lines(frame: pd.DataFrame) -> list[str]:
    """Tabella del report: TPR (Wilson, bootstrap) e FPR per k."""
    out = ["# rilevazione aggregata (§5.10): TPR@τ_k e FPR dei negativi umani della stessa parte;"
           " τ_k dai negativi di sviluppo L1+L2 (ClassEval/L3: nessuna parte di sviluppo, τ_k di"
           " L1+L2 dev, dichiarato)"]  # fmt: skip
    if frame.empty:
        return [*out, "  no cells"]
    out.append(f"  {'config':<16}{'model':<22}{'level':<6}{'condition':<34}{'k':>3}{'TPR':>8}"
               f"{'Wilson':>18}{'bootstrap':>18}{'FPR':>8}{'groups':>8}")  # fmt: skip
    for r in frame.itertuples():
        out.append(f"  {r.label:<16}{r.model_id:<22}{r.level:<6}{r.condition:<34}{r.k:>3}"
                   f"{r.tpr:>8.3f}{f'[{r.tpr_wilson_low:.3f}, {r.tpr_wilson_high:.3f}]':>18}"
                   f"{f'[{r.tpr_boot_low:.3f}, {r.tpr_boot_high:.3f}]':>18}{r.fpr:>8.4f}"
                   f"{r.n_groups:>8}")  # fmt: skip
    return out + fpr_lines(frame)


def fpr_lines(frame: pd.DataFrame) -> list[str]:
    """FPR dell'aggregata **per livello** (τ_k unica di L1+L2 dev, nessuna soglia per livello):
    Wilson con n = gruppi (troppo stretta, i gruppi riusano i problemi), con n = problemi
    distinti e bootstrap per gruppo (v1.3.16, da riportare così nella tesi)."""
    out = ["", "# FPR of the aggregated detection by level (single τ_k from L1+L2 dev; Wilson with "
           "n = groups is too narrow, Wilson with n = distinct problems is conservative)",
           f"  {'config':<16}{'model':<22}{'level':<6}{'k':>3}{'FPR':>8}{'Wilson groups':>18}"
           f"{'Wilson problems':>18}{'bootstrap':>18}{'rows':>7}{'problems':>9}"]  # fmt: skip
    cols = ["label", "model_id", "level", "k"]
    for r in frame.drop_duplicates(cols).itertuples():
        if r.n_neg_problems == 0:
            out.append(f"  {r.label:<16}{r.model_id:<22}{r.level:<6}{r.k:>3}  no negatives")
            continue
        out.append(f"  {r.label:<16}{r.model_id:<22}{r.level:<6}{r.k:>3}{r.fpr:>8.4f}"
                   f"{f'[{r.fpr_wilson_low:.4f}, {r.fpr_wilson_high:.4f}]':>18}"
                   f"{f'[{r.fpr_wilson_low_problems:.4f}, {r.fpr_wilson_high_problems:.4f}]':>18}"
                   f"{f'[{r.fpr_boot_low:.4f}, {r.fpr_boot_high:.4f}]':>18}"
                   f"{r.n_neg_rows:>7}{r.n_neg_problems:>9}")  # fmt: skip
    return out


# --------------------------------------------------------------------------- main
def registered_configs() -> list[Config]:
    from bench.hpo.posthoc_freeze import load_registry

    reg = load_registry()
    return [Config(m, h, label) for m, labels in reg.methods.items() for label, h in labels.items()]


def load_thresholds(store: Any, configs: Sequence[Config], models: Sequence[str], split: str
                    ) -> dict[tuple[str, str, str], dict[str, Any]]:  # fmt: skip
    """Soglie aggregate scritte da ``calibrate``; sul test devono essere quelle congelate."""
    from bench.hpo.freeze import load_freeze
    from bench.hpo.posthoc_freeze import aggregated_path

    out = {}
    for c in configs:
        record = load_freeze(store, c.method) if split == "test" else None
        if split == "test" and (record is None or record.library_commit is None):
            raise SystemExit(f"{c.method} is not frozen: no aggregated metrics on the test split")
        for model in models:
            rel = aggregated_path(c.method, model, c.config_hash)
            path = store.root / rel
            if not path.is_file():
                raise SystemExit(f"missing aggregated thresholds {rel}: run calibrate on dev")
            if record is not None and record.aggregated_sha256.get(rel) != sha256_file(path):
                raise SystemExit(f"{rel} is not the frozen file (sha256 differs from "
                                 f"freeze/{c.method}.json)")  # fmt: skip
            out[(c.method, model, c.config_hash)] = json.loads(path.read_text(encoding="utf-8"))
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("command", choices=("verify", "calibrate", "evaluate"))
    p.add_argument("--config", type=Config.parse, action="append", default=None,
                   help="method:hash:label (verify only; default: the registry)")  # fmt: skip
    p.add_argument("--models", nargs="+", default=None)
    p.add_argument("--split", choices=("dev", "test"), default="dev")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--out", type=Path, help="evaluate: folder of aggregated_<level>.csv")
    p.add_argument("--report", type=Path, required=True, help="text report (never overwritten)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    # gli override Hydra (con «=») scritti dopo --models o --levels finiscono in quelle liste
    for name in ("models", "levels"):
        values = getattr(args, name) or []
        args.overrides += [v for v in values if "=" in v]
        kept = [v for v in values if "=" not in v]
        setattr(args, name, kept or (None if name == "models" else ["L1", "L2"]))
    return args


def write_new(path: Path, text: str) -> None:
    """Scrive un file nuovo; uno già esistente non si tocca."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv: Sequence[str] | None = None, store: Any = None) -> int:
    args = parse_args(argv)
    if args.report.exists():
        print(f"[ERROR] {args.report} exists: not overwritten", file=sys.stderr)
        return 1
    if args.config and args.command != "verify":
        print("[ERROR] --config is only for verify: calibrate and evaluate use the registry",
              file=sys.stderr)  # fmt: skip
        return 1
    from bench.config.builder import load_experiment
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=selftest", *args.overrides])
    store = store or ArtifactStore(Path(cfg.paths.artifacts))
    configs = args.config or registered_configs()
    models = args.models or list(cfg.models)
    agg = Aggregated(store, cfg.global_seed, cfg.detection.target_fpr, configs, models)
    status = 0
    if args.command == "verify":
        if args.split != "dev":
            print("[ERROR] verify runs on dev only", file=sys.stderr)
            return 1
        status = 0 if agg.verify() else 1
    elif args.command == "calibrate":
        status = _calibrate(agg, store, configs)
    else:
        if args.out is None:
            print("[ERROR] evaluate needs --out", file=sys.stderr)
            return 1
        frame = agg.evaluate(args.levels, args.split,
                             load_thresholds(store, configs, models, args.split))  # fmt: skip
        for level, part in frame.groupby("level") if not frame.empty else []:
            target = args.out / f"aggregated_{level}{'' if args.split == 'test' else '_dev'}.csv"
            write_new(target, part.to_csv(index=False))
            agg.w(f"[OK] {target}")
        agg.out = evaluate_lines(frame) + agg.out
    write_new(args.report, "\n".join(agg.out) + "\n")
    print(f"[{'OK' if status == 0 else 'FAIL'}] {args.report}")
    return status


def _calibrate(agg: Aggregated, store: Any, configs: Iterable[Config]) -> int:
    from bench.hpo.freeze import load_freeze

    frozen = sorted({c.method for c in configs if load_freeze(store, c.method) is not None})
    if frozen:
        print(f"[ERROR] {frozen} already frozen: the aggregated thresholds are fixed",
              file=sys.stderr)  # fmt: skip
        return 1
    commit = None
    try:
        import subprocess

        git = ["git", "--no-optional-locks", "rev-parse", "HEAD"]
        commit = subprocess.run(git, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    written = agg.calibrate_all(commit)
    existing = [str(p) for p, _ in written if p.exists()]
    if existing:
        print(f"[ERROR] aggregated thresholds already exist (never overwritten): {existing}",
              file=sys.stderr)  # fmt: skip
        return 1
    for path, data in written:
        write_new(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        agg.w(f"[OK] {path} sha256 {sha256_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
