"""Stato e risultati degli attacchi su L1 test di un metodo congelato (Milestone 10, ADR-012).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_attacks.py METHOD ATTACK[,ATTACK] [--missing] [--report]

Per ogni passo di ``submit_attacks_l1.sh`` (attack, execute, detect, metrics) conta gli artefatti
completi delle celle (modello, linguaggio, configurazione dell'attacco) con la configurazione
congelata del metodo. Il primo passo ``INCOMPLETE`` è il prossimo da lanciare.

Con ``--control`` aggiunge i passi del controllo negativo (D34: attacco sulla baseline,
rilevazione, metriche) per i punti con ``negative_control`` e, con ``--report``, per ogni cella
l'FPR sulla baseline riscritta accanto a TPR e AUROC e l'esito della regola dell'utente.

Con ``--report`` stampa, per cella, l'intensità dell'attacco (dal manifest della fase attack:
stati, frazione modificata, 1 − CodeBLEU, medie dei conteggi) e le metriche (TPR, AUROC, Pass@1,
ΔPass@1 rispetto alla baseline e al marcato non attaccato), con la frazione di campioni
``UNCHANGED`` accanto al TPR; per gli attacchi con LLM anche attaccante, decoding e SHA-256
dei prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Any

import pandas as pd

from bench.attacks.base import grid_attacks, negative_control_attacks
from bench.config.builder import load_experiment
from bench.hpo.freeze import FINAL_LEVELS, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.attack_control import (
    attacked_baseline_ref,
    control_detection_ref,
    control_metrics_ref,
)
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
    parser.add_argument("--control", action="store_true", help="controllo negativo (D34)")
    args = parser.parse_args()
    method = args.method
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, method)
    if record is None:
        sys.stdout.write(f"##### {method}: NOT FROZEN\n")
        return 1
    names: tuple[str, ...] = ("attack", "execute", "detect", "metrics")
    if args.control:
        names += ("control_attack", "control_detect", "control_metrics")
    steps: dict[str, list[tuple[str, Any]]] = {s: [] for s in names}
    report: list[tuple[str, Any, Any]] = []
    control: list[tuple[str, Any, Any]] = []
    for attack_id in args.attacks.split(","):
        controlled = set(negative_control_attacks(attack_id)) if args.control else set()
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
                if params_hash in controlled:
                    key = (attack_id, params_hash)
                    control.append(_add_control(steps, method, model, lang, h, key, name, ref))

    sys.stdout.write(f"##### {method} attacks {args.attacks} on {LEVEL} {SPLIT}\n")
    for step, refs in steps.items():
        missing = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<16} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if args.missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in missing))
    if args.report:
        _write_report(store, report)
        if args.control:
            _write_control(store, control, cfg.detection.target_fpr)
    return 0


def _add_control(
    steps: dict[str, list[tuple[str, Any]]],
    method: str,
    model: str,
    lang: str,
    h: str,
    key: tuple[str, str],
    name: str,
    ref: Any,
) -> tuple[str, Any, Any]:
    """Passi del controllo negativo (D34) di una cella; la baseline riscritta è per modello e
    linguaggio, comune a tutti i metodi."""
    base_ref = (f"{model} {lang}", attacked_baseline_ref(*key, model, LEVEL, lang, SPLIT))
    if base_ref not in steps["control_attack"]:
        steps["control_attack"].append(base_ref)
    det = control_detection_ref(method, model, h, *key, LEVEL, lang, SPLIT)
    steps["control_detect"].append((name, det))
    cref = control_metrics_ref(method, model, h, LEVEL, lang, SPLIT, list(FINAL_LEVELS), key)
    steps["control_metrics"].append((name, cref))
    return name, ref, cref


def _write_report(store: ArtifactStore, report: list[tuple[str, Any, Any]]) -> None:
    """Intensità e metriche per cella (``--report``)."""
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
            if "attacker" in extra:  # attacchi con LLM (T2.2, T2.3)
                attacker = extra["attacker"]
                sys.stdout.write(
                    f"  attacker {attacker.get('hf_repo')} @ {attacker.get('snapshot')}"
                    f"  decoding {json.dumps(attacker.get('decoding'), sort_keys=True)}\n"
                    f"  prompts_sha256 {json.dumps(extra.get('prompts_sha256'))}\n"
                )
        values = _metrics(store, ref)
        unchanged = _unchanged_fraction(extra)
        if values:
            parts = [f"{k} {values[k]:.4f}" for k in REPORT_METRICS if k in values]
            if unchanged is not None:  # accanto al TPR (richiesta dell'utente, T2.2)
                parts.insert(1, f"unchanged_fraction {unchanged:.4f}")
            sys.stdout.write("  " + "  ".join(parts) + "\n")


def _write_control(
    store: ArtifactStore, control: list[tuple[str, Any, Any]], target_fpr: float
) -> None:
    """Controllo negativo (D34): falsi positivi sulla baseline riscritta e AUROC fra riscritti.

    Regola dell'utente (26 ottobre 2026): se l'FPR sulla baseline riscritta resta vicino all'FPR
    nominale, il TPR dopo l'attacco è robustezza vera; se si avvicina al TPR, è un artefatto dello
    stile dell'attaccante. "Vicino" = più vicino all'uno che all'altro; la regola non si applica se
    il TPR dopo l'attacco non supera l'FPR nominale (limite inferiore dell'IC).
    """
    sys.stdout.write(f"\n##### controllo negativo (D34), FPR nominale {target_fpr}\n")
    for name, ref, cref in control:
        attacked, values = _metric_rows(store, ref), _metric_rows(store, cref)
        sys.stdout.write(f"\n##### {name}\n")
        if not values:
            sys.stdout.write("  (metriche del controllo mancanti)\n")
            continue
        parts = [
            _fmt(k, values[k]) for k in
            ("tpr_at_fpr", "fpr_llm_rewritten", "fpr_llm_original", "auroc_rewritten")
            if k in values
        ]  # fmt: skip
        if "auroc" in attacked:
            parts.insert(3, _fmt("auroc_vs_human", attacked["auroc"]))
        sys.stdout.write("  " + "  ".join(parts) + "\n")
        tpr, fpr = values["tpr_at_fpr"], values["fpr_llm_rewritten"]
        if tpr[1] is None or tpr[1] <= target_fpr:
            verdict = "non applicabile: il TPR dopo l'attacco non supera l'FPR nominale"
        elif abs(fpr[0] - target_fpr) <= abs(fpr[0] - tpr[0]):
            verdict = "robustezza vera (FPR sulla baseline riscritta vicino al nominale)"
        else:
            verdict = "artefatto dello stile dell'attaccante (FPR vicino al TPR)"
        sys.stdout.write(f"  REGOLA: {verdict}\n")


def _metric_rows(store: ArtifactStore, ref: Any) -> dict[str, tuple[float, Any, Any]]:
    """Valore e IC di ogni metrica di un artefatto (vuoto se manca)."""
    if not store.exists(ref):
        return {}
    t = store.read_table(ref)

    def ci(x: Any) -> float | None:
        return None if x is None or pd.isna(x) else float(x)

    return {
        n: (float(v), ci(lo), ci(hi))
        for n, v, lo, hi in zip(t["name"], t["value"], t["ci_low"], t["ci_high"], strict=True)
    }


def _fmt(name: str, value: tuple[float, Any, Any]) -> str:
    v, lo, hi = value
    ci = f" [{lo:.4f}, {hi:.4f}]" if lo is not None and hi is not None else ""
    return f"{name} {v:.4f}{ci}"


def _unchanged_fraction(extra: dict[str, Any]) -> float | None:
    """Campioni ``UNCHANGED`` sui campioni a cui l'attacco si applica (esclusi
    ``NOT_APPLICABLE``); restano nelle metriche (I1)."""
    counts = extra.get("status_counts") or {}
    applicable = sum(n for s, n in counts.items() if s != "NOT_APPLICABLE")
    return counts.get("UNCHANGED", 0) / applicable if applicable else None


if __name__ == "__main__":
    raise SystemExit(main())
