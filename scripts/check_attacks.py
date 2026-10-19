"""Stato e risultati degli attacchi su L1 test di un metodo congelato (Milestone 10, ADR-012).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_attacks.py METHOD ATTACK[,ATTACK] [--missing] [--report]

Per ogni passo di ``submit_attacks_l1.sh`` (attack, execute, detect, metrics) conta gli artefatti
completi delle celle (modello, linguaggio, configurazione dell'attacco) con la configurazione
congelata del metodo. Il primo passo ``INCOMPLETE`` è il prossimo da lanciare.

Con ``--report`` stampa, per cella, l'intensità dell'attacco (dal manifest della fase attack:
stati, frazione modificata, 1 − CodeBLEU, medie dei conteggi) e le metriche (TPR, AUROC, Pass@1,
ΔPass@1 rispetto alla baseline e al marcato non attaccato).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Any

from bench.attacks.base import grid_attacks
from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.metrics import metrics_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
REPORT_METRICS = (
    "tpr_at_fpr", "auroc", "pass_at_1", "delta_pass_at_1_pp", "delta_pass_at_1_pp_vs_marked",
    "changed_fraction", "tool_error_fraction", "codebleu_distance",
)  # fmt: skip


def _metrics(store: ArtifactStore, ref: Any) -> dict[str, float]:
    if not store.exists(ref):
        return {}
    table = store.read_table(ref)
    return dict(zip(table["name"], table["value"], strict=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("method", choices=["sweet", "acw", "stone", "promptmark", "mcgmark"])
    parser.add_argument("attacks")
    parser.add_argument("--missing", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n")
        return 1
    steps: dict[str, list[tuple[str, Any]]] = {
        s: [] for s in ("attack", "execute", "detect", "metrics")
    }
    report: list[tuple[str, Any, Any]] = []
    for attack_id in args.attacks.split(","):
        for params_hash, attack in grid_attacks(attack_id).items():
            for cell in record.cells:
                if cell.not_applicable or cell.config_hash is None:
                    continue
                model, lang, h = cell.model_id, cell.language, cell.config_hash
                name = f"{attack_id} {json.dumps(attack.params, sort_keys=True)} {model} {lang}"
                base = Cell(
                    method=method, model_id=model, level=LEVEL, language=lang, split=SPLIT,
                    attack_id=attack_id, attack_params_hash=params_hash,
                )  # fmt: skip
                attacked = attacked_ref(
                    attack_id, params_hash, method, model, h, LEVEL, lang, SPLIT
                )
                steps["attack"].append((name, attacked))
                steps["execute"].append((name, execution_ref(replace(base, source="attacked"), h)))
                subject = attacked_subject(attack_id, params_hash)
                steps["detect"].append(
                    (name, detection_ref(method, model, h, subject, LEVEL, lang, SPLIT))
                )
                ref = metrics_ref(
                    method, model, h, LEVEL, lang, SPLIT, FINAL_LEVELS, (attack_id, params_hash)
                )
                steps["metrics"].append((name, ref))
                report.append((name, attacked, ref))

    sys.stdout.write(f"##### {method} attacks {args.attacks} on {LEVEL} {SPLIT}\n")
    for step, refs in steps.items():
        missing = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<10} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if args.missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in missing))
    if args.report:
        for name, attacked, ref in report:
            sys.stdout.write(f"\n##### {name}\n")
            manifest = store.read_manifest(attacked) if store.exists(attacked) else None
            extra = manifest.extra if manifest else {}
            if extra:
                sys.stdout.write(
                    f"  status {extra.get('status_counts')}"
                    f"  changed {extra.get('changed_fraction')}"
                    f"  1-CodeBLEU {extra.get('codebleu_distance_mean')}"
                    f" (changed only {extra.get('codebleu_distance_mean_changed')})\n"
                    f"  detail means {json.dumps(extra.get('detail_means_changed'))}\n"
                    f"  tools {json.dumps(extra.get('tool_versions'))}\n"
                )
            values = _metrics(store, ref)
            if values:
                sys.stdout.write(
                    "  " + "  ".join(
                        f"{k} {values[k]:.4f}" for k in REPORT_METRICS if k in values
                    ) + "\n"
                )  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
