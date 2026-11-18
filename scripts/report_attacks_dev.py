"""Robustezza su sviluppo prima di P3: i metodi a confronto per attacco, per fascia di siti.

Per ogni configurazione (``--config metodo:hash[:etichetta]``, per esempio i quattro del secondo
giro di P2: ``mcss``, ``multimark`` base e i due punti di sviluppo), modello, livello e punto della
griglia di ogni attacco di ``ATTACKS`` (``configs/attack/*.yaml``), lo script confronta i
campioni attaccati con i marcati senza attacco della stessa cella. Le fasce di ``n_sites`` sono
quelle di ``scripts/report_capacity.py``; per gli attaccati vale ``n_sites`` del genitore, quindi
le due popolazioni di una fascia sono le stesse (I1). Per fascia stampa:

- ``n``;
- ``tpr_cal``: TPR con la soglia calibrata nel secondo giro (``thresholds/.../<livelli>/<hash>``:
  **nessuna nuova calibrazione**);
- ``tpr_2``: TPR con la soglia teorica fissa ``score >= 2`` (p ≤ 0,01, come
  ``scripts/report_metrics.py``);
- ``bit_acc``: ``bit_accuracy`` (rilevazione non ``OK`` = 0 bit, come la fase ``metrics``);
- ``wa_<nome>``: ``wrong_attribution_rate_<nome>`` per ogni punteggio secondario con il messaggio
  scelto (``best_<nome>``, ADR-017 §3): frazione dei rilevati con la soglia secondaria della cella
  il cui messaggio scelto non è quello atteso (``-`` se nessuno è rilevato);
- per ognuna la differenza ``Δ`` rispetto al caso senza attacchi (attaccato − marcato).

Le stesse decisioni della fase ``metrics`` (``bench.metrics.calibration.detected``), senza
intervalli: gli intervalli (bootstrap per problema e Wilson) sono quelli della fase ``metrics``,
che lo script riporta alla fine di ogni attacco per tutti i campioni (``metrics_L1L2/`` senza
attacco e ``.../attacked/<attacco>/<params>/``).

Ordine: prima i 17 punti scelti per il controllo (``CHOSEN``: rinomina, formattazione, modifiche
locali, aggiunta e rimozione di righe; decisioni dell'utente del 18 novembre 2026), poi gli altri
punti delle griglie lanciate per intero. Per T1.4 anche il TPR al variare di ``r``, separato fra
nomi ``random`` e ``natural``.

Gira in **bench-core**, solo lettura degli artefatti::

    python scripts/report_attacks_dev.py --config mcss:1d0c64c5124e \\
        --config multimark:59a04735824c:base [--levels L1 L2] [--models M ...] \\
        [--report FILE] [override Hydra ...]

``--report`` non deve esistere (i log non si sovrascrivono).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
THEORETICAL_THRESHOLD = 2.0
# Attacchi della campagna (griglie intere di configs/attack/*.yaml).
ATTACKS = ("T1.4", "T1.1", "T1.2", "T4.1", "T1.5", "T2.1", "T1.3", "T2.4")
# I 17 punti scelti, nell'ordine del report: (attacco, parametri che il punto deve avere).
CHOSEN: tuple[tuple[str, dict[str, Any]], ...] = (
    ("T1.4", {"ratio": 0.5, "name_kind": "random"}),
    ("T1.4", {"ratio": 0.5, "name_kind": "natural"}),
    ("T1.4", {"ratio": 1.0, "name_kind": "random"}),
    ("T1.4", {"ratio": 1.0, "name_kind": "natural"}),
    ("T1.1", {}),
    ("T1.2", {}),
    ("T4.1", {"rename": False}),
    ("T4.1", {"rename": True}),
    ("T1.5", {"k": 1}),
    ("T1.5", {"k": 3}),
    ("T1.5", {"k": 5}),
    ("T2.1", {"b": 1}),
    ("T2.1", {"b": "all"}),
    ("T1.3", {"mode": "insert"}),
    ("T2.4", {"p": 0.75}),
    ("T2.4", {"p": 0.5}),
    ("T1.3", {"mode": "remove"}),
)
RATIOS = (0.25, 0.5, 0.75, 1.0)


def _capacity() -> Any:
    """``scripts/report_capacity.py`` (fasce e tabella della cella)."""
    spec = importlib.util.spec_from_file_location("report_capacity", REPO_ROOT / "scripts" /
                                                  "report_capacity.py")  # fmt: skip
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Config:
    """Una configurazione da confrontare."""

    method: str
    config_hash: str
    label: str

    @classmethod
    def parse(cls, text: str) -> Config:
        parts = text.split(":")
        if not 2 <= len(parts) <= 3 or not all(parts):  # noqa: PLR2004
            raise argparse.ArgumentTypeError(f"--config {text!r}: expected method:hash[:label]")
        return cls(parts[0], parts[1], parts[2] if parts[2:] else parts[0])

    @property
    def name(self) -> str:
        return f"{self.label} {self.config_hash}"


@dataclass(frozen=True)
class Point:
    """Un punto della griglia di un attacco."""

    attack_id: str
    params_hash: str
    params: dict[str, Any]
    label: str
    chosen: int | None  # posizione in CHOSEN, None per gli altri


def varying_keys(points: Sequence[dict[str, Any]]) -> list[str]:
    """Parametri che cambiano fra i punti della griglia (gli altri sono fissi)."""
    keys = sorted({k for p in points for k in p})
    return [k for k in keys if len({json.dumps(p.get(k), default=str) for p in points}) > 1]


def attack_points(language: str, attacks: Sequence[str] = ATTACKS) -> list[Point]:
    """Punti delle griglie: prima quelli di ``CHOSEN`` nel loro ordine, poi gli altri."""
    from bench.attacks.base import attack_grid, grid_attacks

    points: list[Point] = []
    for attack_id in attacks:
        grid = attack_grid(attack_id)
        keys = varying_keys(grid.points)
        for h, attack in grid_attacks(attack_id, language=language).items():
            params = dict(attack.params)
            shown = " ".join(f"{k}={params[k]}" for k in keys if k in params) or "default"
            chosen = next(
                (i for i, (a, match) in enumerate(CHOSEN)
                 if a == attack_id and match.items() <= params.items()),
                None,
            )  # fmt: skip
            points.append(Point(attack_id, h, params, f"{attack_id} {shown}", chosen))
    big = len(CHOSEN)
    return sorted(points, key=lambda p: (big if p.chosen is None else p.chosen, p.label))


def _hits(table: pd.DataFrame, threshold: float) -> list[bool]:
    from bench.metrics.calibration import detected

    return [
        detected(
            None if pd.isna(s) else float(s), str(st), None if pd.isna(e) else str(e), threshold
        )  # fmt: skip
        for s, st, e in zip(
            pd.to_numeric(table["score"], errors="coerce"),
            table["status"],
            table["embed_status"],
            strict=True,
        )
    ]


def secondary_names(table: pd.DataFrame, adapter: Any) -> list[str]:
    """Punteggi secondari con il messaggio scelto (``best_<nome>``) in qualche riga."""
    extras = [json.loads(r) if isinstance(r, str) and r else {} for r in table["extra_json"]]
    return [n for n in adapter.secondary_scores if any(f"best_{n}" in e for e in extras)]


def band_stats(
    table: pd.DataFrame,
    message_bits: int,
    tau: float,
    adapter: Any,
    secondary: dict[str, float],
    bands: Sequence[tuple[int, int | None]],
) -> dict[str, dict[str, float]]:
    """Misure per fascia di ``n_sites`` (più ``all``): vedi la docstring del modulo."""
    from bench.pipeline.stages.calibrate import with_secondary

    cap = _capacity()
    t = table.reset_index(drop=True)
    band = pd.Series([cap.band_of(v, bands) for v in t["n_sites"]], dtype=object)
    bits = pd.to_numeric(t["bits_correct"], errors="coerce").fillna(0).where(t["status"] == "OK", 0)
    cols = pd.DataFrame(
        {
            "tpr_cal": _hits(t, tau),
            "tpr_2": _hits(t, THEORETICAL_THRESHOLD),
            "bit_acc": bits / message_bits,
        }
    )
    extras = [json.loads(r) if isinstance(r, str) and r else {} for r in t["extra_json"]]
    wrong: dict[str, pd.Series] = {}
    for name, sec_tau in secondary.items():
        hit = _hits(with_secondary(t, adapter, name), sec_tau)
        wrong[name] = pd.Series([
            (e.get(f"best_{name}") != exp) if h else math.nan
            for e, exp, h in zip(extras, t["expected"], hit, strict=True)
        ], dtype=float)  # fmt: skip
    labels = ["all", *(cap.band_label(b) for b in bands), "n_sites=0/none"]
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        if label == "all":
            sel = pd.Series(True, index=t.index)
        elif label == "n_sites=0/none":
            sel = band.isna()
        else:
            sel = band == label
        n = int(sel.sum())
        row: dict[str, float] = {"n": n}
        for col in ("tpr_cal", "tpr_2", "bit_acc"):
            row[col] = float(cols[col][sel].astype(float).mean()) if n else math.nan
        for name, series in wrong.items():
            vals = series[sel].dropna()
            row[f"wa_{name}"] = float(vals.mean()) if len(vals) else math.nan
        out[label] = row
    return out


def _v(x: float) -> str:
    return "-" if x is None or math.isnan(x) else f"{x:.3f}"


def _d(x: float, ref: float) -> str:
    return "-" if any(v is None or math.isnan(v) for v in (x, ref)) else f"{x - ref:+.3f}"


@dataclass
class CellData:
    """Misure senza attacco di una configurazione in una cella (modello, livello)."""

    config: Config
    adapter: Any
    bits: int
    tau: float
    secondary: dict[str, float]
    clean: dict[str, dict[str, float]]


class Reporter:
    """Legge gli artefatti e scrive le righe del report."""

    def __init__(self, cfg: Any, configs: Sequence[Config], t_levels: Sequence[str]) -> None:
        from bench.pipeline.stages.metrics import MetricsStage
        from bench.store.artifact_store import ArtifactStore

        self.cfg = cfg
        self.configs = list(configs)
        self.t_levels = list(t_levels)
        self.stage = MetricsStage(cfg)
        self.store = ArtifactStore(Path(cfg.paths.artifacts))
        self.cap = _capacity()
        self.bands = self.cap.SITE_BANDS

    def info(self, c: Config, model: str, level: str, lang: str, split: str,
             point: Point | None = None) -> dict[str, Any]:  # fmt: skip
        base = {"method": c.method, "model_id": model, "config_hash": c.config_hash,
                "level": level, "language": lang, "split": split}  # fmt: skip
        if point is not None:
            base.update(attack_id=point.attack_id, attack_params_hash=point.params_hash)
        return base

    def table(self, info: dict[str, Any]) -> pd.DataFrame:
        """Tabella della cella (``report_capacity.cell_table``) con il messaggio atteso dei
        campioni (``expected``: marcati o attaccati, per ``sample_id``, come in ``metrics``)."""
        from bench.pipeline.stages.attack import attacked_ref
        from bench.pipeline.stages.watermark import watermarked_ref

        table = self.cap.cell_table(self.store, info)
        keys = ("method", "model_id", "config_hash", "level", "language", "split")
        method, model, h, level, lang, split = (info[k] for k in keys)
        if info.get("attack_id"):
            ref = attacked_ref(info["attack_id"], info["attack_params_hash"], method, model, h,
                               level, lang, split)  # fmt: skip
        else:
            ref = watermarked_ref(method, model, h, level, lang, split)
        samples = self.store.read_table(ref)
        expected = dict(zip(samples["sample_id"], samples["expected_message"], strict=True))
        out: pd.DataFrame = table.assign(expected=[expected.get(s) for s in table["sample_id"]])
        return out

    def cell(self, c: Config, model: str, level: str, lang: str, split: str) -> CellData:
        from bench.domain.models import ThresholdSet
        from bench.pipeline.stages.calibrate import threshold_ref

        thresholds = self.store.read_model(
            threshold_ref(c.method, model, lang, c.config_hash, self.t_levels), ThresholdSet
        )
        adapter = self.stage.adapter(c.method)
        bits = self.stage._message_bits(c.method)
        table = self.table(self.info(c, model, level, lang, split))
        secondary = {
            n: float(thresholds.secondary[n]["threshold"])
            for n in secondary_names(table, adapter)
            if n in thresholds.secondary
        }
        tau = float(thresholds.threshold)
        clean = band_stats(table, bits, tau, adapter, secondary, self.bands)
        return CellData(c, adapter, bits, tau, secondary, clean)

    def attacked(self, d: CellData, point: Point, model: str, level: str, lang: str,
                 split: str) -> dict[str, dict[str, float]] | None:  # fmt: skip
        from bench.pipeline.stages.attack import attacked_subject
        from bench.pipeline.stages.detect import detection_ref

        ref = detection_ref(d.config.method, model, d.config.config_hash,
                            attacked_subject(point.attack_id, point.params_hash), level, lang,
                            split)  # fmt: skip
        if not self.store.exists(ref):
            return None
        info = self.info(d.config, model, level, lang, split, point)
        table = self.table(info)
        return band_stats(table, d.bits, d.tau, d.adapter, d.secondary, self.bands)

    def metric_lines(self, d: CellData, point: Point, model: str, level: str, lang: str,
                     split: str) -> list[str]:  # fmt: skip
        """Valori della fase ``metrics`` (tutti i campioni, con l'intervallo): marcati, poi
        attaccati."""
        from bench.pipeline.stages.metrics import metrics_ref

        c = d.config

        def read(attack: tuple[str, str] | None) -> dict[str, Any]:
            ref = metrics_ref(c.method, model, c.config_hash, level, lang, split, self.t_levels,
                              attack)  # fmt: skip
            if not self.store.exists(ref):
                return {}
            t = self.store.read_table(ref)
            return {r["name"]: r for r in t.to_dict(orient="records")}

        clean, att = read(None), read((point.attack_id, point.params_hash))
        if not att:
            return [f"  {c.name}: no metrics of the attacked cell"]
        names = ["tpr_at_fpr", "bit_accuracy", "message_accuracy",
                 *sorted(n for n in att if n.startswith("wrong_attribution_rate_")),
                 "changed_fraction", "pass_at_1"]  # fmt: skip

        def fmt(r: dict[str, Any] | None) -> str:
            if not r:
                return "-"
            return f"{_v(r['value'])} [{_v(r['ci_low'])},{_v(r['ci_high'])}]"

        parts = [f"{n} {fmt(clean.get(n))} -> {fmt(att.get(n))}" for n in names if n in att]
        return [f"  {c.name}:", *(f"    {p}" for p in parts)]


def point_table(rep: Reporter, cells: Sequence[CellData], point: Point,
                where: tuple[str, str, str, str]) -> list[str]:  # fmt: skip
    """Tabella di un punto: configurazioni × fasce, con le differenze dal caso senza attacchi."""
    model, level, lang, split = where
    names = sorted({n for d in cells for n in d.secondary})
    cols = ["tpr_cal", "tpr_2", "bit_acc", *(f"wa_{n}" for n in names)]
    head = f"  {'config':<28}{'band':<16}{'n':>6}" + "".join(f"{c:>16}{'Δ':>8}" for c in cols)
    lines = [f"### {point.label}  [params {point.params_hash}]  {model} {level} {lang} {split}",
             head]  # fmt: skip
    metric: list[str] = []
    for d in cells:
        stats = rep.attacked(d, point, model, level, lang, split)
        if stats is None:
            lines.append(f"  {d.config.name:<28}no detection of the attacked samples")
            continue
        for label, row in stats.items():
            if row["n"] == 0:
                continue
            ref = d.clean[label]
            vals = "".join(
                f"{_v(row.get(c, math.nan)):>16}{_d(row.get(c, math.nan), ref.get(c, math.nan)):>8}"
                for c in cols
            )
            lines.append(f"  {d.config.name:<28}{label:<16}{int(row['n']):>6}{vals}")
        metric += rep.metric_lines(d, point, model, level, lang, split)
    return [*lines, "  metrics stage, all samples (marked -> attacked, CI):", *metric, ""]


def rename_curve(rep: Reporter, cells: Sequence[CellData], points: Sequence[Point],
                 where: tuple[str, str, str, str]) -> list[str]:  # fmt: skip
    """T1.4: TPR (tutti i campioni) al variare di r, separato fra nomi random e natural."""
    model, level, lang, split = where
    lines = [f"### T1.4 TPR by r (all samples)  {model} {level} {lang} {split}"]
    theoretical = f"theoretical threshold score >= {THEORETICAL_THRESHOLD:g}"
    for col, title in (("tpr_cal", "calibrated threshold"), ("tpr_2", theoretical)):
        lines.append(f"  {title}:")
        lines.append(f"  {'config':<28}{'names':<10}{'r=0':>8}"
                     + "".join(f"{f'r={r:g}':>8}" for r in RATIOS))  # fmt: skip
        for d in cells:
            for kind in ("random", "natural"):
                vals = []
                for r in RATIOS:
                    p = next((q for q in points if q.attack_id == "T1.4"
                              and q.params.get("ratio") == r
                              and q.params.get("name_kind") == kind), None)  # fmt: skip
                    stats = rep.attacked(d, p, model, level, lang, split) if p else None
                    vals.append(_v(stats["all"][col]) if stats else "-")
                lines.append(f"  {d.config.name:<28}{kind:<10}{_v(d.clean['all'][col]):>8}"
                             + "".join(f"{v:>8}" for v in vals))  # fmt: skip
    return [*lines, ""]


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
        print("[ERROR] development check only: --split test is not allowed", file=sys.stderr)
        return 1
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    t_levels = args.threshold_levels or args.levels
    rep = Reporter(cfg, args.config, t_levels)
    points = attack_points(args.language)
    lines = [
        "# robustness on development (scripts/report_attacks_dev.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# thresholds of levels {' '.join(t_levels)} (no recalibration) and theoretical "
        f"score >= {THEORETICAL_THRESHOLD:g}; split {args.split}, language {args.language}",
        "# Δ = attacked - marked without attack, same samples (n_sites of the parent);"
        " wa_<name> = wrong_attribution_rate_<name>",
        f"# points: {len(CHOSEN)} chosen first, then the other "
        f"{sum(q.chosen is None for q in points)} of the full grids",
        "",
    ]
    for model in args.models or list(cfg.models):
        for level in args.levels:
            where = (model, level, args.language, args.split)
            cells = [rep.cell(c, model, level, args.language, args.split) for c in args.config]
            lines.append(f"## {model} {level}: thresholds "
                         + ", ".join(f"{d.config.name} {d.tau:.6g}" for d in cells))  # fmt: skip
            lines.append("")
            for i, point in enumerate(points):
                if i == len(CHOSEN):
                    lines += ["## other points of the full grids", ""]
                lines += point_table(rep, cells, point, where)
            lines += rename_curve(rep, cells, points, where)
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
