"""Giro di capacità su sviluppo (pipeline di multimark v1.3.4): criterio di scelta e curva in K.

Per ogni configurazione (``--config metodo:hash[:etichetta]``: ``mcss``, ``multimark`` base e i
punti di sviluppo di ``configs/hpo/multimark.yaml``), su L1 e L2 dev e per ogni modello, lo
script legge gli artefatti delle fasi ``detect``, ``calibrate`` e ``metrics`` e stampa:

1. **TPR per condizione di S e per fascia di siti.** S = {senza attacco, T1.4 natural r = 1,0,
   T1.4 random r = 0,5, T1.5 k = 5, T2.1 b = all, T2.4 p = 0,5, T4.1 rename = true} (``S``). Le
   griglie degli attacchi si lanciano intere (la pipeline non sceglie singoli punti); qui si
   usano solo i punti di S. Per fascia di ``n_sites`` (fasce di ``scripts/report_capacity.py``;
   per gli attaccati vale ``n_sites`` del genitore): ``tpr_cal`` con la soglia calibrata della
   configurazione (``thresholds/.../L1L2/<hash>``) e ``tpr_2`` con la soglia teorica fissa
   ``score >= 2`` (p ≤ 0,01), con la decisione della fase ``metrics``
   (``bench.metrics.calibration.detected``).
2. **Criterio di scelta (v1.3.3, aggregazione di v1.3.4).** Per ogni configurazione la media su
   S del TPR alla soglia calibrata; il TPR di ogni condizione è la **media semplice delle quattro
   celle** modello × livello (L1 e L2 pesano uguale, qualunque sia il numero di campioni).
   Vincoli:
   - ``ΔPass@1`` (``delta_pass_at_1_pp`` della fase ``metrics``, baseline − marcato, in punti
     percentuali) ≤ 3 su ogni cella (modello, livello) senza attacco;
   - FPR su codice umano (``neg_human``) alla soglia teorica ≤ 1% su ogni livello (i due modelli
     insieme; si stampano anche i valori per modello);
   - impercettibilità (``adversarial_logreg``): si misura in P5, qui ``n/d``.
   **Rinomine**: numero medio di nomi rinominati per campione, da ``n_renamed`` negli ``extra``
   dell'inserimento (``_runs/watermark/<metodo>/<modello>/<hash>/<livello>_<ling>_<split>/
   results.jsonl``; inserimento ``FAILED`` o senza il campo = 0: il codice resta quello della
   baseline), media delle quattro celle come per il TPR. A parità entro 0,01 dalla prima
   configurazione che rispetta i vincoli vince quella con meno rinomine: la classifica mette
   prima il gruppo in parità ordinato per rinomine, poi le altre.
3. **Curva di attribuzione in K (§5.11)** per le configurazioni di ``--curve`` (default:
   ``mcss``, ``multimark``, ``C``), sui marcati senza attacco e sotto T1.4 natural r = 1,0
   (``CURVE_POINT``, v1.3.4): con p = 10^(−score) (il punteggio
   principale è il p-value esatto P[Bin(N, 1/2) ≥ A] di ``(A, N)``), ``score_K = −log10(1 − (1 −
   p)^K)`` (Šidák) e TPR con ``score_K >= 2`` per K ∈ {1, 16, 256, 4.096, 65.536, 2^20, 2^24},
   per fascia di siti. Controllo con le esche vere: per K = 16 e K = 1.024 la frazione dei
   campioni in cui il candidato migliore del registro (``best_nreg<K>``) è il messaggio atteso e
   la correzione di Šidák del suo punteggio (``score_nreg<K>``) è ≥ 2; lo scarto dalla curva si
   stampa e si segna con ``!`` se supera 0,05.

Gira in **bench-core**, solo lettura degli artefatti::

    python scripts/report_round3.py --config mcss:<hash>:mcss \\
        --config multimark:<hash>:multimark --config multimark:<hash>:C ... \\
        [--curve mcss multimark C] [--levels L1 L2] [--report FILE] [override Hydra ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
THEORETICAL_THRESHOLD = 2.0
DELTA_PASS_MAX_PP = 3.0
FPR_MAX = 0.01
TIE = 0.01
CONTROL_GAP = 0.05
# Attacchi lanciati (griglie intere) e punti dell'insieme S di v1.3.3, nell'ordine del report.
ATTACKS = ("T1.4", "T1.5", "T2.1", "T2.4", "T4.1")
S: tuple[tuple[str, dict[str, Any]], ...] = (
    ("T1.4", {"ratio": 1.0, "name_kind": "natural"}),
    ("T1.4", {"ratio": 0.5, "name_kind": "random"}),
    ("T1.5", {"k": 5}),
    ("T2.1", {"b": "all"}),
    ("T2.4", {"p": 0.5}),
    ("T4.1", {"rename": True}),
)
NO_ATTACK = "no attack"
K_VALUES = (1, 16, 256, 4096, 65536, 2**20, 2**24)
CONTROL_K = (16, 1024)
DEFAULT_CURVE = ("mcss", "multimark", "C")
# Condizione attaccata della curva in K (v1.3.4), oltre ai marcati senza attacco.
CURVE_POINT = ("T1.4", {"ratio": 1.0, "name_kind": "natural"})
EMBEDDED = ("OK", "PARTIAL")


def _load(name: str) -> Any:
    """Uno script di ``scripts/`` come modulo (registrato in ``sys.modules`` per le dataclass)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rad = _load("report_attacks_dev")
Config = rad.Config
_v = rad._v


def s_points(language: str) -> list[Any]:
    """I punti di S nelle griglie degli attacchi, nell'ordine di ``S``."""
    points = rad.attack_points(language, ATTACKS)
    out = []
    for attack_id, match in S:
        found = [
            p for p in points if p.attack_id == attack_id and match.items() <= p.params.items()
        ]
        if len(found) != 1:
            raise ValueError(f"point {attack_id} {match} of S: {len(found)} points in the grid")
        out.append(found[0])
    return out


def sidak(score: float, k: int) -> float:
    """``−log10(1 − (1 − p)^K)`` con p = 10^(−score) (§5.11), stabile per p piccolo."""
    if math.isnan(score):
        return math.nan
    if k == 1:
        return score
    if score > 300:  # noqa: PLR2004  p sotto il minimo dei float: primo ordine, p·K
        return score - math.log10(k)
    p = 10.0 ** (-score)
    tail = -math.expm1(k * math.log1p(-p)) if p < 1 else 1.0
    return -math.log10(tail) if tail > 0 else math.inf


def _extras(table: pd.DataFrame) -> list[dict[str, Any]]:
    return [json.loads(r) if isinstance(r, str) and r else {} for r in table["extra_json"]]


def _detected(score: float, status: Any, embed: Any, threshold: float) -> bool:
    from bench.metrics.calibration import detected

    return bool(detected(None if math.isnan(score) else score, str(status),
                         None if pd.isna(embed) else str(embed), threshold))  # fmt: skip


def _band_series(table: pd.DataFrame, cap: Any, bands: Sequence[Any]) -> pd.Series:
    return pd.Series([cap.band_of(v, bands) for v in table["n_sites"]], dtype=object)


def band_labels(cap: Any, bands: Sequence[Any]) -> list[str]:
    return ["all", *(cap.band_label(b) for b in bands), "n_sites=0/none"]


def _select(band: pd.Series, label: str) -> pd.Series:
    if label == "all":
        return pd.Series(True, index=band.index)
    if label == "n_sites=0/none":
        return band.isna()
    return band == label


def curve_stats(table: pd.DataFrame, cap: Any, bands: Sequence[Any]) -> dict[str, dict[str, float]]:
    """Per fascia: ``n``, TPR con ``score_K >= 2`` per ogni K e controllo con le esche
    (``dec<K>``) per K in ``CONTROL_K``."""
    t = table.reset_index(drop=True)
    scores = pd.to_numeric(t["score"], errors="coerce").astype(float)
    extras = _extras(t)
    cols: dict[str, list[bool]] = {}
    for k in K_VALUES:
        cols[f"K{k}"] = [
            _detected(sidak(s, k), st, e, THEORETICAL_THRESHOLD)
            for s, st, e in zip(scores, t["status"], t["embed_status"], strict=True)
        ]
    for k in CONTROL_K:
        name = f"K{k}"
        if name not in cols:
            cols[name] = [
                _detected(sidak(s, k), st, e, THEORETICAL_THRESHOLD)
                for s, st, e in zip(scores, t["status"], t["embed_status"], strict=True)
            ]
        dec = []
        for x, st, e, exp in zip(extras, t["status"], t["embed_status"], t["expected"],
                                 strict=True):  # fmt: skip
            raw = x.get(f"score_nreg{k}")
            s = math.nan if raw is None else float(raw)
            dec.append(x.get(f"best_nreg{k}") == exp
                       and _detected(sidak(s, k), st, e, THEORETICAL_THRESHOLD))  # fmt: skip
        cols[f"dec{k}"] = dec
    frame = pd.DataFrame(cols).astype(float)
    band = _band_series(t, cap, bands)
    out: dict[str, dict[str, float]] = {}
    for label in band_labels(cap, bands):
        sel = _select(band, label)
        n = int(sel.sum())
        row: dict[str, float] = {"n": n}
        for c in frame.columns:
            row[c] = float(frame[c][sel].mean()) if n else math.nan
        out[label] = row
    return out


@dataclass
class CellMean:
    """Media semplice di un valore sulle celle (modello, livello): ogni cella pesa uguale."""

    values: list[float] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def add(self, value: float, where: str) -> None:
        if math.isnan(value):
            self.missing.append(where)
        else:
            self.values.append(value)

    @property
    def rate(self) -> float:
        if not self.values or self.missing:
            return math.nan
        return sum(self.values) / len(self.values)


def renames_per_sample(run_dir: Path) -> tuple[float, int]:
    """(media di ``n_renamed`` per campione, campioni) dai ``results.jsonl`` dell'inserimento;
    inserimento non riuscito o campo assente = 0. ``(nan, 0)`` se il file manca."""
    path = run_dir / "results.jsonl"
    if not path.is_file():
        return math.nan, 0
    values = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            extra = row.get("extra") or {}
            ok = row.get("status") in EMBEDDED
            values.append(float(extra.get("n_renamed") or 0) if ok else 0.0)
    return (sum(values) / len(values), len(values)) if values else (math.nan, 0)


def _rate(table: pd.DataFrame, threshold: float) -> tuple[int, int]:
    scores = pd.to_numeric(table["score"], errors="coerce").astype(float)
    embed = table["embed_status"] if "embed_status" in table else [None] * len(table)
    hits = sum(_detected(s, st, e, threshold)
               for s, st, e in zip(scores, table["status"], embed, strict=True))  # fmt: skip
    return int(hits), len(table)


class Round3:
    """Legge gli artefatti (``report_attacks_dev.Reporter``) e accumula il criterio."""

    def __init__(self, rep: Any, configs: Sequence[Any], points: Sequence[Any]) -> None:
        self.rep = rep
        self.configs = list(configs)
        self.points = list(points)
        self.conditions = [NO_ATTACK, *(p.label for p in points)]
        self.pooled = {c.name: {cond: CellMean() for cond in self.conditions} for c in configs}
        self.renames = {c.name: CellMean() for c in configs}
        self.renames_cells: dict[str, dict[str, tuple[float, int]]] = {c.name: {} for c in configs}
        self.delta_pass: dict[str, dict[str, float]] = {c.name: {} for c in configs}
        self.fpr: dict[str, dict[str, list[tuple[str, int, int]]]] = {c.name: {} for c in configs}

    def metric(self, c: Any, model: str, level: str, lang: str, split: str, name: str) -> float:
        from bench.pipeline.stages.metrics import metrics_ref

        ref = metrics_ref(c.method, model, c.config_hash, level, lang, split, self.rep.t_levels)
        if not self.rep.store.exists(ref):
            return math.nan
        t = self.rep.store.read_table(ref)
        rows = t[t["name"] == name]
        return float(rows["value"].iloc[0]) if len(rows) else math.nan

    def fpr_human(self, c: Any, model: str, level: str, lang: str, split: str) -> tuple[int, int]:
        from bench.pipeline.stages.detect import detection_ref

        ref = detection_ref(c.method, model, c.config_hash, "neg_human", level, lang, split)
        if not self.rep.store.exists(ref):
            return 0, 0
        return _rate(self.rep.store.read_table(ref), THEORETICAL_THRESHOLD)

    def run_dir(self, c: Any, model: str, level: str, lang: str, split: str) -> Path:
        """Cartella del worker dell'inserimento (``WatermarkStage``)."""
        root = Path(self.rep.cfg.paths.artifacts)
        cell = f"{level}_{lang}_{split}"
        out: Path = root / "_runs" / "watermark" / str(c.method) / model / str(c.config_hash) / cell
        return out

    def cell_block(self, model: str, level: str, lang: str, split: str) -> list[str]:
        """Tabelle di S per una cella (modello, livello); accumula criterio e vincoli."""
        rep = self.rep
        cells = []
        lines: list[str] = []
        for c in self.configs:
            try:
                cells.append(rep.cell(c, model, level, lang, split))
            except Exception as exc:  # cella mancante: si segnala e si va avanti
                lines.append(f"  {c.name}: missing clean cell ({type(exc).__name__}: {exc})")
                for cond in self.conditions:
                    self.pooled[c.name][cond].missing.append(f"{model} {level}")
                cells.append(None)
        taus = ", ".join(f"{d.config.name} {d.tau:.6g}" for d in cells if d)
        head = [f"## {model} {level}: calibrated thresholds {taus}", *lines, ""]
        for c in self.configs:
            dp = self.metric(c, model, level, lang, split, "delta_pass_at_1_pp")
            self.delta_pass[c.name][f"{model} {level}"] = dp
            k, n = self.fpr_human(c, model, level, lang, split)
            self.fpr[c.name].setdefault(level, []).append((model, k, n))
            mean, n_samples = renames_per_sample(self.run_dir(c, model, level, lang, split))
            self.renames[c.name].add(mean, f"{model} {level}")
            self.renames_cells[c.name][f"{model} {level}"] = (mean, n_samples)
        out = head
        labels = band_labels(rep.cap, rep.bands)
        for cond, point in [(NO_ATTACK, None), *((p.label, p) for p in self.points)]:
            tag = "" if point is None else f"  [params {point.params_hash}]"
            out.append(f"### {cond}{tag}  {model} {level} {lang} {split}")
            out.append(f"  {'config':<34}{'band':<16}{'n':>6}{'tpr_cal':>10}{'Δ':>8}"
                       f"{'tpr_2':>10}{'Δ':>8}")  # fmt: skip
            for c, d in zip(self.configs, cells, strict=True):
                if d is None:
                    continue
                stats = d.clean if point is None else rep.attacked(d, point, model, level, lang,
                                                                   split)  # fmt: skip
                if stats is None:
                    out.append(f"  {c.name:<34}no detection of the attacked samples")
                    self.pooled[c.name][cond].missing.append(f"{model} {level}")
                    continue
                self.pooled[c.name][cond].add(stats["all"]["tpr_cal"], f"{model} {level}")
                for label in labels:
                    row = stats[label]
                    if row["n"] == 0:
                        continue
                    ref = d.clean[label]
                    out.append(
                        f"  {c.name:<34}{label:<16}{int(row['n']):>6}"
                        f"{_v(row['tpr_cal']):>10}{rad._d(row['tpr_cal'], ref['tpr_cal']):>8}"
                        f"{_v(row['tpr_2']):>10}{rad._d(row['tpr_2'], ref['tpr_2']):>8}"
                    )
            out.append("")
        return out

    def criterion_lines(self) -> list[str]:
        """Media su S, vincoli e classifica (sezione 2 della docstring)."""
        rows = []
        for c in self.configs:
            rates = [self.pooled[c.name][cond].rate for cond in self.conditions]
            mean = math.nan if any(math.isnan(r) for r in rates) else sum(rates) / len(rates)
            dps = list(self.delta_pass[c.name].values())
            dp_max = math.nan if not dps or any(math.isnan(x) for x in dps) else max(dps)
            fprs = {}
            for level, vals in sorted(self.fpr[c.name].items()):
                k, n = sum(v[1] for v in vals), sum(v[2] for v in vals)
                fprs[level] = k / n if n else math.nan
            ok_dp = not math.isnan(dp_max) and dp_max <= DELTA_PASS_MAX_PP
            ok_fpr = bool(fprs) and all(not math.isnan(f) and f <= FPR_MAX for f in fprs.values())
            rows.append((c, mean, rates, dp_max, fprs, ok_dp and ok_fpr, self.renames[c.name].rate))
        levels = sorted({lv for _, _, _, _, f, _, _ in rows for lv in f})
        out = [
            "## selection criterion (v1.3.3): mean over S of the TPR at the calibrated threshold",
            "# TPR of each condition = simple mean of the 4 cells model x level (v1.3.4); "
            "constraints: "
            f"ΔPass@1 <= {DELTA_PASS_MAX_PP:g} pp on every cell without attack, FPR on human code "
            f"at score >= {THEORETICAL_THRESHOLD:g} <= {FPR_MAX:.0%} on every level, "
            "imperceptibility in P5 (n/d)",
            "  "
            + f"{'config':<34}{'mean_S':>8}"
            + "".join(f"{('none' if cond == NO_ATTACK else cond):>26}" for cond in self.conditions),
        ]
        for c, mean, rates, *_ in rows:
            out.append(f"  {c.name:<34}{_v(mean):>8}" + "".join(f"{_v(r):>26}" for r in rates))
        out += ["", "  " + f"{'config':<34}{'max ΔPass@1 pp':>16}"
                + "".join(f"{'FPR_2 ' + lv:>12}" for lv in levels)
                + f"{'imperc.':>9}{'constraints':>13}{'renames/sample':>16}"]  # fmt: skip
        for c, _, _, dp_max, fprs, ok, ren in rows:
            fpr_txt = "".join(f"{_v(fprs.get(lv, math.nan)):>12}" for lv in levels)
            out.append(f"  {c.name:<34}{_v(dp_max):>16}{fpr_txt}{'n/d':>9}"
                       f"{('ok' if ok else 'FAIL'):>13}{_v(ren):>16}")  # fmt: skip
        out.append("  per cell:")
        for c, *_ in rows:
            dp_txt = " ".join(f"{cell} {_v(v)}" for cell, v in self.delta_pass[c.name].items())
            fpr_cells = " ".join(
                f"{lv} {m} {k}/{n}"
                for lv, vals in sorted(self.fpr[c.name].items())
                for m, k, n in vals
            )
            ren_txt = " ".join(
                f"{cell} {_v(m)} (n={n})" for cell, (m, n) in self.renames_cells[c.name].items()
            )
            out.append(f"    {c.name}: ΔPass@1 pp {dp_txt}; FPR_2 {fpr_cells}; renames {ren_txt}")
        out += ["", "  ranking (constraints met, imperceptibility pending until P5; within "
                f"{TIE} of the best mean_S, fewer renames first):"]  # fmt: skip
        for i, (c, mean, ren, tied) in enumerate(ranking(rows), 1):
            note = "  [tie group: fewer renames wins]" if tied else ""
            out.append(f"    {i}. {c.name}  mean_S {_v(mean)}  renames/sample {_v(ren)}{note}")
        if not ranking(rows):
            out.append("    none")
        for c, *_ in rows:
            missing = sorted({m for p in self.pooled[c.name].values() for m in p.missing})
            if missing:
                out.append(f"  {c.name}: incomplete ({', '.join(missing)})")
        return [*out, ""]


def ranking(rows: Sequence[tuple[Any, ...]]) -> list[tuple[Any, float, float, bool]]:
    """(config, mean_S, rinomine, in parità) delle configurazioni che rispettano i vincoli: prima
    il gruppo entro ``TIE`` dalla media migliore, ordinato per rinomine (poi per media), poi le
    altre per media decrescente."""
    valid = sorted((r for r in rows if r[5] and not math.isnan(r[1])), key=lambda r: -r[1])
    if not valid:
        return []
    best = valid[0][1]
    tied = [r for r in valid if best - r[1] <= TIE]
    rest = [r for r in valid if best - r[1] > TIE]
    tied.sort(key=lambda r: (math.isnan(r[6]), r[6] if not math.isnan(r[6]) else 0.0, -r[1]))
    multi = len(tied) > 1
    return [(r[0], r[1], r[6], multi) for r in tied] + [(r[0], r[1], r[6], False) for r in rest]


def curve_lines(rep: Any, cells: Sequence[Any], model: str, level: str, lang: str,
                split: str, point: Any = None) -> list[str]:  # fmt: skip
    """Curva in K (§5.11) e controllo con le esche, per fascia di siti; con ``point`` sui
    campioni attaccati di quel punto (``n_sites`` del genitore)."""
    ks = "".join(f"{f'2^{int(math.log2(k))}' if k > 1 else 'K=1':>9}" for k in K_VALUES)
    ctrl = "".join(f"{f'K{k}':>8}{f'dec{k}':>8}{'gap':>8}" for k in CONTROL_K)
    what = "marked without attack" if point is None else f"{point.label} [{point.params_hash}]"
    out = [f"### attribution vs K (§5.11), {what}  {model} {level} {lang} {split}",
           f"  {'config':<34}{'band':<16}{'n':>6}{ks}   control:{ctrl}"]  # fmt: skip
    for d in cells:
        try:
            table = rep.table(rep.info(d.config, model, level, lang, split, point))
        except Exception as exc:  # rilevazione mancante: si segnala
            out.append(f"  {d.config.name:<34}no detection ({type(exc).__name__})")
            continue
        stats = curve_stats(table, rep.cap, rep.bands)
        for label, row in stats.items():
            if row["n"] == 0:
                continue
            vals = "".join(f"{_v(row[f'K{k}']):>9}" for k in K_VALUES)
            parts = []
            for k in CONTROL_K:
                a, b = row[f"K{k}"], row[f"dec{k}"]
                gap = b - a if not (math.isnan(a) or math.isnan(b)) else math.nan
                flag = "!" if not math.isnan(gap) and abs(gap) > CONTROL_GAP else ""
                gap_txt = "-" if math.isnan(gap) else f"{gap:+.3f}{flag}"
                parts.append(f"{_v(a):>8}{_v(b):>8}{gap_txt:>8}")
            out.append(f"  {d.config.name:<34}{label:<16}{int(row['n']):>6}{vals}"
                       f"           {''.join(parts)}")  # fmt: skip
    return [*out, ""]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--curve", nargs="+", default=list(DEFAULT_CURVE),
                   help="labels of the configurations for the curve in K")  # fmt: skip
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--threshold-levels", nargs="+", help="calibration levels (default: --levels)")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development round only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1
    labels = [c.label for c in args.config]
    unknown = [x for x in args.curve if x not in labels]
    if unknown:
        print(f"[ERROR] --curve {unknown}: not among the labels {labels}", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    t_levels = args.threshold_levels or args.levels
    rep = rad.Reporter(cfg, args.config, t_levels)
    points = s_points(args.language)
    r3 = Round3(rep, args.config, points)
    curve_configs = [c for c in args.config if c.label in args.curve]
    curve_point = next(q for q in points if q.attack_id == CURVE_POINT[0]
                       and CURVE_POINT[1].items() <= q.params.items())  # fmt: skip
    lines = [
        "# capacity round on development (scripts/report_round3.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# thresholds of levels {' '.join(t_levels)} and theoretical score >= "
        f"{THEORETICAL_THRESHOLD:g}; split {args.split}, language {args.language}",
        f"# S = {NO_ATTACK}; " + "; ".join(f"{q.label} [{q.params_hash}]" for q in points),
        "# Δ = attacked - marked without attack, same samples (n_sites of the parent)",
        "",
    ]
    models = args.models or list(cfg.models)
    curves: list[str] = []
    for model in models:
        for level in args.levels:
            lines += r3.cell_block(model, level, args.language, args.split)
            cells = []
            for c in curve_configs:
                try:
                    cells.append(rep.cell(c, model, level, args.language, args.split))
                except Exception as exc:  # cella mancante: si segnala nella curva
                    curves.append(f"  {c.name} {model} {level}: missing ({type(exc).__name__})")
            curves += curve_lines(rep, cells, model, level, args.language, args.split)
            curves += curve_lines(rep, cells, model, level, args.language, args.split, curve_point)
    lines += r3.criterion_lines()
    lines += ["## attribution curve in K (§5.11): TPR with score_K >= 2, "
              f"score_K = -log10(1 - (1 - p)^K); control with decoys (gap > {CONTROL_GAP} = !)",
              "", *curves]  # fmt: skip
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
