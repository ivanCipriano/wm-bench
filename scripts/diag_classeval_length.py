"""Lunghezza in token delle risposte di ClassEval (diagnosi del troncamento del pilota, M13).

Uso (bench-core, nodo di login, nessun job; solo i tokenizer, niente GPU):
    python scripts/diag_classeval_length.py | tee docs/milestone_logs/M13_classeval_length.txt
    (file nuovo: non riscrivere un log già committato; se esiste, usare un altro nome)

Per ciascun modello e per le 94 classi di L3, con il tokenizer del modello:

- ``skeleton``: token dello scheletro (docstring comprese), cioè quanto costa ripeterlo;
- ``solution``: token della canonica (solo codice, senza docstring);
- ``full``: token della risposta ideale con le docstring, stimata come scheletro + righe della
  canonica che non sono già nello scheletro, dentro il blocco recintato;

e quante classi superano 1.024, 1.536 e 2.048 token. Poi, dal pilota
(``$WMB/tmp/classeval_pilot/generate.json``), per i campioni non troncati quanti ripetono le
docstring dello scheletro e, per i troncati, la fine dell'output (ripetizioni o classe lunga).
"""

from __future__ import annotations

import json
import statistics
import sys
from typing import Any

from bench.config.builder import load_experiment
from bench.data.loaders.classeval import ClassEvalLoader
from bench.domain.enums import Language

MODELS = ("qwen25_coder_7b", "deepseek_coder_6p7b")
LIMITS = (1024, 1536, 2048)
TAIL_CHARS = 300


def full_answer(skeleton: str, solution: str) -> str:
    """Risposta ideale: scheletro + righe della canonica non già presenti nello scheletro."""
    present = {line.strip() for line in skeleton.split("\n") if line.strip()}
    extra = [line for line in solution.split("\n") if line.strip() and line.strip() not in present]
    return "```python\n" + skeleton.rstrip() + "\n" + "\n".join(extra) + "\n```"


def main() -> int:
    from transformers import AutoTokenizer

    cfg = load_experiment(["paths=cluster", "stage=generate_baseline"])
    problems = ClassEvalLoader(cfg.datasets["classeval"]).load_problems(Language.PYTHON)
    w = sys.stdout.write
    for model_id in MODELS:
        spec = cfg.models_catalog[model_id]
        tok = AutoTokenizer.from_pretrained(str(spec.tokenizer_path), local_files_only=True)

        def n(text: str, tok: Any = tok) -> int:
            return len(tok(text, add_special_tokens=False)["input_ids"])

        rows = [
            (p.problem_key, n(p.prompt_text), n(str(p.canonical_solution)),
             n(full_answer(p.prompt_text, str(p.canonical_solution))))
            for p in problems
        ]  # fmt: skip
        w(f"\n## {model_id} ({len(rows)} classes)\n")
        for i, name in ((1, "skeleton"), (2, "solution"), (3, "full")):
            vals = sorted(int(r[i]) for r in rows)
            over = ", ".join(f"> {lim}: {sum(v > lim for v in vals)}" for lim in LIMITS)
            w(f"  {name:<9} median {statistics.median(vals):.0f}, max {vals[-1]}; {over}\n")
        w("  longest (full tokens):\n")
        for key, sk, so, fu in sorted(rows, key=lambda r: -r[3])[:10]:
            w(f"    {key:<26} full {fu:5d} skeleton {sk:5d} solution {so:5d}\n")
    pilot = cfg.paths.tmp / "classeval_pilot" / "generate.json"
    if not pilot.is_file():
        w(f"\n{pilot} not found: pilot part skipped\n")
        return 0
    data = json.loads(pilot.read_text(encoding="utf-8"))
    skeletons = {p.problem_key: p.prompt_text for p in problems}
    for model_id in MODELS:
        mine = [r for r in data["rows"] if r["model_id"] == model_id]
        done = [r for r in mine if not r["truncated"]]
        doc = [r for r in done if '"""' in r["code"] and '"""' in skeletons[r["problem_key"]]]
        w(f"\n## pilot {model_id}: not truncated {len(done)}, repeating docstrings {len(doc)}\n")
        for r in (r for r in mine if r["truncated"]):
            tail = r["raw_output"][-TAIL_CHARS:]
            w(f"  truncated {r['problem_key']} #{r['sample_index']} ({r['new_tokens']} tokens), "
              f"end: {tail!r}\n")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
