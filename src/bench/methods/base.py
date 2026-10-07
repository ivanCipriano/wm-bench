"""Adapter dei metodi (SPEC §7.2; ADR-008).

``MethodAdapter`` è il Template Method comune: costruisce la ``WorkerRequest`` e gli
item, invoca il worker tramite ``WorkerClient``, verifica I1 e normalizza gli stati. Le
sottoclassi dichiarano capacità e traduzione degli iperparametri (dall'audit, SPEC §9.0).
Per i linguaggi non supportati l'adapter **non** invoca il worker: produce righe
``NOT_APPLICABLE`` (SPEC §7.2).
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from bench_contracts import SCHEMA_VERSION, WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import DetectStatus, EmbedStatus, WorkerOp

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
    # Misure per campione dell'inserimento (ordine di ``samples``), colonne ``EMBED_COLUMNS``.
    metrics: list[dict[str, Any]] = dataclasses.field(default_factory=list)


# Colonne aggiuntive dei campioni marcati (oltre a ``CodeSample``): siti idonei usati
# dall'inserimento e token generati, ``None`` se il metodo non li misura.
EMBED_COLUMNS = ("n_sites", "n_generated_tokens")


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

    # Metodi che generano un campione alla volta (es. MCGMark, che non supporta più sequenze per
    # volta): un item per campione, con seme proprio (D9: accoppiamento con la baseline per
    # problema). Gli altri: un item per problema con n campioni e il seme della baseline.
    one_sample_per_item: ClassVar[bool] = False
    # Metodi con una baseline gemella (es. MCGMark con il prefill della fence): stesso worker,
    # stesso prompt, stessi semi, watermark disattivato (fase ``generate_baseline_twin``).
    twin_baseline: ClassVar[bool] = False

    def seed_scheme(self) -> str:
        """Schema dei semi registrato nel manifest: ``per_problem`` o ``per_sample``."""
        return "per_sample" if self.one_sample_per_item else "per_problem"

    def sample_seed(self, model: ModelSpec, problem: Problem, index: int) -> int:
        """Seme del campione ``index`` (schema ``per_sample``)."""
        return derive_seed(
            self.cfg.global_seed,
            "gen",
            model.model_id,
            problem.problem_key,
            str(problem.language),
            index,
        )

    def expected_message(self, model: ModelSpec, problem: Problem, index: int) -> str | None:
        """Messaggio atteso del campione (solo metodi multi-bit)."""
        return None

    def embed_metrics(self, result: WorkerResult | None) -> dict[str, Any]:
        """Valori di ``EMBED_COLUMNS`` dal risultato del worker (``None`` se non misurati)."""
        extra = result.extra if result is not None else {}
        return {name: extra.get(name) for name in EMBED_COLUMNS}

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
        watermark: bool = True,
    ) -> EmbedRun:
        """N campioni marcati per problema, con codice estratto (regola unica D4).

        I problemi di linguaggi non supportati danno N righe ``NOT_APPLICABLE`` senza worker.
        Con ``watermark=False`` (solo metodi con ``twin_baseline``) il worker genera la baseline
        gemella: stessa pipeline del metodo con il watermark disattivato (``hparams.watermark``),
        campioni ``llm_baseline`` con ``method`` valorizzato.
        """
        if not watermark and not self.twin_baseline:
            raise ConfigError(f"{self.name} has no twin baseline")
        extractor = extractor or FencedCodeExtractor()
        native = self.to_native_hparams(hp)
        if not watermark:
            native = {**native, "watermark": False}
        cfg_hash = self.config_hash(hp)
        problem_seeds = {
            p.problem_key: generation_seed(
                self.cfg.global_seed, model.model_id, p.problem_key, p.language
            )
            for p in problems
        }

        def seed_of(problem: Problem, index: int) -> int:
            if self.one_sample_per_item:
                return self.sample_seed(model, problem, index)
            return problem_seeds[problem.problem_key]

        supported = [p for p in problems if self.supports(p.language)]
        results: dict[tuple[str, int], WorkerResult] = {}
        worker_run: WorkerRun | None = None
        request_dict: dict[str, Any] | None = None
        if supported:
            item_decoding = dict(decoding)
            if self.one_sample_per_item:
                item_decoding["num_return_sequences"] = 1
            request = self.build_request(
                WorkerOp.EMBED,
                model,
                hp,
                key_id,
                item_decoding,
                prompts.system_prompt,
                run_dir,
                device,
            )
            if not watermark:
                request = dataclasses.replace(request, hparams=native)
            request_dict = request.to_dict()
            items = self._embed_items(supported, model, prompts, n, seed_of)
            worker_run = self.run_worker(request, items)
            results = self._index_results(worker_run)
        samples = []
        metrics = []
        for problem in problems:
            for index in range(n):
                found = results.get((problem.problem_key, index))
                metrics.append(self.embed_metrics(found))
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
                        seed_of(problem, index),
                        raw,
                        code,
                        ok,
                        status,
                        self.expected_message(model, problem, index),
                        watermark,
                    )
                )
        return EmbedRun(
            samples=samples,
            worker=worker_run,
            native_hparams=native,
            request=request_dict,
            metrics=metrics,
        )

    def _index_results(self, worker_run: WorkerRun) -> dict[tuple[str, int], WorkerResult]:
        """Risultati del worker per ``(problem_key, sample_index)``."""
        results: dict[tuple[str, int], WorkerResult] = {}
        for worker_result in worker_run.results:
            if self.one_sample_per_item:
                problem_key, _, index_text = worker_result.item_id.rpartition("#")
                results[(problem_key, int(index_text))] = worker_result
            else:
                key = (worker_result.item_id, int(worker_result.sample_index or 0))
                results[key] = worker_result
        return results

    def _embed_items(
        self,
        problems: Sequence[Problem],
        model: ModelSpec,
        prompts: PromptBuilder,
        n: int,
        seed_of: Any,
    ) -> list[WorkerItem]:
        """Item del worker: uno per problema (n campioni) o uno per campione."""
        items: list[WorkerItem] = []
        for p in problems:
            messages = prompts.build(p)
            if self.one_sample_per_item:
                items += [
                    WorkerItem(
                        item_id=f"{p.problem_key}#{i}",
                        language=str(p.language),
                        seed=seed_of(p, i),
                        prompt_messages=messages,
                        code=None,
                        context_prompt=None,
                        expected_message=self.expected_message(model, p, i),
                        n=1,
                    )
                    for i in range(n)
                ]
            else:
                items.append(
                    WorkerItem(
                        item_id=p.problem_key,
                        language=str(p.language),
                        seed=seed_of(p, 0),
                        prompt_messages=messages,
                        code=None,
                        context_prompt=None,
                        expected_message=None,
                        n=n,
                    )
                )
        return items

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
        expected_message: str | None = None,
        watermark: bool = True,
    ) -> CodeSample:
        # Baseline gemella: sorgente baseline, metodo e configurazione per distinguerla dalla
        # baseline normale, nessuna chiave.
        source = Source.LLM_WATERMARKED if watermark else Source.LLM_BASELINE
        key = key_id if watermark else None
        embed_status = status if watermark or status != EmbedStatus.OK else None
        return CodeSample(
            sample_id=sample_id(
                source=source,
                problem_key=problem.problem_key,
                language=problem.language,
                model_id=model.model_id,
                method=self.name,
                config_hash=cfg_hash,
                key_id=key,
                sample_index=index,
            ),
            problem_key=problem.problem_key,
            dataset=problem.dataset,
            language=problem.language,
            level=problem.level,
            split=problem.split,
            source=source,
            model_id=model.model_id,
            method=self.name,
            config_hash=cfg_hash,
            key_id=key,
            sample_index=index,
            seed=seed,
            raw_output=raw,
            code=code,
            extraction_ok=ok,
            embed_status=embed_status,
            expected_message=expected_message,
            parent_id=None,
            attack_id=None,
            attack_params_hash=None,
            attack_status=None,
            contamination_risk=problem.contamination_risk,
        )


@dataclass(frozen=True)
class DetectInput:
    """Codice da valutare: identità, linguaggio e contesto di condizionamento (SPEC §9.1)."""

    item_id: str
    language: str
    code: str
    prompt_messages: list[dict[str, str]] | None = None
    context_prompt: str | None = None
    expected_message: str | None = None


class Detector(MethodAdapter):
    """Metodi con rilevazione (tutti): un punteggio per codice, più alto = più marcato."""

    def detect_codes(
        self,
        inputs: Sequence[DetectInput],
        hp: dict[str, Any],
        model: ModelSpec,
        key_id: str,
        decoding: dict[str, Any],
        run_dir: Path,
        device: str = "cuda:0",
    ) -> list[WorkerResult]:
        """Un ``WorkerResult`` per input, nell'ordine dato (I1).

        I linguaggi non supportati danno ``NOT_APPLICABLE`` senza invocare il worker.
        """
        supported = [x for x in inputs if self.supports(x.language)]
        found: dict[str, WorkerResult] = {}
        if supported:
            request = self.build_request(
                WorkerOp.DETECT, model, hp, key_id, decoding, "", run_dir, device
            )
            items = [
                WorkerItem(
                    item_id=x.item_id,
                    language=str(x.language),
                    seed=0,
                    prompt_messages=x.prompt_messages,
                    code=x.code,
                    context_prompt=x.context_prompt,
                    expected_message=x.expected_message,
                    n=1,
                )
                for x in supported
            ]
            for result in self.run_worker(request, items).results:
                found[result.item_id] = result
        return [
            found.get(x.item_id)
            or WorkerResult(
                item_id=x.item_id,
                sample_index=None,
                status=DetectStatus.NOT_APPLICABLE,
                raw_output=None,
                code=None,
                score=None,
                native_decision=None,
                decoded_message=None,
            )
            for x in inputs
        ]
