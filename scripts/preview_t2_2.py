"""Anteprima dei prompt dell'attacco T2.2 (bozza) su campioni reali, prima dell'approvazione.

Uso (bench-core, gpuq; vedi scripts/preview_t2_2.sh, che lancia il job):
    python scripts/preview_t2_2.py

Requisiti dell'utente del 21 ottobre 2026: prima di approvare il testo dei prompt si mostrano system
prompt, parametri di generazione e un esempio reale prima/dopo per variante. Campioni: il primo
campione della baseline di qwen25_coder_7b su L1 dev per ciascun linguaggio, più un programma
CodeNet Python di L2 dev (lettura da standard input), scelti nell'ordine
``derive_seed(global_seed, "t2_2-preview", problem_key)``. Si riscrivono con entrambe le varianti
(greedy, Llama-3.1-8B-Instruct) e si applica la regola di estrazione D4.
Scrive solo in ``$WMB/tmp/t2_2_preview/``: niente artefatti, nessuna metrica.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem
from bench.generation.code_extractor import FencedCodeExtractor
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.store.artifact_store import ArtifactStore

ATTACKER = "llama31_8b_attacker"
SOURCE_MODEL = "qwen25_coder_7b"
VARIANTS = ("conservative", "full_rewrite")
MAX_NEW_TOKENS = 2048
LANGUAGE_NAMES = {
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
    Language.CPP: "C++",
    Language.JAVASCRIPT: "JavaScript",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render(template: str, language: Language, code: str) -> str:
    """Segnaposto dei prompt: nome del linguaggio, tag della fence, codice."""
    text = template.replace("{{ language }}", LANGUAGE_NAMES[language])
    return text.replace("{{ tag }}", str(language)).replace("{{ code }}", code.rstrip("\n"))


def _pick(
    store: ArtifactStore, global_seed: int, level: str, language: Language, dataset: str | None
) -> tuple[Problem, CodeSample]:
    """Primo problema (ordine del seme) con un campione della baseline estratto correttamente."""
    table = store.read_table(problems_ref(level, str(language)))
    problems = [
        Problem.model_validate(r) for r in table[table["split"] == "dev"].to_dict(orient="records")
    ]
    if dataset is not None:
        problems = [p for p in problems if p.dataset == dataset]
    problems.sort(key=lambda p: derive_seed(global_seed, "t2_2-preview", p.problem_key))
    base = store.read_table(baseline_ref(SOURCE_MODEL, level, str(language), "dev"))
    samples = [CodeSample.model_validate(r) for r in base.to_dict(orient="records")]
    for problem in problems:
        found = sorted(
            (s for s in samples if s.problem_key == problem.problem_key and s.extraction_ok),
            key=lambda s: s.sample_index or 0,
        )
        if found:
            return problem, found[0]
    raise SystemExit(f"no baseline sample for {level} {language} {dataset}")


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

    cfg = load_experiment(["paths=cluster", "stage=metrics"])
    store = ArtifactStore(cfg.paths.artifacts)
    out = cfg.paths.tmp / "t2_2_preview"
    out.mkdir(parents=True, exist_ok=True)
    prompt_dir = cfg.paths.repo / "configs" / "prompt" / "attacks"
    files = {name: prompt_dir / f"{name}.txt" for name in ("system", *VARIANTS)}
    system = files["system"].read_text(encoding="utf-8").strip()
    picks = [
        _pick(store, cfg.global_seed, "L1", lang, None)
        for lang in (Language.PYTHON, Language.JAVA, Language.CPP, Language.JAVASCRIPT)
    ]
    picks.append(_pick(store, cfg.global_seed, "L2", Language.PYTHON, "codenet"))

    spec = cfg.models_catalog[ATTACKER]
    tokenizer = AutoTokenizer.from_pretrained(str(spec.tokenizer_path), local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(spec.path), torch_dtype=torch.bfloat16, local_files_only=True
    ).to("cuda")
    model.eval()
    eos = model.generation_config.eos_token_id
    gen_cfg = GenerationConfig(
        do_sample=False,  # temperatura 0: decodifica greedy
        num_beams=1,
        max_new_tokens=MAX_NEW_TOKENS,
        repetition_penalty=1.0,
        eos_token_id=eos,
        pad_token_id=tokenizer.eos_token_id,
    )
    extractor = FencedCodeExtractor()
    rows: list[dict[str, Any]] = []
    lines = [
        "##### T2.2 preview (draft prompts)",
        f"attacker {spec.hf_repo} @ {spec.snapshot}",
        f"generation {json.dumps(gen_cfg.to_diff_dict(), sort_keys=True)}",
        "decode skip_special_tokens=True clean_up_tokenization_spaces=False",
        *(f"sha256 {name}.txt {_sha256(path)}" for name, path in files.items()),
        "",
        "##### system prompt",
        system,
    ]
    for problem, sample in picks:
        for variant in VARIANTS:
            template = files[variant].read_text(encoding="utf-8")
            user = _render(template, problem.language, sample.code)
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            input_ids = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, return_tensors="pt"
            ).to("cuda")
            with torch.no_grad():
                output = model.generate(
                    input_ids=input_ids,
                    attention_mask=torch.ones_like(input_ids),
                    generation_config=gen_cfg,
                )
            new = output[0, input_ids.shape[1] :]
            # Llama 3.1 ha clean_up_tokenization_spaces=True: toglierebbe lo spazio prima di
            # ``!``, ``.``, ``,``, ``?`` anche nel codice e nelle
            # stringhe (``a != b`` -> ``a!= b``).
            raw = tokenizer.decode(
                new, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            code, ok = extractor.extract(raw, problem)
            truncated = len(new) >= MAX_NEW_TOKENS
            rows.append(
                {
                    "problem_key": problem.problem_key,
                    "dataset": problem.dataset,
                    "language": str(problem.language),
                    "sample_id": sample.sample_id,
                    "variant": variant,
                    "user_prompt": user,
                    "raw_output": raw,
                    "code": code,
                    "extraction_ok": ok,
                    "new_tokens": len(new),
                    "truncated": truncated,
                }
            )
            lines += [
                "",
                f"##### {problem.problem_key} ({problem.dataset}, {problem.language}) {variant}",
                f"extraction_ok {ok}  new_tokens {len(new)}  truncated {truncated}",
                "----- user prompt",
                user,
                "----- raw output",
                raw,
                "----- extracted code",
                code,
            ]
            print(f"{problem.problem_key} {variant}: ok {ok}, {len(new)} tokens", flush=True)
    (out / "preview.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    (out / "preview.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written {out / 'preview.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
