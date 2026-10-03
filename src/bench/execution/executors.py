"""Executor dei test (SPEC §11.3, ADR-001).

Un executor riceve un problema e i suoi campioni e restituisce un ``ExecutionRecord`` per
campione: nessun campione sparisce (I1). I campioni con ``extraction_ok=False`` non vengono
eseguiti (``EXTRACTION_FAILED``). Gli altri girano in **un'unica invocazione** della sandbox
per problema; il runner (``containers/runner/wmb_runner.py``) li esegue uno per uno.

- ``evalplus`` (HumanEval+, MBPP+): test base+plus con le funzioni di EvalPlus 0.3.1 e la
  ground truth calcolata una volta dalla fase ``evalplus_groundtruth``;
- ``humanevalpack`` (Java, C++, JavaScript): logica di ``humanevalsynthesize-<lang>`` di
  bigcode-evaluation-harness (``bench.execution.harness``);
- ``syntax`` (dati senza test, L3/L4): tree-sitter sull'host, poi compilazione o controllo
  sintattico nella sandbox.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from bench.config.schema import ExperimentConfig
from bench.domain.enums import ExecStatus, Language
from bench.domain.errors import ConfigError
from bench.domain.models import ExecutionRecord, Problem
from bench.execution import harness
from bench.execution.sandbox import Sandbox
from bench.registry import Registry

logger = logging.getLogger(__name__)

EXECUTORS: Registry[Executor] = Registry("executor")

# Margine dell'invocazione oltre la somma dei tempi dei campioni (avvio del container).
INVOCATION_MARGIN_S = 60.0


@dataclass(frozen=True)
class SampleToRun:
    """Campione da eseguire: identità e codice."""

    sample_id: str
    problem_key: str
    sample_index: int | None
    code: str
    extraction_ok: bool = True


class Executor(ABC):
    """Strategia di esecuzione di un dataset.

    Args:
        config: configurazione validata.
        sandbox: sandbox in cui eseguire il runner.
        language: linguaggio della cella.
        workdir_root: cartella sotto cui creare le cartelle di lavoro temporanee.
    """

    name: ClassVar[str]

    def __init__(
        self, config: ExperimentConfig, sandbox: Sandbox, language: Language, workdir_root: Path
    ) -> None:
        self.config = config
        self.exec_cfg = config.execution
        self.sandbox = sandbox
        self.language = language
        self.workdir_root = workdir_root
        self.last_versions: dict[str, str] = {}  # versioni degli strumenti dell'immagine

    # ------------------------------------------------------------------ da ridefinire
    @abstractmethod
    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        """``job.json`` del runner e file accessori (nome → contenuto) per un problema."""

    @abstractmethod
    def budget_s(self, n_samples: int) -> float:
        """Tempo massimo dell'invocazione per ``n_samples`` campioni."""

    def canonical(self, problem: Problem) -> str:
        """Programma della soluzione canonica (come lo comporrebbe l'harness)."""
        return (problem.prompt_text or "") + (problem.canonical_solution or "")

    def precheck(self, sample: SampleToRun) -> ExecutionRecord | None:
        """Esito deciso senza eseguire (``None`` = da eseguire)."""
        return None

    # ------------------------------------------------------------------ comune
    def record(self, sample: SampleToRun, status: ExecStatus, **fields: Any) -> ExecutionRecord:
        """``ExecutionRecord`` con executor e hash dell'immagine."""
        return ExecutionRecord(
            sample_id=sample.sample_id,
            status=status,
            n_tests=fields.get("n_tests"),
            n_passed=fields.get("n_passed"),
            duration_s=float(fields.get("duration_s", 0.0)),
            stderr_tail=fields.get("stderr_tail"),
            executor=self.name,
            sandbox_image_hash=self.sandbox.image_hash,
        )

    def run_problem(self, problem: Problem, samples: list[SampleToRun]) -> list[ExecutionRecord]:
        """Esegue i campioni di un problema; l'ordine dei record segue quello dei campioni."""
        decided: dict[str, ExecutionRecord] = {}
        todo: list[SampleToRun] = []
        for sample in samples:
            if not sample.extraction_ok:
                decided[sample.sample_id] = self.record(sample, ExecStatus.EXTRACTION_FAILED)
                continue
            early = self.precheck(sample)
            if early is not None:
                decided[sample.sample_id] = early
            else:
                todo.append(sample)
        if todo:
            decided.update(self._invoke(problem, todo))
        return [decided[s.sample_id] for s in samples]

    def _invoke(self, problem: Problem, todo: list[SampleToRun]) -> dict[str, ExecutionRecord]:
        workdir = self.workdir_root / f"exec_{os.getpid()}_{uuid.uuid4().hex[:12]}"
        workdir.mkdir(parents=True, exist_ok=False)
        try:
            job, files = self.job(problem, todo)
            for name, content in files.items():
                (workdir / name).write_bytes(content)
            (workdir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            result = self.sandbox.run(
                self.sandbox.runner_command(workdir), workdir, self.budget_s(len(todo))
            )
            out = workdir / "result.json"
            if not out.is_file():
                reason = "timeout" if result.timed_out else f"exit code {result.returncode}"
                tail = (result.stderr or result.stdout).strip()[-self.exec_cfg.stderr_tail_chars :]
                logger.warning("%s: sandbox failed (%s): %s", problem.problem_key, reason, tail)
                return {
                    s.sample_id: self.record(
                        s, ExecStatus.SANDBOX_ERROR, stderr_tail=f"sandbox {reason}: {tail}"
                    )
                    for s in todo
                }
            data = json.loads(out.read_text(encoding="utf-8"))
            by_id = {str(r["id"]): r for r in data["results"]}
            records = {}
            for i, sample in enumerate(todo):
                raw = by_id.get(str(i))
                if raw is None:
                    records[sample.sample_id] = self.record(
                        sample, ExecStatus.SANDBOX_ERROR, stderr_tail="no result from the runner"
                    )
                    continue
                records[sample.sample_id] = self.record(
                    sample,
                    ExecStatus(raw["status"]),
                    n_tests=raw.get("n_tests"),
                    n_passed=raw.get("n_passed"),
                    duration_s=raw.get("duration_s", 0.0),
                    stderr_tail=raw.get("stderr_tail"),
                )
            self.last_versions = data.get("versions", {})
            return records
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _base_job(self, kind: str) -> dict[str, Any]:
        lang = str(self.language)
        return {
            "mode": "samples",
            "kind": kind,
            "language": lang,
            "timeout_s": self.exec_cfg.timeouts_s[lang],
            "compile_timeout_s": self.exec_cfg.compile_timeout_s,
            "mem_mb": self.exec_cfg.mem_mb[lang],
            "stderr_tail_chars": self.exec_cfg.stderr_tail_chars,
        }


@EXECUTORS.register("humanevalpack")
class HumanEvalPackExecutor(Executor):
    """HumanEvalPack: programma = generazione preparata + test, compilato ed eseguito."""

    name: ClassVar[str] = "humanevalpack"

    def __init__(
        self, config: ExperimentConfig, sandbox: Sandbox, language: Language, workdir_root: Path
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        if language not in harness.RUN_CMD:
            raise ConfigError(f"humanevalpack executor does not support {language}")
        self._tests = self._load_tests()

    def _load_tests(self) -> dict[str, str]:
        from bench.data.loaders.base import read_parquet
        from bench.data.loaders.humanevalpack import DIRS

        spec = self.config.datasets["humanevalpack"]
        frame = read_parquet(spec.file(DIRS[self.language]), ("task_id", "test"))
        return {str(t): str(x) for t, x in zip(frame["task_id"], frame["test"], strict=True)}

    def test_for(self, problem: Problem) -> str:
        """Test del problema (colonna ``test`` del dataset)."""
        if problem.test_ref not in self._tests:
            raise ConfigError(f"no humanevalpack test for {problem.test_ref}")
        return self._tests[problem.test_ref]

    def budget_s(self, n_samples: int) -> float:
        per_sample = self.exec_cfg.compile_timeout_s + self.exec_cfg.timeouts_s[str(self.language)]
        return n_samples * (per_sample + 5) + INVOCATION_MARGIN_S

    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        test = self.test_for(problem)
        job = self._base_job("program")
        job.update(
            {
                "rule": str(self.language),
                "compile_cmd": harness.COMPILE_CMD[self.language],
                "run_cmd": harness.RUN_CMD[self.language],
                "samples": [
                    {"id": str(i), "program": harness.check_program(s.code, test, self.language)}
                    for i, s in enumerate(samples)
                ],
            }
        )
        return job, {}


@EXECUTORS.register("evalplus")
class EvalPlusExecutor(Executor):
    """HumanEval+ e MBPP+: test base+plus di EvalPlus con la ground truth precalcolata.

    Args:
        groundtruth: ``task_id`` → riga della ground truth (``payload`` pickle e
            ``canonical_program``), prodotta da ``evalplus_groundtruth``.
    """

    name: ClassVar[str] = "evalplus"
    DATASETS: ClassVar[dict[str, str]] = {"humanevalplus": "humaneval", "mbppplus": "mbpp"}

    def __init__(
        self,
        config: ExperimentConfig,
        sandbox: Sandbox,
        language: Language,
        workdir_root: Path,
        groundtruth: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        if language is not Language.PYTHON:
            raise ConfigError("evalplus executor is Python only")
        self.groundtruth = groundtruth or {}

    def _entry(self, problem: Problem) -> dict[str, Any]:
        entry = self.groundtruth.get(problem.test_ref or "")
        if entry is None:
            raise ConfigError(f"no EvalPlus ground truth for {problem.test_ref}")
        return entry

    def canonical(self, problem: Problem) -> str:
        return str(self._entry(problem)["canonical_program"])

    def budget_s(self, n_samples: int) -> float:
        # untrusted_check: al più min(60, somma dei limiti) + 3 s per parte (base e plus).
        return n_samples * 2 * 65.0 + INVOCATION_MARGIN_S

    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        entry = self._entry(problem)
        job = self._base_job("evalplus")
        job.update(
            {
                "evalplus": {
                    "dataset": self.DATASETS[problem.dataset],
                    "payload": "payload.pkl",
                    "min_time_limit": self.exec_cfg.evalplus.min_time_limit,
                    "gt_time_limit_factor": self.exec_cfg.evalplus.gt_time_limit_factor,
                },
                "samples": [{"id": str(i), "program": s.code} for i, s in enumerate(samples)],
            }
        )
        return job, {"payload.pkl": bytes(entry["payload"])}


# Controllo sintattico per linguaggio: comando nella sandbox e file sorgente (regola del runner).
SYNTAX_CMD: dict[Language, list[str]] = {
    Language.PYTHON: ["python3", "-m", "py_compile", "main.py"],
    Language.JAVA: ["javac", "-d", "classes", "Main.java"],
    Language.CPP: ["g++", "-std=c++17", "-fsyntax-only", "main.cpp"],
    Language.JAVASCRIPT: ["node", "--check", "main.js"],
}


@EXECUTORS.register("syntax")
class SyntaxExecutor(Executor):
    """Validità sintattica (tree-sitter) e compilazione per i dati senza test (SPEC §11.3).

    I metodi Java isolati (CodeSearchNet) vengono racchiusi in una classe prima di ``javac``.
    Da riverificare quando arrivano i dati di L3/L4 (Milestone 8).
    """

    name: ClassVar[str] = "syntax"

    def __init__(
        self, config: ExperimentConfig, sandbox: Sandbox, language: Language, workdir_root: Path
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        from bench.lang.parsers import default_parsers

        self.parsers = default_parsers()

    def precheck(self, sample: SampleToRun) -> ExecutionRecord | None:
        if not sample.code.strip() or self.parsers.has_error(sample.code, self.language):
            return self.record(
                sample, ExecStatus.SYNTAX_ERROR, stderr_tail="tree-sitter: ERROR node"
            )
        return None

    def budget_s(self, n_samples: int) -> float:
        return n_samples * (self.exec_cfg.compile_timeout_s + 5) + INVOCATION_MARGIN_S

    def source(self, code: str) -> str:
        """Sorgente compilato (i metodi Java isolati vanno in una classe)."""
        if self.language is Language.JAVA:
            root = self.parsers.parse(code, Language.JAVA).root_node
            kinds = {child.type for child in root.children}
            declarations = {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
            }
            if not kinds & declarations:
                return "class Main {\n" + code + "\n}\n"
            return code.replace("public class ", "class ").replace(
                "public final class ", "final class "
            )
        return code

    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        job = self._base_job("program")
        job.update(
            {
                "rule": f"syntax_{self.language}",
                "compile_cmd": SYNTAX_CMD[self.language],
                "run_cmd": None,
                "samples": [
                    {"id": str(i), "program": self.source(s.code)} for i, s in enumerate(samples)
                ],
            }
        )
        return job, {}


def executor_name(dataset: str) -> str:
    """Executor di un dataset (SPEC §11.3)."""
    names = {
        "humanevalplus": "evalplus",
        "mbppplus": "evalplus",
        "humanevalpack": "humanevalpack",
        "codesearchnet": "syntax",
        "thestack_cpp": "syntax",
    }
    if dataset not in names:
        raise ConfigError(f"no executor for dataset '{dataset}' (yet)")
    return names[dataset]
