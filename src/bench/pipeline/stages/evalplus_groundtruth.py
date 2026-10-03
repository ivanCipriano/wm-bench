"""Fase ``evalplus_groundtruth``: output attesi e tempi delle soluzioni canoniche (ADR-001).

EvalPlus confronta l'output di ogni campione con quello della soluzione canonica sugli stessi
input (base e plus) e ricava i limiti di tempo dai tempi della canonica. EvalPlus calcola
questa *ground truth* una volta e la tiene in cache; qui la si calcola una volta, **dentro la
sandbox** (è esecuzione di codice) con ``evalplus.evaluate.get_groundtruth``, a partire dai
file JSONL fissati (cluster_info §8.1), e la si salva come artefatto con manifest.

Output: ``execution/_groundtruth/evalplus.parquet``, una riga per problema di HumanEval+ e
MBPP+ con il pickle prodotto da EvalPlus (problema e oracolo) e il programma canonico.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from typing import Any, ClassVar

import pandas as pd

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError, SandboxError
from bench.execution import sandbox as sandbox_mod
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.registry import STAGES
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

GROUNDTRUTH_COLUMNS = ["dataset", "task_id", "canonical_program", "n_base", "n_plus", "payload"]


def groundtruth_ref() -> ArtifactRef:
    """Ground truth di EvalPlus per HumanEval+ e MBPP+."""
    return ArtifactRef.of("evalplus_groundtruth", "execution/_groundtruth/evalplus.parquet")


@STAGES.register("evalplus_groundtruth")
class EvalPlusGroundTruthStage(Stage):
    """Ground truth di EvalPlus, calcolata nella sandbox."""

    name: ClassVar[str] = "evalplus_groundtruth"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"evalplus_groundtruth"})

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        return []

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        return [groundtruth_ref()]

    def run(self, cell: Cell, ctx: StageContext) -> None:
        if self.config is None:
            raise ConfigError("evalplus_groundtruth needs the experiment configuration")
        cfg = self.config
        he = cfg.datasets["humanevalplus"].file("data")
        mbpp = cfg.datasets["mbppplus"].file("data")
        if he.parent != mbpp.parent:
            raise ConfigError(f"HumanEval+ and MBPP+ must be in the same folder: {he}, {mbpp}")
        sandbox = sandbox_mod.make_sandbox(cfg.execution)
        workdir = cfg.paths.tmp / "evalplus_groundtruth" / uuid.uuid4().hex[:12]
        workdir.mkdir(parents=True)
        try:
            job = {
                "mode": "groundtruth",
                "datasets": {"humaneval": sandbox.data_path(he), "mbpp": sandbox.data_path(mbpp)},
                "out_dir": "gt",
                "mem_mb": cfg.execution.mem_mb["python"],
            }
            (workdir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            logger.info("computing the EvalPlus ground truth in the sandbox (minutes)")
            result = sandbox.run(
                sandbox.runner_command(workdir),
                workdir,
                cfg.execution.groundtruth_timeout_s,
                data_dir=he.parent,
            )
            out = workdir / "result.json"
            if not out.is_file():
                raise SandboxError(
                    f"ground truth failed (exit {result.returncode}, timeout={result.timed_out}): "
                    f"{(result.stderr or result.stdout).strip()[-3000:]}"
                )
            data = json.loads(out.read_text(encoding="utf-8"))
            rows = []
            for entry in data["problems"]:
                payload = (workdir / entry["file"]).read_bytes()
                rows.append({**{k: entry[k] for k in GROUNDTRUTH_COLUMNS[:-1]}, "payload": payload})
            self._write(
                ctx, cell, pd.DataFrame(rows, columns=GROUNDTRUTH_COLUMNS), data, sandbox.image_hash
            )
            logger.info("%s", result.stdout.strip())
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _write(
        self, ctx: StageContext, cell: Cell, df: pd.DataFrame, data: dict[str, Any], image_hash: str
    ) -> None:
        ref = groundtruth_ref()
        cfg = ctx.config
        # I1: una riga per problema dei due file (conteggi fissati in configs/dataset/*.yaml).
        expected = sum(
            cfg.datasets[name].expected_rows.get("data", 0)
            for name in ("humanevalplus", "mbppplus")
        )
        counts = df.groupby("dataset").size().to_dict() if len(df) else {}
        manifest = ctx.make_manifest(
            ref,
            n_rows_in=expected,
            n_rows_out=len(df),
            n_rows_expected=expected,
            extra={
                "dataset_hash": data.get("dataset_hash", {}),
                "problems": {str(k): int(v) for k, v in counts.items()},
                "sandbox_versions": data.get("versions", {}),
            },
        ).model_copy(update={"sandbox_image_hash": image_hash})
        ctx.store.write_table(ref, df, manifest)


def load_groundtruth(table: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Ground truth indicizzata per ``task_id`` (es. ``HumanEval/0``, ``Mbpp/2``)."""
    return {
        str(r["task_id"]): {str(k): v for k, v in r.items()}
        for r in table.to_dict(orient="records")
    }
