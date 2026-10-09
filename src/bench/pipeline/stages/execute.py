"""Fase ``execute``: esecuzione dei test nella sandbox (SPEC §11, §15.2; ADR-001).

Celle:

- ``source=canonical``: soluzioni canoniche di un livello e linguaggio (tutti i problemi,
  senza modello né parte), per verificare l'executor (atteso Pass@1 = 100%);
- ``source=llm_baseline``: campioni della baseline di (modello, livello, linguaggio, parte);
- ``source=llm_watermarked``: campioni marcati di (metodo, modello, livello, linguaggio, parte), con
  la configurazione di default del metodo (fino all'HPO); servono a Pass@1 e ΔPass@1 (M7);
- ``source=baseline_twin``: baseline gemella dei metodi che la prevedono (MCGMark, D20).

Output: ``execution/canonical/<L>_<lang>.parquet``,
``execution/llm_baseline/<modello>/<L>_<lang>_<parte>.parquet`` e, per le fonti di un metodo,
``execution/<fonte>/<metodo>/<modello>/<config_hash>/<L>_<lang>_<parte>.parquet``; una riga
per campione in input (I1) con i campi di ``ExecutionRecord`` più ``problem_key``,
``sample_index`` e ``dataset``. Un'invocazione della sandbox per problema; i problemi girano in
parallelo su ``max_workers`` thread (il lavoro è nei sottoprocessi della sandbox).

**Ripresa** (cluster_info §1): ogni problema completato va in un file parziale JSONL
(``execution/.../_partial/``); al rilancio si eseguono solo i problemi mancanti.
"""

from __future__ import annotations

import contextlib
import logging
import os
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from bench_contracts import append_jsonl, iter_jsonl

from bench.config.resources import ResourceClass
from bench.config.schema import ExperimentConfig
from bench.domain.enums import ExecStatus, Language, Source
from bench.domain.errors import ConfigError
from bench.domain.ids import sample_id
from bench.domain.models import ExecutionRecord, Problem
from bench.execution import sandbox as sandbox_mod
from bench.execution.executors import (
    EXECUTORS,
    EvalPlusExecutor,
    Executor,
    SampleToRun,
    executor_name,
)
from bench.execution.sandbox import Sandbox
from bench.pipeline.stage import Cell, Stage, StageContext
from bench.pipeline.stages.evalplus_groundtruth import groundtruth_ref, load_groundtruth
from bench.pipeline.stages.generate_baseline import baseline_ref, problems_ref
from bench.registry import METHODS, STAGES
from bench.store.hashing import sha256_json
from bench.store.refs import ArtifactRef

logger = logging.getLogger(__name__)

EXTRA_COLUMNS = ["problem_key", "sample_index", "dataset"]
OUTPUT_COLUMNS = [*EXTRA_COLUMNS, *ExecutionRecord.model_fields]


METHOD_SOURCES = ("llm_watermarked", "baseline_twin")


def execution_ref(cell: Cell, config_hash: str | None = None) -> ArtifactRef:
    """Esiti dell'esecuzione di una cella (``config_hash`` per le fonti di un metodo)."""
    if cell.source == "canonical":
        return ArtifactRef.of(
            "execution", f"execution/canonical/{cell.level}_{cell.language}.parquet"
        )
    if cell.source in METHOD_SOURCES:
        if not config_hash:
            raise ConfigError(f"execute: {cell.source} needs the method config hash")
        return ArtifactRef.of(
            "execution",
            f"execution/{cell.source}/{cell.method}/{cell.model_id}/{config_hash}/"
            f"{cell.level}_{cell.language}_{cell.split}.parquet",
        )
    return ArtifactRef.of(
        "execution",
        f"execution/{cell.source}/{cell.model_id}/{cell.level}_{cell.language}_{cell.split}.parquet",
    )


def job_cpus(cfg: ExperimentConfig) -> int:
    """CPU assegnate al job: configurazione, poi ``SLURM_CPUS_PER_TASK``, poi l'affinità del
    processo (mai ``os.cpu_count()``, che conta tutti i core del nodo)."""
    if cfg.execution.max_workers:
        return cfg.execution.max_workers
    slurm = os.environ.get("SLURM_CPUS_PER_TASK")
    if slurm:
        return max(1, int(slurm))
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        return max(1, len(affinity(0)))
    return max(1, os.cpu_count() or 1)  # solo dove manca sched_getaffinity (Windows, test)


def max_workers(cfg: ExperimentConfig, language: str | None = None) -> int:
    """Thread paralleli: le CPU del job; per Python la metà (come il default di EvalPlus,
    perché i limiti di tempo dipendono dal carico, ADR-007)."""
    cpus = job_cpus(cfg)
    if language == str(Language.PYTHON):
        return max(1, cpus // 2)
    return cpus


@STAGES.register("execute")
class ExecuteStage(Stage):
    """Esecuzione dei test di soluzioni canoniche e campioni."""

    name: ClassVar[str] = "execute"
    resources: ClassVar[ResourceClass] = ResourceClass.CPU
    output_kinds: ClassVar[frozenset[str]] = frozenset({"execution"})
    cell_axes: ClassVar[tuple[str, ...]] = (
        "source",
        "method",
        "model_id",
        "level",
        "language",
        "split",
    )

    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config

    @classmethod
    def create(cls, config: ExperimentConfig) -> Stage:
        return cls(config)

    @classmethod
    def normalize_cell(cls, cell: Cell) -> Cell | None:
        if cell.source == "canonical":
            return Cell(source="canonical", level=cell.level, language=cell.language)
        if cell.source == "llm_baseline":
            return Cell(
                source=cell.source,
                model_id=cell.model_id,
                level=cell.level,
                language=cell.language,
                split=cell.split,
            )
        if cell.source == "baseline_twin" and cell.method:
            import bench.methods.adapters  # noqa: F401  (popola METHODS)

            adapter_cls = METHODS.get(cell.method)
            if not getattr(adapter_cls, "twin_baseline", False):
                return None  # il metodo non ha una baseline gemella
        return cell

    def _cfg(self) -> ExperimentConfig:
        if self.config is None:
            raise ConfigError("execute needs the experiment configuration")
        return self.config

    @staticmethod
    def _check(cell: Cell) -> None:
        if cell.source == "canonical" and cell.level and cell.language:
            return
        complete = bool(cell.model_id and cell.level and cell.language and cell.split)
        if cell.source == "llm_baseline" and complete:
            return
        if cell.source in METHOD_SOURCES and complete and cell.method:
            return
        raise ConfigError(f"execute: incomplete or unknown cell {cell.key()}")

    def config_hash(self, cell: Cell) -> str | None:
        """Configurazione di default del metodo per le fonti di un metodo (fino all'HPO)."""
        if cell.source not in METHOD_SOURCES:
            return None
        from bench.pipeline.stages.watermark import make_adapter

        adapter = make_adapter(self._cfg(), str(cell.method))
        return adapter.config_hash(adapter.default_hparams())

    def samples_ref(self, cell: Cell) -> ArtifactRef | None:
        """Campioni da eseguire (``None`` per le canoniche)."""
        from bench.pipeline.stages.watermark import baseline_twin_ref, watermarked_ref

        level, language, split = str(cell.level), str(cell.language), str(cell.split)
        if cell.source == "llm_baseline":
            return baseline_ref(str(cell.model_id), level, language, split)
        cfg_hash = self.config_hash(cell)
        if cell.source == "llm_watermarked":
            return watermarked_ref(
                str(cell.method), str(cell.model_id), str(cfg_hash), level, language, split
            )
        if cell.source == "baseline_twin":
            return baseline_twin_ref(
                str(cell.method), str(cell.model_id), str(cfg_hash), level, language, split
            )
        return None

    def inputs(self, cell: Cell) -> list[ArtifactRef]:
        self._check(cell)
        refs = [problems_ref(str(cell.level), str(cell.language))]
        samples = self.samples_ref(cell)
        if samples is not None:
            refs.append(samples)
        if cell.language == str(Language.PYTHON):
            refs.append(groundtruth_ref())
        return refs

    def outputs(self, cell: Cell) -> list[ArtifactRef]:
        self._check(cell)
        return [execution_ref(cell, self.config_hash(cell))]

    # ------------------------------------------------------------------ campioni
    def _problems(self, ctx: StageContext, cell: Cell) -> list[Problem]:
        table = ctx.store.read_table(problems_ref(str(cell.level), str(cell.language)))
        if cell.split is not None:
            table = table[table["split"] == cell.split]
        problems = [Problem.model_validate(r) for r in table.to_dict(orient="records")]
        return sorted(problems, key=lambda p: p.problem_key)

    def _samples(
        self, ctx: StageContext, cell: Cell, problems: list[Problem], executors: dict[str, Executor]
    ) -> dict[str, list[SampleToRun]]:
        if cell.source == "canonical":
            # La canonica gira ``canonical_repeats`` volte: sample_index = ripetizione (ADR-007).
            repeats = self._cfg().execution.canonical_repeats
            return {
                p.problem_key: [
                    SampleToRun(
                        sample_id=sample_id(
                            source=Source.HUMAN,
                            problem_key=p.problem_key,
                            language=p.language,
                            sample_index=rep,
                        ),
                        problem_key=p.problem_key,
                        sample_index=rep,
                        code=executors[p.dataset].canonical(p),
                    )
                    for rep in range(repeats)
                ]
                for p in problems
            }
        ref = self.samples_ref(cell)
        assert ref is not None
        table = ctx.store.read_table(ref)
        by_problem: dict[str, list[SampleToRun]] = {p.problem_key: [] for p in problems}
        table = table.sort_values(["problem_key", "sample_index"])
        for row in table.to_dict(orient="records"):
            key = str(row["problem_key"])
            if key not in by_problem:
                raise ConfigError(f"baseline sample for unknown problem {key}")
            by_problem[key].append(
                SampleToRun(
                    sample_id=str(row["sample_id"]),
                    problem_key=key,
                    sample_index=int(row["sample_index"]),
                    code=str(row["code"]),
                    extraction_ok=bool(row["extraction_ok"]),
                )
            )
        return by_problem

    def _executors(
        self, ctx: StageContext, cell: Cell, problems: list[Problem], sandbox: Sandbox
    ) -> dict[str, Executor]:
        cfg = self._cfg()
        language = Language(str(cell.language))
        root = sandbox.workdir_root(cfg.paths.tmp / "execute")
        root.mkdir(parents=True, exist_ok=True)
        groundtruth = None
        if language is Language.PYTHON:
            groundtruth = load_groundtruth(ctx.store.read_table(groundtruth_ref()))
        executors: dict[str, Executor] = {}
        for dataset in sorted({p.dataset for p in problems}):
            cls = EXECUTORS.get(executor_name(dataset))
            if cls is EvalPlusExecutor:
                executors[dataset] = EvalPlusExecutor(cfg, sandbox, language, root, groundtruth)
            else:
                executors[dataset] = cls(cfg, sandbox, language, root)
        return executors

    # ------------------------------------------------------------------ esecuzione
    def run(self, cell: Cell, ctx: StageContext) -> None:
        cfg = self._cfg()
        problems = self._problems(ctx, cell)
        # Attributo letto a ogni lancio: i test lo sostituiscono con una sandbox finta.
        sandbox = sandbox_mod.make_sandbox(cfg.execution)
        executors = self._executors(ctx, cell, problems, sandbox)
        samples = self._samples(ctx, cell, problems, executors)

        input_hashes = {
            str(ref.path): (m.data_sha256 if (m := ctx.store.read_manifest(ref)) else None)
            for ref in self.inputs(cell)
        }
        fingerprint = sha256_json(
            {
                "execution": cfg.execution.model_dump(mode="json"),
                "image": sandbox.image_hash,
                "inputs": input_hashes,
            }
        )
        ref = self.outputs(cell)[0]
        partial = ctx.store.root / ref.path.parent / "_partial" / f"{ref.path.stem}.jsonl"
        done, retries = self._load_partial(partial, fingerprint)
        todo = [p for p in problems if p.problem_key not in done]
        workers = max_workers(cfg, cell.language)
        logger.info(
            "%s: %d problems, %d already done, %d to run with %d workers",
            cell.key(),
            len(problems),
            len(done),
            len(todo),
            workers,
        )
        lock = threading.Lock()
        versions: dict[str, str] = {}

        def work(problem: Problem) -> None:
            start = time.perf_counter()
            executor = executors[problem.dataset]
            records = executor.run_problem(problem, samples[problem.problem_key])
            entry = {
                "problem_key": problem.problem_key,
                "elapsed_s": time.perf_counter() - start,
                "records": [r.model_dump(mode="json") for r in records],
            }
            with lock:
                append_jsonl(partial, entry)
                done[problem.problem_key] = entry
                versions.update(executor.last_versions)

        if todo:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(work, p) for p in todo]
                for i, future in enumerate(as_completed(futures), start=1):
                    future.result()
                    if i % 50 == 0 or i == len(futures):
                        logger.info("%s: %d/%d problems executed", cell.key(), i, len(futures))
        self._retry_timeouts(cell, problems, samples, executors, done, retries, partial)
        self._write(ctx, cell, problems, samples, done, retries, sandbox, versions, partial)

    def _retry_timeouts(
        self,
        cell: Cell,
        problems: list[Problem],
        samples: dict[str, list[SampleToRun]],
        executors: dict[str, Executor],
        done: dict[str, dict[str, Any]],
        retries: dict[str, dict[str, Any]],
        partial: Path,
    ) -> None:
        """Ripete una volta, da soli e uno alla volta, i campioni finiti in ``TIMEOUT`` (ADR-007).

        Solo i fallimenti per tempo: risposte sbagliate ed errori non si ripetono. Il risultato
        finale è quello della ripetizione; il primo esito resta nel record.
        """
        pending = [
            (problem, sample)
            for problem in problems
            for sample, record in zip(
                samples[problem.problem_key], done[problem.problem_key]["records"], strict=True
            )
            if record["status"] == ExecStatus.TIMEOUT.value and sample.sample_id not in retries
        ]
        if not pending:
            return
        logger.info("%s: retrying %d timed-out sample(s) alone", cell.key(), len(pending))
        for problem, sample in pending:
            record = executors[problem.dataset].run_problem(problem, [sample])[0]
            retries[sample.sample_id] = record.model_dump(mode="json")
            append_jsonl(partial, {"retry": sample.sample_id, "record": retries[sample.sample_id]})

    @staticmethod
    def _load_partial(
        path: Path, fingerprint: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Problemi già eseguiti e ripetizioni già fatte (per ``sample_id``)."""
        done: dict[str, dict[str, Any]] = {}
        retries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            records = list(iter_jsonl(path))
            if records and records[0].get("fingerprint") == fingerprint:
                for record in records[1:]:
                    if "retry" in record:
                        retries[str(record["retry"])] = dict(record["record"])
                    else:
                        done[str(record["problem_key"])] = record
                return done, retries
            logger.warning("partial file %s has a different configuration: starting over", path)
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(path, {"fingerprint": fingerprint})
        return done, retries

    @staticmethod
    def _final(first: dict[str, Any], retry: dict[str, Any] | None) -> ExecutionRecord:
        """Record finale: esito della ripetizione se c'è stata, con il primo esito registrato."""
        first_record = ExecutionRecord.model_validate(first)
        if retry is None:
            return first_record.model_copy(update={"first_attempt_status": first_record.status})
        retry_record = ExecutionRecord.model_validate(retry)
        return retry_record.model_copy(
            update={
                "first_attempt_status": first_record.status,
                "retry_status": retry_record.status,
            }
        )

    def _write(
        self,
        ctx: StageContext,
        cell: Cell,
        problems: list[Problem],
        samples: dict[str, list[SampleToRun]],
        done: dict[str, dict[str, Any]],
        retries: dict[str, dict[str, Any]],
        sandbox: Sandbox,
        versions: dict[str, str],
        partial: Path,
    ) -> None:
        rows = []
        for problem in problems:
            records = done[problem.problem_key]["records"]
            for sample, record in zip(samples[problem.problem_key], records, strict=True):
                final = self._final(record, retries.get(sample.sample_id))
                rows.append(
                    {
                        "problem_key": problem.problem_key,
                        "sample_index": sample.sample_index,
                        "dataset": problem.dataset,
                        **final.model_dump(mode="json"),
                    }
                )
        df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        df["sample_index"] = df["sample_index"].astype("Int64")
        n_expected = sum(len(v) for v in samples.values())
        statuses = Counter(df["status"])
        first = Counter(df["first_attempt_status"])
        retried = df[df["retry_status"].notna()]
        elapsed = [float(done[p.problem_key]["elapsed_s"]) for p in problems]
        n = len(df)
        extra = {
            "status_counts": {s.value: int(statuses.get(s.value, 0)) for s in ExecStatus},
            "first_attempt_status_counts": {
                s.value: int(first.get(s.value, 0)) for s in ExecStatus
            },
            "retried": len(retried),
            "recovered_timeout_to_passed": int(
                (retried["retry_status"] == ExecStatus.PASSED.value).sum()
            ),
            "passed_fraction": round(statuses.get(ExecStatus.PASSED.value, 0) / n, 6)
            if n
            else None,
            "sandbox_versions": versions,
            "seconds_per_problem": {
                "mean": round(statistics.fmean(elapsed), 3) if elapsed else None,
                "max": round(max(elapsed), 3) if elapsed else None,
            },
            "workers": max_workers(self._cfg(), cell.language),
            "job_cpus": job_cpus(self._cfg()),
        }
        ref = self.outputs(cell)[0]
        manifest = ctx.make_manifest(
            ref,
            inputs=self.inputs(cell),
            n_rows_in=n_expected,
            n_rows_out=n,
            n_rows_expected=n_expected,
            extra=extra,
        ).model_copy(update={"sandbox_image_hash": sandbox.image_hash})
        ctx.store.write_table(ref, df, manifest)
        partial.unlink(missing_ok=True)
        with contextlib.suppress(OSError):  # cartella _partial vuota
            partial.parent.rmdir()
        logger.info(
            "%s: %d records, status %s, %d retried (%d recovered)",
            cell.key(),
            n,
            dict(statuses),
            extra["retried"],
            extra["recovered_timeout_to_passed"],
        )
