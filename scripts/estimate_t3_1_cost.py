"""Stima del costo di T3.1 per metodo e modello, prima del lancio (ADR-014 §3; richiesta
dell'utente del 27 ottobre 2026).

Uso (con bench-core, nodo di login, nessun job; pochi secondi):
    python scripts/estimate_t3_1_cost.py [METHOD ...] | tee docs/milestone_logs/M11_t3_1_cost.txt

Senza argomenti: tutti i metodi congelati (SWEET compreso, quando lo sarà). I tempi vengono dai
lanci già fatti con la configurazione congelata: somma di ``elapsed_s`` dei risultati del worker
(``_runs/<fase>/.../results.jsonl``) o, se la cartella non c'è più, la durata dal manifest
(``finished_at - started_at``, che con una ripresa sottostima). Per ogni (modello, linguaggio):

- **ri-marcatura** (fase attack di T3.1): un campione per item. Per i metodi che generano un
  campione per item già nella fase watermark (MCGMark, PromptMark) il costo atteso è quello della
  fase watermark di L1 test; per gli altri (STONE, SWEET: 6 campioni per item) si danno due
  estremi, stessa resa della fase watermark (minimo) e un item per campione senza vantaggio dal
  lotto (massimo, ×6). ACW: come la sua fase watermark (CPU, Sourcery). Il prompt è più lungo
  (contiene il codice marcato): i valori sono un ordine di grandezza, da rivedere dopo un pilota;
- **rilevazione con k2**: come quella con k1 degli stessi soggetti (negativi umani di dev L1 e L2 e
  integrazione combinata, negativi umani di test, positivi di test al posto degli attaccati);
- risorse: GPU o CPU secondo ``configs/resources/default.yaml``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.config.resources import ResourcePolicy
from bench.hpo.freeze import load_freeze
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.watermark import watermarked_ref
from bench.store.artifact_store import ArtifactStore

METHODS = ("sweet", "acw", "stone", "promptmark", "mcgmark")
ONE_PER_ITEM = frozenset({"mcgmark", "promptmark", "acw"})
BATCH_N = 6  # campioni per item della fase watermark per STONE e SWEET (decoding n)
DETECT_SUBJECTS = (  # (soggetto, livello, parte) della rilevazione con k2
    ("neg_human", "L1", "dev"),
    ("neg_human", "L2", "dev"),
    ("neg_integration", "L2", "dev"),
    ("neg_human", "L1", "test"),
    ("positives", "L1", "test"),  # al posto degli attaccati di T3.1
)


def worker_seconds(results: Path) -> float | None:
    """Somma di ``elapsed_s`` dei risultati del worker, ``None`` se il file non c'è."""
    if not results.is_file():
        return None
    total = 0.0
    with results.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                total += float(json.loads(line).get("elapsed_s") or 0.0)
    return total


def manifest_seconds(store: ArtifactStore, ref: Any) -> float | None:
    manifest = store.read_manifest(ref)
    if manifest is None or manifest.finished_at is None:
        return None
    return (manifest.finished_at - manifest.started_at).total_seconds()


def seconds(store: ArtifactStore, ref: Any, run_dir: Path) -> tuple[float | None, str]:
    """Tempo di calcolo di un artefatto e da dove viene (worker o manifest)."""
    found = worker_seconds(run_dir / "results.jsonl")
    if found is not None:
        return found, "worker"
    return manifest_seconds(store, ref), "manifest"


def hours(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 3600:.1f} h"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("methods", nargs="*", help=f"fra {', '.join(METHODS)} (default: tutti)")
    args = parser.parse_args()
    unknown = sorted(set(args.methods) - set(METHODS))
    if unknown:
        parser.error(f"unknown methods {unknown}")
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    policy = ResourcePolicy(cfg.resources, {}, cfg.slurm)
    runs = store.root / "_runs"
    for method in args.methods or METHODS:
        record = load_freeze(store, method)
        if record is None:
            sys.stdout.write(f"\n##### {method}: NOT FROZEN (stima quando sarà congelato)\n")
            continue
        gen = policy.resource_for("attack", method=method, attack="T3.1").value
        det = policy.resource_for("detect_k2", method=method).value
        sys.stdout.write(f"\n##### {method}  (ri-marcatura su {gen}, rilevazione su {det})\n")
        totals = {"gen_min": 0.0, "gen_max": 0.0, "detect": 0.0}
        for cell in record.cells:
            if cell.not_applicable or cell.config_hash is None:
                continue
            model, lang, h = cell.model_id, cell.language, cell.config_hash
            ref = watermarked_ref(method, model, h, "L1", lang, "test")
            wm, source = seconds(
                store, ref, runs / "watermark" / method / model / h / f"L1_{lang}_test"
            )
            low = wm
            high = None if wm is None else wm * (1 if method in ONE_PER_ITEM else BATCH_N)
            parts = []
            det_total = 0.0
            for subject, level, split in DETECT_SUBJECTS:
                dref = detection_ref(method, model, h, subject, level, lang, split)
                run = runs / "detect" / method / model / h / subject / f"{level}_{lang}_{split}"
                value, src = seconds(store, dref, run)
                det_total += value or 0.0
                parts.append(f"{subject}/{level}_{split} {hours(value)} ({src})")
            sys.stdout.write(
                f"  {model} {lang}: ri-marcatura {hours(low)} – {hours(high)} "
                f"(watermark L1 test, {source}); rilevazione k2 {hours(det_total)}\n"
                f"      {'; '.join(parts)}\n"
            )
            totals["gen_min"] += low or 0.0
            totals["gen_max"] += high or 0.0
            totals["detect"] += det_total
        sys.stdout.write(
            f"  TOTALE {method}: ri-marcatura {hours(totals['gen_min'])} – "
            f"{hours(totals['gen_max'])} ({gen}); "
            f"rilevazione k2 {hours(totals['detect'])} ({det})\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
