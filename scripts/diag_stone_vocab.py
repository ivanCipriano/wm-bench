"""Verifiche sul punteggio di STONE con la GPU (Milestone 8, prima dell'HPO). Job gpuq, circa 1 h.

Uso (con bench-core, su un nodo NVIDIA; comando completo fornito con le istruzioni della M8):
    python scripts/diag_stone_vocab.py [--artifacts DIR] [--out DIR] [--n 100] [--n-problems 20]

Due prove, entrambe con la catena del framework (adapter, worker e shim di STONE, nessuna patch);
l'unica differenza è ``vocab_size`` passato allo shim, che lo legge già dagli iperparametri:

1. **Vocabolario** (Qwen, Python/Java/C++): un campione di positivi della M7, di negativi umani
   nativi e di integrazione ripunteggiato con ``vocab_size`` 50.272 (autori, ``run.py``) e con il
   vocabolario del generatore (D15). Confronta z ufficiale, frazione di verdi fra i non sintattici
   e AUROC. I conteggi di token sintattici e non sintattici non dipendono dal vocabolario.
2. **Configurazione degli autori** (Qwen, Python): 20 problemi HumanEval+ e 20 MBPP+ di sviluppo,
   5 campioni ciascuno, generati con ``vocab_size`` 50.272, γ = 0,5 e δ del repository (0,5 per
   HumanEval, 1,0 per MBPP+); AUROC dello z ufficiale contro le soluzioni canoniche degli stessi
   problemi, come ``run.py``.

Tutto fuori dagli artefatti del benchmark (``--out``, default ``$WMB/tmp/diag_stone``).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from bench.config.builder import load_experiment
from bench.domain.models import Problem
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.adapters.stone import StoneAdapter
from bench.methods.base import DetectInput
from bench.pipeline.stages.generate_baseline import decoding_for
from bench.pipeline.stages.watermark import KEY_ID, WORKER_FACTORY

WMB = os.environ.get("WMB", "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench")
MODEL = "qwen25_coder_7b"
LANGS = ["python", "java", "cpp"]
AUTHORS_VOCAB = 50272
INTEGRATION = {"codesearchnet", "thestack_cpp"}
AUTHORS_DELTA = {"humanevalplus": 0.5, "mbppplus": 1.0}  # run.sh del repository
MIN_GROUP = 5


class VocabStone(StoneAdapter):
    """STONE con ``vocab_size`` esplicito negli iperparametri (solo per questa diagnosi)."""

    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        vocab = hp.get("vocab_size")
        native = super().to_native_hparams({k: v for k, v in hp.items() if k != "vocab_size"})
        native["vocab_size"] = vocab
        return native


def auroc(pos: list[float], neg: list[float]) -> float:
    p = [x for x in pos if x is not None and not math.isnan(x)]
    n = [x for x in neg if x is not None and not math.isnan(x)]
    if len(p) < MIN_GROUP or len(n) < MIN_GROUP:
        return math.nan
    labels = np.concatenate([np.ones(len(p)), np.zeros(len(n))])
    return float(roc_auc_score(labels, np.array(p + n)))


def score_rows(results: list[Any]) -> list[dict[str, float]]:
    """z ufficiale, z_nonsyntax e frazione di verdi fra i non sintattici per risultato."""
    rows = []
    for r in results:
        extra = r.extra or {}
        n_ns = float(extra.get("n_nonsyntax") or 0)
        rows.append(
            {
                "z": float(r.score) if r.score is not None else math.nan,
                "z_ns": float(extra["z_nonsyntax"])
                if extra.get("z_nonsyntax") is not None
                else math.nan,
                "green": float(extra.get("n_green_nonsyntax") or 0) / n_ns if n_ns else math.nan,
            }
        )
    return rows


class Diag:
    def __init__(self, artifacts: Path, out: Path) -> None:
        self.cfg = load_experiment(["paths=cluster", "stage=watermark"])
        method_cfg = self.cfg.methods_catalog["stone"]
        self.adapter = VocabStone(
            method_cfg, self.cfg, WORKER_FACTORY(self.cfg, method_cfg.worker_timeout_s)
        )
        self.model = self.cfg.models_catalog[MODEL]
        self.root = artifacts
        self.out = out
        decoding = decoding_for(self.cfg, "L1")
        self.decoding = {**neutral_settings(decoding), "torch_dtype": self.model.dtype}

    def detect(self, name: str, codes: list[tuple[str, str]], lang: str, hp: dict[str, Any]) -> Any:
        inputs = [DetectInput(item_id=i, language=lang, code=c) for i, c in codes]
        results = self.adapter.detect_codes(
            inputs, hp, self.model, KEY_ID, self.decoding, self.out / name, "cuda:0"
        )
        return score_rows(results)

    def _cfg_hash(self) -> str:
        folder = self.root / "watermarked" / "stone" / MODEL
        (cfg,) = (p.name for p in folder.iterdir())
        return cfg

    # ------------------------------------------------------------------ prova 1
    def vocabulary(self, n: int) -> None:
        sys.stdout.write("##### 1. vocabolario: 50.272 (autori) contro generatore (D15), Qwen\n")
        cfg_hash = self._cfg_hash()
        for lang in LANGS:
            pos = pd.read_parquet(
                self.root / "watermarked" / "stone" / MODEL / cfg_hash / f"L1_{lang}_dev.parquet"
            )
            pos = pos[pos["embed_status"] == "OK"]
            native = pd.read_parquet(self.root / "data" / "negatives" / f"L1_{lang}_native.parquet")
            native = native[native["split"] == "dev"]
            integ = pd.read_parquet(
                self.root / "data" / "negatives" / f"L1_{lang}_integration_dev.parquet"
            )
            groups = {
                "positives": pos.sample(min(n, len(pos)), random_state=0),
                "human_native": native.sample(min(n, len(native)), random_state=0),
                "human_integration": integ.sample(min(n, len(integ)), random_state=0),
            }
            for vocab in (AUTHORS_VOCAB, None):
                hp = {"gamma": 0.5, "delta": 1.0, "vocab_size": vocab}
                tag = f"v{vocab or 'gen'}"
                scored = {
                    g: self.detect(
                        f"vocab/{lang}/{tag}/{g}",
                        list(zip(df["sample_id"], df["code"], strict=True)),
                        lang,
                        hp,
                    )
                    for g, df in groups.items()
                }
                sys.stdout.write(f"\n{lang} vocab_size={vocab or 'len(tokenizer)'}\n")
                for g, rows in scored.items():
                    z = np.nanmedian([r["z"] for r in rows])
                    green = np.nanmedian([r["green"] for r in rows])
                    sys.stdout.write(
                        f"  {g:<18} n={len(rows):>4} median z_uff {z:7.2f}  verdi_ns {green:.3f}\n"
                    )
                for g in ("human_native", "human_integration"):
                    a_off = auroc(
                        [r["z"] for r in scored["positives"]], [r["z"] for r in scored[g]]
                    )
                    a_ns = auroc(
                        [r["z_ns"] for r in scored["positives"]], [r["z_ns"] for r in scored[g]]
                    )
                    sys.stdout.write(
                        f"  AUROC positives vs {g:<18} z_uff {a_off:.3f}  z_nonsyntax {a_ns:.3f}\n"
                    )

    # ------------------------------------------------------------------ prova 2
    def authors(self, n_problems: int) -> None:
        sys.stdout.write(
            "\n##### 2. configurazione degli autori: vocab 50.272, γ 0,5, δ del repository, "
            "5 campioni, contro le soluzioni canoniche\n"
        )
        table = pd.read_parquet(self.root / "data" / "problems" / "L1_python.parquet")
        table = table[table["split"] == "dev"].sort_values("problem_key")
        prompts = PromptBuilder.from_config(self.cfg.prompt)
        pooled: dict[str, list[float]] = {"pos": [], "neg": [], "pos_ns": [], "neg_ns": []}
        for dataset, delta in AUTHORS_DELTA.items():
            rows = table[table["dataset"] == dataset].head(n_problems).to_dict(orient="records")
            problems = [Problem.model_validate(r) for r in rows]
            hp = {"gamma": 0.5, "delta": delta, "vocab_size": AUTHORS_VOCAB}
            run = self.adapter.embed_from_prompts(
                problems,
                hp,
                self.model,
                KEY_ID,
                self.decoding,
                5,
                prompts,
                self.out / "authors" / dataset / "embed",
            )
            marked = [(s.sample_id, s.code) for s in run.samples if s.code.strip()]
            # Negativi nativi = codice umano di run.py (HumanEval+: prompt + soluzione canonica,
            # MBPP+: soluzione canonica), degli stessi problemi.
            keys = {p.problem_key for p in problems}
            native = pd.read_parquet(self.root / "data" / "negatives" / "L1_python_native.parquet")
            native = native[native["problem_key"].isin(keys)]
            canonical = list(zip(native["sample_id"], native["code"], strict=True))
            pos = self.detect(f"authors/{dataset}/pos", marked, "python", hp)
            neg = self.detect(f"authors/{dataset}/neg", canonical, "python", hp)
            a_off = auroc([r["z"] for r in pos], [r["z"] for r in neg])
            a_ns = auroc([r["z_ns"] for r in pos], [r["z_ns"] for r in neg])
            sys.stdout.write(
                f"{dataset:<14} δ={delta} marked={len(pos)} canonical={len(neg)}"
                f"  AUROC z_uff {a_off:.3f}  z_nonsyntax {a_ns:.3f}"
                f"  median z_uff marked {np.nanmedian([r['z'] for r in pos]):.2f}"
                f" canonical {np.nanmedian([r['z'] for r in neg]):.2f}\n"
            )
            pooled["pos"] += [r["z"] for r in pos]
            pooled["neg"] += [r["z"] for r in neg]
            pooled["pos_ns"] += [r["z_ns"] for r in pos]
            pooled["neg_ns"] += [r["z_ns"] for r in neg]
        sys.stdout.write(
            f"pooled          AUROC z_uff {auroc(pooled['pos'], pooled['neg']):.3f}"
            f"  z_nonsyntax {auroc(pooled['pos_ns'], pooled['neg_ns']):.3f}\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", default=f"{WMB}/artifacts")
    parser.add_argument("--out", default=f"{WMB}/tmp/diag_stone")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--n-problems", type=int, default=20)
    args = parser.parse_args()
    diag = Diag(Path(args.artifacts), Path(args.out))
    diag.vocabulary(args.n)
    diag.authors(args.n_problems)


if __name__ == "__main__":
    main()
