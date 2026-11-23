"""Ultimo giro su sviluppo prima del congelamento (P4+P5 ridotte, pipeline v1.3.11 e v1.3.14).

Stesso impianto del giro 4 (``scripts/report_round4.py``) su tutte le configurazioni passate con
``--config metodo:hash[:etichetta]``: i punti di P4 alla libreria b2b6c18 (``mcss``, B, D e le
cinque ablazioni rispetto a B), quelli dei giri precedenti e, come riferimento, le ``mcss``. Per
ogni configurazione, su L1 e L2 dev e per ogni modello:

1. **inserimento identico** (``--pair metodo:nuovo:vecchio[:etichetta]``,
   ``scripts/check_embed_identity.py``): mcss, B e D a b2b6c18 contro le celle già misurate con le
   stesse opzioni. Con ``--same nuovo=vecchio`` (etichette) l'AUROC di ``adversarial_logreg`` e di
   ``adversarial_unixcoder`` della configurazione nuova si legge dalla cella vecchia: la loro
   impercettibilità non si rimisura (decisione dell'utente del 23 novembre 2026). Il report lo
   segna (``borrowed``) e lo vale solo se il controllo dell'identità è riuscito;
2. vincoli, AUROC di ``adversarial_logreg`` e rinomine incoerenti per cella e le **regole 1 e 2
   di v1.3.10** su tutti i punti di ``multimark`` passati, per conferma (``Round4``);
3. **ablazioni affiancate a B** (``--b`` e ``--ablation``), per condizione: senza attacco, i punti
   di S, i due punti di T1.6 e i tre di T2.5; TPR alla soglia calibrata, media semplice delle
   quattro celle (v1.3.4) con la differenza da B, poi per cella; sotto, media su S, ΔPass@1
   massimo, FPR su codice umano alla soglia teorica, AUROC di ``adversarial_logreg`` e
   ``adversarial_unixcoder`` (media delle celle e differenza massima da B), rinomine incoerenti e
   siti medi. Le ablazioni di ``--one-bit`` (V0 e V1) girano a 1 bit per nome
   (``names.bits_per_name = 2`` è ammesso solo con V2), quindi una seconda tabella le affianca
   anche alla base ``--base`` (``3c1bd11758fc``, V2 a 1 bit per nome), da cui differiscono solo
   per la variante dei nomi (richiesta dell'utente del 23 novembre 2026);
4. **T2.5 per punto e per metodo**: per configurazione (prima ``mcss``, poi ``multimark``) e per
   punto di T2.5, TPR alla soglia calibrata e alla soglia teorica ``score >= 2`` per cella e
   media, differenza dal marcato senza attacco, quota di campioni ``APPLIED`` e righe di codice
   aggiunte in media (``attack_detail.added_loc``);
5. **lettura a 8 e 16 bit** per B e D (``--bits``; PIPELINE §6.3 riga «lunghezza del messaggio» e
   §5.11, v1.3.2), senza reinserire: il punteggio principale non dipende dalla lunghezza del
   messaggio ma dal numero K di identificativi fra cui si attribuisce, quindi la lettura a
   ``m`` bit è il punto K = 2^m della curva di attribuzione (decisione dell'utente del 23 novembre
   2026). Dai ``(A, N)`` già salvati, p = 10^(−score) e ``score_K = −log10(1 − (1 − p)^K)``
   (Šidák, ``report_round3.sidak``): TPR con ``score_K >= 2`` (soglia teorica) per K = 1 (il
   registro di un solo messaggio), 2^8, 2^16 e 2^24, per fascia di siti, sui marcati senza
   attacco e sotto T1.4 natural r = 1,0 (``report_round3.CURVE_POINT``). La curva intera è in
   ``P4_capacity_dev.txt``.

Rinomine incoerenti: ``multimark.rename_check`` (``scripts/check_rename_dev.py``) dalla copia
``--multimark`` (commit che deve cominciare con ``--expect-commit``).

Gira in **bench-core**, solo lettura, solo sviluppo::

    python scripts/report_p4.py --config mcss:<hash>:mcss_new ... --config multimark:<hash>:B ...
        --b B --ablation B_V0 ... --bits B D --same B=r4_B ... --pair multimark:<new>:<old>:B ...
        [--base multimark] [--mcss mcss_new] [--multimark DIR] [--expect-commit SHA]
        [--levels L1 L2] [--report FILE] [override ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> Any:
    """Uno script di ``scripts/`` come modulo (lo stesso caricatore di ``report_round4``)."""
    import importlib.util

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r4 = _load("report_round4")
cei = _load("check_embed_identity")
r3, crd, rad = r4.r3, r4.crd, r4.rad
Config = rad.Config
_v, _pct = rad._v, r4._pct

P4_COMMIT = "b2b6c18"
EXTRA_ATTACKS = ("T1.6", "T2.5")
UNIXCODER = "adversarial_unixcoder_auroc_oof"
BITS_K = ((24, 1), (8, 2**8), (16, 2**16), (24, 2**24))  # (bit, K): lettura a m bit = K = 2^m


def imperceptibility_cell(store: Any, c: Any, model: str, level: str, lang: str, split: str,
                          name: str) -> tuple[float, float, float]:  # fmt: skip
    """AUROC out-of-fold di un classificatore con IC (``metrics_imperceptibility``)."""
    from bench.pipeline.stages.imperceptibility import imperceptibility_ref

    ref = imperceptibility_ref(c.method, model, c.config_hash, level, lang, split)
    if not store.exists(ref):
        return math.nan, math.nan, math.nan
    t = store.read_table(ref)
    rows = t[t["name"] == name]
    if not len(rows):
        return math.nan, math.nan, math.nan
    row = rows.iloc[0]

    def num(x: Any) -> float:
        return math.nan if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)

    return num(row["value"]), num(row.get("ci_low")), num(row.get("ci_high"))


def attack_detail_stats(table: Any) -> dict[str, float]:
    """Quota ``APPLIED`` e righe aggiunte in media (``attack_detail.added_loc``, campioni
    ``APPLIED``) di una tabella di campioni attaccati."""
    n = len(table)
    if not n:
        return {"n": 0, "applied": math.nan, "added_loc": math.nan}
    applied = [str(s) == "APPLIED" for s in table["attack_status"]]
    locs = []
    for ok, raw in zip(applied, table["attack_detail"], strict=True):
        if ok and isinstance(raw, str) and raw:
            locs.append(float(json.loads(raw).get("added_loc") or 0))
    return {"n": n, "applied": sum(applied) / n,
            "added_loc": sum(locs) / len(locs) if locs else math.nan}  # fmt: skip


def bits_stats(table: Any, cap: Any, bands: Sequence[Any]) -> dict[str, dict[str, float]]:
    """Per fascia: ``n`` e TPR con ``score_K >= 2`` per i K di ``BITS_K``."""
    import pandas as pd

    t = table.reset_index(drop=True)
    scores = pd.to_numeric(t["score"], errors="coerce").astype(float)
    cols = {
        f"K{k}": [r3._detected(r3.sidak(s, k), st, e, r3.THEORETICAL_THRESHOLD)
                  for s, st, e in zip(scores, t["status"], t["embed_status"], strict=True)]
        for _, k in BITS_K
    }  # fmt: skip
    frame = pd.DataFrame(cols).astype(float)
    band = r3._band_series(t, cap, bands)
    out: dict[str, dict[str, float]] = {}
    for label in r3.band_labels(cap, bands):
        sel = r3._select(band, label)
        n = int(sel.sum())
        out[label] = {"n": n, **{c: float(frame[c][sel].mean()) if n else math.nan
                                 for c in frame.columns}}  # fmt: skip
    return out


def _mean(values: Sequence[float]) -> float:
    vals = list(values)
    return math.nan if not vals or any(math.isnan(x) for x in vals) else sum(vals) / len(vals)


def _dmax(values: Sequence[float]) -> float:
    vals = list(values)
    return math.nan if not vals or any(math.isnan(x) for x in vals) else max(vals)


class P4(r4.Round4):
    """Misure del giro 4 più T1.6, T2.5, ``adversarial_unixcoder`` e la lettura a 8/16 bit."""

    def __init__(self, rep: Any, configs: Sequence[Any], check: Any, language: str,
                 same: dict[str, str] | None = None) -> None:  # fmt: skip
        super().__init__(rep, configs, check, language)
        self.same = dict(same or {})  # nome nuovo -> nome della cella con lo stesso inserimento
        self.extra_points = rad.attack_points(language, EXTRA_ATTACKS)
        self.extra: dict[str, dict[str, dict[str, dict[str, float]]]] = {
            c.name: {} for c in configs
        }  # config -> condizione -> cella -> misure ("all")
        self.unix: dict[str, dict[str, tuple[float, float, float]]] = {c.name: {} for c in configs}
        self.t25: dict[str, dict[str, dict[str, dict[str, float]]]] = {c.name: {} for c in configs}
        self.clean: dict[str, dict[str, dict[str, float]]] = {c.name: {} for c in configs}
        self.lang, self.split = language, "dev"

    @property
    def conditions(self) -> list[str]:
        return [*self.r3.conditions, *(p.label for p in self.extra_points)]

    def collect(self, models: Sequence[str], levels: Sequence[str], lang: str, split: str) -> None:
        super().collect(models, levels, lang, split)
        self.lang, self.split = lang, split
        rep = self.rep
        for model in models:
            for level in levels:
                cell = f"{model} {level}"
                for c in self.configs:
                    self.unix[c.name][cell] = imperceptibility_cell(
                        rep.store, c, model, level, lang, split, UNIXCODER
                    )
                    try:
                        d = rep.cell(c, model, level, lang, split)
                    except Exception:  # cella senza attacco mancante: misure assenti
                        continue
                    self.clean[c.name][cell] = d.clean["all"]
                    for point in [*self.r3.points, *self.extra_points]:
                        stats = rep.attacked(d, point, model, level, lang, split)
                        by_cell = self.extra[c.name].setdefault(point.label, {})
                        if stats is not None:
                            by_cell[cell] = stats["all"]
                        if point.attack_id == "T2.5":
                            self.t25[c.name].setdefault(point.label, {})[cell] = self._t25_detail(
                                c, point, model, level, lang, split
                            )
        for new, old in self.same.items():
            self.data[new].logreg = dict(self.data[old].logreg)
            self.unix[new] = dict(self.unix[old])

    def _t25_detail(self, c: Any, point: Any, model: str, level: str, lang: str,
                    split: str) -> dict[str, float]:  # fmt: skip
        from bench.pipeline.stages.attack import attacked_ref

        ref = attacked_ref(point.attack_id, point.params_hash, c.method, model, c.config_hash,
                           level, lang, split)  # fmt: skip
        if not self.rep.store.exists(ref):
            return {"n": 0, "applied": math.nan, "added_loc": math.nan}
        return attack_detail_stats(self.rep.store.read_table(ref))

    # ------------------------------------------------------------------ misure per condizione
    def cond_cell(self, name: str, cond: str, cell: str, key: str = "tpr_cal") -> float:
        """TPR di una condizione in una cella (``nan`` se manca)."""
        if cond == r3.NO_ATTACK:
            return float(self.clean[name].get(cell, {}).get(key, math.nan))
        stats = self.extra[name].get(cond, {}).get(cell)
        return math.nan if stats is None else float(stats.get(key, math.nan))

    def cond_mean(self, name: str, cond: str) -> float:
        """Media semplice delle celle (v1.3.4; per S coincide con ``Round3``)."""
        return _mean([self.cond_cell(name, cond, cell) for cell in self.cells])

    # ------------------------------------------------------------------ sezioni
    def identity_lines(self, pairs: Sequence[Any], models: Sequence[str],
                       levels: Sequence[str]) -> tuple[list[str], bool]:  # fmt: skip
        if not pairs:
            return [], True
        body, ok = cei.run(self.rep.store, pairs, models, levels, self.lang, self.split)
        head = ["## embedding identity of mcss, B and D at b2b6c18 with the measured cells "
                "(scripts/check_embed_identity.py)"]  # fmt: skip
        return [*head, *body, ""], ok

    def borrowed_lines(self, identical: bool) -> list[str]:
        if not self.same:
            return []
        state = ("identity verified" if identical
                 else "IDENTITY NOT VERIFIED: the borrowed values do not hold")  # fmt: skip
        pairs = "; ".join(f"{n} <- {o}" for n, o in self.same.items())
        return [f"## imperceptibility borrowed from the cells with the same embedding ({state}): "
                f"{pairs}", ""]  # fmt: skip

    def ablation_section(self, b: Any, ablations: Sequence[Any], base: Any = None,
                         one_bit: Sequence[Any] = ()) -> list[str]:  # fmt: skip
        """Ablazioni affiancate a B per condizione (sezione 3 della docstring); quelle a 1 bit per
        nome (``one_bit``: V0 e V1) anche alla base ``base`` (1 bit per nome, V2)."""
        if not ablations:
            return []
        out = self._compare(
            b, ablations,
            f"## ablations next to {b.name}, one factor at a time (§6.3): TPR at the calibrated "
            "threshold, simple mean of the 4 cells (Δ from B)",
        )  # fmt: skip
        if base is not None and one_bit:
            labels = ", ".join(c.label for c in one_bit)
            out += self._compare(
                base, one_bit,
                f"## {labels} next to the base {base.name}: they run at 1 bit per name "
                "(names.bits_per_name = 2 is allowed only with V2), so besides B (2 bits per "
                "name, V2) they are compared with the base, which differs from them only in the "
                "names variant (V2, 1 bit per name); TPR at the calibrated threshold, simple mean "
                "of the 4 cells (Δ from the base)",
            )  # fmt: skip
        return out

    def _compare(self, b: Any, others: Sequence[Any], header: str) -> list[str]:
        """Tabella di ``others`` affiancati al riferimento ``b`` (Δ da ``b``)."""
        cols = [b, *others]
        width = max(18, *(len(c.label) + 2 for c in cols))
        tag = b.label
        out = [header, "  " + f"{'condition':<30}"
               + "".join(f"{c.label:>{width}}" for c in cols)]  # fmt: skip
        for cond in self.conditions:
            ref = self.cond_mean(b.name, cond)
            vals = []
            for c in cols:
                x = self.cond_mean(c.name, cond)
                vals.append(f"{_v(x)}" if c is b else f"{_v(x)} ({rad._d(x, ref)})")
            label = "none" if cond == r3.NO_ATTACK else cond
            out.append("  " + f"{label:<30}" + "".join(f"{v:>{width}}" for v in vals))
        out += ["", "  per cell (TPR at the calibrated threshold):"]
        for cond in self.conditions:
            label = "none" if cond == r3.NO_ATTACK else cond
            for cell in self.cells:
                parts = " ".join(
                    f"{c.label} {_v(self.cond_cell(c.name, cond, cell))}" for c in cols
                )
                out.append(f"    {label} | {cell}: {parts}")
        out += ["", "  " + f"{'measure':<30}" + "".join(f"{c.label:>{width}}" for c in cols)]
        rows: list[tuple[str, list[str]]] = []
        rows.append(("mean_S", [_v(self.data[c.name].mean_s) for c in cols]))
        rows.append(("max ΔPass@1 pp", [_v(self.data[c.name].dp_max) for c in cols]))
        levels = sorted({lv for d in self.data.values() for lv in d.fprs})
        for lv in levels:
            rows.append((f"FPR_2 {lv}", [_v(self.data[c.name].fprs.get(lv, math.nan))
                                         for c in cols]))  # fmt: skip
        for title, getter in (("logreg AUROC", lambda n: self.data[n].logreg),
                              ("unixcoder AUROC", lambda n: self.unix[n])):  # fmt: skip
            means, deltas = [], []
            for c in cols:
                vals = getter(c.name)
                means.append(_v(_mean([vals.get(cell, (math.nan,))[0] for cell in self.cells])))
                ref = getter(b.name)
                d = _dmax([vals.get(cell, (math.nan,))[0] - ref.get(cell, (math.nan,))[0]
                           for cell in self.cells])  # fmt: skip
                deltas.append("-" if c is b else rad._d(d, 0.0))
            rows.append((f"{title} (mean)", means))
            rows.append((f"{title} max Δ from {tag}", deltas))
        rows.append(("inconsistent renaming (max)", [_pct(self.data[c.name].rename_max)
                                                     for c in cols]))  # fmt: skip
        rows.append(("renames/sample", [_v(self.data[c.name].renames) for c in cols]))
        for field in r4.SITE_FIELDS:
            rows.append((f"{field} (mean)", [
                _v(_mean([self.data[c.name].sites.get(cell, {}).get(field, math.nan)
                          for cell in self.cells])) for c in cols]))  # fmt: skip
        for title, vals in rows:
            out.append("  " + f"{title:<30}" + "".join(f"{v:>{width}}" for v in vals))
        borrowed = [c.label for c in cols if c.name in self.same]
        if borrowed:
            out.append(f"  (imperceptibility of {', '.join(borrowed)} read from the cells with the "
                       "same embedding)")  # fmt: skip
        return [*out, ""]

    def t25_section(self, configs: Sequence[Any]) -> list[str]:
        """T2.5 per punto e per metodo (sezione 4 della docstring)."""
        points = [p for p in self.extra_points if p.attack_id == "T2.5"]
        if not points:
            return []
        ordered = sorted(configs, key=lambda c: (c.method != "mcss", 0))
        out = ["## T2.5 dilution per point and per method: TPR at the calibrated threshold "
               "(tpr_cal) and at score >= 2 (tpr_2), Δ from the marked samples without attack, "
               "share APPLIED and mean added lines of code",
               "  " + f"{'method':<10}{'config':<34}{'point':<30}{'cell':<30}{'tpr_cal':>9}"
               f"{'Δ':>8}{'tpr_2':>9}{'Δ':>8}{'applied':>9}{'add_loc':>9}"]  # fmt: skip
        for p in points:
            for c in ordered:
                cal, two = [], []
                for cell in self.cells:
                    att = self.extra[c.name].get(p.label, {}).get(cell)
                    clean = self.clean[c.name].get(cell, {})
                    det = self.t25[c.name].get(p.label, {}).get(cell, {})
                    if att is None:
                        out.append(f"  {c.method:<10}{c.name:<34}{p.label:<30}{cell:<30}"
                                   "no detection of the attacked samples")  # fmt: skip
                        cal.append(math.nan)
                        two.append(math.nan)
                        continue
                    cal.append(float(att["tpr_cal"]))
                    two.append(float(att["tpr_2"]))
                    out.append(
                        f"  {c.method:<10}{c.name:<34}{p.label:<30}{cell:<30}"
                        f"{_v(att['tpr_cal']):>9}"
                        f"{rad._d(att['tpr_cal'], clean.get('tpr_cal', math.nan)):>8}"
                        f"{_v(att['tpr_2']):>9}"
                        f"{rad._d(att['tpr_2'], clean.get('tpr_2', math.nan)):>8}"
                        f"{_v(det.get('applied', math.nan)):>9}"
                        f"{_v(det.get('added_loc', math.nan)):>9}"
                    )
                clean_cal = _mean([self.clean[c.name].get(x, {}).get("tpr_cal", math.nan)
                                   for x in self.cells])  # fmt: skip
                out.append(f"  {c.method:<10}{c.name:<34}{p.label:<30}{'mean of the cells':<30}"
                           f"{_v(_mean(cal)):>9}{rad._d(_mean(cal), clean_cal):>8}"
                           f"{_v(_mean(two)):>9}")  # fmt: skip
            out.append("")
        return out

    def bits_section(self, configs: Sequence[Any]) -> list[str]:
        """Lettura a 8 e 16 bit (sezione 5 della docstring)."""
        if not configs:
            return []
        rep = self.rep
        curve_point = next((q for q in self.r3.points if q.attack_id == r3.CURVE_POINT[0]
                            and r3.CURVE_POINT[1].items() <= q.params.items()), None)  # fmt: skip
        heads = "".join(f"{f'K=1 ({b}b)' if k == 1 else f'2^{b} ({b}b)':>13}" for b, k in BITS_K)
        out = ["## message length 8 and 16 bits read without embedding again (v1.3.2, §5.11): "
               "the reading at m bits is the point K = 2^m of the attribution curve; TPR with "
               f"score_K >= {r3.THEORETICAL_THRESHOLD:g} (Šidák from the saved (A, N))",
               f"  {'config':<34}{'cell':<26}{'condition':<26}{'band':<16}{'n':>6}"
               f"{heads}"]  # fmt: skip
        for c in configs:
            for cell in self.cells:
                model, level = cell.split(" ", 1)
                for point in (None, curve_point):
                    what = "no attack" if point is None else point.label
                    try:
                        table = rep.table(rep.info(c, model, level, self.lang, self.split, point))
                    except Exception as exc:  # rilevazione mancante: si segnala
                        out.append(f"  {c.name:<34}{cell:<26}{what:<26}no detection "
                                   f"({type(exc).__name__})")  # fmt: skip
                        continue
                    for label, row in bits_stats(table, rep.cap, rep.bands).items():
                        if row["n"] == 0:
                            continue
                        vals = "".join(f"{_v(row[f'K{k}']):>13}" for _, k in BITS_K)
                        out.append(f"  {c.name:<34}{cell:<26}{what:<26}{label:<16}"
                                   f"{int(row['n']):>6}{vals}")  # fmt: skip
        return [*out, ""]


def _labels(by_label: dict[str, Any], labels: Sequence[str], flag: str) -> list[Any]:
    missing = [x for x in labels if x not in by_label]
    if missing:
        raise ValueError(f"{flag} {missing}: not among the labels {list(by_label)}")
    return [by_label[x] for x in labels]


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--base", default="multimark", help="label of the base for rule 1")
    p.add_argument("--mcss", default="mcss_new_3c47a1f", help="label of mcss for rule 2")
    p.add_argument("--b", default="B", help="label of B (the ablations are next to it)")
    p.add_argument("--ablation", nargs="*", default=[], help="labels of the ablations of B")
    p.add_argument("--one-bit", nargs="*", default=[],
                   help="ablations at 1 bit per name (V0, V1): also next to --base")  # fmt: skip
    p.add_argument("--bits", nargs="*", default=[], help="labels read at 8 and 16 bits")
    p.add_argument("--t25", nargs="*", help="labels for the T2.5 section (default: all new)")
    p.add_argument("--same", nargs="*", default=[],
                   help="NEW=OLD labels: imperceptibility of NEW read from OLD")  # fmt: skip
    p.add_argument("--pair", type=cei.Pair.parse, action="append", default=[],
                   help="method:new:old[:label] for the embedding identity check")  # fmt: skip
    p.add_argument("--multimark", type=Path, default=crd.DEFAULT_MULTIMARK,
                   help="copy of the MultiMark library with multimark/rename_check.py")  # fmt: skip
    p.add_argument("--expect-commit", default=P4_COMMIT,
                   help="required prefix of the commit of the copy ('' = any)")  # fmt: skip
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--threshold-levels", nargs="+", help="calibration levels (default: --levels)")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    return p.parse_args(argv)


def resolve_labels(args: argparse.Namespace) -> dict[str, Any]:
    """Configurazioni delle etichette di ``--base``, ``--mcss``, ``--b``, ``--ablation``,
    ``--bits``, ``--t25`` (default: le coppie, B, le ablazioni e i punti letti a 8/16 bit) e
    ``--same``.

    Raises:
        ValueError: etichetta sconosciuta o ``--same`` malformato.
    """
    by_label = {c.label: c for c in args.config}
    base, mcss, b = _labels(by_label, [args.base, args.mcss, args.b], "--base/--mcss/--b")
    wanted_t25 = args.t25
    if wanted_t25 is None:
        default_t25 = [x.label for x in args.pair] + [args.b, *args.ablation, *args.bits]
        wanted_t25 = list(dict.fromkeys(x for x in default_t25 if x in by_label))
    same: dict[str, str] = {}
    for item in args.same:
        new, sep, old = item.partition("=")
        if not sep:
            raise ValueError(f"--same {item!r}: expected NEW=OLD")
        n_cfg, o_cfg = _labels(by_label, [new, old], "--same")
        same[n_cfg.name] = o_cfg.name
    return {"base": base, "mcss": mcss, "b": b,
            "ablations": _labels(by_label, args.ablation, "--ablation"),
            "one_bit": _labels(by_label, args.one_bit, "--one-bit"),
            "bits": _labels(by_label, args.bits, "--bits"),
            "t25": _labels(by_label, wanted_t25, "--t25"), "same": same}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.split == "test":
        print("[ERROR] development round only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    try:
        sel = resolve_labels(args)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    base, mcss, b, ablations = sel["base"], sel["mcss"], sel["b"], sel["ablations"]
    bits, t25, same = sel["bits"], sel["t25"], sel["same"]
    try:
        check, commit = crd.load_checker(args.multimark, args.expect_commit)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    t_levels = args.threshold_levels or args.levels
    rep = rad.Reporter(cfg, args.config, t_levels)
    report = P4(rep, args.config, check, args.language, same)
    models = args.models or list(cfg.models)
    report.collect(models, args.levels, args.language, args.split)
    identity, identical = report.identity_lines(args.pair, models, args.levels)
    points = report.r3.points
    n_mm = sum(1 for c in args.config if c.method == "multimark")
    lines = [
        "# P4+P5 reduced on development, last round before the freeze (scripts/report_p4.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# base {base.name}, mcss {mcss.name}; ranked: the {n_mm} multimark configurations; "
        f"B {b.name}; ablations {', '.join(a.label for a in ablations) or '-'}",
        f"# thresholds of levels {' '.join(t_levels)} and theoretical score >= "
        f"{r3.THEORETICAL_THRESHOLD:g}; split {args.split}, language {args.language}",
        f"# S = {r3.NO_ATTACK}; " + "; ".join(f"{q.label} [{q.params_hash}]" for q in points),
        "# other conditions: "
        + "; ".join(f"{q.label} [{q.params_hash}]" for q in report.extra_points),
        f"# renaming check: multimark.rename_check from {args.multimark} at commit {commit}",
        "",
        *identity,
        *report.borrowed_lines(identical),
        *report.lines(base, mcss),
        *report.ranking_section(base, mcss),
        *report.ablation_section(b, ablations, base, sel["one_bit"]),
        *report.t25_section(t25),
        *report.bits_section(bits),
        *report.extras_section(),
    ]
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
