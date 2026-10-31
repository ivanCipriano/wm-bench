"""Stima complessiva della chiusura (fase 3 di ``M13_M14_plan.md``): ore GPU e CPU dei passi che
mancano, per metodo e livello, e giorni reali.

Uso (con bench-core, nodo di login, nessun job; qualche minuto per leggere i manifest):
    python scripts/estimate_closing_cost.py | tee docs/milestone_logs/M13_closing_cost.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Metodo:

- **passi che mancano**: quelli delle catene (``bench.chains.all_steps``) con output assenti negli
  artefatti veri; delle celle pianificabili si contano solo quelle senza output; le celle di SWEET
  sul test (non ancora congelato) si pianificano senza la configurazione congelata;
- **tempi misurati**: dai manifest degli artefatti veri, per cella ``finished_at - started_at``
  (dal primo inizio all'ultima fine degli output della cella) e righe prodotte. Per ogni cella da
  fare si usa la mediana delle celle già fatte con la stessa fase, metodo, modello, linguaggio,
  attacco e sorgente, nell'ordine: stesso livello e parte, stesso livello, L1 test, qualsiasi
  (T2.4k usa i tempi di T2.4). Per SWEET, fuori dalla parte di sviluppo, ci sono solo i tempi dello
  sweep (mediana delle configurazioni): per Qwen il pilota a nodo fisso (``M13_sweet_timing.txt``)
  dà tempi più bassi, quindi la stima di SWEET è per eccesso;
- **righe attese**: righe per problema della cella di riferimento per i problemi della cella da
  fare; nella rilevazione senza attacco si aggiungono a parte i negativi umani del livello
  (``righe = (righe_rif - umani_rif) / problemi_rif * problemi + umani``). Le fasi di metriche,
  soglie, scelta e congelamento valgono la mediana di una loro cella;
- **giorni reali**: per proprietario, il massimo fra (a) le ore-job divise per i 10 posti
  (MaxSubmit) e (b) il cammino critico delle dipendenze, con ogni passo diviso fra i suoi job.
  Non comprendono l'attesa in coda (gpuq, aiq), che va aggiunta.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.chains import MAX_JOBS, OWNERS, Step, all_steps
from bench.config.builder import load_experiment
from bench.config.resources import ResourceClass
from bench.domain.enums import Language
from bench.domain.errors import BenchError
from bench.pipeline.stage import Cell, CellPlanner
from bench.pipeline.stages.prepare_data import native_ref, problems_ref
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore

BASE = ["paths=cluster"]
SKIP_DIRS = {"_runs", "_slurm", "data", "smoke"}
FLAT = ("metrics", "calibrate", "tune", "freeze")  # fasi brevi: tempo per cella, non per riga
HUMAN = {"detect", "detect_k2"}  # rilevazione con i negativi umani del livello
ATTACK_PROXY = {"T2.4k": "T2.4"}
LEVELS = ("L1", "L2", "L3")


@dataclass
class Measured:
    """Una cella già fatta: secondi, righe prodotte e coordinate."""

    stage: str
    cell: dict[str, Any]
    seconds: float
    rows: int


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def scan(root: Path) -> list[Measured]:
    """Celle fatte dai manifest (output della stessa cella uniti)."""
    spans: dict[tuple[str, str], list[Any]] = {}
    for top in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        for path in top.rglob("*.manifest.json"):
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not m.get("finished_at") or m.get("status") not in (None, "complete"):
                continue
            key = (str(m["stage"]), json.dumps(m.get("cell") or {}, sort_keys=True))
            start, end = _time(m["started_at"]), _time(m["finished_at"])
            rows = int(m.get("n_rows_out") or 0)
            if key in spans:
                s = spans[key]
                s[0], s[1], s[2] = min(s[0], start), max(s[1], end), s[2] + rows
            else:
                spans[key] = [start, end, rows]
    return [
        Measured(stage, json.loads(cell), (s[1] - s[0]).total_seconds(), s[2])
        for (stage, cell), s in spans.items()
        if s[1] > s[0]
    ]


class Counts:
    """Problemi e negativi umani per (livello, linguaggio, parte)."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store
        self._cache: dict[tuple[str, str, str], tuple[int, int]] = {}

    def get(self, level: str | None, lang: str | None, split: str | None) -> tuple[int, int]:
        if level is None or lang is None:
            return 0, 0
        key = (level, lang, split or "test")
        if key not in self._cache:
            self._cache[key] = (self._count(problems_ref(Language(lang), level), key[2]),
                                self._count(native_ref(Language(lang), level), key[2]))  # fmt: skip
        return self._cache[key]

    def _count(self, ref: Any, split: str) -> int:
        if not self.store.exists(ref):
            return 0
        table = self.store.read_table(ref)
        return int((table["split"] == split).sum()) if "split" in table.columns else len(table)


def _coords(stage: str, cell: dict[str, Any]) -> tuple[Any, ...]:
    attack = str(cell.get("attack_id") or "") or None
    return (stage, cell.get("method"), cell.get("model_id"), cell.get("language"),
            (ATTACK_PROXY.get(attack, attack) if attack else None), cell.get("source"))  # fmt: skip


class Rates:
    """Secondi per riga e righe per problema delle celle fatte, con le ricadute."""

    def __init__(self, measured: list[Measured], counts: Counts) -> None:
        self.counts = counts
        self.by: dict[tuple[Any, ...], list[Measured]] = defaultdict(list)
        self.flat: dict[str, list[float]] = defaultdict(list)
        for m in measured:
            self.by[_coords(m.stage, m.cell)].append(m)
            self.flat[m.stage].append(m.seconds)

    def seconds(self, stage: str, cell: Cell) -> tuple[float, str] | None:
        """Secondi stimati di una cella da fare e da dove vengono (``None``: nessun tempo)."""
        if stage.startswith(FLAT):
            values = self.flat.get(stage)
            return (statistics.median(values), "cell median") if values else None
        refs = self.by.get(_coords(stage, cell.as_dict()), [])
        for label, keep in (
            ("same level/split", lambda c: c.get("level") == cell.level
             and c.get("split") == cell.split),
            ("same level", lambda c: c.get("level") == cell.level),
            ("L1 test", lambda c: c.get("level") == "L1" and c.get("split") == "test"),
            ("any", lambda c: True),
        ):  # fmt: skip
            chosen = [m for m in refs if keep(m.cell) and m.rows > 0]
            estimate = self._estimate(stage, cell, chosen)
            if estimate is not None:
                return estimate, label
        return None

    def _estimate(self, stage: str, cell: Cell, refs: list[Measured]) -> float | None:
        human = stage in HUMAN and cell.attack_id is None
        per_row, per_problem = [], []
        for m in refs:
            p, h = self.counts.get(m.cell.get("level"), m.cell.get("language"), m.cell.get("split"))
            if p <= 0:
                continue
            per_row.append(m.seconds / m.rows)
            per_problem.append(max(0, m.rows - (h if human else 0)) / p)
        if not per_row:
            return None
        p, h = self.counts.get(cell.level, cell.language, cell.split)
        rows = statistics.median(per_problem) * p + (h if human else 0)
        return statistics.median(per_row) * rows


def missing_cells(step: Step, store: ArtifactStore) -> Iterator[Cell]:
    """Celle senza output di un passo (SWEET non congelato: tutte, senza configurazione)."""
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    stage_cls = STAGES.get(step.stage)
    try:
        cfg = load_experiment([*BASE, f"stage={step.stage}", *step.overrides])
        stage = stage_cls.create(cfg)
        for cell in CellPlanner(cfg).cells_for(stage_cls):
            if not all(store.exists(ref) for ref in stage.outputs(cell)):
                yield cell
    except BenchError:
        cfg = load_experiment([*BASE, f"stage={step.stage}", *step.overrides,
                               "hpo.use_selected=false"])  # fmt: skip
        yield from (replace(c, config_hash=None) for c in CellPlanner(cfg).cells_for(stage_cls))


def h(seconds: float) -> str:
    return f"{seconds / 3600:8.1f}"


def main() -> int:
    import bench.pipeline.stages  # noqa: F401  (popola STAGES)

    cfg = load_experiment([*BASE, "stage=selftest"])
    store = ArtifactStore(cfg.paths.artifacts)
    w = sys.stdout.write
    w("lettura dei manifest...\n")
    counts = Counts(store)
    rates = Rates(scan(Path(cfg.paths.artifacts)), counts)
    steps = all_steps(str(cfg.paths.wmb))
    by_key = {s.key: s for s in steps}

    step_s: dict[str, float] = {}
    gpu_of: dict[str, bool] = {}
    table: dict[tuple[str, str, str], float] = defaultdict(float)  # (metodo, livello, risorsa)
    unknown: list[str] = []
    w("\n##### passi che mancano (ore-job, fonte dei tempi)\n")
    for step in steps:
        gpu = STAGES.get(step.stage).resources is not ResourceClass.CPU
        total, sources, n = 0.0, set(), 0
        for cell in missing_cells(step, store):
            n += 1
            found = rates.seconds(step.stage, cell)
            if found is None:
                unknown.append(f"{step.key}: {cell.key()}")
                continue
            total += found[0]
            sources.add(found[1])
            level = cell.level if cell.level in LEVELS else "-"
            table[(step.key.split("/")[0], f"{level} {cell.split or ''}".strip(),
                   "GPU" if gpu else "CPU")] += found[0]  # fmt: skip
        if n == 0:
            continue
        step_s[step.key], gpu_of[step.key] = total, gpu
        acw = " (HELD: licenza)" if step.acw else ""
        w(f"  {step.key:<40} {step.owner:<7} {'GPU' if gpu else 'CPU'} {n:>4} celle "
          f"{h(total)} h  [{', '.join(sorted(sources)) or '-'}]{acw}\n")  # fmt: skip

    w("\n##### per metodo e livello (ore-job)\n")
    w(f"  {'metodo':<12} {'livello':<10} {'GPU h':>8} {'CPU h':>8}\n")
    for method, part in sorted({(m, lv) for m, lv, _ in table}):
        w(f"  {method:<12} {part:<10} {h(table[(method, part, 'GPU')])} "
          f"{h(table[(method, part, 'CPU')])}\n")  # fmt: skip

    w("\n##### giorni reali per proprietario (senza attesa in coda)\n")
    for with_acw in (False, True):
        w(f"  {'con' if with_acw else 'senza'} ACW su L2 test e L3:\n")
        for owner in OWNERS:
            mine = [k for k in step_s if by_key[k].owner == owner
                    and (with_acw or not by_key[k].acw)]  # fmt: skip
            gpu_s = sum(step_s[k] for k in mine if gpu_of[k])
            cpu_s = sum(step_s[k] for k in mine if not gpu_of[k])
            slots = (gpu_s + cpu_s) / MAX_JOBS
            path = critical_path(mine, step_s, by_key)
            days = max(slots, path) / 86400
            w(f"    {owner:<7} GPU {h(gpu_s)} h, CPU {h(cpu_s)} h; 10 posti {h(slots)} h; cammino "
              f"critico {h(path)} h -> {days:.1f} giorni\n")  # fmt: skip
    sweet = [k for k in step_s if k.startswith("sweet/")]
    w(f"\n  SWEET (priorità sulle GPU): cammino critico {h(critical_path(sweet, step_s, by_key))}"
      f" h, GPU {h(sum(step_s[k] for k in sweet if gpu_of[k]))} h\n")  # fmt: skip
    if unknown:
        w(f"\n##### celle senza tempi di riferimento ({len(unknown)}; non contate)\n")
        for line in unknown[:40]:
            w(f"  {line}\n")
    return 0


def critical_path(keys: list[str], seconds: dict[str, float], by_key: dict[str, Step]) -> float:
    """Cammino più lungo fra i passi dati, ogni passo diviso fra i suoi job."""
    wanted = set(keys)
    memo: dict[str, float] = {}

    def longest(key: str) -> float:
        if key not in memo:
            step = by_key[key]
            before = [longest(n) for n in step.needs if n in wanted]
            memo[key] = seconds.get(key, 0.0) / max(1, step.jobs) + max(before, default=0.0)
        return memo[key]

    return max((longest(k) for k in keys), default=0.0)


if __name__ == "__main__":
    raise SystemExit(main())
