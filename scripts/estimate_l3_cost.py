"""Stima del costo del Livello 3 prima del lancio (M13; richiesta dell'utente del 29 ottobre 2026).

Uso (con bench-core, nodo di login, nessun job; dopo ``bash scripts/submit_l3.sh data``):
    python scripts/estimate_l3_cost.py | tee docs/milestone_logs/M13_l3_cost.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Conteggi: problemi e negativi di L3 dagli artefatti di ``prepare_data`` (per linguaggio e
dataset). Costi unitari misurati sui lanci di L2 (stesso decoding a 1.024 token, codice più lungo
di quello di CodeSearchNet e The Stack: valori per eccesso), per modello e linguaggio:

- **baseline** (GPU): ``seconds_per_problem`` dei manifest della baseline L2. ClassEval ha 2.048
  token (D40): il suo costo per problema va da 1× a 2× quello di L2 Python;
- **esecuzione** (CPU): durata dei job dell'esecuzione della baseline L2 per campione (dal
  manifest, ``finished_at - started_at``, comprende l'avvio della sandbox); le canoniche di L3
  girano 3 volte;
- **SWEET** (GPU; non ancora congelato): generazione = somma di ``elapsed_s`` dei worker della fase
  watermark su L2 dev diviso i problemi (mediana sulle configurazioni dello sweep); rilevazione =
  ``elapsed_s`` per campione dei worker di ``detect`` sui negativi umani di L2 dev (mediana sulle
  configurazioni), applicata a positivi (6 per problema), baseline (6 per problema) e negativi
  umani di L3. ClassEval come sopra (1×-2×).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.pipeline.stage import Cell
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.prepare_data import native_ref, problems_ref
from bench.store.artifact_store import ArtifactStore

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
LANGUAGES = ("python", "java", "javascript", "cpp")
N = 6  # campioni per problema
CANONICAL_REPEATS = 3
LONG = "classeval"  # 2.048 token (D40)


def h(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def span(low: float, high: float) -> str:
    return h(low) if abs(high - low) < 1 else f"{h(low)} – {h(high)}"


def manifest_seconds(store: ArtifactStore, ref: Any) -> float | None:
    manifest = store.read_manifest(ref) if store.exists(ref) else None
    if manifest is None or manifest.finished_at is None:
        return None
    return (manifest.finished_at - manifest.started_at).total_seconds()


def baseline_per_problem(store: ArtifactStore, model: str, lang: str) -> float | None:
    """Secondi per problema della baseline L2 (media pesata fra dev e test)."""
    total, n = 0.0, 0
    for split in ("dev", "test"):
        ref = baseline_ref(model, "L2", lang, split)
        manifest = store.read_manifest(ref) if store.exists(ref) else None
        stats = ((manifest.extra or {}).get("seconds_per_problem") or {}) if manifest else {}
        if stats.get("mean") is None or manifest is None or not manifest.n_rows_out:
            continue
        problems = manifest.n_rows_out // N
        total += float(stats["mean"]) * problems
        n += problems
    return total / n if n else None


def execution_per_sample(store: ArtifactStore, model: str, lang: str) -> float | None:
    """Secondi di job per campione dell'esecuzione della baseline L2 (dev e test)."""
    total, n = 0.0, 0
    for split in ("dev", "test"):
        cell = Cell(source="llm_baseline", model_id=model, level="L2", language=lang, split=split)
        ref = execution_ref(cell)
        seconds = manifest_seconds(store, ref)
        manifest = store.read_manifest(ref) if store.exists(ref) else None
        if seconds is None or manifest is None or not manifest.n_rows_out:
            continue
        total += seconds
        n += manifest.n_rows_out
    return total / n if n else None


def worker_rate(paths: list[Path], per: str) -> float | None:
    """Mediana fra le configurazioni dei secondi per problema (``per="item"``) o per campione."""
    rates = []
    for path in paths:
        seconds, items, lines = 0.0, set(), 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                seconds += float(row.get("elapsed_s") or 0.0)
                items.add(str(row.get("item_id")).split("#")[0])
                lines += 1
        count = len(items) if per == "item" else lines
        if count:
            rates.append(seconds / count)
    return statistics.median(rates) if rates else None


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=generate_baseline", "levels=[L3]"])
    store = ArtifactStore(cfg.paths.artifacts)
    runs = store.root / "_runs"
    w = sys.stdout.write
    counts: dict[str, Counter[str]] = {}
    negatives: dict[str, int] = {}
    for lang in LANGUAGES:
        ref, nat = problems_ref(Language(lang), "L3"), native_ref(Language(lang), "L3")
        if not store.exists(ref) or not store.exists(nat):
            w(f"L3 {lang} not prepared: run bash scripts/submit_l3.sh data\n")
            return 1
        counts[lang] = Counter(str(d) for d in store.read_table(ref)["dataset"])
        negatives[lang] = len(store.read_table(nat))
    w("##### L3 test: problemi e negativi umani\n")
    for lang in LANGUAGES:
        w(f"  {lang:<10} problemi {dict(counts[lang])}, negativi {negatives[lang]}\n")

    totals: defaultdict[str, float] = defaultdict(float)
    w("\n##### baseline L3 (GPU) ed esecuzione (CPU), per modello\n")
    for model in MODELS:
        for lang in LANGUAGES:
            per = baseline_per_problem(store, model, lang)
            exe = execution_per_sample(store, model, lang)
            n_long = counts[lang].get(LONG, 0)
            n_std = sum(counts[lang].values()) - n_long
            if per is None:
                w(f"  {model} {lang}: baseline L2 senza tempi\n")
                continue
            low = per * (n_std + n_long)
            high = per * (n_std + 2 * n_long)
            samples = N * (n_std + n_long)
            exe_s = (exe or 0.0) * samples
            # canoniche: una volta per linguaggio (non dipendono dal modello)
            canon_s = (
                (exe or 0.0) * CANONICAL_REPEATS * (n_std + n_long) if model == MODELS[0] else 0.0
            )
            totals["base_low"] += low
            totals["base_high"] += high
            totals["exec"] += exe_s
            totals["canon"] += canon_s
            w(f"  {model} {lang}: {per:.1f} s/problema L2 -> baseline {span(low, high)}; "
              f"esecuzione {exe or 0:.2f} s/campione -> {h(exe_s)}\n")  # fmt: skip
    w(f"  TOTALE baseline {span(totals['base_low'], totals['base_high'])} GPU; esecuzione "
      f"{h(totals['exec'])} CPU; canoniche (3 ripetizioni) {h(totals['canon'])} CPU\n")  # fmt: skip
    sweet_cost(runs, counts, negatives)
    return 0


def sweet_cost(runs: Path, counts: dict[str, Counter[str]], negatives: dict[str, int]) -> None:
    """Generazione e rilevazione di SWEET su L3 dai tempi dei worker di L2 dev."""
    w = sys.stdout.write
    w("\n##### SWEET su L3 (GPU; non congelato: costi della configurazione media dello sweep)\n")
    sweet: defaultdict[str, float] = defaultdict(float)
    for model in MODELS:
        for lang in LANGUAGES:
            gen = worker_rate(
                sorted(
                    (runs / "watermark" / "sweet" / model).glob(f"*/L2_{lang}_dev/results.jsonl")
                ),
                "item",
            )
            det = worker_rate(
                sorted((runs / "detect" / "sweet" / model).glob(
                    f"*/neg_human/L2_{lang}_dev/results.jsonl")),
                "sample",
            )  # fmt: skip
            n_long = counts[lang].get(LONG, 0)
            n_all = sum(counts[lang].values())
            if gen is None or det is None:
                w(f"  {model} {lang}: tempi di L2 dev assenti (gen {gen}, detect {det})\n")
                continue
            low, high = gen * n_all, gen * (n_all + n_long)
            subjects = {"positives": N * n_all, "neg_llm": N * n_all, "neg_human": negatives[lang]}
            det_s = det * sum(subjects.values())
            sweet["gen_low"] += low
            sweet["gen_high"] += high
            sweet["det"] += det_s
            parts = ", ".join(f"{k} {v}" for k, v in subjects.items())
            w(f"  {model} {lang}: generazione {gen:.1f} s/problema -> {span(low, high)}; "
              f"rilevazione {det:.3f} s/campione x {sum(subjects.values())} ({parts}) "
              f"-> {h(det_s)}\n")  # fmt: skip
    w(f"  TOTALE SWEET L3: generazione {span(sweet['gen_low'], sweet['gen_high'])} GPU; "
      f"rilevazione {h(sweet['det'])} GPU\n")  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main())
