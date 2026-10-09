"""Riepilogo della Milestone 7: soglie e metriche con IC per metodo, modello e linguaggio.

Uso (con bench-core):
    python scripts/report_metrics.py [--artifacts DIR] [--level L1] [--split dev] [--method M]

Per ogni cella stampa la soglia (con FPR raggiunto sui negativi di sviluppo, numerosità e le
bandiere ``provisional``, ``underpowered`` e ``unreachable``), le soglie dei punteggi secondari e
tutte le metriche con il loro intervallo; poi il tasso di inserimento riuscito e i siti idonei dei
campioni marcati (manifest della fase watermark, TODO 15).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"


def _manifest(path: Path) -> dict:  # type: ignore[type-arg]
    file = Path(f"{path}.manifest.json")
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}


def _fmt(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def report(root: Path, level: str, split: str, method: str | None) -> None:
    pattern = f"{method or '*'}/*/*/{level}_*_{split}.parquet"
    paths = sorted((root / "metrics").glob(pattern))
    if not paths:
        sys.stdout.write(f"no metrics under {root / 'metrics'} for {pattern}\n")
        return
    for path in paths:
        meth, model, cfg_hash = path.parts[-4:-1]
        language = path.stem.split("_")[1]
        extra = _manifest(path).get("extra", {})
        sys.stdout.write(
            f"\n##### {meth} {model} {language} ({level} {split}, config {cfg_hash})\n"
        )
        threshold_file = root / "thresholds" / meth / model / language / f"{cfg_hash}.json"
        if threshold_file.is_file():
            t = json.loads(threshold_file.read_text(encoding="utf-8"))
            flags = [k for k in ("provisional", "underpowered") if t.get(k)]
            if t.get("threshold") == "Infinity":
                flags.append("unreachable")
            sys.stdout.write(
                f"threshold {t['threshold']}  achieved FPR dev {_fmt(t['achieved_fpr_dev'])}"
                f"  n_neg {t['n_negatives']}  levels {t.get('levels')}  {' '.join(flags)}\n"
            )
            for name, sec in (t.get("secondary") or {}).items():
                sys.stdout.write(
                    f"  secondary {name}: threshold {sec['threshold']}"
                    f"  achieved FPR dev {_fmt(sec['achieved_fpr_dev'])}\n"
                )
        if extra.get("not_applicable"):
            sys.stdout.write("NOT_APPLICABLE\n")
            continue
        df = pd.read_parquet(path)
        for row in df.itertuples():
            sys.stdout.write(
                f"  {row.name:<32} {_fmt(row.value):>9}  [{_fmt(row.ci_low)}, {_fmt(row.ci_high)}]"
                f"  n={row.n}  {row.ci_method}\n"
            )
        marked = (
            root / "watermarked" / meth / model / cfg_hash / f"{level}_{language}_{split}.parquet"
        )
        mextra = _manifest(marked).get("extra", {})
        if mextra:
            sys.stdout.write(
                f"  embed_success_rate {_fmt(mextra.get('embed_success_rate'))}"
                f"  n_sites {json.dumps(mextra.get('n_sites'))}\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--level", default="L1")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--method", default=None)
    args = parser.parse_args()
    report(Path(args.artifacts), args.level, args.split, args.method)


if __name__ == "__main__":
    main()
