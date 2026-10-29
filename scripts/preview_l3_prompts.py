"""Anteprima dei prompt del Livello 3 prima della generazione (TODO 35, ADR-016 §2).

Uso (bench-core, nodo di login, nessun job; dopo ``bash scripts/submit_l3.sh data``):
    python scripts/preview_l3_prompts.py | tee docs/milestone_logs/M13_l3_prompts.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Per ogni linguaggio: problemi e negativi per dataset, conteggi dei filtri del pool dei prompt,
funzioni escluse perché già negativi di integrazione del test L1 (D39), tipi di commento di
documentazione e righe del corpo; poi, per ogni dataset senza test (CodeSearchNet, The Stack), il
messaggio utente completo di ``N_EXAMPLES`` prompt (nell'ordine ``derive_seed(global_seed,
"l3-preview", problem_key)``) e la funzione umana da cui viene.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.generation.prompt_builder import PromptBuilder
from bench.pipeline.stages.prepare_data import native_ref, problems_ref, split_ref
from bench.store.artifact_store import ArtifactStore

LANGUAGES = ("python", "java", "javascript", "cpp")
N_EXAMPLES = 2


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=prepare_data", "levels=[L3]"])
    store = ArtifactStore(cfg.paths.artifacts)
    prompts = PromptBuilder.from_config(cfg.prompt)
    w = sys.stdout.write
    split = split_ref("L3")
    if not store.exists(split):
        w("L3 data not prepared: run bash scripts/submit_l3.sh data\n")
        return 1
    manifest = store.read_manifest(split)
    counts = (manifest.extra or {}).get("counts", {}) if manifest else {}
    w(f"##### L3 split: {json.dumps(counts)}\n")
    for lang in LANGUAGES:
        ref, nat = problems_ref(Language(lang), "L3"), native_ref(Language(lang), "L3")
        if not store.exists(ref):
            w(f"\n##### {lang}: MISSING\n")
            continue
        table = store.read_table(ref)
        natives = store.read_table(nat)
        m = store.read_manifest(ref)
        extra: dict[str, Any] = (m.extra or {}) if m else {}
        w(f"\n##### {lang}: problems {dict(table['dataset'].value_counts())}, "
          f"negatives {dict(natives['dataset'].value_counts())}\n")  # fmt: skip
        w(f"  loc_to_generate: {json.dumps(extra.get('loc_to_generate', {}))}\n")
        for dataset, info in extra.get("sources", {}).items():
            w(f"  {dataset}:\n")
            for key, value in info.items():
                shown = value
                if key == "prompt_sampling":
                    shown = {
                        k: value.get(k) for k in ("pool", "selected_loc", "moved_between_bins")
                    }
                w(f"    {key}: {json.dumps(shown)}\n")
        problems = [Problem.model_validate(r) for r in table.to_dict(orient="records")]
        for dataset in sorted({p.dataset for p in problems} - {"classeval"}):
            chosen = sorted(
                (p for p in problems if p.dataset == dataset),
                key=lambda p: derive_seed(cfg.global_seed, "l3-preview", p.problem_key),
            )[:N_EXAMPLES]
            for p in chosen:
                w(f"\n  ----- {dataset} {lang} {p.problem_key} (entry_point {p.entry_point}, "
                  f"loc_to_generate {p.loc_to_generate})\n")  # fmt: skip
                w("  [user message]\n")
                w(_indent(prompts.render_user(p)))
                w("\n  [human function]\n")
                w(_indent(p.canonical_solution or ""))
    return 0


def _indent(text: str) -> str:
    return "".join(f"    | {line}\n" for line in text.rstrip("\n").split("\n"))


if __name__ == "__main__":
    raise SystemExit(main())
