"""Riepilogo della Milestone 7: soglie e metriche con IC per metodo, modello e linguaggio.

Uso (con bench-core):
    python scripts/report_metrics.py [--artifacts DIR] [--level L1] [--split dev] [--method M]
                                     [--final] [--config HASH [HASH ...]]

Per ogni cella stampa la soglia (con FPR raggiunto sui negativi di sviluppo, numerosità e le
bandiere ``provisional``, ``underpowered`` e ``unreachable``), le soglie dei punteggi secondari e
tutte le metriche con il loro intervallo; poi il tasso di inserimento riuscito e i siti idonei dei
campioni marcati (manifest della fase watermark, TODO 15).

Con ``--final`` legge le metriche con le soglie definitive su L1+L2 (``metrics_L1L2/`` e
``thresholds/.../L1L2/``), come sulla parte di test della M10 (ADR-013).

Con ``--config`` si stampano solo le celle di quelle configurazioni (per esempio quelle del
secondo giro di P2, con la libreria nuova: ``scripts/posthoc_config_hashes.py``). Una
configurazione con la rilevazione ma senza le metriche (un punto dello sweep di
``configs/hpo/<metodo>.yaml``: la fase ``metrics`` non si espande per configurazione) si stampa
lo stesso, con la soglia calibrata e le righe a soglia fissa calcolate dalla rilevazione.

**Soglia teorica fissa (pipeline di multimark v1.3.0).** Per ``mcss`` e ``multimark`` il
punteggio principale è ``-log10 p`` con ``p`` esatto, quindi oltre alla soglia calibrata del
benchmark (che resta la regola, per la comparabilità) si riporta la soglia fissa ``score >= 2``
(p ≤ 0,01), fissata prima di ogni risultato sul test: TPR sui positivi, FPR sul codice umano
(``neg_human``) e sul codice LLM senza watermark (``neg_llm``), per livello (ogni cella è un
livello). Stessa decisione della fase ``metrics`` (``bench.metrics.calibration.detected``:
rilevazione riuscita, inserimento non ``FAILED``, ``score >= soglia``); intervalli di
Clopper-Pearson per campione (senza il bootstrap per problema della fase ``metrics``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
# Soglia teorica fissa: score = -log10 p >= 2, cioè p <= 0,01 (pipeline di multimark v1.3.0).
THEORETICAL_THRESHOLD = 2.0
THEORETICAL_METHODS = ("mcss", "multimark")
# (nome della riga, soggetto della rilevazione)
FIXED_SUBJECTS = (("tpr", "positives"), ("fpr_human", "neg_human"), ("fpr_llm", "neg_llm"))


def _manifest(path: Path) -> dict:  # type: ignore[type-arg]
    file = Path(f"{path}.manifest.json")
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}


def _fmt(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def rate_at(table: pd.DataFrame, threshold: float) -> tuple[int, int]:
    """(rilevati, campioni) alla soglia, con la decisione della fase ``metrics``."""
    from bench.metrics.calibration import detected

    embed = table["embed_status"] if "embed_status" in table else pd.Series([None] * len(table))
    hits = sum(
        detected(
            None if pd.isna(s) else float(s),
            str(st),
            None if pd.isna(e) else str(e),
            threshold,
        )
        for s, st, e in zip(
            pd.to_numeric(table["score"], errors="coerce"), table["status"], embed, strict=True
        )
    )
    return int(hits), len(table)


def fixed_lines(
    root: Path, meth: str, model: str, cfg_hash: str, stem: str, threshold: float, label: str
) -> list[str]:
    """Righe TPR, FPR umano e FPR LLM della cella alla soglia ``threshold``."""
    from bench.metrics.stats import clopper_pearson

    lines = [f"  {label}"]
    for name, subject in FIXED_SUBJECTS:
        path = root / "detection" / meth / model / cfg_hash / subject / f"{stem}.parquet"
        if not path.is_file():
            lines.append(f"    {name:<30} -  (no {subject} detection)")
            continue
        k, n = rate_at(pd.read_parquet(path), threshold)
        low, high = clopper_pearson(k, n)
        value = k / n if n else float("nan")
        lines.append(
            f"    {name:<30} {_fmt(value):>9}  [{_fmt(low)}, {_fmt(high)}]"
            f"  {k}/{n}  clopper_pearson"
        )
    return lines


def _threshold(threshold_file: Path) -> dict[str, Any] | None:
    if not threshold_file.is_file():
        return None
    data: dict[str, Any] = json.loads(threshold_file.read_text(encoding="utf-8"))
    return data


def _threshold_lines(t: dict[str, Any]) -> list[str]:
    flags = [k for k in ("provisional", "underpowered") if t.get(k)]
    if t.get("threshold") == "Infinity":
        flags.append("unreachable")
    lines = [
        f"threshold {t['threshold']}  achieved FPR dev {_fmt(t['achieved_fpr_dev'])}"
        f"  n_neg {t['n_negatives']}  levels {t.get('levels')}  {' '.join(flags)}"
    ]
    for name, sec in (t.get("secondary") or {}).items():
        lines.append(
            f"  secondary {name}: threshold {sec['threshold']}"
            f"  achieved FPR dev {_fmt(sec['achieved_fpr_dev'])}"
        )
    return lines


def _write(lines: Sequence[str]) -> None:
    sys.stdout.write("".join(f"{line}\n" for line in lines))


def find_cells(
    root: Path, level: str, split: str, method: str | None, final: bool,
    configs: Sequence[str] | None,
) -> list[tuple[Path, str, str, str, bool]]:  # fmt: skip
    """(percorso, metodo, modello, hash, punto dello sweep) delle celle da stampare.

    Le celle con le metriche prima; con ``configs`` anche i punti dello sweep (rilevazione dei
    positivi senza metriche), con il percorso della rilevazione."""
    pattern = f"{method or '*'}/*/*/{level}_*_{split}.parquet"
    folder = root / ("metrics_L1L2" if final else "metrics")
    paths = sorted(folder.glob(pattern))
    if configs is not None:
        paths = [p for p in paths if p.parts[-2] in configs]
    cells = [(p, p.parts[-4], p.parts[-3], p.parts[-2], False) for p in paths]
    if configs is not None:
        seen = {(m, mo, h, p.stem) for p, m, mo, h, _ in cells}
        detection = root / "detection"
        for p in sorted(detection.glob(f"{method or '*'}/*/*/positives/{level}_*_{split}.parquet")):
            meth, model, cfg_hash = p.parts[-5], p.parts[-4], p.parts[-3]
            if cfg_hash in configs and (meth, model, cfg_hash, p.stem) not in seen:
                cells.append((p, meth, model, cfg_hash, True))
    return cells


def cell_lines(
    root: Path, path: Path, meth: str, model: str, cfg_hash: str, is_sweep: bool, final: bool
) -> list[str]:
    """Righe di una cella (vedi docstring del modulo)."""
    level, language, split = path.stem.split("_")
    note = "; no metrics: sweep point, rates from the detection" if is_sweep else ""
    lines = ["", f"##### {meth} {model} {language} ({level} {split}, config {cfg_hash}{note})"]
    threshold_dir = root / "thresholds" / meth / model / language
    t = _threshold((threshold_dir / "L1L2" if final else threshold_dir) / f"{cfg_hash}.json")
    if t is not None:
        lines += _threshold_lines(t)
    if is_sweep:
        if t is not None and t.get("threshold") not in (None, "Infinity"):
            tau = float(t["threshold"])
            label = f"calibrated threshold {tau:.6g}:"
            lines += fixed_lines(root, meth, model, cfg_hash, path.stem, tau, label)
    else:
        if _manifest(path).get("extra", {}).get("not_applicable"):
            return [*lines, "NOT_APPLICABLE"]
        for row in pd.read_parquet(path).itertuples():
            lines.append(
                f"  {row.name:<32} {_fmt(row.value):>9}  [{_fmt(row.ci_low)}, "
                f"{_fmt(row.ci_high)}]  n={row.n}  {row.ci_method}"
            )
    if meth in THEORETICAL_METHODS:
        label = (
            f"theoretical threshold score >= {THEORETICAL_THRESHOLD:g} (p <= 0.01, "
            "fixed; pipeline v1.3.0):"
        )
        lines += fixed_lines(root, meth, model, cfg_hash, path.stem, THEORETICAL_THRESHOLD, label)
    marked = root / "watermarked" / meth / model / cfg_hash / f"{path.stem}.parquet"
    mextra = _manifest(marked).get("extra", {})
    if mextra:
        lines.append(
            f"  embed_success_rate {_fmt(mextra.get('embed_success_rate'))}"
            f"  n_sites {json.dumps(mextra.get('n_sites'))}"
        )
    return lines


def report(
    root: Path,
    level: str,
    split: str,
    method: str | None,
    final: bool = False,
    configs: Sequence[str] | None = None,
) -> None:
    cells = find_cells(root, level, split, method, final, configs)
    if not cells:
        folder = root / ("metrics_L1L2" if final else "metrics")
        sys.stdout.write(
            f"no metrics under {folder} for {method or '*'}/*/*/{level}_*_{split}.parquet\n"
        )
        return
    for path, meth, model, cfg_hash, is_sweep in cells:
        _write(cell_lines(root, path, meth, model, cfg_hash, is_sweep, final))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--level", default="L1")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--method", default=None)
    parser.add_argument("--final", action="store_true", help="soglie definitive su L1+L2")
    parser.add_argument("--config", nargs="+", help="solo queste configurazioni (config_hash)")
    args = parser.parse_args(argv)
    report(Path(args.artifacts), args.level, args.split, args.method, args.final, args.config)


if __name__ == "__main__":
    main()
