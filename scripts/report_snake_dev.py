"""T1.6 (nomi in ``snake_case``, ADR-018) su sviluppo, sulle configurazioni del giro di capacità.

Per ogni configurazione (``--config metodo:hash[:etichetta]``: ``mcss``, ``multimark`` base e i
punti di sviluppo di ``configs/hpo/multimark.yaml``), su L1 e L2 dev e per ogni modello, lo
script legge gli artefatti delle fasi ``attack``, ``detect``, ``calibrate`` e ``metrics`` e
stampa, **in righe separate per i due punti** ``caps=convert`` e ``caps=keep`` (decisione
dell'utente del 19 novembre 2026: con un solo punto ``convert`` il confronto favorirebbe
multimark, i cui nomi non portano lo stile):

1. **TPR per fascia di siti** (fasce di ``scripts/report_capacity.py``; per gli attaccati vale
   ``n_sites`` del genitore): ``tpr_cal`` con la soglia calibrata nel giro di capacità
   (``thresholds/.../L1L2/<hash>``: **nessuna nuova calibrazione**) e ``tpr_2`` con la soglia
   teorica ``score >= 2``, con la differenza ``Δ`` dai marcati senza attacco (stessi campioni).
2. **Riepilogo** per configurazione e punto, media semplice delle quattro celle modello × livello
   (come il criterio del giro di capacità): ``tpr_cal``, ``tpr_2`` e le loro ``Δ``; ``changed``
   (frazione dei campioni applicabili con codice cambiato); per campione applicabile le medie di
   ``n_renamed``, ``n_caps_kept`` e ``n_collisions`` di ``attack_detail``; ``ren/cand``
   (rinominati sui candidati, somme della cella); ``Δscore%`` (variazione relativa del punteggio
   principale medio, attaccato contro genitore, sulle coppie con entrambi i punteggi);
   ``ΔPass@1`` (attaccato − marcato, in punti percentuali, dalla fase ``metrics``).
3. **Attesi di PIPELINE §P3** (solo riportati, non sono vincoli): per ``multimark`` T1.6 non
   cambia il punteggio principale di più dello 0,5% in media (invarianza della V2 allo stile:
   ``|Δscore%| <= 0,5``); per ``mcss`` ``changed`` e ``ren/cand`` sono la frazione delle variabili
   che portano uno stile diverso da ``snake_case``.

Gira in **bench-core**, solo lettura degli artefatti::

    python scripts/report_snake_dev.py --config mcss:<hash>:mcss \\
        --config multimark:<hash>:multimark ... [--levels L1 L2] [--report FILE] [override ...]

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
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTACK = "T1.6"
SCORE_SHIFT_MAX = 0.005  # PIPELINE §P3: multimark, variazione media del punteggio <= 0,5%
DETAIL_KEYS = ("n_renamed", "n_caps_kept", "n_collisions")


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


r3 = _load("report_round3")
rad = r3.rad
Config = rad.Config
CellMean = r3.CellMean
_v = rad._v


def detail_stats(samples: pd.DataFrame) -> dict[str, float]:
    """Misure dai campioni attaccati (``attacked_ref``): ``changed``, medie per campione
    applicabile dei contatori di ``attack_detail`` e ``ren_cand``."""
    t = samples[samples["attack_status"] != "NOT_APPLICABLE"]
    n = len(t)
    out: dict[str, float] = {"n": float(n)}
    if not n:
        return {**out, "changed": math.nan, "ren_cand": math.nan,
                **{k: math.nan for k in DETAIL_KEYS}}  # fmt: skip
    details = [json.loads(d) if isinstance(d, str) and d else {} for d in t["attack_detail"]]
    out["changed"] = float((t["attack_status"] == "APPLIED").mean())
    for key in DETAIL_KEYS:
        out[key] = sum(float(d.get(key) or 0) for d in details) / n
    cand = sum(float(d.get("n_candidates") or 0) for d in details)
    renamed = sum(float(d.get("n_renamed") or 0) for d in details)
    out["ren_cand"] = renamed / cand if cand else math.nan
    return out


def score_shift(clean: pd.DataFrame, attacked: pd.DataFrame, parents: dict[str, str]) -> float:
    """Variazione relativa del punteggio principale medio, attaccato contro genitore, sulle
    coppie in cui entrambi i punteggi sono finiti (``nan`` se nessuna o media del genitore 0)."""
    base = dict(zip(clean["sample_id"],
                    pd.to_numeric(clean["score"], errors="coerce"), strict=True))  # fmt: skip
    pairs = []
    scores = pd.to_numeric(attacked["score"], errors="coerce")
    for sid, score in zip(attacked["sample_id"], scores, strict=True):
        ref = base.get(parents.get(sid, ""))
        if ref is not None and math.isfinite(ref) and math.isfinite(score):
            pairs.append((float(ref), float(score)))
    if not pairs:
        return math.nan
    ref_mean = sum(p[0] for p in pairs) / len(pairs)
    att_mean = sum(p[1] for p in pairs) / len(pairs)
    return att_mean / ref_mean - 1 if ref_mean else math.nan


class SnakeReport:
    """Legge gli artefatti (``report_attacks_dev.Reporter``) e accumula il riepilogo."""

    COLUMNS = ("tpr_cal", "d_cal", "tpr_2", "d_2", "changed", "n_renamed", "n_caps_kept",
               "n_collisions", "ren_cand", "score_shift", "d_pass")  # fmt: skip

    def __init__(self, rep: Any, configs: Sequence[Any], points: Sequence[Any]) -> None:
        self.rep = rep
        self.configs = list(configs)
        self.points = list(points)
        self.summary = {
            (c.name, p.label): {k: CellMean() for k in self.COLUMNS}
            for c in configs for p in points
        }  # fmt: skip

    def metric(self, c: Any, model: str, level: str, lang: str, split: str, name: str,
               attack: tuple[str, str] | None = None) -> float:  # fmt: skip
        from bench.pipeline.stages.metrics import metrics_ref

        ref = metrics_ref(c.method, model, c.config_hash, level, lang, split, self.rep.t_levels,
                          attack)  # fmt: skip
        if not self.rep.store.exists(ref):
            return math.nan
        t = self.rep.store.read_table(ref)
        rows = t[t["name"] == name]
        return float(rows["value"].iloc[0]) if len(rows) else math.nan

    def extras(self, d: Any, point: Any, model: str, level: str, lang: str,
               split: str) -> dict[str, float]:  # fmt: skip
        """Misure di ``attack_detail``, spostamento del punteggio e ΔPass@1 di una cella."""
        from bench.pipeline.stages.attack import attacked_ref

        rep, c = self.rep, d.config
        ref = attacked_ref(point.attack_id, point.params_hash, c.method, model, c.config_hash,
                           level, lang, split)  # fmt: skip
        if not rep.store.exists(ref):
            return {}
        samples = rep.store.read_table(ref)
        out = detail_stats(samples)
        parents = dict(zip(samples["sample_id"], samples["parent_id"], strict=True))
        try:
            clean = rep.table(rep.info(c, model, level, lang, split))
            attacked = rep.table(rep.info(c, model, level, lang, split, point))
            out["score_shift"] = score_shift(clean, attacked, parents)
        except Exception:  # rilevazione mancante: nan
            out["score_shift"] = math.nan
        p_clean = self.metric(c, model, level, lang, split, "pass_at_1")
        p_att = self.metric(c, model, level, lang, split, "pass_at_1",
                            (point.attack_id, point.params_hash))  # fmt: skip
        out["d_pass"] = 100 * (p_att - p_clean)
        return out

    def cell_block(self, model: str, level: str, lang: str, split: str) -> list[str]:
        rep = self.rep
        where = f"{model} {level}"
        cells, lines = [], []
        for c in self.configs:
            try:
                cells.append(rep.cell(c, model, level, lang, split))
            except Exception as exc:  # cella mancante: si segnala e si va avanti
                lines.append(f"  {c.name}: missing clean cell ({type(exc).__name__}: {exc})")
                for p in self.points:
                    for acc in self.summary[(c.name, p.label)].values():
                        acc.missing.append(where)
                cells.append(None)
        taus = ", ".join(f"{d.config.name} {d.tau:.6g}" for d in cells if d)
        out = [f"## {model} {level}: calibrated thresholds {taus}", *lines, ""]
        labels = r3.band_labels(rep.cap, rep.bands)
        for point in self.points:
            out.append(f"### {point.label}  [params {point.params_hash}]  {model} {level} {lang} "
                       f"{split}")  # fmt: skip
            out.append(f"  {'config':<34}{'band':<16}{'n':>6}{'tpr_cal':>10}{'Δ':>8}"
                       f"{'tpr_2':>10}{'Δ':>8}")  # fmt: skip
            for c, d in zip(self.configs, cells, strict=True):
                if d is None:
                    continue
                acc = self.summary[(c.name, point.label)]
                stats = rep.attacked(d, point, model, level, lang, split)
                if stats is None:
                    out.append(f"  {c.name:<34}no detection of the attacked samples")
                    for a in acc.values():
                        a.missing.append(where)
                    continue
                for label in labels:
                    row, ref = stats[label], d.clean[label]
                    if row["n"] == 0:
                        continue
                    out.append(
                        f"  {c.name:<34}{label:<16}{int(row['n']):>6}"
                        f"{_v(row['tpr_cal']):>10}{rad._d(row['tpr_cal'], ref['tpr_cal']):>8}"
                        f"{_v(row['tpr_2']):>10}{rad._d(row['tpr_2'], ref['tpr_2']):>8}"
                    )
                a, r = stats["all"], d.clean["all"]
                values = {
                    "tpr_cal": a["tpr_cal"], "d_cal": a["tpr_cal"] - r["tpr_cal"],
                    "tpr_2": a["tpr_2"], "d_2": a["tpr_2"] - r["tpr_2"],
                    **self.extras(d, point, model, level, lang, split),
                }  # fmt: skip
                for key in self.COLUMNS:
                    acc[key].add(float(values.get(key, math.nan)), where)
            out.append("")
        return out

    def summary_lines(self) -> list[str]:
        head = ("  " + f"{'config':<34}{'point':<20}" + f"{'tpr_cal':>9}{'Δ':>8}{'tpr_2':>9}"
                f"{'Δ':>8}{'changed':>9}{'ren/smp':>9}{'caps_kept':>10}{'collis':>8}"
                f"{'ren/cand':>10}{'Δscore%':>9}{'ΔPass@1':>9}")  # fmt: skip
        out = ["## summary: simple mean of the 4 cells model x level (one row per point)", head]
        for c in self.configs:
            for p in self.points:
                acc = self.summary[(c.name, p.label)]
                rate = {k: acc[k].rate for k in self.COLUMNS}
                shift = rate["score_shift"]
                out.append(
                    f"  {c.name:<34}{p.label:<20}{_v(rate['tpr_cal']):>9}"
                    f"{_signed(rate['d_cal']):>8}{_v(rate['tpr_2']):>9}{_signed(rate['d_2']):>8}"
                    f"{_v(rate['changed']):>9}{_v(rate['n_renamed']):>9}"
                    f"{_v(rate['n_caps_kept']):>10}{_v(rate['n_collisions']):>8}"
                    f"{_v(rate['ren_cand']):>10}{_pct(shift):>9}{_signed(rate['d_pass'], 2):>9}"
                )
        out.append("")
        out.append("## expectations of PIPELINE §P3 (reported, not constraints)")
        out.append(f"  multimark: |Δscore%| <= {SCORE_SHIFT_MAX:.1%} (V2 invariant to the style); "
                   "mcss: changed and ren/cand = share of names not in snake_case")  # fmt: skip
        for c in self.configs:
            for p in self.points:
                acc = self.summary[(c.name, p.label)]
                if c.method == "multimark":
                    shift = acc["score_shift"].rate
                    verdict = ("n/d" if math.isnan(shift)
                               else "ok" if abs(shift) <= SCORE_SHIFT_MAX else "ABOVE")  # fmt: skip
                    out.append(f"  {c.name:<34}{p.label:<20}Δscore% {_pct(shift)}  {verdict}")
                else:
                    out.append(f"  {c.name:<34}{p.label:<20}changed {_v(acc['changed'].rate)}  "
                               f"ren/cand {_v(acc['ren_cand'].rate)}")  # fmt: skip
        for c in self.configs:
            missing = sorted({m for p in self.points
                              for a in self.summary[(c.name, p.label)].values()
                              for m in a.missing})  # fmt: skip
            if missing:
                out.append(f"  {c.name}: incomplete ({', '.join(missing)})")
        return [*out, ""]


def _signed(x: float, digits: int = 3) -> str:
    return "-" if x is None or math.isnan(x) else f"{x:+.{digits}f}"


def _pct(x: float) -> str:
    return "-" if x is None or math.isnan(x) else f"{100 * x:+.2f}"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"])
    p.add_argument("--threshold-levels", nargs="+", help="calibration levels (default: --levels)")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--report", type=Path, help="output file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.split == "test":
        print("[ERROR] development run only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    t_levels = args.threshold_levels or args.levels
    rep = rad.Reporter(cfg, args.config, t_levels)
    points = rad.attack_points(args.language, (ATTACK,))
    report = SnakeReport(rep, args.config, points)
    lines = [
        "# T1.6 snake_case on development (scripts/report_snake_dev.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# thresholds of levels {' '.join(t_levels)} (capacity round, no new calibration) and "
        f"theoretical score >= {r3.THEORETICAL_THRESHOLD:g}; split {args.split}, "
        f"language {args.language}",
        "# points: " + "; ".join(f"{q.label} [{q.params_hash}]" for q in points),
        "# Δ = attacked - marked without attack, same samples (n_sites of the parent)",
        "",
    ]
    for model in args.models or list(cfg.models):
        for level in args.levels:
            lines += report.cell_block(model, level, args.language, args.split)
    lines += report.summary_lines()
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
