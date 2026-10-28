"""Stato e risultati degli attacchi su L1 test di un metodo congelato (Milestone 10, ADR-012).

Uso (con bench-core, nodo di login, nessun job):
    python scripts/check_attacks.py METHOD ATTACK[,ATTACK] [--missing] [--report]

Per ogni passo di ``submit_attacks_l1.sh`` (attack, execute, detect, metrics) conta gli artefatti
completi delle celle (modello, linguaggio, configurazione dell'attacco) con la configurazione
congelata del metodo. Il primo passo ``INCOMPLETE`` è il prossimo da lanciare.

Con ``--control`` aggiunge i passi del controllo negativo (D34: attacco sulla baseline,
rilevazione, metriche) per i punti con ``negative_control`` e, con ``--report``, per ogni cella
l'FPR sulla baseline riscritta accanto a TPR e AUROC e l'esito della regola dell'utente.

Per T3.1 (ri-marcatura con k2, ADR-014 §3) aggiunge i passi della seconda chiave (soglia di k2,
congelamento, negativi umani di test con k2, attaccati con k2, metriche della ri-marcatura) e, con
``--report``, le quattro categorie k1/k2, TPR, AUROC e FPR di test con k2, le due soglie e le
frazioni di stato, tutte con intervallo di confidenza.

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
from collections import Counter
from dataclasses import replace
from typing import Any

import pandas as pd

from bench.attacks.base import grid_attacks, negative_control_attacks
from bench.attacks.interface import pass_failures
from bench.config.builder import load_experiment
from bench.domain.models import Problem
from bench.hpo.freeze import FINAL_LEVELS, freeze_ref, load_freeze
from bench.pipeline.stage import Cell
from bench.pipeline.stages.attack import attacked_ref, attacked_subject
from bench.pipeline.stages.attack_control import (
    attacked_baseline_ref,
    control_detection_ref,
    control_metrics_ref,
)
from bench.pipeline.stages.calibrate import primary_view, threshold_ref
from bench.pipeline.stages.detect import detection_ref
from bench.pipeline.stages.execute import execution_ref
from bench.pipeline.stages.generate_baseline import problems_ref
from bench.pipeline.stages.metrics import _detected, metrics_ref
from bench.pipeline.stages.remark import ALT_KEY_ID, REMARK_ATTACK, remark_metrics_ref
from bench.pipeline.stages.watermark import make_adapter
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT = "L1", "test"
LLM_REWRITES = frozenset({"T2.2", "T3.1"})  # riscritture con LLM: funzione d'ingresso rinominata?
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
    steps: dict[str, list[tuple[str, Any]]] = {s: [] for s in _step_names(args)}
    report: list[tuple[str, Any, Any, Any, Any]] = []
    control: list[tuple[str, Any, Any]] = []
    remark: list[tuple[str, Any]] = []
    for attack_id in args.attacks.split(","):
        for attack in grid_attacks(attack_id).values():
            for cell in record.cells:
                if cell.not_applicable or cell.config_hash is None:
                    continue
                if not attack.supports(cell.language):  # es. T4 in Java e C++: nessuna cella
                    continue
                model, lang, h = cell.model_id, cell.language, cell.config_hash
                # hash della cella con la versione corrente del catalogo del suo linguaggio
                params_hash = attack.params_hash(lang)
                name = f"{attack_id} {json.dumps(attack.params, sort_keys=True)} {model} {lang}"
                base = Cell(
                    method=method, model_id=model, level=LEVEL, language=lang, split=SPLIT,
                    attack_id=attack_id, attack_params_hash=params_hash,
                )  # fmt: skip
                attacked = attacked_ref(
                    attack_id, params_hash, method, model, h, LEVEL, lang, SPLIT
                )
                steps["attack"].append((name, attacked))
                exe = execution_ref(replace(base, source="attacked"), h)
                steps["execute"].append((name, exe))
                subject = attacked_subject(attack_id, params_hash)
                det = detection_ref(method, model, h, subject, LEVEL, lang, SPLIT)
                steps["detect"].append((name, det))
                ref = metrics_ref(
                    method, model, h, LEVEL, lang, SPLIT, FINAL_LEVELS, (attack_id, params_hash)
                )
                steps["metrics"].append((name, ref))
                report.append((name, attacked, ref, exe, det))
                if args.control and params_hash in negative_control_attacks(
                    attack_id, language=lang
                ):
                    key = (attack_id, params_hash)
                    control.append(_add_control(steps, method, model, lang, h, key, name, ref))
                if attack_id == REMARK_ATTACK:
                    remark.append(_add_remark(steps, method, model, lang, h, params_hash, name))

    sys.stdout.write(f"##### {method} attacks {args.attacks} on {LEVEL} {SPLIT}\n")
    _write_steps(store, steps, args.missing)
    if args.report:
        _write_report(store, report, make_adapter(cfg, method))
        if args.control:
            _write_control(store, control, cfg.detection.target_fpr)
        if remark:
            _write_remark(store, remark)
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


def _step_names(args: argparse.Namespace) -> tuple[str, ...]:
    """Passi da controllare: quelli di ogni attacco, del controllo negativo e di T3.1."""
    names: tuple[str, ...] = ("attack", "execute", "detect", "metrics")
    if args.control:
        names += ("control_attack", "control_detect", "control_metrics")
    if REMARK_ATTACK in args.attacks.split(","):
        names += ("k2_threshold", "k2_freeze", "k2_neg_test", "k2_detect", "k2_metrics")
    return names


def _write_steps(
    store: ArtifactStore, steps: dict[str, list[tuple[str, Any]]], show_missing: bool
) -> None:
    """Artefatti completi per passo (il primo ``INCOMPLETE`` è il prossimo da lanciare)."""
    for step, refs in steps.items():
        missing = [n for n, r in refs if not store.exists(r)]
        state = "COMPLETE" if not missing else "INCOMPLETE"
        sys.stdout.write(f"{step:<16} {len(refs) - len(missing):>4}/{len(refs):<4} {state}\n")
        if show_missing:
            sys.stdout.write("".join(f"    missing: {m}\n" for m in missing))


def _add_remark(
    steps: dict[str, list[tuple[str, Any]]],
    method: str,
    model: str,
    lang: str,
    h: str,
    params_hash: str,
    name: str,
) -> tuple[str, Any]:
    """Passi della seconda chiave di una cella T3.1; ritorna (nome, metriche della ri-marcatura)."""
    steps["k2_threshold"].append(
        (name, threshold_ref(method, model, lang, h, FINAL_LEVELS, ALT_KEY_ID))
    )
    if not steps["k2_freeze"]:
        steps["k2_freeze"].append((method, freeze_ref(method, ALT_KEY_ID)))
    steps["k2_neg_test"].append(
        (name, detection_ref(method, model, h, "neg_human", LEVEL, lang, SPLIT, ALT_KEY_ID))
    )
    subject = attacked_subject(REMARK_ATTACK, params_hash)
    steps["k2_detect"].append(
        (name, detection_ref(method, model, h, subject, LEVEL, lang, SPLIT, ALT_KEY_ID))
    )
    ref = remark_metrics_ref(
        method, model, h, LEVEL, lang, SPLIT, list(FINAL_LEVELS), (REMARK_ATTACK, params_hash)
    )
    steps["k2_metrics"].append((name, ref))
    return name, ref


def _write_remark(store: ArtifactStore, remark: list[tuple[str, Any]]) -> None:
    """T3.1: tutte le metriche della ri-marcatura con IC; le due soglie a confronto."""
    for name, ref in remark:
        sys.stdout.write(f"\n##### T3.1 k1/k2 {name}\n")
        if not store.exists(ref):
            sys.stdout.write("  (metrics_remark missing)\n")
            continue
        table = store.read_table(ref)
        for row in table.to_dict(orient="records"):
            low, high = row["ci_low"], row["ci_high"]
            ci = f"[{low:.4f}; {high:.4f}]" if pd.notna(low) and pd.notna(high) else ""
            sys.stdout.write(f"  {row['name']!s:<34} {row['value']:.4f} {ci} n={row['n']}\n")
        values = dict(zip(table["name"], table["value"], strict=True))
        t1, t2 = values.get("threshold_k1"), values.get("threshold_k2")
        if t1 is not None and t2 is not None:
            ratio = t2 / t1 if t1 else float("nan")
            sys.stdout.write(f"  THRESHOLDS k1 {t1:.4f}  k2 {t2:.4f}  k2/k1 {ratio:.3f}\n")


def _write_report(
    store: ArtifactStore, report: list[tuple[str, Any, Any, Any, Any]], adapter: Any
) -> None:
    """Intensità e metriche per cella (``--report``)."""
    for name, attacked, ref, exe, det in report:
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
            if extra.get("attack_id") == "T2.1":
                _write_refactor(store, attacked)
            if extra.get("attack_id") == "T2.4":
                _write_truncate(store, attacked)
        values = _metrics(store, ref)
        unchanged = _unchanged_fraction(extra)
        if values:
            parts = [f"{k} {values[k]:.4f}" for k in REPORT_METRICS if k in values]
            if unchanged is not None:  # accanto al TPR (richiesta dell'utente, T2.2)
                parts.insert(1, f"unchanged_fraction {unchanged:.4f}")
            sys.stdout.write("  " + "  ".join(parts) + "\n")
        if store.exists(attacked):
            _write_outcomes(store, attacked, ref, exe, det, adapter)
            if extra.get("attack_id") in LLM_REWRITES and store.exists(exe):
                _write_entry(store, attacked, exe)


def _write_entry(store: ArtifactStore, attacked: Any, exe: Any) -> None:
    """Fallimenti di Pass@1 divisi fra funzione d'ingresso rinominata o mancante e altri
    (decisione dell'utente del 28 ottobre 2026, T3.1; ``bench.attacks.interface``)."""
    samples = store.read_table(attacked)
    lang = str(samples["language"].iloc[0])
    problems = {
        str(r["problem_key"]): Problem.model_validate(r)
        for r in store.read_table(problems_ref(LEVEL, lang)).to_dict(orient="records")
    }
    runs = store.read_table(exe)
    status = {str(k): str(v) for k, v in zip(runs["sample_id"], runs["status"], strict=True)}
    got = pass_failures(samples.to_dict(orient="records"), status, problems)
    applied = int((samples["attack_status"] == "APPLIED").sum())
    lost = got["renamed"] + got["missing"]
    sys.stdout.write(
        f"  pass@1 failures {got['failed']}/{got['n']}: entry point renamed {got['renamed']}, "
        f"missing {got['missing']}, other {got['other']}; entry lost in "
        f"{lost}/{applied} APPLIED ({lost / max(1, applied):.4f})\n"
    )


def _write_outcomes(
    store: ArtifactStore, attacked: Any, ref: Any, exe: Any, det: Any, adapter: Any
) -> None:
    """Per ogni cella (decisioni dell'utente del 26 ottobre 2026, T2.4):

    - frazioni di ``UNCHANGED``, ``NOT_APPLICABLE`` e ``TOOL_ERROR`` su tutti i campioni;
    - esito dell'esecuzione dei campioni modificati: non compila (``COMPILE_ERROR``,
      ``SYNTAX_ERROR``) oppure compila ma fallisce (test falliti, errore, timeout);
    - rilevazioni ``FAILED`` (il rilevatore va in errore sul codice attaccato);
    - TPR secondario sui soli campioni modificati (``APPLIED``), con la soglia congelata della
      cella: ``UNCHANGED``, ``NOT_APPLICABLE`` e ``TOOL_ERROR`` esclusi (il TPR principale li
      comprende, I1).
    """
    samples = store.read_table(attacked)
    n = len(samples)
    status = samples.set_index("sample_id")["attack_status"]
    shares = {
        s: float((status == s).sum()) / n for s in ("UNCHANGED", "NOT_APPLICABLE", "TOOL_ERROR")
    }
    sys.stdout.write("  fractions " + "  ".join(f"{k} {v:.4f}" for k, v in shares.items()) + "\n")
    applied = set(status.index[status == "APPLIED"])
    if store.exists(exe):
        runs = store.read_table(exe)
        runs = runs[runs["sample_id"].isin(applied)]
        counts = Counter(str(s) for s in runs["status"])
        compile_errors = counts["COMPILE_ERROR"] + counts["SYNTAX_ERROR"]
        failing = sum(v for k, v in counts.items() if k not in ("PASSED", "COMPILE_ERROR",
                                                               "SYNTAX_ERROR"))  # fmt: skip
        sys.stdout.write(
            f"  exec (APPLIED {len(runs)}): passed {counts['PASSED']}  not compiling "
            f"{compile_errors}  compiling but failing {failing}  {dict(counts)}\n"
        )
    if store.exists(det):
        scores = store.read_table(det)
        detect = Counter(str(s) for s in scores["status"])
        failed = detect["FAILED"] / len(scores) if len(scores) else 0.0
        sys.stdout.write(f"  detection status {dict(detect)}  FAILED fraction {failed:.4f}\n")
        manifest = store.read_manifest(ref) if store.exists(ref) else None
        tau = (manifest.extra or {}).get("threshold") if manifest else None
        if tau is not None:
            view = primary_view(scores[scores["sample_id"].isin(applied)], adapter)
            view = view[view["status"] != "NOT_APPLICABLE"]
            hits = _detected(view, float(tau))
            tpr = float(hits.mean()) if len(hits) else float("nan")
            sys.stdout.write(f"  tpr_at_fpr_changed_only {tpr:.4f} (n={len(hits)})\n")


def _write_truncate(store: ArtifactStore, attacked: Any) -> None:
    """T2.4: distribuzione di n (istruzioni del corpo bersaglio) e quante se ne sono tolte, per
    capire quanto l'attacco ha tagliato davvero; motivi dei ``NOT_APPLICABLE``."""
    table = store.read_table(attacked)
    details = [json.loads(d) if isinstance(d, str) and d else {} for d in table["attack_detail"]]
    ns = sorted(int(d["n"]) for d in details if "n" in d)
    reasons = Counter(str(d["reason"]) for d in details if "reason" in d)
    targets = Counter(str(d["target"]) for d in details if "target" in d)
    if ns:
        q = [ns[min(len(ns) - 1, int(f * len(ns)))] for f in (0.0, 0.25, 0.5, 0.75)] + [ns[-1]]
        removed = [int(d.get("removed", 0)) for d in details if "n" in d]
        sys.stdout.write(
            f"  T2.4 n min/p25/median/p75/max {q}  n=1 {sum(x == 1 for x in ns) / len(ns):.4f}"
            f"  removed mean {sum(removed) / len(removed):.3f}  targets {dict(targets)}\n"
        )
    if reasons:
        sys.stdout.write(f"  T2.4 NOT_APPLICABLE reasons {dict(reasons)}\n")


def _write_refactor(store: ArtifactStore, attacked: Any) -> None:
    """T2.1 (decisioni dell'utente del 26 ottobre 2026): tipi di regola effettivamente applicati
    (media su tutti i campioni a cui l'attacco si applica, UNCHANGED compresi) e, per ACW, la quota
    dei tipi applicati equivalenti a una regola di inserimento della configurazione congelata
    (attacco in parte mirato, da dichiarare nella tesi)."""
    table = store.read_table(attacked)
    table = table[table["attack_status"] != "NOT_APPLICABLE"]
    details = [json.loads(d) if isinstance(d, str) and d else {} for d in table["attack_detail"]]
    if not details:
        return
    n_types = [int(d.get("n_types", 0)) for d in details]
    applicable = [int(d.get("n_types_applicable", 0)) for d in details]
    rejected = Counter(t for d in details for t in d.get("rejected", []))
    applied = Counter(t for d in details for t in d.get("applied", []))
    sys.stdout.write(
        f"  T2.1 n_types mean {sum(n_types) / len(n_types):.3f}"
        f"  n_types_applicable mean {sum(applicable) / len(applicable):.3f}"
        f"  rejected {dict(rejected)}\n  T2.1 applied types {dict(sorted(applied.items()))}\n"
    )
    first = table.iloc[0]
    if first["method"] != "acw":
        return
    det = detection_ref(
        "acw", str(first["model_id"]), str(first["config_hash"]), "positives", LEVEL,
        str(first["language"]), SPLIT,
    )  # fmt: skip
    if not store.exists(det):
        sys.stdout.write("  ACW overlap: no detection of the positives\n")
        return
    extra: dict[str, Any] = next(
        (json.loads(e) for e in store.read_table(det)["extra_json"] if "rule_unchanged" in e), {}
    )
    selected = {int(r) for r in extra.get("rule_unchanged", {})}
    overlap = total = 0
    shares = []
    for d in details:
        hits = sum(1 for rules in d.get("acw_equivalent", {}).values() if selected & set(rules))
        if d.get("n_types"):
            shares.append(hits / int(d["n_types"]))
            overlap += hits
            total += int(d["n_types"])
    sys.stdout.write(
        f"  ACW overlap (regole di inserimento congelate: {sorted(selected)}): tipi applicati "
        f"equivalenti {overlap}/{total} ({overlap / total if total else 0:.1%}), media per "
        f"campione {sum(shares) / len(shares) if shares else 0:.1%}; attacco in parte mirato\n"
    )


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
