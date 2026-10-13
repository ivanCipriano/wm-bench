"""Confronto delle lunghezze dei negativi umani di sviluppo, L1 e L2 (Milestone 9, TODO 3).

Uso (con bench-core, dopo ``codenet_select.py finalize``; job CPU su defq, The Stack è lento):
    python scripts/compare_dev_negatives.py [--languages python,java,cpp,javascript]

Per ogni linguaggio confronta i decili delle righe di codice (SPEC §10.3, ``count_code_lines`` sul
codice intero) di:
- nativi dev di L1 (``data/negatives/L1_<lang>_native.parquet``, parte dev);
- nativi dev di L2 (5 negativi per problema CodeNet dev, da ``codenet_selected.yaml``);
- **opzione 1**: nativi L1 + integrazione dev esistente (``L1_<lang>_integration_dev``) + nativi L2;
- **opzione 2**: nativi L1 + nativi L2 + integrazione ricampionata (``StratifiedSampler``) sulla
  distribuzione combinata dei nativi L1+L2, fino a ``min_dev_negatives`` in tutto, dagli stessi
  candidati di ``prepare_data`` (stessi filtri e deduplicazione), con le righe di §10.3.

Distanza di Kolmogorov–Smirnov (statistica a due campioni) di ciascun pool dalla distribuzione
combinata dei nativi L1+L2. Regola dell'utente (13 ottobre 2026): KS dell'opzione 1 < 0,1 in tutti i
linguaggi → opzione 1 (deviazione registrata), altrimenti opzione 2 (TODO 3) con nuova rilevazione.
Per confronto si stampano anche i decili di ``function_loc`` (la misura della stratificazione di
L1, ADR-004), che sui programmi CodeNet conta solo le righe dentro le funzioni.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
import yaml

from bench.config.builder import load_experiment
from bench.data.codenet import LANG_NAMES
from bench.data.negatives import StratifiedSampler, code_fingerprint, deduplicate, ks_statistic
from bench.data.splitter import ProblemSplitter
from bench.domain.enums import Language, Level, Split
from bench.domain.errors import DataError
from bench.domain.ids import derive_seed
from bench.domain.models import Problem
from bench.lang.functions import function_loc
from bench.lang.line_counter import count_code_lines
from bench.lang.parsers import default_parsers
from bench.pipeline.stages.prepare_data import PrepareDataStage, integration_ref, native_ref
from bench.store.artifact_store import ArtifactStore

KS_THRESHOLD = 0.1
DECILES = [i / 10 for i in range(11)]


def deciles(values: Sequence[float]) -> str:
    q = np.quantile(np.asarray(values, dtype=float), DECILES)
    return " ".join(f"{v:5.0f}" for v in q)


def l2_dev_codes(cfg: Any, lang: Language) -> list[str]:
    """Codice dei 5 negativi dei problemi CodeNet di sviluppo."""
    spec = cfg.datasets["codenet"]
    selected = yaml.safe_load(spec.file("selected").read_text(encoding="utf-8"))
    problems = [
        Problem(
            problem_key=p["problem_key"],
            dataset="codenet",
            level=Level.L2,
            language=lang,
            split=None,
            prompt_text="",
            entry_point=None,
            canonical_solution=None,
            test_ref=None,
            contamination_risk=False,
            loc_to_generate=None,
        )
        for p in selected["problems"]
    ]
    split = ProblemSplitter(cfg.split, cfg.global_seed).assign(problems)
    codes = []
    for p in selected["problems"]:
        if split[p["problem_key"]] is not Split.DEV:
            continue
        pid = p["problem_key"].split("/", 1)[1]
        for neg in p["submissions"][str(lang)]["negatives"]:
            path = (
                spec.dirs["data"] / pid / LANG_NAMES[lang] / f"{neg['submission_id']}.{neg['ext']}"
            )
            codes.append(path.read_text(encoding="utf-8", errors="replace"))
    return codes


def main() -> int:  # noqa: PLR0915 - un rapporto lineare per linguaggio
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--languages", default="python,java,cpp,javascript")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=prepare_data", "levels=[L1]"])
    store = ArtifactStore(cfg.paths.artifacts)
    parsers = default_parsers()
    stage = PrepareDataStage(cfg)
    out = sys.stdout.write
    verdicts: dict[str, float] = {}
    for lang in (Language(x) for x in args.languages.split(",")):

        def lines(code: str, lang: Language = lang) -> int:
            return count_code_lines(code, lang, parsers=parsers)

        natives = store.read_table(native_ref(lang))
        l1 = [str(c) for c in natives[natives["split"] == "dev"]["code"]]
        integ = [str(c) for c in store.read_table(integration_ref(lang, "dev"))["code"]]
        l2 = l2_dev_codes(cfg, lang)
        l1_n, integ_n, l2_n = (
            [lines(c) for c in l1],
            [lines(c) for c in integ],
            [lines(c) for c in l2],
        )
        combined = l1_n + l2_n
        option1 = l1_n + integ_n + l2_n

        # Opzione 2: stessi candidati e filtri di prepare_data, righe di §10.3.
        n_new = max(0, cfg.negatives.min_dev_negatives - len(l1) - len(l2))
        cands, _, source = stage._candidates(lang, "dev")
        cands = [dataclasses.replace(c, loc=lines(c.code)) for c in cands]
        cands, _ = deduplicate(cands, [code_fingerprint(c) for c in l1 + l2])
        seed = derive_seed(cfg.global_seed, "sample", source, str(lang), "l1l2_dev_negatives")
        try:
            result = StratifiedSampler(cfg.negatives.n_bins).sample(combined, cands, n_new, seed)
            option2: list[int] | None = combined + [c.loc for c in result.selected]
            moved = result.moved
            out_of_range = result.details.get("out_of_range")
        except DataError as exc:
            option2, moved, out_of_range = None, None, str(exc)

        ks1 = ks_statistic(option1, combined)
        verdicts[str(lang)] = ks1
        out(f"\n##### {lang}  (righe §10.3; decili 0%,10%,...,100%)\n")
        rows = [
            ("L1 native dev", l1_n),
            ("L2 native dev", l2_n),
            ("combined natives L1+L2", combined),
            ("option 1 pool", option1),
        ]
        if option2 is not None:
            rows.append(("option 2 pool", option2))
        for name, vals in rows:
            out(f"{name:<24} n={len(vals):>5}  {deciles(vals)}\n")
        out(f"KS(option 1, combined natives) = {ks1:.4f}\n")
        if option2 is not None:
            out(
                f"KS(option 2, combined natives) = {ks_statistic(option2, combined):.4f}  "
                f"(new integration {n_new}, moved between bins {moved}, "
                f"out of range {out_of_range})\n"
            )
        else:
            out(f"option 2 not feasible with the current candidates: {out_of_range}\n")
        fl = {
            "L1 native dev": [function_loc(c, lang, parsers) for c in l1],
            "L2 native dev": [function_loc(c, lang, parsers) for c in l2],
            "L1 integration dev": [function_loc(c, lang, parsers) for c in integ],
        }
        out("function_loc (misura della stratificazione di L1, solo per confronto):\n")
        for name, vals in fl.items():
            zeros = sum(1 for v in vals if v == 0)
            out(f"  {name:<22} n={len(vals):>5}  {deciles(vals)}  (zero: {zeros})\n")
        frame = pd.Series(l2_n)
        out(f"L2 native dev lines: mean {frame.mean():.1f}, median {frame.median():.0f}\n")

    out("\n##### verdetto (regola dell'utente: KS dell'opzione 1 < 0,1 in tutti i linguaggi)\n")
    for name, ks in verdicts.items():
        out(f"{name:<11} KS {ks:.4f}  {'< 0.1' if ks < KS_THRESHOLD else '>= 0.1'}\n")
    ok = all(ks < KS_THRESHOLD for ks in verdicts.values())
    out(
        "VERDICT: "
        + (
            "option 1 (freeze L1 integration, add L2 natives)"
            if ok
            else "option 2 (resample the integration, re-detect neg_human)"
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
