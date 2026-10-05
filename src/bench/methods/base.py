"""Adapter dei metodi (SPEC §7.2; ADR-008).

``MethodAdapter`` è il Template Method comune: costruisce la ``WorkerRequest`` e gli
item, invoca il worker tramite ``WorkerClient``, verifica I1 e normalizza gli stati. Le
sottoclassi dichiarano capacità e traduzione degli iperparametri (dall'audit, SPEC §9.0).
Per i linguaggi non supportati l'adapter **non** invoca il worker: produce righe
``NOT_APPLICABLE`` (SPEC §7.2).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from bench_contracts import SCHEMA_VERSION, WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import EmbedStatus, WorkerOp

from bench.config.builder import repo_root
from bench.config.schema import ExperimentConfig, MethodConfig, ModelSpec
from bench.domain.enums import Language, MethodFamily, Source
from bench.domain.errors import ConfigError
from bench.domain.ids import config_hash, derive_seed, sample_id
from bench.domain.models import CodeSample, Problem
from bench.generation.code_extractor import CodeExtractor, FencedCodeExtractor
from bench.generation.hf_generator import generation_seed
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.worker_client import MethodSource, WorkerClient, WorkerRun

logger = logging.getLogger(__name__)

KEY_MODULUS = 2**31


@dataclass(frozen=True)
class EmbedRun:
    """Campioni marcati di una chiamata ``embed_from_prompts`` e diagnostica del worker."""

    samples: list[CodeSample]
    worker: WorkerRun | None
    native_hparams: dict[str, Any]
    request: dict[str, Any] | None


class MethodAdapter(ABC):
    """Adapter di un metodo (SPEC §7.2).

    Args:
        method_cfg: configurazione statica del metodo (``configs/method/<nome>.yaml``).
        cfg: configurazione dell'esperimento (seme globale, prompt, decoding).
        worker: client dei worker.
    """

    name: ClassVar[str]
    family: ClassVar[MethodFamily]
    gpu_for_embed: ClassVar[bool]
    gpu_for_detect: ClassVar[bool]
    secrets: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self, method_cfg: MethodConfig, cfg: ExperimentConfig, worker: WorkerClient
    ) -> None:
        if method_cfg.name != self.name:
            raise ConfigError(f"adapter {self.name} got config of {method_cfg.name}")
        self.method_cfg = method_cfg
        self.cfg = cfg
        self.worker = worker

    # ------------------------------------------------------------------ capacità
    @property
    def supported_languages(self) -> frozenset[Language]:
        """Linguaggi supportati (audit); tutti se la configurazione non li restringe."""
        langs = self.method_cfg.supported_languages
        return frozenset(Language) if langs is None else frozenset(langs)

    def supports(self, language: Language | str) -> bool:
        """``True`` se il metodo si applica al linguaggio."""
        return Language(str(language)) in self.supported_languages

    @property
    def env_name(self) -> str:
        """Ambiente del worker (``configs/envs/envs.yaml``)."""
        return self.method_cfg.env

    def source(self) -> MethodSource:
        """Copia patchata del metodo e cartella da mettere nel ``PYTHONPATH``."""
        root = repo_root() / self.method_cfg.patched_dir
        sub = self.method_cfg.patched_subdir
        return MethodSource(pythonpath=root / sub if sub else root, root=root)

    def source_commit(self) -> str:
        """Commit del submodule della copia patchata (``build/patched/<metodo>.source_commit``).

        Raises:
            ConfigError: se la copia patchata non esiste (``scripts/apply_patches.sh``).
        """
        path = Path(f"{self.source().root}.source_commit")
        if not path.is_file():
            raise ConfigError(f"{path} not found: run scripts/apply_patches.sh {self.name}")
        return path.read_text(encoding="utf-8").strip()

    # ------------------------------------------------------------------ iperparametri
    def default_hparams(self) -> dict[str, Any]:
        """Configurazione di default (nomi del protocollo), usata fino all'HPO."""
        return dict(self.method_cfg.default_hparams)

    @abstractmethod
    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """Nomi del protocollo → nomi del repository (dall'audit)."""

    def config_hash(self, hp: dict[str, Any]) -> str:
        """Hash della configurazione effettiva (iperparametri nativi + contratto + commit)."""
        return config_hash(self.to_native_hparams(hp), self.source_commit())

    def key(self, key_id: str) -> int:
        """Chiave segreta (SPEC §9.6): ``derive_seed(global_seed, "wm-key", metodo, key_id)``
        modulo ``2**31``."""
        return derive_seed(self.cfg.global_seed, "wm-key", self.name, key_id) % KEY_MODULUS

    # ------------------------------------------------------------------ worker
    def build_request(
        self,
        op: str,
        model: ModelSpec,
        hp: dict[str, Any],
        key_id: str,
        decoding: dict[str, Any],
        system_prompt: str,
        run_dir: Path,
        device: str,
    ) -> WorkerRequest:
        """``WorkerRequest`` con percorsi stabili in ``run_dir`` (ripresa)."""
        return WorkerRequest(
            schema_version=SCHEMA_VERSION,
            op=op,
            method=self.name,
            model_id=model.model_id,
            model_path=str(model.path),
            tokenizer_path=str(model.tokenizer_path),
            hparams=self.to_native_hparams(hp),
            key=self.key(key_id),
            key_id=key_id,
            decoding=dict(decoding),
            system_prompt=system_prompt,
            items_path=str(run_dir / "items.jsonl"),
            output_path=str(run_dir / "results.jsonl"),
            device=device,
            log_path=str(run_dir / "worker.log"),
        )

    def run_worker(self, request: WorkerRequest, items: Sequence[WorkerItem]) -> WorkerRun:
        """Invoca il worker del metodo (Template Method, passo comune)."""
        return self.worker.run(
            self.env_name, self.name, request, items, source=self.source(), secrets=self.secrets
        )

    def introspect(self) -> dict[str, Any]:
        """Versioni dell'ambiente del worker, per il manifest."""
        return self.worker.introspect(self.env_name, self.name, self.source())


class PromptEmbedder(MethodAdapter):
    """Metodi che inseriscono il watermark durante la generazione (logit o prompt)."""

    def embed_from_prompts(
        self,
        problems: Sequence[Problem],
        hp: dict[str, Any],
        model: ModelSpec,
        key_id: str,
        decoding: dict[str, Any],
        n: int,
        prompts: PromptBuilder,
        run_dir: Path,
        device: str = "cuda:0",
        extractor: CodeExtractor | None = None,
    ) -> EmbedRun:
        """N campioni marcati per problema, con codice estratto (regola unica D4).

        I problemi di linguaggi non supportati danno N righe ``NOT_APPLICABLE`` senza worker.
        """
        extractor = extractor or FencedCodeExtractor()
        native = self.to_native_hparams(hp)
        cfg_hash = self.config_hash(hp)
        seeds = {
            p.problem_key: generation_seed(
                self.cfg.global_seed, model.model_id, p.problem_key, p.language
            )
            for p in problems
        }
        supported = [p for p in problems if self.supports(p.language)]
        results: dict[tuple[str, int], WorkerResult] = {}
        worker_run: WorkerRun | None = None
        request_dict: dict[str, Any] | None = None
        if supported:
            request = self.build_request(
                WorkerOp.EMBED, model, hp, key_id, decoding, prompts.system_prompt, run_dir, device
            )
            request_dict = request.to_dict()
            items = [
                WorkerItem(
                    item_id=p.problem_key,
                    language=str(p.language),
                    seed=seeds[p.problem_key],
                    prompt_messages=prompts.build(p),
                    code=None,
                    context_prompt=None,
                    expected_message=None,
                    n=n,
                )
                for p in supported
            ]
            worker_run = self.run_worker(request, items)
            for worker_result in worker_run.results:
                key = (worker_result.item_id, int(worker_result.sample_index or 0))
                results[key] = worker_result
        samples = []
        for problem in problems:
            for index in range(n):
                found = results.get((problem.problem_key, index))
                if found is None:  # linguaggio non supportato
                    status, raw = EmbedStatus.NOT_APPLICABLE, None
                else:
                    status, raw = found.status, found.raw_output
                if raw is not None and status in (EmbedStatus.OK, EmbedStatus.PARTIAL):
                    code, ok = extractor.extract(raw, problem)
                else:
                    code, ok = "", False
                samples.append(
                    self._sample(
                        problem,
                        model,
                        cfg_hash,
                        key_id,
                        index,
                        seeds[problem.problem_key],
                        raw,
                        code,
                        ok,
                        status,
                    )
                )
        return EmbedRun(
            samples=samples, worker=worker_run, native_hparams=native, request=request_dict
        )

    def _sample(
        self,
        problem: Problem,
        model: ModelSpec,
        cfg_hash: str,
        key_id: str,
        index: int,
        seed: int,
        raw: str | None,
        code: str,
        ok: bool,
        status: str,
    ) -> CodeSample:
        return CodeSample(
            sample_id=sample_id(
                source=Source.LLM_WATERMARKED,
                problem_key=problem.problem_key,
                language=problem.language,
                model_id=model.model_id,
                method=self.name,
                config_hash=cfg_hash,
                key_id=key_id,
                sample_index=index,
            ),
            problem_key=problem.problem_key,
            dataset=problem.dataset,
            language=problem.language,
            level=problem.level,
            split=problem.split,
            source=Source.LLM_WATERMARKED,
            model_id=model.model_id,
            method=self.name,
            config_hash=cfg_hash,
            key_id=key_id,
            sample_index=index,
            seed=seed,
            raw_output=raw,
            code=code,
            extraction_ok=ok,
            embed_status=status,
            expected_message=None,
            parent_id=None,
            attack_id=None,
            attack_params_hash=None,
            attack_status=None,
            contamination_risk=problem.contamination_risk,
        )
