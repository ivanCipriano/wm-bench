"""Controllo ridotto di T3.1 senza watermark (decisione dell'utente del 28 ottobre 2026; ADR-014
§3.5): quanta parte delle rinomine della funzione d'ingresso si deve al watermark.

Uso (con bench-core):
    python scripts/t3_1_nowm_control.py --estimate        # nodo di login: costo, nessun job
    LOG=<file nuovo> bash scripts/t3_1_nowm_control.sh    # job GPU (STONE, DeepSeek)

Stessa riscrittura di T3.1 (``RemarkAttack``: system prompt e ``conservative.txt``, decoding di
generazione di L1, seme del campione di T3.1, regola D4) con il generatore del metodo e la
configurazione congelata, ma **δ = 0**: il processor di STONE non sposta i logit, quindi il
campionamento è quello del modello senza watermark (la chiave non conta). Default: STONE,
DeepSeek, Java e C++, ``--n 200`` campioni marcati per linguaggio, i primi nell'ordine
``derive_seed(global_seed, "t3_1-nowm-control", sample_id)``.

Per linguaggio, sugli stessi campioni: funzione d'ingresso rinominata o mancante
(``bench.attacks.interface``) senza watermark e in T3.1 (k2), sui campioni riscritti (``APPLIED``)
e sul totale, con intervallo di Clopper-Pearson al 95%; coppie in cui solo uno dei due rinomina.
Con ``--estimate`` stampa solo il costo atteso: somma di ``elapsed_s`` di T3.1 sugli stessi
campioni (δ = 0 non cambia il costo della generazione). Tutto in ``$WMB/tmp/t3_1_nowm_control``,
niente negli artefatti del benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from bench.attacks.base import AttackContext, MethodSetup, grid_attacks, sample_rng
from bench.attacks.interface import KEPT, entry_status
from bench.config.builder import load_experiment
from bench.domain.enums import Language
from bench.domain.ids import derive_seed
from bench.domain.models import CodeSample, Problem
from bench.generation.decoding import neutral_settings
from bench.hpo.freeze import load_freeze
from bench.hpo.grid import resolve
from bench.lang.parsers import default_parsers
from bench.metrics.stats import clopper_pearson
from bench.pipeline.stages.attack import attacked_ref
from bench.pipeline.stages.generate_baseline import decoding_for, problems_ref
from bench.pipeline.stages.watermark import make_adapter, watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL, SPLIT, ATTACK = "L1", "test", "T3.1"


def clean(row: dict[Any, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        try:
            missing = bool(pd.isna(value))
        except (TypeError, ValueError):
            missing = False
        out[str(key)] = None if missing else value
    return out


def rate(k: int, n: int) -> str:
    lo, hi = clopper_pearson(k, n)
    return f"{k}/{n} = {k / max(1, n):.3f} [{lo:.3f}; {hi:.3f}]"


def lost(code: str, status: str, problem: Problem, original: str) -> bool:
    return status == "APPLIED" and entry_status(code, problem, original).status != KEPT


def t31_seconds(results: Path, ids: set[str]) -> float:
    """Somma di ``elapsed_s`` dei risultati di T3.1 per gli item scelti (id = campione marcato)."""
    secs = 0.0
    with results.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                r = json.loads(line)
                if r.get("item_id") in ids:
                    secs += float(r.get("elapsed_s") or 0.0)
    return secs


def compare(
    parents: list[CodeSample], outcomes: list[Any], t31: Any, problems: dict[str, Problem]
) -> None:
    """Funzione persa senza watermark e in T3.1 sugli stessi campioni."""
    nowm_lost = t31_lost = nowm_applied = t31_applied = only_nowm = only_t31 = 0
    for p, out in zip(parents, outcomes, strict=True):
        problem = problems[p.problem_key]
        a = lost(out.code, str(out.status), problem, p.code)
        row = t31.loc[p.sample_id]
        b = lost(str(row["code"]), str(row["attack_status"]), problem, p.code)
        nowm_applied += str(out.status) == "APPLIED"
        t31_applied += str(row["attack_status"]) == "APPLIED"
        nowm_lost += a
        t31_lost += b
        only_nowm += a and not b
        only_t31 += b and not a
    n = len(parents)
    sys.stdout.write(
        f"  senza watermark (delta 0): APPLIED {nowm_applied}/{n}; funzione persa sugli "
        f"APPLIED {rate(nowm_lost, nowm_applied)}, sul totale {rate(nowm_lost, n)}\n"
        f"  T3.1 (k2), stessi campioni: APPLIED {t31_applied}/{n}; funzione persa sugli "
        f"APPLIED {rate(t31_lost, t31_applied)}, sul totale {rate(t31_lost, n)}\n"
        f"  coppie discordanti: solo senza watermark {only_nowm}, solo T3.1 {only_t31}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--method", default="stone")
    parser.add_argument("--model", default="deepseek_coder_6p7b")
    parser.add_argument("--languages", nargs="+", default=["java", "cpp"])
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--estimate", action="store_true")
    args = parser.parse_args()
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    record = load_freeze(store, args.method)
    if record is None:
        sys.stdout.write(f"##### {args.method}: NOT FROZEN\n")
        return 1
    attack = next(a for a in grid_attacks(ATTACK).values() if a.params.get("key_id") == "k2")
    adapter = make_adapter(cfg, args.method)
    model = cfg.models_catalog[args.model]
    work = Path(cfg.paths.tmp) / "t3_1_nowm_control"
    total_s = 0.0
    for lang in args.languages:
        cell = next(
            c for c in record.cells
            if c.model_id == args.model and c.language == lang and c.config_hash
        )  # fmt: skip
        h = str(cell.config_hash)
        ph = attack.params_hash(lang)
        table = store.read_table(watermarked_ref(args.method, args.model, h, LEVEL, lang, SPLIT))
        columns = list(
            CodeSample.model_fields
        )  # come la fase attack: senza le colonne d'inserimento
        rows = [clean(r) for r in table[columns].to_dict(orient="records")]
        rows.sort(key=lambda r: derive_seed(cfg.global_seed, "t3_1-nowm-control", r["sample_id"]))
        parents = [CodeSample.model_validate(r) for r in rows[: args.n]]
        ids = {p.sample_id for p in parents}
        run_t31 = store.root.joinpath(
            "_runs", "attack", ATTACK, ph, args.method, args.model, h, f"{LEVEL}_{lang}_{SPLIT}"
        )
        secs = t31_seconds(run_t31 / "results.jsonl", ids)
        total_s += secs
        sys.stdout.write(
            f"\n##### {args.method} {args.model} {lang}: {len(parents)} campioni marcati, "
            f"costo atteso {secs / 3600:.2f} h (elapsed_s di T3.1 sugli stessi campioni)\n"
        )
        if args.estimate:
            continue
        problems = {
            str(r["problem_key"]): Problem.model_validate(r)
            for r in store.read_table(problems_ref(LEVEL, lang)).to_dict(orient="records")
        }
        hp, _, _ = resolve(cfg, adapter, h)
        setup = MethodSetup(
            adapter,
            {**hp, "delta": 0.0},  # watermark spento: il processor non sposta i logit
            model,
            {**neutral_settings(decoding_for(cfg, LEVEL)), "torch_dtype": model.dtype},
            problems,
            work / args.model / f"{LEVEL}_{lang}_{SPLIT}",
            "cuda:0",
        )
        parsers = default_parsers()
        ctxs = [
            AttackContext(
                rng=sample_rng(cfg.global_seed, ATTACK, ph, p.sample_id),  # seme di T3.1
                problem=problems.get(p.problem_key),
                parsers=parsers,
                sample_id=p.sample_id,
            )
            for p in parents
        ]
        outcomes = attack.apply_with_method(parents, Language(lang), ctxs, setup)
        t31 = store.read_table(attacked_ref(ATTACK, ph, args.method, args.model, h, LEVEL, lang,
                                            SPLIT))  # fmt: skip
        compare(parents, outcomes, t31[t31["parent_id"].isin(ids)].set_index("parent_id"), problems)
    sys.stdout.write(f"\nTOTALE costo atteso {total_s / 3600:.2f} h di GPU (più il caricamento)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
