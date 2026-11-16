"""Retrocompatibilità delle modifiche comuni di P2 sulle celle reali (ADR-017).

Per ogni parquet di metriche già calcolato sotto ``<artifacts>/metrics*/`` dei metodi esistenti
(senza attacco e con attacco) lo script ricalcola le metriche della cella con il codice attuale
(``MetricsStage.run`` con la scrittura sostituita da una raccolta in memoria: **nessun file
d'artefatto viene toccato**) e le confronta riga per riga con il parquet: valore, ``ci_low``,
``ci_high`` a tolleranza 1e-9, ``n`` e ``ci_method`` identici.

Sono ammesse solo righe **nuove** delle modifiche comuni: ``message_accuracy_detected`` e
``wrong_attribution_rate_*`` dappertutto, la capacità (``message_accuracy``, ``bit_accuracy``,
``embedding_success_rate``) nelle celle con attacco dei metodi multi-bit. Una metrica che cambia o
sparisce è un errore (exit code 1).

**Cella dal manifest.** Il seme di ogni intervallo bootstrap è ``bootstrap_seed(global_seed,
metrica, cell.key())``, e la chiave contiene solo i campi valorizzati della cella. Le celle con la
configurazione di default sono state calcolate con ``config_hash = None`` (lo hash compare nel
percorso ma non nella cella); quelle dello sweep o del test con lo hash. Per riprodurre gli
intervalli la cella si ricostruisce dal manifest del parquet (``<file>.manifest.json``, campo
``cell``), mai dal percorso: con lo hash del percorso cambiano tutti i semi e quindi ``ci_low``
e ``ci_high`` (primo tentativo sul cluster, 16 novembre 2026). Il percorso serve solo a
controllare che il manifest sia della stessa cella; anche ``detection.n_bootstrap`` deve essere
quello del manifest.

Nota: il parquet è stato scritto dal codice del momento in cui la cella è stata calcolata. Una
differenza può quindi venire anche da una modifica precedente a P2; in quel caso si rilancia lo
stesso controllo dal commit di prima delle modifiche comuni (``955ab01``) per attribuirla.

Gira in **bench-core**, solo CPU. Uso::

    python scripts/check_metrics_compat.py [--methods mcgmark stone ...] [--max-cells N] \\
        --report docs/milestone_logs/P2_metrics_compat.txt [override Hydra ...]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

EXISTING = ("sweet", "acw", "stone", "promptmark", "mcgmark")
TOL = 1e-9
NEW_METRICS = re.compile(r"^(message_accuracy_detected|wrong_attribution_rate_\w+)$")
NEW_ATTACKED = re.compile(r"^(message_accuracy|bit_accuracy|embedding_success_rate)$")
FILE_RE = re.compile(r"^(L\d)_([a-z]+)_(dev|test)$")
PLAIN_PARTS = 5  # metrics*/<metodo>/<modello>/<cfg>/<file>
ATTACKED_PARTS = 8  # metrics*/attacked/<attacco>/<params>/<metodo>/<modello>/<cfg>/<file>


def parse_path(rel: Path) -> dict[str, Any] | None:
    """Cella di un parquet di metriche dal percorso relativo agli artefatti (``None`` se non è
    una cella riconoscibile)."""
    parts = rel.parts
    m = FILE_RE.match(rel.stem)
    if m is None or not parts[0].startswith("metrics"):
        return None
    tag = parts[0][len("metrics") :].lstrip("_")
    levels = ["L1"] if not tag else re.findall(r"L\d", tag)
    head: dict[str, Any] = {"levels": levels, "attack_id": None, "attack_params_hash": None}
    if len(parts) == ATTACKED_PARTS and parts[1] == "attacked":
        head.update(attack_id=parts[2], attack_params_hash=parts[3])
        method, model, cfg_hash = parts[4:7]
    elif len(parts) == PLAIN_PARTS:
        method, model, cfg_hash = parts[1:4]
    else:
        return None
    level, language, split = m.groups()
    return {**head, "method": method, "model_id": model, "config_hash": cfg_hash,
            "level": level, "language": language, "split": split}  # fmt: skip


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _same(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is None or fb is None:
        return fa is None and fb is None
    return abs(fa - fb) <= TOL or (math.isinf(fa) and fa == fb)


def compare(old: pd.DataFrame, new: list[Any], attacked: bool) -> tuple[list[str], list[str]]:
    """``(differenze, righe nuove)`` fra il parquet e le metriche ricalcolate."""
    now = {r.name: r for r in new}
    diffs: list[str] = []
    for rec in old.to_dict(orient="records"):
        name = str(rec["name"])
        if name not in now:
            diffs.append(f"{name}: missing")
            continue
        r = now[name]
        for field, a, b in (("value", rec["value"], r.value), ("ci_low", rec["ci_low"], r.ci_low),
                            ("ci_high", rec["ci_high"], r.ci_high)):  # fmt: skip
            if not _same(a, b):
                diffs.append(f"{name}.{field}: {a!r} -> {b!r}")
        n_old = None if pd.isna(rec["n"]) else int(rec["n"])
        if n_old != r.n:
            diffs.append(f"{name}.n: {n_old} -> {r.n}")
        if str(rec["ci_method"]) != str(r.ci_method):
            diffs.append(f"{name}.ci_method: {rec['ci_method']} -> {r.ci_method}")
    allowed = [NEW_METRICS, NEW_ATTACKED] if attacked else [NEW_METRICS]
    added = sorted(set(now) - set(old["name"]))
    diffs += [f"{n}: unexpected new metric" for n in added if not any(p.match(n) for p in allowed)]
    return diffs, added


class CellError(RuntimeError):
    """Manifest assente o di un'altra cella, ``n_bootstrap`` diverso."""


CELL_FIELDS = ("method", "model_id", "level", "language", "split", "attack_id",
               "attack_params_hash")  # fmt: skip


def manifest_cell(path: Path, info: dict[str, Any], n_bootstrap: int) -> Any:
    """Cella esatta con cui la fase ``metrics`` ha scritto ``path`` (campo ``cell`` del manifest).

    Raises:
        CellError: manifest mancante o illeggibile, campi diversi dal percorso, ``config_hash``
            diverso da quello del percorso (se valorizzato), ``n_bootstrap`` diverso.
    """
    from bench.pipeline.stage import Cell

    mpath = path.with_name(path.name + ".manifest.json")
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
        cell_data = dict(data["cell"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CellError(f"no usable manifest {mpath.name}: {exc}") from exc
    for field in CELL_FIELDS:
        if cell_data.get(field) != info[field]:
            raise CellError(f"manifest cell {field}={cell_data.get(field)!r}, path {info[field]!r}")
    if cell_data.get("config_hash") not in (None, info["config_hash"]):
        raise CellError(
            f"manifest config_hash {cell_data['config_hash']}, path {info['config_hash']}"
        )
    recorded = ((data.get("config") or {}).get("detection") or {}).get("n_bootstrap")
    if recorded is not None and int(recorded) != n_bootstrap:
        raise CellError(f"n_bootstrap {recorded} in the manifest, {n_bootstrap} now")
    return Cell(**cell_data)


def recompute(cfg: Any, cell: Any) -> list[Any]:
    """Metriche della cella ricalcolate in memoria (``MetricsStage._write`` sostituito)."""
    from bench.pipeline.facade import BenchmarkFacade
    from bench.pipeline.stage import StageContext
    from bench.pipeline.stages.metrics import MetricsStage

    captured: list[Any] = []

    def collect(ctx: Any, c: Any, rows: list[Any], *args: Any, **kwargs: Any) -> None:
        captured.extend(rows)

    facade = BenchmarkFacade(cfg)
    stage = MetricsStage(cfg)
    stage._write = collect  # type: ignore[method-assign,assignment]
    stage.run(cell, StageContext(cfg, facade.store, facade.provenance, "metrics", cell))
    return captured


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0915
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--methods", nargs="+", default=list(EXISTING), choices=EXISTING)
    p.add_argument("--max-cells", type=int, default=None, help="stop after N cells (trial run)")
    p.add_argument("--report", type=Path, required=True, help="log file (must not exist)")
    p.add_argument("overrides", nargs="*", help="Hydra overrides (default paths=cluster)")
    args = p.parse_args(argv)
    if args.report.exists():
        print(f"[ERROR] {args.report} already exists (logs are never overwritten)", file=sys.stderr)
        return 1

    import bench
    from bench.config.builder import load_experiment

    configs: dict[tuple[str, ...], Any] = {}

    def config(levels: list[str]) -> Any:
        key = tuple(levels)
        if key not in configs:
            over = [f"detection.threshold_levels=[{','.join(levels)}]"]
            paths = [] if any(o.startswith("paths=") for o in args.overrides) else ["paths=cluster"]
            configs[key] = load_experiment([*paths, "stage=metrics", *over, *args.overrides])
        return configs[key]

    root = Path(config(["L1"]).paths.artifacts)
    paths = sorted(p for folder in root.glob("metrics*") for p in folder.rglob("*.parquet"))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=False).stdout.strip()  # fmt: skip
    lines = [
        "# P2 common changes: metrics of existing methods recomputed vs metrics*/ parquet "
        "(scripts/check_metrics_compat.py, ADR-017)",
        f"# date {datetime.now().astimezone().isoformat(timespec='seconds')}, "
        f"host {socket.gethostname()}, wm-bench {commit}",
        f"# artifacts {root}; methods {' '.join(args.methods)}; tolerance {TOL}",
        f"# bench from {Path(bench.__file__).resolve().parent}; cells from the parquet manifests",
        "",
    ]
    counts = {"identical": 0, "with_new_rows": 0, "different": 0, "error": 0, "skipped": 0}
    n_cells = 0
    start = time.time()
    for path in paths:
        info = parse_path(path.relative_to(root))
        if info is None or info["method"] not in args.methods:
            counts["skipped"] += info is not None
            continue
        if args.max_cells is not None and n_cells >= args.max_cells:
            break
        n_cells += 1
        rel = path.relative_to(root).as_posix()
        try:
            cfg = config(info["levels"])
            cell = manifest_cell(path, info, int(cfg.detection.n_bootstrap))
            captured = recompute(cfg, cell)
            old = pd.read_parquet(path)
            diffs, added = compare(old, captured, info["attack_id"] is not None)
        except Exception as exc:  # una cella che non si ricalcola si conta e si mostra
            counts["error"] += 1
            last = traceback.format_exc().strip().splitlines()[-1]
            lines.append(f"[ERROR] {rel}: {type(exc).__name__}: {last}")
            continue
        if diffs:
            counts["different"] += 1
            lines.append(f"[DIFF] {rel}")
            lines += [f"    {d}" for d in diffs]
        elif added:
            counts["with_new_rows"] += 1
            lines.append(f"[OK+] {rel}: {len(old)} rows identical; new: {', '.join(added)}")
        else:
            counts["identical"] += 1
            lines.append(f"[OK] {rel}: {len(old)} rows identical")
    lines += [
        "",
        f"cells compared: {n_cells} in {time.time() - start:.0f} s",
        *(f"  {k}: {v}" for k, v in counts.items()),
        "outcome: " + ("PASS" if counts["different"] == 0 and counts["error"] == 0 else "FAIL"),
    ]
    text = "\n".join(lines) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(text)
    return 0 if counts["different"] == 0 and counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
