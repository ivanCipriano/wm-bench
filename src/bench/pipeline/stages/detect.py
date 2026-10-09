"""Fase ``detect``: punteggi di rilevazione su positivi e negativi (SPEC §13.1, §15.2).

Una cella = (metodo, modello, livello, linguaggio, parte), con la configurazione di default del
metodo (fino all'HPO). Soggetti, un artefatto ciascuno
(``detection/<metodo>/<modello>/<cfg>/<soggetto>/<L>_<lang>_<parte>.parquet``):

- ``positives``: campioni marcati della cella (tutti, compresi ``FAILED`` e ``PARTIAL``, I2);
- ``neg_human``: negativi umani della parte (nativi e integrazione, §10.4);
- ``neg_llm``: baseline senza watermark dello stesso modello;
- ``neg_llm_twin``: baseline gemella, solo per i metodi che la prevedono (MCGMark, D20; è il
  riferimento primario del suo ``fpr_llm``, decisione dell'utente del 9 ottobre 2026).

Contesto di condizionamento (SPEC §9.1): il prompt chat del problema quando esiste (positivi,
negativi LLM, negativi umani nativi con un problema del livello); contesto vuoto per i negativi
senza prompt (integrazione e soluzioni MBPP fuori da MBPP+, D3). Messaggio atteso (MCGMark):
quello del campione per i positivi, dagli identificativi per i negativi (SPEC §9.5).

Una riga per codice (I1), con i campi di ``DetectionRecord`` (``extra`` come JSON) più
``problem_key``, ``sample_index``, ``dataset``, ``source``, ``embed_status`` e ``parent_id``.
**Ripresa:** la cartella di lavoro del worker è stabile per cella e soggetto.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.domain.models import CodeSample, DetectionRecord, Problem
from bench.generation.decoding import neutral_settings
from bench.generation.prompt_builder import PromptBuilder
from bench.methods.base import DetectInput, Detector
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.generate_baseline import baseline_ref, decoding_for, problems_ref
from bench.pipeline.stages.prepare_data import integration_ref, native_ref
from bench.pipeline.stages.watermark import (
    KEY_ID,
    baseline_twin_ref,
    make_adapter,
    watermarked_ref,
)
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

SUBJECTS = ("positives", "neg_human", "neg_llm", "neg_llm_twin")
EXTRA_COLUMNS = ["problem_key", "sample_index", "dataset", "source", "embed_status", "parent_id"]
RECORD_COLUMNS = [f for f in DetectionRecord.model_fields if f != "extra"] + ["extra_json"]
OUTPUT_COLUMNS = [*EXTRA_COLUMNS, *RECORD_COLUMNS]


def detection_ref(
    method: str, model_id: str, cfg_hash: str, subject: str, level: str, language: str, split: str
) -> ArtifactRef:
    """Punteggi di un soggetto di una cella."""
    if subject not in SUBJECTS:
        raise ConfigError(f"unknown detection subject {subject!r}")
    return ArtifactRef.of(
        "detection",
        f"detection/{method}/{model_id}/{cfg_hash}/{subject}/{level}_{language}_{split}.parquet",
    )


@STAGES.register("detect")
class DetectStage(Stage):
    """Rilevazione su positivi, negativi umani e negativi LLM."""

    name: ClassVar[str] = "detect"
    resources: ClassVar[ResourceClass] = ResourceClass.GPU_NVIDIA
    output_kinds: ClassVar[frozenset[str]] = frozenset({"detection"})
    cell_axes: ClassVar[tuple[str, ...]] = ("method", "model_id", "level", "language", "split")

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config
        self._adapters: dict[str, Any] = {}

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("detect needs the experiment configuration")
        return self.config

    def adapter(self, method: str) -> Any:
        if method not in self._adapters:
            self._adapters[method] = make_adapter(self._cfg(), method)
        return self._adapters[method]

    @staticmethod
    def _require(cell: Cell) -> tuple[str, str, str, str, str]:
        if not (cell.method and cell.model_id and cell.level and cell.language and cell.split):
            raise ConfigError(
                f"detect needs method, model_id, level, language, split: {cell.key()}"
            )
        return cell.method, cell.model_id, cell.level, cell.language, cell.split

    def config_hash(self, method: str) -> str:
        adapter = self.adapter(method)
        return str(adapter.config_hash(adapter.default_hparams()))

    def subjects(self, method: str) -> tuple[str, ...]:
        twin = getattr(self.adapter(method), "twin_baseline", False)
        return tuple(s for s in SUBJECTS if s != "neg_llm_twin" or twin)

    def _sample_refs(self, cell: Cell) -> dict[str, list[ArtifactRef]]:
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(method)
        refs: dict[str, list[ArtifactRef]] = {
            "positives": [watermarked_ref(method, model_id, cfg_hash, level, language, split)],
            "neg_human": [
                native_ref(Language(language)),
                integration_ref(Language(language), split),
            ],
            "neg_llm": [baseline_ref(model_id, level, language, split)],
        }
        if "neg_llm_twin" in self.subjects(method):
            refs["neg_llm_twin"] = [
                baseline_twin_ref(method, model_id, cfg_hash, level, language, split)
            ]
        return refs

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        _, _, level, language, _ = self._require(cell)
        refs = [problems_ref(level, language)]
        for subject_refs in self._sample_refs(cell).values():
            refs.extend(subject_refs)
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        method, model_id, level, language, split = self._require(cell)
        cfg_hash = self.config_hash(method)
        return [
            detection_ref(method, model_id, cfg_hash, s, level, language, split)
            for s in self.subjects(method)
        ]

    # ------------------------------------------------------------------ campioni
    def _samples(self, ctx: StageContext, cell: Cell, subject: str) -> list[CodeSample]:
        split = str(cell.split)
        frames = [ctx.store.read_table(ref) for ref in self._sample_refs(cell)[subject]]
        table = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        if subject == "neg_human":
            table = table[table["split"] == split]  # i nativi contengono entrambe le parti
        fields = list(CodeSample.model_fields)
        rows = table[fields].to_dict(orient="records")
        samples = [CodeSample.model_validate(_clean(r)) for r in rows]
        return sorted(samples, key=lambda s: (s.problem_key, s.sample_index or 0, s.sample_id))

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        method, model_id, level, language, split = self._require(cell)
        adapter = self.adapter(method)
        if not isinstance(adapter, Detector):
            raise ConfigError(f"{method} has no detector")
        model = cfg.models_catalog[model_id]
        hp = adapter.default_hparams()
        cfg_hash = self.config_hash(method)
        prompts = PromptBuilder.from_config(cfg.prompt)
        problems = {
            r["problem_key"]: Problem.model_validate(r)
            for r in ctx.store.read_table(problems_ref(level, language)).to_dict(orient="records")
        }
        decoding = {**neutral_settings(decoding_for(cfg, level)), "torch_dtype": model.dtype}
        device = "cuda:0" if adapter.gpu_for_detect else "cpu"
        for subject, ref in zip(self.subjects(method), self.outputs(cell), strict=True):
            samples = self._samples(ctx, cell, subject)
            inputs = [
                self._input(adapter, model, prompts, problems, sample, subject)
                for sample in samples
            ]
            run_dir = (
                ctx.store.root / "_runs" / "detect" / method / model_id / cfg_hash / subject
                / f"{level}_{language}_{split}"
            )  # fmt: skip
            logger.info("%s %s: %d codes", cell.key(), subject, len(inputs))
            results = adapter.detect_codes(inputs, hp, model, KEY_ID, decoding, run_dir, device)
            self._write(ctx, cell, subject, ref, samples, results, cfg_hash, model_id)

    def _input(
        self,
        adapter: Any,
        model: Any,
        prompts: PromptBuilder,
        problems: dict[str, Problem],
        sample: CodeSample,
        subject: str,
    ) -> DetectInput:
        problem = problems.get(sample.problem_key)
        messages = prompts.build(problem) if problem is not None else None  # D3 senza prompt
        if subject == "positives":
            message = sample.expected_message
        else:
            message = adapter.message_for(
                model, sample.problem_key, str(sample.language), sample.sample_index
            )
        return DetectInput(
            item_id=sample.sample_id,
            language=str(sample.language),
            code=sample.code,
            prompt_messages=messages,
            expected_message=message,
        )

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        subject: str,
        ref: ArtifactRef,
        samples: list[CodeSample],
        results: list[Any],
        cfg_hash: str,
        model_id: str,
    ) -> None:
        rows = []
        for sample, result in zip(samples, results, strict=True):
            record = DetectionRecord(
                sample_id=sample.sample_id,
                method=str(cell.method),
                model_id=model_id,
                config_hash=cfg_hash,
                key_id=KEY_ID,
                status=result.status,
                score=result.score,
                native_decision=result.native_decision,
                decoded_message=result.decoded_message,
                bits_correct=_bits_correct(result),
                extra=dict(result.extra or {}),
            )
            dump = record.model_dump(mode="json")
            extra = dump.pop("extra")
            rows.append(
                {
                    "problem_key": sample.problem_key,
                    "sample_index": sample.sample_index,
                    "dataset": sample.dataset,
                    "source": str(sample.source),
                    "embed_status": sample.embed_status,
                    "parent_id": sample.parent_id,
                    **dump,
                    "extra_json": json.dumps(extra, sort_keys=True),
                }
            )
        df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        df["sample_index"] = df["sample_index"].astype("Int64")
        statuses = Counter(df["status"])
        scores = pd.to_numeric(df["score"], errors="coerce")
        extra = {
            "subject": subject,
            "status_counts": dict(statuses),
            "score_summary": {
                "n": int(scores.notna().sum()),
                "mean": float(scores.mean()) if scores.notna().any() else None,
                "min": float(scores.min()) if scores.notna().any() else None,
                "max": float(scores.max()) if scores.notna().any() else None,
            },
            "key_id": KEY_ID,
        }
        manifest = ctx.make_manifest(
            ref,
            inputs=self.inputs(cell),
            n_rows_in=len(samples),
            n_rows_out=len(df),
            n_rows_expected=len(samples),
            config_hash=cfg_hash,
            extra=extra,
        )
        ctx.store.write_table(ref, df, manifest)
        logger.info("%s %s: %d records, status %s", cell.key(), subject, len(df), dict(statuses))


def _bits_correct(result: Any) -> int | None:
    """Bit corretti (MCGMark): il punteggio è il numero di bit uguali al messaggio (D1)."""
    if result.decoded_message is None or result.score is None:
        return None
    return int(result.score)


def _clean(row: dict[Any, Any]) -> dict[str, Any]:
    """Valori mancanti di pandas → ``None`` per la validazione."""
    return {str(k): (None if _is_missing(v) else v) for k, v in row.items()}


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


__all__ = ["SUBJECTS", "DetectStage", "detection_ref"]
