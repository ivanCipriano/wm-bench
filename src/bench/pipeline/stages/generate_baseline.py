"""Fase ``generate_baseline`` (SPEC §10.5, §15.2; ADR-006).

Una cella = (modello, livello, linguaggio, parte). Output:
``baseline/<modello>/<livello>_<linguaggio>_<parte>.parquet`` con N = 6 campioni per problema.

**Ripresa obbligatoria** (cluster_info §1): dopo ogni problema i campioni vanno in un file
parziale JSONL (``baseline/<modello>/_partial/<cella>.jsonl``) con ``fsync``. Un job
interrotto (timeout SLURM) si rilancia e riparte dai problemi mancanti. Il file parziale
inizia con un'impronta della configurazione: se la configurazione cambia, si riparte da zero.
"""

from __future__ import annotations

import gc
import logging
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from bench_contracts import append_jsonl, iter_jsonl

from bench.config.resources import ResourceClass
from bench.config.schema import DecodingConfig, ExperimentConfig, ModelSpec
from bench.domain.enums import Level
from bench.domain.errors import ConfigError
from bench.domain.models import CodeSample, Problem
from bench.generation.hf_generator import HFBaselineGenerator, HFTextGenerator, TextGenerator
from bench.generation.prompt_builder import PromptBuilder
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.hashing import sha256_json
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

# Fabbrica del backend: sostituibile nei test (nessuna GPU).
BackendFactory = Callable[[ModelSpec, DecodingConfig], TextGenerator]
BACKEND_FACTORY: BackendFactory = HFTextGenerator


def problems_ref(level: str, language: str) -> ArtifactRef:
    """Problemi di un livello e linguaggio (prodotti da ``prepare_data``)."""
    return ArtifactRef.of("problems", f"data/problems/{level}_{language}.parquet")


def baseline_ref(model_id: str, level: str, language: str, split: str) -> ArtifactRef:
    """Campioni della baseline di una cella."""
    return ArtifactRef.of("baseline", f"baseline/{model_id}/{level}_{language}_{split}.parquet")


def release_backend() -> None:
    """Libera la memoria della GPU fra due backend della stessa cella (nessun effetto senza)."""
    gc.collect()
    try:
        import torch
    except ImportError:  # pragma: no cover - torch c'è sempre in bench-core
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# Dataset con un decoding proprio (fuori da L1): ClassEval con 2.048 token nuovi (D40).
DATASET_DECODING: dict[str, str] = {"classeval": "classeval"}


def decoding_for(cfg: ExperimentConfig, level: str, dataset: str | None = None) -> DecodingConfig:
    """Decoding del livello: ``level1`` per L1, ``level234`` per gli altri (SPEC §2.1).

    Con ``dataset``, i dataset di ``DATASET_DECODING`` hanno il loro (D40: ClassEval).

    Raises:
        ConfigError: se il decoding del dataset non è configurato.
    """
    if Level(level) is not Level.L1 and dataset in DATASET_DECODING:
        key = DATASET_DECODING[str(dataset)]
        if key not in cfg.decoding:
            raise ConfigError(f"decoding '{key}' for dataset {dataset} is not configured")
        return cfg.decoding[key]
    return cfg.decoding["level1" if Level(level) is Level.L1 else "level234"]


def decoding_overrides(
    cfg: ExperimentConfig, level: str, problems: Sequence[Problem]
) -> dict[str, DecodingConfig]:
    """Dataset dei problemi con un decoding diverso da quello del livello (D40)."""
    default = decoding_for(cfg, level)
    found = {p.dataset: decoding_for(cfg, level, p.dataset) for p in problems}
    return {d: dec for d, dec in sorted(found.items()) if dec != default}


@STAGES.register("generate_baseline")
class GenerateBaselineStage(Stage):
    """Baseline senza watermark, 6 campioni per problema."""

    name: ClassVar[str] = "generate_baseline"
    resources: ClassVar[ResourceClass] = ResourceClass.GPU_NVIDIA
    output_kinds: ClassVar[frozenset[str]] = frozenset({"baseline"})
    cell_axes: ClassVar[tuple[str, ...]] = ("model_id", "level", "language", "split")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("generate_baseline needs the experiment configuration")
        return self.config

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str, str]:
        if not (cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(
                f"generate_baseline needs model_id, level, language, split: {cell.key()}"
            )
        return cell.model_id, cell.level, cell.language, cell.split

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        _, level, language, _ = self._require(cell)
        return [problems_ref(level, language)]

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [baseline_ref(*self._require(cell))]

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        model_id, level, language, split = self._require(cell)
        model = cfg.models_catalog[model_id]
        decoding = decoding_for(cfg, level)
        prompts = PromptBuilder.from_config(cfg.prompt)

        table = ctx.store.read_table(problems_ref(level, language))
        table = table[table["split"] == split]
        problems = [Problem.model_validate(row) for row in table.to_dict(orient="records")]
        problems.sort(key=lambda p: p.problem_key)

        overrides = decoding_overrides(cfg, level, problems)
        by_dataset = {d: dec.model_dump(mode="json") for d, dec in overrides.items()}
        fingerprint = sha256_json(
            {
                "model": model.model_dump(mode="json"),
                "decoding": decoding.model_dump(mode="json"),
                "prompts": prompts.prompt_hashes(),
                "global_seed": cfg.global_seed,
                # solo con dataset dal decoding proprio (D40): le impronte di L1/L2 non cambiano
                **({"decoding_by_dataset": by_dataset} if by_dataset else {}),
            }
        )
        partial = (
            ctx.store.root
            / "baseline"
            / model_id
            / "_partial"
            / f"{level}_{language}_{split}.jsonl"
        )
        done, info = self._load_partial(partial, fingerprint)
        todo = [p for p in problems if p.problem_key not in done]
        logger.info(
            "%s: %d problems, %d already done, %d to generate",
            cell.key(),
            len(problems),
            len(done),
            len(todo),
        )

        # Un backend per decoding (D40: ClassEval con 2.048 token nella cella L3 Python).
        groups: dict[str | None, list[Problem]] = {}
        for problem in todo:
            groups.setdefault(problem.dataset if problem.dataset in overrides else None, [])
            groups[problem.dataset if problem.dataset in overrides else None].append(problem)
        for dataset, group in sorted(groups.items(), key=lambda kv: kv[0] or ""):
            release_backend()  # il modello del gruppo precedente esce dalla GPU
            group_decoding = decoding if dataset is None else overrides[dataset]
            backend = BACKEND_FACTORY(model, group_decoding)
            generator = HFBaselineGenerator(
                model, group_decoding, prompts=prompts, global_seed=cfg.global_seed, backend=backend
            )
            info = backend.info()
            append_jsonl(partial, {"info": info})
            for problem in group:
                start = time.perf_counter()
                samples = generator.generate_problem(problem)
                elapsed = time.perf_counter() - start
                record = {
                    "problem_key": problem.problem_key,
                    "elapsed_s": elapsed,
                    "samples": [s.model_dump(mode="json") for s in samples],
                }
                append_jsonl(partial, record)
                done[problem.problem_key] = record
            del backend, generator
        self._write(ctx, cell, problems, done, decoding, prompts, info, partial, by_dataset)

    @staticmethod
    def _load_partial(
        path: Path, fingerprint: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Problemi già generati e ultima diagnostica del backend.

        Crea il file con l'impronta se manca o se l'impronta non coincide.
        """
        done: dict[str, dict[str, Any]] = {}
        info: dict[str, Any] = {}
        if path.is_file():
            records = list(iter_jsonl(path))
            if records and records[0].get("fingerprint") == fingerprint:
                for record in records[1:]:
                    if "problem_key" in record:
                        done[str(record["problem_key"])] = record
                    elif "info" in record:
                        info = dict(record["info"])
                return done, info
            logger.warning("partial file %s has a different configuration: starting over", path)
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(path, {"fingerprint": fingerprint})
        return done, info

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        problems: list[Problem],
        done: dict[str, dict[str, Any]],
        decoding: DecodingConfig,
        prompts: PromptBuilder,
        info: dict[str, Any],
        partial: Path,
        decoding_by_dataset: dict[str, Any] | None = None,
    ) -> None:
        samples = [
            CodeSample.model_validate(s) for p in problems for s in done[p.problem_key]["samples"]
        ]
        df = pd.DataFrame(
            [s.model_dump(mode="json") for s in samples], columns=list(CodeSample.model_fields)
        )
        ok_by_dataset: Counter[str] = Counter(s.dataset for s in samples if s.extraction_ok)
        all_by_dataset: Counter[str] = Counter(s.dataset for s in samples)
        elapsed = [float(done[p.problem_key]["elapsed_s"]) for p in problems]
        extra = {
            "extraction_rate": round(sum(ok_by_dataset.values()) / len(samples), 6)
            if samples
            else None,
            "extraction_rate_by_dataset": {
                d: round(ok_by_dataset[d] / n, 6) for d, n in sorted(all_by_dataset.items())
            },
            "extraction_failed": len(samples) - sum(ok_by_dataset.values()),
            "prompt_hashes": prompts.prompt_hashes(),
            "system_prompt_sha256": prompts.system_prompt_sha256,
            "decoding": decoding.model_dump(mode="json"),
            **({"decoding_by_dataset": decoding_by_dataset} if decoding_by_dataset else {}),
            "generation": info,
            "seconds_per_problem": {
                "mean": round(statistics.fmean(elapsed), 3) if elapsed else None,
                "max": round(max(elapsed), 3) if elapsed else None,
            },
        }
        ref = self.outputs(cell)[0]
        ctx.store.write_table(
            ref,
            df,
            ctx.make_manifest(
                ref,
                inputs=self.inputs(cell),
                n_rows_in=len(problems),
                n_rows_out=len(df),
                n_rows_expected=len(problems) * decoding.n,
                extra=extra,
            ),
        )
        partial.unlink(missing_ok=True)
        logger.info(
            "%s: %d samples, extraction rate %s", cell.key(), len(df), extra["extraction_rate"]
        )
