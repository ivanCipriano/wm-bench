"""Capacità per numero di siti e per lunghezza del problema (PIPELINE di multimark §5.6, P2).

Versione da riga di comando, per i controlli su dev. La funzione ``capacity_by_sites`` di
``src/bench/report.py`` (ADR-020) arriva con P3. Per i metodi multi-bit (MCGMark, ``mcss``,
``multimark``) e per ogni cella senza attacco o con attacco già rilevata, lo script fa tre cose:

1. Unisce, per campione:
   - la rilevazione (positivi o attaccati);
   - ``n_sites`` del campione marcato (per gli attaccati, quello del genitore); per MCGMark
     ``n_sites`` sono le posizioni marcate;
   - l'esecuzione (``PASSED``);
   - ``loc_to_generate`` del problema.
2. Raggruppa i campioni per fascia di ``n_sites``: ``[1–7], [8–12], [13–19], [20–31], [32–55],
   [56+]``, cioè i confini della tabella di §3.10.3 e della fascia D32 di MCSS. I campioni con
   ``n_sites`` pari a 0 o mancante si contano a parte.
3. Per fascia calcola:
   - ``n``;
   - ``tpr_at_fpr`` con la soglia della cella (``thresholds/``, quelle definitive con
     ``--levels L1 L2``);
   - ``message_accuracy``: tutti i bit giusti, con rilevazione ``FAILED`` = 0 bit, come la fase
     ``metrics``;
   - ``bit_accuracy``;
   - ``pass_at_1``.

   Proporzioni con l'IC di Wilson; ``bit_accuracy`` con il bootstrap per problema della fase
   ``metrics``.

Lo stesso si fa per fascia di ``loc_to_generate`` (1–5, 6–10, 11–20, 21–40, 41+, le fasce
dell'inventario).

Uscite in ``--out-dir`` (default ``<artifacts>/_runs/diagnostics/capacity/<config_hash>``; fino
al primo giro di P2 il default era la cartella ``capacity`` senza l'hash, e lì restano le tabelle
di quel giro): ``capacity_by_sites_<metodo>_<modello>_<livello>[_<attacco>_<params>].csv`` e
``capacity_by_loc_…csv``. Una tabella che esiste già con lo stesso contenuto non si riscrive; con
contenuto diverso lo script si ferma senza toccarla (il nome non contiene l'hash della
configurazione: due giri con la stessa cartella si scontrerebbero). Il riepilogo va su stdout o
in ``--report`` (che non deve esistere).
Gira in **bench-core**, solo lettura degli artefatti.

Uso::

    python scripts/report_capacity.py --method multimark [--models M ...] [--levels L1 L2] \\
        [--split dev] [--language python] [--attacks] [--report FILE] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

SITE_BANDS: tuple[tuple[int, int | None], ...] = (
    (1, 7), (8, 12), (13, 19), (20, 31), (32, 55), (56, None),
)  # fmt: skip
LOC_BANDS: tuple[tuple[int, int | None], ...] = ((1, 5), (6, 10), (11, 20), (21, 40), (41, None))
N_RESAMPLES = 1000


def band_label(band: tuple[int, int | None]) -> str:
    low, high = band
    return f"{low}+" if high is None else f"{low}-{high}"


def band_of(value: Any, bands: Sequence[tuple[int, int | None]]) -> str | None:
    """Etichetta della fascia chiusa che contiene ``value`` (``None`` per 0, mancante o fuori)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    v = int(value)
    for low, high in bands:
        if v >= low and (high is None or v <= high):
            return band_label((low, high))
    return None


def band_rows(
    table: pd.DataFrame,
    column: str,
    bands: Sequence[tuple[int, int | None]],
    message_bits: int,
    tau: float,
    seed: int,
) -> list[dict[str, Any]]:
    """Righe della tabella per fascia di ``column`` (vedi docstring del modulo)."""
    from bench.metrics.calibration import detected
    from bench.metrics.stats import ratio_interval, wilson

    t = table.copy()
    t["band"] = [band_of(v, bands) for v in t[column]]
    t["hit"] = [
        detected(None if pd.isna(s) else float(s), str(st), None if pd.isna(e) else str(e), tau)
        for s, st, e in zip(t["score"], t["status"], t["embed_status"], strict=True)
    ]
    bits = pd.to_numeric(t["bits_correct"], errors="coerce").fillna(0).where(t["status"] == "OK", 0)
    t["full"] = bits == message_bits
    t["bit_frac"] = bits / message_bits
    t["passed"] = t["exec_status"] == "PASSED"
    rows: list[dict[str, Any]] = []
    labels = [band_label(b) for b in bands] + [None]
    for label in labels:
        sel = t[t["band"].isna()] if label is None else t[t["band"] == label]
        n = len(sel)
        row: dict[str, Any] = {"band": label or f"{column}=0/none", "n": n}
        for name, col in (("tpr_at_fpr", "hit"), ("message_accuracy", "full"),
                          ("pass_at_1", "passed")):  # fmt: skip
            k = int(sel[col].sum())
            low, high = wilson(k, n)
            row.update({name: k / n if n else math.nan, f"{name}_low": low, f"{name}_high": high})
        row["bit_accuracy"] = float(sel["bit_frac"].mean()) if n else math.nan
        low, high = ratio_interval(sel, "bit_frac", seed, N_RESAMPLES) if n else (math.nan,) * 2
        row.update(bit_accuracy_low=low, bit_accuracy_high=high)
        rows.append(row)
    return rows


def cell_table(store: Any, info: dict[str, Any]) -> pd.DataFrame:
    """Rilevazioni della cella con ``n_sites``, esito dell'esecuzione e ``loc_to_generate``."""
    from bench.pipeline.stage import Cell
    from bench.pipeline.stages.attack import attacked_ref, attacked_subject
    from bench.pipeline.stages.detect import detection_ref
    from bench.pipeline.stages.execute import execution_ref
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.pipeline.stages.watermark import watermarked_ref

    method, model, h = info["method"], info["model_id"], info["config_hash"]
    level, lang, split = info["level"], info["language"], info["split"]
    attack, params = info.get("attack_id"), str(info.get("attack_params_hash"))
    subject = attacked_subject(str(attack), params) if attack else "positives"
    det = store.read_table(detection_ref(method, model, h, subject, level, lang, split))
    det = det[det["status"] != "NOT_APPLICABLE"]
    marked = store.read_table(watermarked_ref(method, model, h, level, lang, split))
    sites = dict(zip(marked["sample_id"], marked["n_sites"], strict=True))
    if attack:
        children = store.read_table(
            attacked_ref(str(attack), params, method, model, h, level, lang, split)
        )
        parent = dict(zip(children["sample_id"], children["parent_id"], strict=True))
        det = det.assign(n_sites=[sites.get(parent.get(s)) for s in det["sample_id"]])
    else:
        det = det.assign(n_sites=[sites.get(s) for s in det["sample_id"]])
    source = "attacked" if attack else "llm_watermarked"
    cell = Cell(source=source, method=method, model_id=model, level=level, language=lang,
                split=split, attack_id=attack, attack_params_hash=params)  # fmt: skip
    exe = store.read_table(execution_ref(cell, h))
    status = dict(zip(exe["sample_id"], exe["status"], strict=True))
    problems = store.read_table(problems_ref(level, lang))
    loc = dict(zip(problems["problem_key"], problems["loc_to_generate"], strict=True))
    out: pd.DataFrame = det.assign(
        exec_status=[status.get(s) for s in det["sample_id"]],
        loc_to_generate=[loc.get(p) for p in det["problem_key"]],
    )
    return out


def _fmt(row: dict[str, Any]) -> str:
    def f(name: str) -> str:
        v = row[name]
        if isinstance(v, float) and math.isnan(v):
            return f"{'-':>22}"
        return f"{v:.3f} [{row[name + '_low']:.2f},{row[name + '_high']:.2f}]".rjust(22)

    return (f"  {row['band']:<16}{row['n']:>6}" + f("tpr_at_fpr") + f("message_accuracy")
            + f("bit_accuracy") + f("pass_at_1"))  # fmt: skip


class CapacityError(RuntimeError):
    """Tabella già presente con contenuto diverso."""


def write_table(path: Path, rows: list[dict[str, Any]]) -> None:
    """Scrive la tabella; la lascia com'è se c'è già uguale.

    Raises:
        CapacityError: se esiste con contenuto diverso (non si sovrascrive).
    """
    data = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise CapacityError(f"{path} exists with different content: use another --out-dir")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def report_cell(
    store: Any, info: dict[str, Any], message_bits: int, tau: float, seed: int, out_dir: Path
) -> list[str]:
    """Tabelle per fascia di siti e di ``loc_to_generate`` di una cella; righe del riepilogo."""
    table = cell_table(store, info)
    name = f"{info['method']}_{info['model_id']}_{info['level']}_{info['language']}_{info['split']}"
    if info.get("attack_id"):
        name += f"_{info['attack_id']}_{info['attack_params_hash']}"
    head = f"{'':<18}{'n':>6}{'tpr_at_fpr':>22}{'message_accuracy':>22}{'bit_accuracy':>22}"
    head += f"{'pass_at_1':>22}"
    lines = [f"### {name} (config {info['config_hash']}, threshold {tau:g}, "
             f"{message_bits} bit, {len(table)} samples)"]  # fmt: skip
    for kind, column, bands in (("sites", "n_sites", SITE_BANDS),
                                ("loc", "loc_to_generate", LOC_BANDS)):  # fmt: skip
        rows = band_rows(table, column, bands, message_bits, tau, seed)
        write_table(out_dir / f"capacity_by_{kind}_{name}.csv", rows)
        lines += [f"by {column}:", head, *(_fmt(r) for r in rows)]
    return [*lines, ""]


def attacked_cells(store_root: Path, method: str, model: str, h: str, level: str, lang: str,
                   split: str) -> list[tuple[str, str]]:  # fmt: skip
    """(attacco, params_hash) con una rilevazione degli attaccati nella cella."""
    base = store_root / "detection" / method / model / h / "attacked"
    found = sorted(base.glob(f"*/*/{level}_{lang}_{split}.parquet")) if base.is_dir() else []
    return [(p.parent.parent.name, p.parent.name) for p in found]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--method", required=True)
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1"], help="levels of the cells")
    p.add_argument("--threshold-levels", nargs="+", help="calibration levels (default: --levels)")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("--config-hash", help="default: the default configuration of the method")
    p.add_argument("--attacks", action="store_true", help="also every attacked cell found")
    p.add_argument("--out-dir", type=Path)
    p.add_argument("--report", type=Path, help="summary file (default stdout; must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.report is not None and args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1

    from bench.config.builder import load_experiment
    from bench.domain.ids import derive_seed
    from bench.domain.models import ThresholdSet
    from bench.pipeline.stages.calibrate import threshold_ref
    from bench.pipeline.stages.metrics import MetricsStage
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=metrics", *args.overrides])
    stage = MetricsStage(cfg)
    if not stage._multibit(args.method):
        print(f"[ERROR] {args.method} is not a multi-bit method", file=sys.stderr)
        return 1
    bits = stage._message_bits(args.method)
    h = args.config_hash or stage.config_hash(args.method)
    store = ArtifactStore(Path(cfg.paths.artifacts))
    out_dir = args.out_dir or Path(cfg.paths.artifacts) / "_runs" / "diagnostics" / "capacity" / h
    t_levels = args.threshold_levels or args.levels
    lines = [
        f"# capacity by number of sites (scripts/report_capacity.py; PIPELINE §5.6), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# method {args.method}, config {h}, threshold levels {' '.join(t_levels)}; "
        f"tables in {out_dir}",
        "",
    ]
    for model in args.models or list(cfg.models):
        thresholds = store.read_model(
            threshold_ref(args.method, model, args.language, h, t_levels), ThresholdSet
        )
        for level in args.levels:
            base = {"method": args.method, "model_id": model, "config_hash": h, "level": level,
                    "language": args.language, "split": args.split}  # fmt: skip
            cells = [base]
            if args.attacks:
                found = attacked_cells(store.root, args.method, model, h, level, args.language,
                                       args.split)  # fmt: skip
                cells += [{**base, "attack_id": a, "attack_params_hash": ph} for a, ph in found]
            for info in cells:
                seed = derive_seed(cfg.global_seed, "capacity", *sorted(map(str, info.values())))
                try:
                    lines += report_cell(store, info, bits, thresholds.threshold, seed, out_dir)
                except CapacityError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    return 1
    text = "\n".join(lines) + "\n"
    if args.report is None:
        sys.stdout.write(text)
    else:
        with args.report.open("x", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
