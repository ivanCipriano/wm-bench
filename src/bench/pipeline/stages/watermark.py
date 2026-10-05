"""Fase ``watermark``: campioni marcati dai metodi in generazione (SPEC §15.2; ADR-008).

Una cella = (metodo, modello, livello, linguaggio, parte), con la configurazione di default
del metodo (fino all'HPO). Output:
``watermarked/<metodo>/<modello>/<config_hash>/<livello>_<linguaggio>_<parte>.parquet``
con N campioni ``CodeSample`` per problema (``source=llm_watermarked``), stesso prompt e
stesso seme della baseline; i linguaggi non supportati dal metodo danno N righe
``NOT_APPLICABLE`` (I1).

**Ripresa:** la cartella di lavoro del worker è stabile per cella
(``_runs/watermark/<metodo>/<modello>/<cfg>/<cella>/``): rilanciando la fase, il runner dello
shim salta gli item già scritti in ``results.jsonl``.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any, ClassVar

import pandas as pd

from bench.config.builder import repo_root
from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.models import CodeSample, Problem
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.base import MethodAdapter, PromptEmbedder
from bench.methods.worker_client import WorkerClient
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.generate_baseline import decoding_for, problems_ref
from bench.registry import METHODS, STAGES
from bench.store.hashing import sha256_json
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

KEY_ID = "k1"  # chiave principale (SPEC §9.6)


def make_worker_client(cfg: ExperimentConfig, timeout_s: float) -> WorkerClient:
    """Client dei worker con gli interpreti di ``configs/envs/envs.yaml``."""
    return WorkerClient(
        envs={name: spec.python for name, spec in cfg.envs.items()},
        shims_root=repo_root() / "shims",
        timeout_s=timeout_s,
    )


# Fabbrica del client: sostituibile nei test (interpreti locali).
WORKER_FACTORY: Callable[[ExperimentConfig, float], WorkerClient] = make_worker_client


def make_adapter(cfg: ExperimentConfig, method: str) -> MethodAdapter:
    """Adapter registrato del metodo, con il suo client dei worker."""
    import bench.methods.adapters  # noqa: F401  (popola METHODS)

    if method not in cfg.methods_catalog:
        raise ConfigError(f"no configs/method/{method}.yaml")
    method_cfg = cfg.methods_catalog[method]
    return METHODS.get(method)(method_cfg, cfg, WORKER_FACTORY(cfg, method_cfg.worker_timeout_s))


def watermarked_ref(
    method: str, model_id: str, cfg_hash: str, level: str, language: str, split: str
) -> ArtifactRef:
    """Campioni marcati di una cella."""
    return ArtifactRef.of(
        "watermarked",
        f"watermarked/{method}/{model_id}/{cfg_hash}/{level}_{language}_{split}.parquet",
    )


@STAGES.register("watermark")
class WatermarkStage(Stage):
    """Inserimento del watermark in generazione (metodi ``PromptEmbedder``)."""

    name: ClassVar[str] = "watermark"
    resources: ClassVar[ResourceClass] = ResourceClass.GPU_NVIDIA
    output_kinds: ClassVar[frozenset[str]] = frozenset({"watermarked"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "level", "language", "split")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, MethodAdapter] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("watermark needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> MethodAdapter:
        """Adapter del metodo (uno per fase)."""
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str, str, str]:
        if not (cell.method and cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(
                f"watermark needs method, model_id, level, language, split: {cell.key()}"
            )
        return cell.method, cell.model_id, cell.level, cell.language, cell.split

    def config_hash(self, method: str) -> str:
        """Hash della configurazione di default del metodo."""
        adapter = self.adapter(method)
        return adapter.config_hash(adapter.default_hparams())

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        _, _, level, language, _ = self._require(cell)
        return [problems_ref(level, language)]

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        return [watermarked_ref(method, model_id, self.config_hash(method), level, language, split)]

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        adapter = self.adapter(method)
        if not isinstance(adapter, PromptEmbedder):
            raise ConfigError(f"{method} does not embed from prompts (stage watermark)")
        model = cfg.models_catalog[model_id]
        decoding = decoding_for(cfg, level)
        prompts = PromptBuilder.from_config(cfg.prompt)
        hp = adapter.default_hparams()
        cfg_hash = adapter.config_hash(hp)

        table = ctx.store.read_table(problems_ref(level, language))
        table = table[table["split"] == split]
        problems = sorted(
            (Problem.model_validate(r) for r in table.to_dict(orient="records")),
            key=lambda p: p.problem_key,
        )
        run_dir = (
            ctx.store.root
            / "_runs"
            / "watermark"
            / method
            / model_id
            / cfg_hash
            / f"{level}_{language}_{split}"
        )
        request_decoding = {**neutral_settings(decoding), "torch_dtype": model.dtype}
        supported = adapter.supports(language)
        logger.info(
            "%s: %d problems, method %s %s (config %s)",
            cell.key(),
            len(problems),
            method,
            "supported" if supported else "NOT_APPLICABLE",
            cfg_hash,
        )
        result = adapter.embed_from_prompts(
            problems,
            hp,
            model,
            KEY_ID,
            request_decoding,
            decoding.n,
            prompts,
            run_dir,
        )
        introspect = adapter.introspect() if supported else {}
        self._write(
            ctx,
            cell,
            problems,
            result.samples,
            decoding.n,
            hp,
            cfg_hash,
            result,
            introspect,
            prompts,
        )

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        problems: list[Problem],
        samples: list[CodeSample],
        n: int,
        hp: dict[str, Any],
        cfg_hash: str,
        result: Any,
        introspect: dict[str, Any],
        prompts: PromptBuilder,
    ) -> None:
        df = pd.DataFrame(
            [s.model_dump(mode="json") for s in samples], columns=list(CodeSample.model_fields)
        )
        statuses = Counter(s.embed_status for s in samples)
        extracted = sum(1 for s in samples if s.extraction_ok)
        generation = next(
            (
                r.extra.get("generation_config")
                for r in (result.worker.results if result.worker else [])
                if r.extra.get("generation_config")
            ),
            None,
        )
        extra = {
            "method": cell.method,
            "hparams": hp,
            "native_hparams": result.native_hparams,
            "key_id": KEY_ID,
            "config_hash": cfg_hash,
            "embed_status_counts": dict(statuses),
            "extraction_rate": round(extracted / len(samples), 6) if samples else None,
            "generation_config": generation,
            "request_sha256": sha256_json(result.request) if result.request else None,
            "worker": {
                "attempts": result.worker.attempts if result.worker else 0,
                "missing_results": result.worker.missing if result.worker else 0,
            },
            "worker_env": introspect,
            "prompt_hashes": prompts.prompt_hashes(),
        }
        ref = self.outputs(cell)[0]
        manifest = ctx.make_manifest(
            ref,
            inputs=self.inputs(cell),
            n_rows_in=len(problems),
            n_rows_out=len(df),
            n_rows_expected=len(problems) * n,
            config_hash=cfg_hash,
            extra=extra,
        )
        packages = introspect.get("packages_sha256")
        if introspect:
            manifest = manifest.model_copy(
                update={
                    "worker_env": {k: v for k, v in introspect.items() if k != "packages"},
                    "worker_packages_sha256": packages,
                }
            )
        ctx.store.write_table(ref, df, manifest)
        logger.info(
            "%s: %d samples, status %s, extraction %s",
            cell.key(),
            len(df),
            dict(statuses),
            extra["extraction_rate"],
        )
