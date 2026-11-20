"""Stessa soglia per due configurazioni che leggono allo stesso modo (rifacimento di mcss).

La correzione della libreria MultiMark 3c47a1f tocca solo la scrittura
(``rewrite.rename_variables``), non la lettura: i negativi (codice umano e LLM, mai marcati)
devono quindi avere gli stessi punteggi con il vecchio e il nuovo ``config_hash`` di mcss, e la
soglia calibrata deve essere identica. Non è una nuova calibrazione: la fase ``calibrate`` del
nuovo hash serve solo a questo confronto.

Per ogni modello (``--models``, default quelli della configurazione) lo script confronta:

1. le rilevazioni dei negativi di sviluppo, campione per campione, a uguaglianza esatta:
   ``neg_human`` (L1 e L2), ``neg_integration`` (L2), ``neg_llm`` (L1 e L2), sulle colonne
   ``status``, ``score``, ``native_decision``, ``decoded_message``, ``bits_correct`` ed
   ``extra_json`` (come dizionario; chiavi con ``elapsed`` o ``time`` escluse);
2. la soglia (``thresholds/.../<livelli>/<hash>``): ``threshold``, ``achieved_fpr_dev``,
   ``n_negatives``, ``underpowered``, ``provisional``, ``levels``, ``target_fpr`` e le soglie
   secondarie (``secondary``), a uguaglianza esatta.

Esce con 0 se tutto coincide, con 1 alla prima cella diversa o mancante (stampa le differenze).
Gira in **bench-core**, solo lettura::

    python scripts/compare_thresholds.py --method mcss --old b521002e09f5 --new ae63f16de28f \\
        [--levels L1 L2] [--models M ...] [override Hydra ...]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

SUBJECTS = (("neg_human", "L1"), ("neg_human", "L2"), ("neg_integration", "L2"),
            ("neg_llm", "L1"), ("neg_llm", "L2"))  # fmt: skip
COLUMNS = ("status", "score", "native_decision", "decoded_message", "bits_correct")
THRESHOLD_FIELDS = ("threshold", "achieved_fpr_dev", "n_negatives", "underpowered",
                    "provisional", "levels", "target_fpr", "secondary")  # fmt: skip
MAX_SHOWN = 5


def _same(a: Any, b: Any) -> bool:
    """Uguaglianza esatta, con ``NaN == NaN`` e ``None == NaN`` (celle vuote del parquet)."""
    na = a is None or (isinstance(a, float) and math.isnan(a))
    nb = b is None or (isinstance(b, float) and math.isnan(b))
    if na or nb:
        return na and nb
    return bool(a == b)


def _extra(raw: Any) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) and raw else {}
    return {k: v for k, v in data.items() if "elapsed" not in k and "time" not in k}


def compare_detections(old: pd.DataFrame, new: pd.DataFrame) -> list[str]:
    """Differenze fra due tabelle di rilevazione degli stessi campioni (vuota se identiche)."""
    out: list[str] = []
    a, b = old.set_index("sample_id"), new.set_index("sample_id")
    if set(a.index) != set(b.index):
        only_old, only_new = set(a.index) - set(b.index), set(b.index) - set(a.index)
        out.append(f"different samples: {len(only_old)} only old, {len(only_new)} only new")
        return out
    for sid in sorted(a.index):
        ra, rb = a.loc[sid], b.loc[sid]
        diff = [c for c in COLUMNS if c in a.columns and not _same(ra[c], rb[c])]
        if "extra_json" in a.columns and _extra(ra["extra_json"]) != _extra(rb["extra_json"]):
            diff.append("extra_json")
        if diff:
            out.append(f"{sid}: " + ", ".join(f"{c} {ra[c]!r} -> {rb[c]!r}" if c != "extra_json"
                                               else c for c in diff))  # fmt: skip
    return out


def compare_thresholds(old: Any, new: Any) -> list[str]:
    """Differenze fra due ``ThresholdSet`` sui campi di ``THRESHOLD_FIELDS``."""
    return [
        f"{f}: {getattr(old, f)!r} -> {getattr(new, f)!r}"
        for f in THRESHOLD_FIELDS
        if not _same(getattr(old, f), getattr(new, f))
    ]


def compare_model(store: Any, args: Any, model: str) -> int:
    """Confronto di un modello (negativi e soglia); stampa l'esito, restituisce le differenze."""
    from bench.domain.models import ThresholdSet
    from bench.pipeline.stages.calibrate import threshold_ref
    from bench.pipeline.stages.detect import detection_ref

    failures = 0
    for subject, level in SUBJECTS:
        if level not in args.levels:
            continue
        refs = [detection_ref(args.method, model, h, subject, level, args.language, args.split)
                for h in (args.old, args.new)]  # fmt: skip
        missing = [h for h, r in zip((args.old, args.new), refs, strict=True)
                   if not store.exists(r)]  # fmt: skip
        if missing:
            print(f"[DIFF] {model} {subject} {level}: missing detection for {missing}")
            failures += 1
            continue
        old_t, new_t = store.read_table(refs[0]), store.read_table(refs[1])
        diffs = compare_detections(old_t, new_t)
        if diffs:
            failures += 1
            print(f"[DIFF] {model} {subject} {level}: {len(diffs)} differences out of {len(old_t)}")
            for d in diffs[:MAX_SHOWN]:
                print(f"         {d}")
        else:
            print(f"[SAME] {model} {subject} {level}: {len(old_t)} samples identical")
    trefs = [threshold_ref(args.method, model, args.language, h, args.levels)
             for h in (args.old, args.new)]  # fmt: skip
    if not all(store.exists(r) for r in trefs):
        print(f"[DIFF] {model} threshold: missing ({[str(r.path) for r in trefs]})")
        return failures + 1
    old_s, new_s = (store.read_model(r, ThresholdSet) for r in trefs)
    tdiff = compare_thresholds(old_s, new_s)
    if tdiff:
        print(f"[DIFF] {model} threshold: " + "; ".join(tdiff))
        return failures + 1
    print(f"[SAME] {model} threshold {new_s.threshold!r}, secondary "
          f"{json.dumps(new_s.secondary, sort_keys=True)}")  # fmt: skip
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--method", default="mcss")
    p.add_argument("--old", required=True, help="config_hash already calibrated")
    p.add_argument("--new", required=True, help="config_hash to check")
    p.add_argument("--models", nargs="+", help="default: models of the configuration")
    p.add_argument("--levels", nargs="+", default=["L1", "L2"], help="calibration levels")
    p.add_argument("--language", default="python")
    p.add_argument("--split", default="dev")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)

    from bench.config.builder import load_experiment
    from bench.store.artifact_store import ArtifactStore

    paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
    cfg = load_experiment([*paths, "stage=calibrate", *args.overrides])
    store = ArtifactStore(Path(cfg.paths.artifacts))
    print(f"# compare {args.method} {args.old} (old) vs {args.new} (new): negatives and thresholds")
    failures = sum(compare_model(store, args, m) for m in (args.models or list(cfg.models)))
    if failures:
        print(f"[STOP] {failures} differences: the thresholds of {args.new} are not those of "
              f"{args.old}", file=sys.stderr)  # fmt: skip
        return 1
    print("[OK] negatives and thresholds identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
