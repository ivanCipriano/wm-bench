"""Giro P3 su sviluppo (pipeline di multimark v1.3.13): proponente neurale dei nomi (MD31).

Stesso report del giro 4 (``scripts/report_round4.py``) su tutte le configurazioni passate con
``--config metodo:hash[:etichetta]``: i 2 punti di P3 (B e D con ``names.proposer=neural``), i 12
del giro 4, le 6 configurazioni di ``multimark`` del giro di capacità e, come riferimento, le due
``mcss``. Per ogni configurazione, su L1 e L2 dev e per ogni modello:

1. vincoli, AUROC di ``adversarial_logreg`` e rinomine incoerenti per cella (``Round4.lines``);
2. le **regole 1 e 2 di v1.3.10** (``Round4.ranking_section``) calcolate su tutti i punti di
   ``multimark`` misurati (v1.3.13: scelta fra tutti i punti); ``mcss`` resta il riferimento;
3. ripiego di ``names.suffix_by_type`` e siti medi (``Round4.extras_section``);
4. **ripiego del proponente neurale** (MD31): per cella, somma di ``n_names_neural_fallback``
   (nomi per cui nessuno dei 16 candidati del modello aveva i bit giusti, scelti fra i candidati
   statici) negli ``extra`` dell'inserimento, campioni con almeno un ripiego e quota sui nomi
   rinominati (``n_renamed``); solo le configurazioni con il campo (proponente neurale);
5. **tempo di inserimento per campione** (``elapsed_s`` del worker, in secondi): media, mediana,
   90° percentile e totale per cella, per ogni configurazione. Per i punti neurali il caricamento
   del modello è escluso (lo shim lo fa in ``setup``) e si riportano anche i lotti di prompt letti
   dalla cache su disco dello shim e quelli generati dal modello (``n_neural_batches_cached``,
   ``n_neural_batches_generated``) e la media sui soli campioni con almeno un lotto generato: le
   celle di D seguono quelle di B nello stesso job e leggono dalla cache le risposte per gli
   stessi codici, quindi il loro tempo medio non è il costo del modello. I punti statici sono
   girati su CPU.

Rinomine incoerenti: ``multimark.rename_check`` (``scripts/check_rename_dev.py``) dalla copia
``--multimark`` (commit che deve cominciare con ``--expect-commit``; ``rename_check.py`` è uguale a
quello di 0664817).

Gira in **bench-core**, solo lettura, solo sviluppo::

    python scripts/report_p3.py --config mcss:<hash>:mcss_new ... \\
        --config multimark:<hash>:p3_B ... [--base multimark] [--mcss mcss_new] \\
        [--multimark DIR] [--expect-commit SHA] [--levels L1 L2] [--report FILE] [override ...]

``--report`` non deve esistere (i log non si sovrascrivono); ``--split test`` non è ammesso.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import statistics
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
r3, crd, rad = r4.r3, r4.crd, r4.rad
Config = rad.Config
_v, _pct = rad._v, r4._pct

P3_COMMIT = "b2b6c18"
NEURAL_FIELD = "n_names_neural_fallback"


# ---------------------------------------------------------------------- estrazioni per cella
def embed_rows(run_dir: Path) -> list[dict[str, Any]]:
    """(stato, ``elapsed_s``, extra) dell'inserimento per campione, da ``results.jsonl``."""
    path = run_dir / "results.jsonl"
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                rows.append({**(row.get("extra") or {}), "status": row.get("status"),
                             "elapsed_s": row.get("elapsed_s")})  # fmt: skip
    return rows


def neural_stats(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Ripiego del proponente neurale: ``n`` campioni, ``with_field``, ``total`` ripieghi,
    ``samples`` con almeno un ripiego, ``renamed`` e ``share`` = total / renamed (NaN senza campo:
    proponente statico)."""
    with_field = [r for r in rows if NEURAL_FIELD in r]
    total = sum(int(r.get(NEURAL_FIELD) or 0) for r in with_field)
    renamed = sum(int(r.get("n_renamed") or 0) for r in with_field)
    have = bool(with_field)
    return {
        "n": float(len(rows)),
        "with_field": float(len(with_field)),
        "total": float(total) if have else math.nan,
        "samples": float(sum(1 for r in with_field if (r.get(NEURAL_FIELD) or 0) > 0))
        if have else math.nan,
        "renamed": float(renamed) if have else math.nan,
        "share": total / renamed if have and renamed else math.nan,
    }  # fmt: skip


def _percentile(vals: Sequence[float], q: float) -> float:
    """Percentile con interpolazione lineare (``q`` in [0, 1]); NaN se vuoto."""
    if not vals:
        return math.nan
    s = sorted(vals)
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def time_stats(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Tempo di inserimento per campione (secondi) e lotti del modello dei nomi (sezione 5)."""
    times = [float(r["elapsed_s"]) for r in rows if r.get("elapsed_s") is not None]
    neural = [r for r in rows if "n_neural_batches_generated" in r]
    generated = [
        float(r["elapsed_s"])
        for r in neural
        if r.get("elapsed_s") is not None and int(r["n_neural_batches_generated"]) > 0
    ]
    return {
        "n": float(len(times)),
        "mean": sum(times) / len(times) if times else math.nan,
        "median": statistics.median(times) if times else math.nan,
        "p90": _percentile(times, 0.9),
        "total": sum(times) if times else math.nan,
        "neural": float(len(neural)),
        "cached": float(sum(int(r.get("n_neural_batches_cached") or 0) for r in neural))
        if neural else math.nan,
        "generated": float(sum(int(r["n_neural_batches_generated"]) for r in neural))
        if neural else math.nan,
        "n_gen": float(len(generated)),
        "mean_gen": sum(generated) / len(generated) if generated else math.nan,
    }  # fmt: skip


# ---------------------------------------------------------------------- report
class P3(r4.Round4):  # type: ignore[name-defined,misc]
    """Il report del giro 4 più ripiego neurale e tempi di inserimento (sezioni 4 e 5)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.neural: dict[str, dict[str, dict[str, float]]] = {c.name: {} for c in self.configs}
        self.times: dict[str, dict[str, dict[str, float]]] = {c.name: {} for c in self.configs}

    def collect(self, models: Sequence[str], levels: Sequence[str], lang: str, split: str) -> None:
        super().collect(models, levels, lang, split)
        for model in models:
            for level in levels:
                cell = f"{model} {level}"
                for c in self.configs:
                    rows = embed_rows(self.r3.run_dir(c, model, level, lang, split))
                    self.neural[c.name][cell] = neural_stats(rows)
                    self.times[c.name][cell] = time_stats(rows)

    def neural_section(self) -> list[str]:
        out = [f"## neural proposer fallback to the static candidates ({NEURAL_FIELD})",
               "  " + f"{'config':<34}{'cell':<30}{'samples':>8}{'fallback':>10}"
               f"{'smp>=1':>8}{'renamed':>9}{'share':>9}"]  # fmt: skip
        for c in self.configs:
            for cell in self.cells:
                f = self.neural[c.name].get(cell, {})
                if not f or not f.get("with_field"):
                    continue
                widths = (("n", 8), ("total", 10), ("samples", 8), ("renamed", 9))
                counts = "".join(f"{int(f[k]):>{w}}" for k, w in widths)
                out.append(f"  {c.name:<34}{cell:<30}{counts}{_pct(f['share']):>9}")
        out.append("  (configurations with the static proposer: field absent, not listed)")
        return [*out, ""]

    def time_section(self) -> list[str]:
        out = ["## embedding time per sample (worker elapsed_s, seconds; neural points: model "
               "loading excluded, batches of prompts read from the disk cache / generated)",
               "  " + f"{'config':<34}{'cell':<30}{'samples':>8}{'mean':>9}{'median':>9}"
               f"{'p90':>9}{'total':>10}{'cached':>8}{'gen':>7}{'smp_gen':>8}"
               f"{'mean_gen':>9}"]  # fmt: skip
        for c in self.configs:
            for cell in self.cells:
                t = self.times[c.name].get(cell, {})
                if not t or not t.get("n"):
                    out.append(f"  {c.name:<34}{cell:<30}{'no embedding results':>8}")
                    continue
                if t["neural"]:
                    extra = (f"{int(t['cached']):>8}{int(t['generated']):>7}{int(t['n_gen']):>8}"
                             f"{_v(t['mean_gen']):>9}")  # fmt: skip
                else:
                    extra = f"{'-':>8}{'-':>7}{'-':>8}{'-':>9}"
                out.append(f"  {c.name:<34}{cell:<30}{int(t['n']):>8}{_v(t['mean']):>9}"
                           f"{_v(t['median']):>9}{_v(t['p90']):>9}{t['total']:>10.1f}"
                           f"{extra}")  # fmt: skip
        out.append("  (static points ran on CPU; D cells follow B cells in the same GPU job and "
                   "read the disk cache for the same codes: their mean is not the model "
                   "cost)")  # fmt: skip
        return [*out, ""]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--config", type=Config.parse, action="append", required=True,
                   help="method:hash[:label], repeatable")  # fmt: skip
    p.add_argument("--base", default="multimark", help="label of the base for rule 1")
    p.add_argument("--mcss", default="mcss_new_3c47a1f", help="label of mcss for rule 2")
    p.add_argument("--multimark", type=Path, default=crd.DEFAULT_MULTIMARK,
                   help="copy of the MultiMark library with multimark/rename_check.py")  # fmt: skip
    p.add_argument("--expect-commit", default=P3_COMMIT,
                   help="required prefix of the commit of the copy ('' = any)")  # fmt: skip
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
    by_label = {c.label: c for c in args.config}
    for flag, label in (("--base", args.base), ("--mcss", args.mcss)):
        if label not in by_label:
            print(f"[ERROR] {flag} {label}: not among the labels {list(by_label)}", file=sys.stderr)
            return 1
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
    report = P3(rep, args.config, check, args.language)
    report.collect(args.models or list(cfg.models), args.levels, args.language, args.split)
    points = report.r3.points
    base, mcss = by_label[args.base], by_label[args.mcss]
    n_mm = sum(1 for c in args.config if c.method == "multimark")
    lines = [
        "# P3 on development, neural names proposer, the two rules of v1.3.10 over all measured "
        f"points (scripts/report_p3.py), "
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}, {socket.gethostname()}",
        f"# configs: {', '.join(c.name for c in args.config)}",
        f"# base {base.name}, mcss {mcss.name}; ranked: the {n_mm} multimark configurations",
        f"# thresholds of levels {' '.join(t_levels)} and theoretical score >= "
        f"{r3.THEORETICAL_THRESHOLD:g}; split {args.split}, language {args.language}",
        f"# S = {r3.NO_ATTACK}; " + "; ".join(f"{q.label} [{q.params_hash}]" for q in points),
        f"# renaming check: multimark.rename_check from {args.multimark} at commit {commit}",
        "",
        *report.lines(base, mcss),
        *report.ranking_section(base, mcss),
        *report.extras_section(),
        *report.neural_section(),
        *report.time_section(),
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
