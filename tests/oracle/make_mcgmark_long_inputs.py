"""Input dell'oracle di MCGMark su codice lungo (bench-core, cluster).

    python tests/oracle/make_mcgmark_long_inputs.py

Decisione dell'utente del 7 ottobre 2026: su L1 MCGMark non arriva mai a un ciclo di 24 posizioni,
quindi la catena inserimento → estrazione D4 → rilevazione → messaggio si verifica su codice più
lungo. Scrive ``tests/fixtures/oracle/mcgmark/inputs_long.json`` con lo stesso schema di
``inputs.json``:
- i 3 problemi CodeNet di ``configs/dataset/codenet_excluded.yaml`` (uso ``oracle_mcgmark``,
  esclusi dalla selezione della M9), con la descrizione HTML senza tag;
- 6 classi di ClassEval, in ordine ``derive_seed(global_seed, "classeval-oracle-mcgmark",
  task_id)`` fra quelle con almeno 5 metodi; codici fissati: le loro soluzioni canoniche;
- template provvisori ``tests/oracle/templates/`` (solo oracle), system prompt bloccato;
- decoding neutro dei livelli 2-4 (``max_new_tokens`` 1024) con n = 1; seme e messaggio del
  campione 0 (schema ``per_sample``).
I campioni servono solo alla verifica: nessuna metrica e nessuna scelta di iperparametri.
"""

from __future__ import annotations

import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from bench.config.builder import load_experiment, repo_root
from bench.data.codenet_exclusions import load_excluded
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.base import PromptEmbedder
from bench.methods.worker_client import WorkerClient
from bench.pipeline.stages.watermark import make_adapter
from jinja2 import Environment, FileSystemLoader, StrictUndefined

MODEL = "qwen25_coder_7b"
METHOD = "mcgmark"
N_CLASSEVAL = 6
MIN_METHODS = 5
BLOCKS = {"p", "div", "br", "li", "pre", "h1", "h2", "h3", "h4", "section", "tr"}


class _Text(HTMLParser):
    """Testo di una descrizione HTML di CodeNet: tag rimossi, a capo sui blocchi."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    lines = [line.rstrip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=watermark"])
    adapter = make_adapter(cfg, METHOD)
    adapter.worker = WorkerClient({}, Path("."), 1)  # serve solo per chiave e iperparametri
    assert isinstance(adapter, PromptEmbedder)
    model = cfg.models_catalog[MODEL]
    system = PromptBuilder.from_config(cfg.prompt).system_prompt
    env = Environment(
        loader=FileSystemLoader(str(repo_root() / "tests" / "oracle" / "templates")),
        undefined=StrictUndefined,
        autoescape=False,
    )
    problems: list[Problem] = []
    excluded = list(load_excluded(cfg.datasets["codenet"].file("excluded")))
    if len(excluded) != 3:
        raise SystemExit("run scripts/oracle_method.sh mcgmark select first (3 CodeNet problems)")
    desc_dir = cfg.datasets["codenet"].dirs["problem_descriptions"]
    for key in excluded:
        pid = key.split("/", 1)[1]
        text = html_text((desc_dir / f"{pid}.html").read_text(encoding="utf-8", errors="replace"))
        problems.append(_problem(key, "codenet", "L2", None, text, None, None))
    classeval = json.loads(cfg.datasets["classeval"].file("data").read_text(encoding="utf-8"))
    eligible = [c for c in classeval if len(c["methods_info"]) >= MIN_METHODS]
    eligible.sort(
        key=lambda c: derive_seed(cfg.global_seed, "classeval-oracle-mcgmark", c["task_id"])
    )
    for c in eligible[:N_CLASSEVAL]:
        problems.append(
            _problem(
                f"classeval/{c['task_id']}",
                "classeval",
                "L3",
                "test",
                c["skeleton"],
                c["class_name"],
                c["solution_code"],
            )
        )
    prompts, codes = [], []
    for problem in problems:
        template = env.get_template(f"{problem.dataset}_python.j2")
        prompt = (
            problem.prompt_text
            if problem.prompt_text.endswith("\n")
            else problem.prompt_text + "\n"
        )
        user = template.render(prompt=prompt, entry_point=problem.entry_point)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        message = adapter.expected_message(model, problem, 0)
        prompts.append(
            {
                "problem_key": problem.problem_key,
                "language": "python",
                "seed": adapter.sample_seed(model, problem, 0),
                "messages": messages,
                "message": message,
                "problem": problem.model_dump(mode="json"),
            }
        )
        if problem.canonical_solution:
            codes.append(
                {
                    "id": f"canonical:{problem.problem_key}",
                    "language": "python",
                    "code": problem.canonical_solution,
                    "messages": messages,
                    "message": message,
                }
            )
    decoding = cfg.decoding["level234"].model_copy(update={"n": 1})
    data = {
        "method": METHOD,
        "model_id": MODEL,
        "model_path": str(model.path),
        "key": adapter.key("k1"),
        "native_hparams": adapter.to_native_hparams(adapter.default_hparams()),
        "decoding": {**neutral_settings(decoding), "torch_dtype": model.dtype},
        "prompts": prompts,
        "codes": codes,
    }
    out = repo_root() / "tests" / "fixtures" / "oracle" / METHOD / "inputs_long.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")  # scrittura atomica (job paralleli)
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    sys.stdout.write(f"wrote {out} ({len(prompts)} prompts, {len(codes)} codes)\n")
    return 0


def _problem(
    key: str,
    dataset: str,
    level: str,
    split: str | None,
    prompt: str,
    entry_point: str | None,
    solution: str | None,
) -> Problem:
    return Problem.model_validate(
        {
            "problem_key": key,
            "dataset": dataset,
            "level": level,
            "language": "python",
            "split": split,
            "prompt_text": prompt,
            "entry_point": entry_point,
            "canonical_solution": solution,
            "test_ref": None,
            "contamination_risk": False,
            "loc_to_generate": None,
        }
    )


if __name__ == "__main__":
    sys.exit(main())
