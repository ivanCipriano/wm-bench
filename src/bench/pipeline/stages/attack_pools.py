"""Fase ``attack_pools``: raccolte di commenti e nomi per T1.3(b) e T1.4 (ADR-012).

Una cella per linguaggio, output ``data/attack_pools/<linguaggio>.json`` (``AttackPool``):

- Python, Java, JavaScript: ``max_functions`` funzioni di CodeSearchNet **train**
  (``whole_func_string``) in ordine casuale fissato dal seme;
- C++: funzioni dei file di The Stack i cui repository sono nel bucket ``attack_pools`` della
  partizione (``negatives.thestack_partition``); la cella C++ conta anche repository, file e
  funzioni di **ogni** bucket (``thestack_partition`` nel manifest), per verificare che tutti gli
  usi abbiano abbastanza dati (``scripts/thestack_partition_report.py``).

Filtri e conteggi: ``bench.data.attack_pools``.
"""

from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict
from typing import Any, ClassVar

import pyarrow.parquet as pq

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.data.attack_pools import PoolBuilder
from bench.data.loaders.base import check_rows, normalize_newlines
from bench.data.sources import STACK_COLUMNS, TheStackCppSource
from bench.domain.enums import Language
from bench.domain.errors import ConfigError, DataError
from bench.domain.ids import derive_seed
from bench.lang.functions import extract_functions
from bench.lang.parsers import default_parsers
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

MAX_FUNCTIONS = 50_000
MAX_ITEMS = 20_000
BUCKET = "attack_pools"


def attack_pool_ref(language: str) -> ArtifactRef:
    """Raccolta di commenti e nomi di un linguaggio."""
    return ArtifactRef.of("attack_pools", f"data/attack_pools/{language}.json")


def pool_seed(cfg: ExperimentConfig, dataset: str, language: str) -> int:
    """Seme dei campionamenti della raccolta (SPEC §18)."""
    return derive_seed(cfg.global_seed, "sample", dataset, language, "attack_pools")


@STAGES.register("attack_pools")
class AttackPoolsStage(Stage):
    """Commenti e nomi umani per linguaggio."""

    name: ClassVar[str] = "attack_pools"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"attack_pools"})
    cell_axes: ClassVar[tuple[str, ...]] = ("language",)

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("attack_pools needs the experiment configuration")
        return self.config

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [attack_pool_ref(str(cell.language))]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        lang = Language(str(cell.language))
        builder = PoolBuilder(lang, default_parsers())
        if lang is Language.CPP:
            dataset = "thestack_cpp"
            functions, extra = self._stack_functions()
        else:
            dataset = "codesearchnet"
            functions, extra = self._csn_functions(lang)
        for i, code in enumerate(functions, start=1):
            builder.add_function(code)
            if i % 10_000 == 0:
                logger.info("%s: %d/%d functions", cell.key(), i, len(functions))
        pool = builder.build(
            source=f"{dataset}/{BUCKET if lang is Language.CPP else 'train'}",
            seed=pool_seed(self._cfg(), dataset, str(lang)),
            max_items=MAX_ITEMS,
        )
        ref = attack_pool_ref(str(lang))
        ctx.store.write_model(
            ref,
            pool,
            ctx.make_manifest(
                ref,
                inputs=[],
                n_rows_in=len(functions),
                n_rows_out=1,
                n_rows_expected=1,
                extra={"stats": pool.stats, "max_functions": MAX_FUNCTIONS, **extra},
            ),
        )
        logger.info(
            "%s: %d comments, %d names from %d functions",
            ref.path,
            len(pool.comments),
            len(pool.names),
            len(functions),
        )

    # ------------------------------------------------------------------ fonti
    def _csn_functions(self, lang: Language) -> tuple[list[str], dict[str, Any]]:
        spec = self._cfg().datasets["codesearchnet"]
        role = f"{lang}/train"
        path = spec.file(role)
        if not path.is_file():
            raise DataError(f"file not found: {path}")
        table = pq.read_table(path, columns=["whole_func_string", "repository_name"])
        check_rows(spec, role, table.num_rows)
        order = list(range(table.num_rows))
        random.Random(pool_seed(self._cfg(), "codesearchnet", str(lang))).shuffle(order)
        chosen = sorted(order[:MAX_FUNCTIONS])
        codes = table.column("whole_func_string").take(chosen).to_pylist()
        repos = table.column("repository_name").take(chosen).to_pylist()
        functions = [normalize_newlines(str(c)) for c in codes]
        return functions, {
            "rows": table.num_rows,
            "sampled_functions": len(functions),
            "sampled_repos": len(set(repos)),
        }

    def _stack_functions(self) -> tuple[list[str], dict[str, Any]]:
        cfg = self._cfg()
        spec = cfg.datasets["thestack_cpp"]
        partition = cfg.negatives.thestack_partition
        if BUCKET not in partition:
            raise ConfigError(f"negatives.thestack_partition has no {BUCKET!r} bucket")
        source = TheStackCppSource(spec, partition, cfg.global_seed)
        parsers = default_parsers()
        repos: dict[str, set[str]] = defaultdict(set)
        files: Counter[str] = Counter()
        n_functions: Counter[str] = Counter()
        pool_functions: list[tuple[str, str]] = []
        rows = 0
        for role in sorted(spec.files):
            path = spec.file(role)
            if not path.is_file():
                raise DataError(f"file not found: {path}")
            table = pq.read_table(path, columns=list(STACK_COLUMNS))
            check_rows(spec, role, table.num_rows)
            rows += table.num_rows
            for row in table.to_pylist():
                repo = str(row["max_stars_repo_name"] or row["hexsha"])
                bucket = source.bucket(repo)
                repos[bucket].add(repo)
                files[bucket] += 1
                content = normalize_newlines(str(row["content"] or ""))
                for fn in extract_functions(content, Language.CPP, parsers):
                    n_functions[bucket] += 1
                    if bucket == BUCKET:
                        pool_functions.append((f"{row['hexsha']}:{fn.start_line}", fn.code))
            logger.info("thestack %s: %d files read", role, table.num_rows)
        pool_functions.sort()
        rng = random.Random(pool_seed(cfg, "thestack_cpp", str(Language.CPP)))
        chosen = rng.sample(pool_functions, min(MAX_FUNCTIONS, len(pool_functions)))
        report = {
            b: {"repos": len(repos[b]), "files": files[b], "functions": n_functions[b]}
            for b in partition
        }
        return [code for _, code in sorted(chosen)], {
            "rows": rows,
            "thestack_partition": report,
            "partition_fractions": dict(partition),
            "sampled_functions": len(chosen),
        }
