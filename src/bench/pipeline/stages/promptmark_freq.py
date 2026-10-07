"""Fase ``promptmark_freq``: lista di frequenza delle iniziali per PromptMark (SPEC §9.4, D8).

Output ``data/promptmark_freq/python.json`` (``LetterFrequencies``) dallo split di training di
CodeSearchNet Python (colonna ``whole_func_string``, la stessa dei negativi), disgiunto dai negativi
di valutazione. Solo Python: PromptMark si applica solo a Python (audit di PromptMark §5).

Differenza di implementazione rispetto alla SPEC (§15.2 la metteva in ``prepare_data``): fase a sé,
così non si rilancia ``prepare_data`` già chiusa (M2).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import ClassVar

import pyarrow.parquet as pq

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.data.loaders.base import check_rows
from bench.data.promptmark_freq import letter_frequencies
from bench.domain.errors import ConfigError
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

ROLE = "python/train"
COLUMN = "whole_func_string"


def promptmark_freq_ref(language: str = "python") -> ArtifactRef:
    """Lista di frequenza di un linguaggio."""
    return ArtifactRef.of("promptmark_freq", f"data/promptmark_freq/{language}.json")


@STAGES.register("promptmark_freq")
class PromptMarkFreqStage(Stage):
    """Iniziali degli identificatori di CodeSearchNet Python train."""

    name: ClassVar[str] = "promptmark_freq"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"promptmark_freq"})
    cell_axes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [promptmark_freq_ref()]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        if self.config is None:
            raise ConfigError("promptmark_freq needs the experiment configuration")
        spec = self.config.datasets["codesearchnet"]
        path = spec.file(ROLE)
        rows = pq.ParquetFile(path).metadata.num_rows
        check_rows(spec, ROLE, rows)

        def codes() -> Iterator[object]:
            for batch in pq.ParquetFile(path).iter_batches(columns=[COLUMN], batch_size=10_000):
                yield from batch.column(0).to_pylist()

        freqs = letter_frequencies(codes(), source=f"codesearchnet/{ROLE}/{COLUMN}")
        ref = promptmark_freq_ref()
        ctx.store.write_model(
            ref,
            freqs,
            ctx.make_manifest(
                ref,
                inputs=[],
                n_rows_in=rows,
                n_rows_out=1,
                n_rows_expected=1,
                extra={
                    "n_programs": freqs.n_programs,
                    "n_skipped": freqs.n_skipped,
                    "total_identifiers": freqs.total_identifiers,
                },
            ),
        )
        logger.info(
            "%s: %d programs, %d skipped, %d identifiers",
            ref.path,
            freqs.n_programs,
            freqs.n_skipped,
            freqs.total_identifiers,
        )
