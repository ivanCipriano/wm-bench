"""Anteprima dei prompt di CodeNet da approvare (Milestone 9). Nodo di login, nessun job.

Uso (con bench-core, dopo ``codenet_select.py finalize``):
    python scripts/codenet_preview.py [--pid p02547]

Stampa:
1. i 4 template in bozza (``configs/prompt/drafts/codenet_<lang>.j2``) riga per riga, con i
   caratteri non visibili resi espliciti (``·`` spazio finale, ``⇥`` tabulazione, ``⏎`` fine riga);
2. per i 250 problemi selezionati: quanti hanno formule, tabelle, blocchi ``<pre>``, immagini e
   caratteri non inglesi dopo la conversione;
3. un problema selezionato reale (con formule e, se c'è, una tabella), con un frammento dell'HTML
   originale accanto al testo convertito, e il messaggio utente completo per ogni linguaggio.

I template restano bozze finché l'utente non li approva: solo allora vanno in
``configs/prompt/user/`` (cartella dei testi approvati, i cui hash entrano nei manifest).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from bench.config.builder import load_experiment, repo_root
from bench.data.codenet import describe
from bench.domain.enums import Language
from bench.generation.prompt_builder import load_system_prompt

DRAFTS = Path("configs") / "prompt" / "drafts"


def _visible(line: str) -> str:
    stripped = line.rstrip(" ")
    return (stripped + "·" * (len(line) - len(stripped))).replace("\t", "⇥") + "⏎"


def _fragment(html: str, pattern: str, width: int = 400) -> str | None:
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    start = max(0, match.start() - 50)
    return html[start : start + width]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", default=None, help="problema da mostrare (default: automatico)")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=prepare_data"])
    spec = cfg.datasets["codenet"]
    drafts = repo_root() / DRAFTS
    env = Environment(
        loader=FileSystemLoader(str(drafts)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=False,
    )
    out = sys.stdout.write

    out("##### 1. template in bozza (configs/prompt/drafts), riga per riga\n")
    for lang in Language:
        path = drafts / f"codenet_{lang}.j2"
        out(f"\n--- {path.name}\n")
        for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            out(f"{i:>2} | {_visible(line)}\n")
    out("\nsystem prompt (fisso, uguale per tutti i livelli):\n")
    out(load_system_prompt(cfg.prompt) + "\n")

    selected = yaml.safe_load(spec.file("selected").read_text(encoding="utf-8"))
    pids = [p["problem_key"].split("/", 1)[1] for p in selected["problems"]]
    html = {
        p: (spec.dirs["problem_descriptions"] / f"{p}.html").read_text(
            encoding="utf-8", errors="replace"
        )
        for p in pids
    }
    desc = {p: describe(h) for p, h in html.items()}

    out("\n##### 2. conversione sui problemi selezionati\n")
    n_pre = sum(1 for p in pids if "```" in desc[p].text)
    out(f"problems: {len(pids)}\n")
    out(f"with math (TeX $...$ or <var>): {sum(desc[p].has_math for p in pids)}\n")
    out(f"with tables: {sum(desc[p].has_table for p in pids)}\n")
    out(f"with <pre> blocks: {n_pre}\n")
    out(f"with images: {sum(desc[p].n_images > 0 for p in pids)}\n")
    out(f"with Japanese characters after conversion: {sum(desc[p].has_japanese for p in pids)}\n")
    bilingual = sum(1 for p in pids if re.search(r"class=[\"']lang-en", html[p]))
    out(f"AtCoder bilingual pages (only span.lang-en kept): {bilingual}\n")
    non_ascii = sorted({ch for p in pids for ch in desc[p].text if not ch.isascii()}, key=ord)
    out(f"non-ASCII characters left in the texts: {''.join(non_ascii)[:200]!r}\n")

    pid = args.pid or next(
        (p for p in pids if desc[p].has_math and desc[p].has_table),
        next((p for p in pids if desc[p].has_math), pids[0]),
    )
    out(f"\n##### 3. esempio reale: {pid}\n")
    for label, pattern in (("formula", r"<var>|\$|\\\("), ("tabella", r"<table")):
        frag = _fragment(html[pid], pattern)
        if frag is not None:
            out(f"\nHTML originale ({label}, frammento):\n{frag}\n")
    out(f"\ntesto convertito (inserito parola per parola nel template):\n{desc[pid].text}\n")
    for lang in Language:
        user = env.get_template(f"codenet_{lang}.j2").render(prompt=desc[pid].text)
        out(f"\n--- messaggio utente, {lang}\n{user}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
