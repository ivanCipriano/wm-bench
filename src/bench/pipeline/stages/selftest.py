"""Fase ``selftest``: verifica end-to-end di store, manifest, salto e invarianti.

Scrive una piccola tabella deterministica in ``_selftest/selftest.parquet``. Non dipende
da modelli, dataset o GPU: serve per il criterio di accettazione della M1 e per provare
lo store sul filesystem del cluster.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.domain.ids import derive_seed
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

SELFTEST_ROWS = 100


@STAGES.register("selftest")
class SelfTestStage(Stage):
    """Fase di autoverifica senza input."""

    name: ClassVar[str] = "selftest"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"selftest"})
    cell_axes: ClassVar[tuple[str, ...]] = ()

    OUTPUT = ArtifactRef.of("selftest", "_selftest/selftest.parquet")

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [self.OUTPUT]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        seeds = [derive_seed(ctx.config.global_seed, "selftest", i) for i in range(SELFTEST_ROWS)]
        df = pd.DataFrame({"index": range(SELFTEST_ROWS), "seed": seeds})
        manifest = ctx.make_manifest(
            self.OUTPUT, n_rows_in=None, n_rows_out=len(df), n_rows_expected=SELFTEST_ROWS
        )
        ctx.store.write_table(self.OUTPUT, df, manifest)
