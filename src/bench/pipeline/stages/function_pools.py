"""Fase ``function_pools``: raccolta di funzioni Python per T2.5 (ADR-018 §4; PIPELINE di
multimark §5.5).

Una cella per linguaggio, solo Python (gli altri linguaggi fermano la fase: T2.5 è solo Python).
Se la fonte finisce prima di ``MAX_FUNCTIONS`` funzioni (solo sulle fixture dei test) la raccolta
ha quelle che ci sono e il manifest lo dice (``exhausted``). Output
``data/function_pools/python.parquet`` (colonne ``bench.data.function_pools.COLUMNS``): funzioni
di CodeSearchNet **train** lette in ordine casuale fissato dal seme
``derive_seed(global_seed, "function_pool", "python")``, filtri e conteggi in
``bench.data.function_pools`` (manifest: ``stats``).
"""

from __future__ import annotations

import logging
import random
from typing import ClassVar

import pandas as pd
import pyarrow.parquet as pq

from bench.attacks.base import Attack, AttackPools
from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.data.function_pools import COLUMNS, MAX_FUNCTIONS, FunctionPoolBuilder
from bench.data.loaders.base import check_rows
from bench.domain.enums import Language
from bench.domain.errors import ConfigError, DataError
from bench.domain.ids import derive_seed
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.artifact_store import ArtifactStore
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)


def function_pool_ref(language: str) -> ArtifactRef:
    """Raccolta di funzioni di un linguaggio (solo Python)."""
    return ArtifactRef.of("function_pools", f"data/function_pools/{language}.parquet")


def function_pool_seed(cfg: ExperimentConfig, language: str) -> int:
    """Seme dell'ordine di lettura (§5.5)."""
    return derive_seed(cfg.global_seed, "function_pool", language)


def load_attack_pools(store: ArtifactStore, attack: Attack, language: str) -> AttackPools:
    """Raccolte che servono all'attacco: commenti e nomi (``attack_pools``, se ``needs_pools``)
    e funzioni (``function_pools``, se ``needs_function_pool``)."""
    comments: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    if attack.needs_pools:
        from bench.data.attack_pools import AttackPool
        from bench.pipeline.stages.attack_pools import attack_pool_ref

        pool = store.read_model(attack_pool_ref(language), AttackPool)
        comments, names = tuple(pool.comments), tuple(pool.names)
    if attack.needs_function_pool:
        table = store.read_table(function_pool_ref(language)).sort_values("function_id")
        functions = tuple(str(c) for c in table["code"])
    return AttackPools(comments, names, functions)


@STAGES.register("function_pools")
class FunctionPoolsStage(Stage):
    """Funzioni Python di CodeSearchNet train per T2.5."""

    name: ClassVar[str] = "function_pools"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"function_pools"})
    cell_axes: ClassVar[tuple[str, ...]] = ("language",)

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("function_pools needs the experiment configuration")
        return self.config

    @staticmethod
    def _language(cell: Cell) -> Language:
        lang = Language(str(cell.language))
        if lang is not Language.PYTHON:
            raise ConfigError(f"function_pools: Python only (T2.5), got {lang}")
        return lang

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [function_pool_ref(str(self._language(cell)))]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        lang = self._language(cell)
        cfg = self._cfg()
        spec = cfg.datasets["codesearchnet"]
        role = f"{lang}/train"
        path = spec.file(role)
        if not path.is_file():
            raise DataError(f"file not found: {path}")
        table = pq.read_table(path, columns=["whole_func_string", "repository_name"])
        check_rows(spec, role, table.num_rows)
        order = list(range(table.num_rows))
        random.Random(function_pool_seed(cfg, str(lang))).shuffle(order)
        codes = table.column("whole_func_string")
        repos = table.column("repository_name")
        builder = FunctionPoolBuilder(MAX_FUNCTIONS)
        for i in order:
            if builder.full:
                break
            builder.add(str(codes[i].as_py() or ""), str(repos[i].as_py() or ""))
            if builder.scanned % 20_000 == 0:
                logger.info("%s: %d scanned, %d kept", cell.key(), builder.scanned,
                            len(builder.rows))  # fmt: skip
        if not builder.rows:
            raise DataError("function_pools: no function passed the filters")
        frame = pd.DataFrame(builder.rows, columns=list(COLUMNS))
        ref = function_pool_ref(str(lang))
        stats = builder.stats()
        ctx.store.write_table(
            ref,
            frame,
            ctx.make_manifest(
                ref,
                inputs=[],
                n_rows_in=builder.scanned,
                n_rows_out=len(frame),
                # I1: MAX_FUNCTIONS, o meno solo se la fonte è stata letta tutta (exhausted)
                n_rows_expected=MAX_FUNCTIONS if builder.full else len(frame),
                extra={
                    "stats": stats,
                    "rows": table.num_rows,
                    "source": "codesearchnet/train",
                    "seed": function_pool_seed(cfg, str(lang)),
                    "exhausted": not builder.full,
                },  # fmt: skip
            ),
        )
        logger.info("%s: %d functions from %d scanned (%d rows)", ref.path, len(frame),
                    builder.scanned, table.num_rows)  # fmt: skip
