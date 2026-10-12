"""Stima del costo dell'HPO (Milestone 8) dai tempi misurati in M7, più il rifacimento in M9.

Uso (con bench-core, nodo di login, nessun job):
    python scripts/estimate_hpo_cost.py [--artifacts DIR] [--no-sacct]

Costi unitari misurati sugli artefatti della M7 (configurazione di default):
- generazione e rilevazione: ``elapsed_s`` per item nei ``results.jsonl`` dei worker (``_runs/``),
  completi anche dopo le riprese; avvio del worker da ``worker.log``;
- esecuzione dei test: ``_timing/timings.jsonl`` (fase ``execute``, campioni marcati);
- attesa in coda: ``sacct`` dell'account dal 1° ottobre 2026 (Start - Submit per partizione).

Modelli di costo per configurazione: SWEET e STONE come il default; PromptMark proporzionale alle
iterazioni effettive (``min(iterazioni, k)``); ACW proporzionale alle chiamate a Sourcery attese
(``n·35/43 + 2`` per lotto). Le configurazioni di default sono già calcolate in M7 e non si contano.
M9 riusa i risultati di L1 della M8 (stesse configurazioni): il costo in più è solo L2 dev, stimato
con i costi per campione di L1 (limite inferiore: il codice di CodeNet è più lungo).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
LANGS = {
    "sweet": ["python", "java", "cpp", "javascript"],
    "stone": ["python", "java", "cpp"],
    "mcgmark": ["python"],
    "promptmark": ["python"],
    "acw": ["python"],
}
GPU_GEN = {"sweet", "stone", "mcgmark", "promptmark"}
GPU_DET = {"sweet", "stone", "mcgmark"}
SUBJECTS = ["positives", "neg_human", "neg_llm"]
BUDGET_S = {"gpu": 5.5 * 3600, "cpu": 7.0 * 3600}  # obiettivo ≤ 80% del limite (cluster_info §1)
# L2 dev per linguaggio e modello (stime da SPEC §10.2: ~50 prompt × 6, ~1/5 dei 1.250 negativi).
L2_COUNTS = {"positives": 300, "neg_human": 250, "neg_llm": 300}
ACW_SOURCERY = 35
ACW_RULES = 43
SACCT_FIELDS = 5

DEFAULTS: dict[str, dict[str, Any]] = {
    "sweet": {"gamma": 0.5, "delta": 0.5, "entropy_threshold": 0.5},
    "stone": {"gamma": 0.5, "delta": 1.0},
    "mcgmark": {},
    "promptmark": {"iter_cap": 5, "z_threshold": 2.12, "green_size": None},
    "acw": {"num_transforms": 43},
}
# Iperparametri da cui dipende il punteggio di rilevazione (per la condivisione dei negativi).
DETECT_KEYS = {
    "sweet": ("gamma", "entropy_threshold"),
    "stone": ("gamma",),
    "mcgmark": (),
    "promptmark": ("green_size",),
    "acw": ("num_transforms",),
}


def _product(**axes: Any) -> list[dict[str, Any]]:
    keys = list(axes)
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*axes.values())]


def grids() -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """Griglie candidate: nome → (metodo, configurazioni, default compreso)."""
    sweet_full = _product(
        gamma=[0.1, 0.25, 0.5],
        delta=[0.5, 1.0, 2.0, 3.0, 4.0],
        entropy_threshold=[0.3, 0.6, 0.9, 1.2],
    )
    sweet_reduced = _product(
        gamma=[0.25, 0.5], delta=[1.0, 2.0, 3.0], entropy_threshold=[0.3, 0.6, 0.9, 1.2]
    )
    promptmark = _product(
        iter_cap=[1, 3, 5], z_threshold=[1.64, 2.12, 2.58], green_size=[8, 10, 12]
    )
    return {
        "sweet_full_61": ("sweet", [*sweet_full, DEFAULTS["sweet"]]),
        "sweet_reduced_25": ("sweet", [*sweet_reduced, DEFAULTS["sweet"]]),
        "stone_10": ("stone", _product(gamma=[0.25, 0.5], delta=[0.5, 1.0, 1.5, 2.0, 3.0])),
        "promptmark_28": ("promptmark", [*promptmark, DEFAULTS["promptmark"]]),
        "acw_4": ("acw", _product(num_transforms=[10, 20, 30, 43])),
        "mcgmark_1": ("mcgmark", [DEFAULTS["mcgmark"]]),
    }


# ---------------------------------------------------------------------- misure
@dataclass
class Unit:
    """Costi unitari misurati di una cella (metodo, modello, linguaggio) di L1 dev."""

    gen_s: float = 0.0  # secondi di worker per campione generato
    n_pos: int = 0
    iterations: list[int] = field(default_factory=list)  # PromptMark
    det_s: dict[str, float] = field(default_factory=dict)  # secondi per codice, per soggetto
    n_det: dict[str, int] = field(default_factory=dict)
    exec_s: float = 0.0  # secondi di esecuzione per campione marcato


def _results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # riga vuota o in scrittura da un worker ancora attivo (es. ACW)
    return rows


def _cfg(root: Path, method: str, model: str) -> str | None:
    folder = root / "watermarked" / method / model
    hashes = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    return hashes[0] if len(hashes) == 1 else None


def startup_seconds(log: Path) -> list[float]:
    """Avvio del worker per tentativo: dalla riga ``=====`` a ``N items, M already done``."""
    out: list[float] = []
    start: datetime | None = None
    if not log.is_file():
        return out
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("===== "):
            start = _parse_time(line[6:25])
        elif start is not None and "already done" in line:
            end = _parse_time(line[:19])
            if end is not None:
                out.append((end - start).total_seconds())
            start = None
    return out


def _parse_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def execute_seconds(root: Path) -> dict[tuple[str, str, str], float]:
    """Secondi della fase execute per (metodo, modello, linguaggio), campioni marcati."""
    path = root / "_timing" / "timings.jsonl"
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in _results(path):
        if row.get("stage") != "execute":
            continue
        cell = dict(part.split("=", 1) for part in str(row["cell"]).split("__") if "=" in part)
        if cell.get("source") != "llm_watermarked" or cell.get("split") != "dev":
            continue
        key = (cell.get("method", ""), cell.get("model_id", ""), cell.get("language", ""))
        totals[key] += float(row.get("duration_s", 0.0))
    return dict(totals)


def measure(root: Path) -> tuple[dict[tuple[str, str, str], Unit], dict[str, float]]:
    """Costi unitari per cella e avvio mediano del worker per metodo."""
    units: dict[tuple[str, str, str], Unit] = {}
    startups: dict[str, list[float]] = defaultdict(list)
    exec_totals = execute_seconds(root)
    for method, langs in LANGS.items():
        for model in MODELS:
            cfg = _cfg(root, method, model)
            if cfg is None:
                continue
            for lang in langs:
                cell = f"L1_{lang}_dev"
                unit = Unit()
                run = root / "_runs" / "watermark" / method / model / cfg / cell
                rows = _results(run / "results.jsonl")
                startups[method] += startup_seconds(run / "worker.log")
                if rows:
                    unit.n_pos = len(rows)
                    unit.gen_s = sum(float(r.get("elapsed_s") or 0.0) for r in rows) / len(rows)
                    unit.iterations = [
                        int((r.get("extra") or {}).get("n_iterations") or 0) for r in rows
                    ]
                for subject in SUBJECTS:
                    drun = root / "_runs" / "detect" / method / model / cfg / subject / cell
                    drows = _results(drun / "results.jsonl")
                    startups[method] += startup_seconds(drun / "worker.log")
                    if drows:
                        unit.det_s[subject] = sum(
                            float(r.get("elapsed_s") or 0.0) for r in drows
                        ) / len(drows)
                    unit.n_det[subject] = _detect_count(root, method, model, cfg, subject, cell)
                if unit.n_pos:
                    unit.exec_s = exec_totals.get((method, model, lang), 0.0) / unit.n_pos
                    units[(method, model, lang)] = unit
    median_startup = {m: statistics.median(v) if v else 60.0 for m, v in startups.items()}
    return units, median_startup


def _detect_count(root: Path, method: str, model: str, cfg: str, subject: str, cell: str) -> int:
    """Codici di un soggetto dal manifest della rilevazione (anche se il worker non ha finito)."""
    manifest = root / "detection" / method / model / cfg / subject / f"{cell}.parquet.manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return int(data.get("n_rows_out") or 0)
    items = root / "_runs" / "detect" / method / model / cfg / subject / cell / "items.jsonl"
    return len(_results(items))


# ---------------------------------------------------------------------- modelli di costo
def gen_factor(method: str, hp: dict[str, Any], iterations: list[int]) -> float:
    """Costo di generazione di una configurazione rispetto al default misurato."""
    if method == "promptmark":
        total = sum(iterations)
        if not total:
            return float(hp["iter_cap"]) / 5.0
        return sum(min(i, int(hp["iter_cap"])) for i in iterations) / total
    if method == "acw":
        return acw_factor(int(hp["num_transforms"]))
    return 1.0


def acw_factor(n: int) -> float:
    """Chiamate a Sourcery attese per lotto con n regole su 43, rispetto a tutte le 43."""
    calls = n * ACW_SOURCERY / ACW_RULES + 2  # regole Sourcery singole + congiunta + canarino
    return calls / (ACW_SOURCERY + 2)


def det_factor(method: str, hp: dict[str, Any]) -> float:
    return acw_factor(int(hp["num_transforms"])) if method == "acw" else 1.0


def detect_signature(method: str, hp: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(hp.get(k) for k in DETECT_KEYS[method])


def pack_jobs(durations: list[float], budget: float, startup: float) -> list[float]:
    """Impacchettamento first-fit decreasing; una cella più lunga del budget si divide."""
    pieces: list[float] = []
    for d in durations:
        parts = max(1, math.ceil(d / max(1.0, budget - startup)))
        pieces.extend([d / parts] * parts)
    jobs: list[float] = []
    for d in sorted(pieces, reverse=True):
        for i, load in enumerate(jobs):
            if load + d <= budget:
                jobs[i] = load + d
                break
        else:
            jobs.append(startup + d)
    return jobs


@dataclass
class Cost:
    gpu_gen_h: float = 0.0
    gpu_det_h: float = 0.0
    cpu_gen_h: float = 0.0
    cpu_det_h: float = 0.0
    cpu_exec_h: float = 0.0
    jobs: dict[str, list[float]] = field(default_factory=dict)  # gruppo → durate dei job (s)

    def add_jobs(self, group: str, durations: list[float]) -> None:
        self.jobs.setdefault(group, []).extend(durations)

    @property
    def n_jobs(self) -> int:
        return sum(len(v) for v in self.jobs.values())


def grid_cost(
    method: str,
    configs: list[dict[str, Any]],
    units: dict[tuple[str, str, str], Unit],
    startup: float,
    level: str,
    share_negatives: bool,
) -> Cost:
    """Costo di una griglia: L1 (M8, senza il default già fatto) o L2 (M9, tutte le config)."""
    if level == "L1":
        configs = [c for c in configs if c != DEFAULTS[method]]
    default_sig = detect_signature(method, DEFAULTS[method])
    seen_sig: set[tuple[Any, ...]] = {default_sig} if level == "L1" else set()
    cost = Cost()
    gen_cells: list[float] = []
    det_cells: list[float] = []
    exec_cells: list[float] = []
    for hp in configs:
        sig = detect_signature(method, hp)
        negatives = not share_negatives or sig not in seen_sig
        seen_sig.add(sig)
        for (m, _model, _lang), unit in units.items():
            if m != method:
                continue
            counts = (
                {"positives": unit.n_pos, **{s: unit.n_det.get(s, 0) for s in SUBJECTS[1:]}}
                if level == "L1"
                else dict(L2_COUNTS)
            )
            gen = unit.gen_s * counts["positives"] * gen_factor(method, hp, unit.iterations)
            det = 0.0
            for subject in SUBJECTS:
                if subject != "positives" and not negatives:
                    continue
                det += unit.det_s.get(subject, 0.0) * counts[subject] * det_factor(method, hp)
            exe = unit.exec_s * counts["positives"]
            gen_cells.append(gen)
            det_cells.append(det)
            exec_cells.append(exe)
    gen_res = "gpu" if method in GPU_GEN else "cpu"
    det_res = "gpu" if method in GPU_DET else "cpu"
    if gen_res == "gpu":
        cost.gpu_gen_h = sum(gen_cells) / 3600
    else:
        cost.cpu_gen_h = sum(gen_cells) / 3600
    if det_res == "gpu":
        cost.gpu_det_h = sum(det_cells) / 3600
    else:
        cost.cpu_det_h = sum(det_cells) / 3600
    cost.cpu_exec_h = sum(exec_cells) / 3600
    for group, cells, res, start in (
        ("watermark", gen_cells, gen_res, startup),
        ("detect", det_cells, det_res, startup),
        ("execute", exec_cells, "cpu", 30.0),
    ):
        cost.add_jobs(f"{group}:{res}", pack_jobs([d for d in cells if d], BUDGET_S[res], start))
    return cost


def wall_days(cost: Cost, slots: int, wait_h: dict[str, float]) -> float:
    """Ondate sequenziali per gruppo: ceil(job / slot) × (durata media + attesa mediana)."""
    hours = 0.0
    for group, durations in cost.jobs.items():
        if not durations:
            continue
        res = group.split(":")[1]
        mean_h = statistics.mean(durations) / 3600
        hours += math.ceil(len(durations) / slots) * (mean_h + wait_h.get(res, 0.0))
    return hours / 24


# ---------------------------------------------------------------------- coda
def queue_wait_hours(use_sacct: bool) -> dict[str, float]:
    """Attesa mediana in coda (ore) per gpu (gpuq) e cpu (defq) dall'account."""
    if not use_sacct:
        return {}
    cmd = [
        "sacct", "-A", "did_tesi_nlp_330", "--allusers", "-X", "-S", "2026-10-01",
        "--parsable2", "--noheader", "-o", "JobName,Partition,Submit,Start,State",
    ]  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    waits: dict[str, list[float]] = defaultdict(list)
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < SACCT_FIELDS or not parts[0].startswith("wmb-"):
            continue
        try:
            submit = datetime.fromisoformat(parts[2])
            start = datetime.fromisoformat(parts[3])
        except ValueError:
            continue
        res = "gpu" if parts[1] in ("gpuq", "aiq") else "cpu"
        waits[res].append(max(0.0, (start - submit).total_seconds() / 3600))
    report = {}
    for res, values in waits.items():
        values.sort()
        p90 = values[min(len(values) - 1, int(0.9 * len(values)))]
        sys.stdout.write(
            f"queue wait {res}: n={len(values)} median {statistics.median(values):.2f} h"
            f"  p90 {p90:.2f} h  max {values[-1]:.2f} h\n"
        )
        report[res] = statistics.median(values)
    return report


# ---------------------------------------------------------------------- report
def _row(name: str, n_cfg: int | str, cost: Cost, wait: dict[str, float]) -> str:
    return (
        f"{name:<20} {n_cfg!s:>4}{cost.gpu_gen_h:>8.1f} {cost.gpu_det_h:>8.1f} "
        f"{cost.cpu_gen_h:>8.1f} {cost.cpu_det_h:>8.1f} {cost.cpu_exec_h:>8.1f} "
        f"{cost.n_jobs:>5} {wall_days(cost, 6, wait):>7.1f} {wall_days(cost, 3, wait):>7.1f}\n"
    )


HEADER = (
    f"{'grid':<20} {'cfg':>4} {'GPUgen_h':>8} {'GPUdet_h':>8} {'CPUgen_h':>8} {'CPUdet_h':>8} "
    f"{'CPUexe_h':>8} {'jobs':>5} {'days@6':>7} {'days@3':>7}\n"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    parser.add_argument("--no-sacct", action="store_true")
    args = parser.parse_args()
    root = Path(args.artifacts)
    units, startup = measure(root)

    sys.stdout.write("##### costi unitari misurati in M7 (L1 dev, configurazione di default)\n")
    for (method, model, lang), u in sorted(units.items()):
        det = " ".join(f"{s}={u.det_s.get(s, 0):.2f}s×{u.n_det.get(s, 0)}" for s in SUBJECTS)
        iters = (
            f"  iter mean {statistics.mean(u.iterations):.2f}"
            if method == "promptmark" and u.iterations
            else ""
        )
        sys.stdout.write(
            f"{method:<10} {model:<20} {lang:<10} gen {u.gen_s:.2f}s×{u.n_pos}  {det}"
            f"  exec {u.exec_s:.2f}s{iters}\n"
        )
    sys.stdout.write(
        "worker startup (median s): "
        + " ".join(f"{m}={v:.0f}" for m, v in sorted(startup.items()))
        + "\n\n##### attesa in coda (sacct, job wmb-* dal 1° ottobre 2026)\n"
    )
    wait = queue_wait_hours(not args.no_sacct)
    if not wait:
        sys.stdout.write("sacct not available: wall-clock without queue wait\n")

    totals: dict[str, dict[str, Cost]] = {}
    for share in (False, True):
        label = "con condivisione dei negativi" if share else "come oggi (negativi per ogni config)"
        for level, title in (("L1", "M8 (L1 dev, senza i default già fatti)"),
                             ("L2", "M9 in più (solo L2 dev, stima)")):  # fmt: skip
            sys.stdout.write(f"\n##### {title}, {label}\n{HEADER}")
            for name, (method, configs) in grids().items():
                cost = grid_cost(method, configs, units, startup.get(method, 60.0), level, share)
                totals.setdefault(f"{level}:{share}", {})[name] = cost
                n_cfg = len([c for c in configs if level == "L2" or c != DEFAULTS[method]])
                sys.stdout.write(_row(name, n_cfg, cost, wait))

    sys.stdout.write("\n##### totali M8 + M9 (tutti i metodi)\n")
    for share in (False, True):
        for sweet in ("sweet_full_61", "sweet_reduced_25"):
            names = [sweet, "stone_10", "promptmark_28", "acw_4", "mcgmark_1"]
            total = Cost()
            for level in ("L1", "L2"):
                for name in names:
                    c = totals[f"{level}:{share}"][name]
                    total.gpu_gen_h += c.gpu_gen_h
                    total.gpu_det_h += c.gpu_det_h
                    total.cpu_gen_h += c.cpu_gen_h
                    total.cpu_det_h += c.cpu_det_h
                    total.cpu_exec_h += c.cpu_exec_h
                    for group, durations in c.jobs.items():
                        total.add_jobs(f"{level}{name}{group}", durations)
            label = f"{sweet}, {'con' if share else 'senza'} condivisione dei negativi"
            sys.stdout.write(f"{label}\n{HEADER}{_row('total', '-', total, wait)}")


if __name__ == "__main__":
    main()
