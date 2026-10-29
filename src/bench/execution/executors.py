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
  sintattico nella sandbox;
- ``classeval`` (L3): la classe con i test ufficiali di ClassEval (``classeval_check.py``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import textwrap
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

    La misura di riferimento è la validità con tree-sitter (``tree_sitter_valid``: nessun nodo
    ERROR, con gli involucri delle funzioni isolate): se manca, ``SYNTAX_ERROR`` con
    ``tree-sitter: ERROR node`` senza invocare la sandbox. Gli altri campioni passano a ``javac``,
    ``g++ -fsyntax-only``, ``node --check`` o ``py_compile``, solo informativi per Java e C++
    (decisione dell'utente del 29 ottobre 2026, ADR-016): staccati dal loro progetto falliscono
    anche su codice corretto. I metodi Java isolati vanno in una classe prima di ``javac``; il
    codice Python perde l'indentazione comune.
    """

    name: ClassVar[str] = "syntax"

    def __init__(
        self, config: ExperimentConfig, sandbox: Sandbox, language: Language, workdir_root: Path
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        from bench.lang.parsers import default_parsers

        self.parsers = default_parsers()

    def precheck(self, sample: SampleToRun) -> ExecutionRecord | None:
        from bench.data.l3_prompts import tree_sitter_valid

        if not tree_sitter_valid(sample.code, self.language, self.parsers):
            return self.record(
                sample, ExecStatus.SYNTAX_ERROR, stderr_tail="tree-sitter: ERROR node"
            )
        return None

    def canonical(self, problem: Problem) -> str:
        """La funzione umana completa (in L3 il prompt ne è già una parte, ADR-016)."""
        return problem.canonical_solution or ""

    def budget_s(self, n_samples: int) -> float:
        return n_samples * (self.exec_cfg.compile_timeout_s + 5) + INVOCATION_MARGIN_S

    def source(self, code: str) -> str:
        """Sorgente controllato e compilato: i metodi Java isolati vanno in una classe; il codice
        Python perde l'indentazione comune (``textwrap.dedent``: metodi di CodeSearchNet, ADR-016).
        """
        if self.language is Language.PYTHON:
            return textwrap.dedent(code)
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


# CodeNet: compilazione (o controllo sintattico) ed esecuzione del programma completo.
CODENET_COMPILE: dict[Language, list[str]] = {
    Language.PYTHON: ["python3", "-m", "py_compile", "main.py"],
    Language.JAVA: ["javac", "Main.java"],
    Language.CPP: ["g++", "-std=c++17", "-O2", "-o", "a.out", "main.cpp"],
    Language.JAVASCRIPT: ["node", "--check", "main.js"],
}
CODENET_RUN: dict[Language, list[str]] = {
    Language.PYTHON: ["python3", "main.py"],
    Language.JAVA: ["java", "-cp", ".", "Main"],
    Language.CPP: ["./a.out"],
    Language.JAVASCRIPT: ["node", "main.js"],
}
IO_CHECK = Path(__file__).with_name("io_check.py")


@EXECUTORS.register("codenet")
class CodeNetExecutor(Executor):
    """CodeNet (L2): programma completo con l'input del problema su stdin, stdout confrontato.

    Usa la modalità ``program`` del runner già nell'immagine, senza ricostruirla: i nomi dei file
    vengono dalle regole ``syntax_<lang>`` (``main.py``, ``Main.java``, ``main.cpp``, ``main.js``),
    la compilazione dà ``COMPILE_ERROR`` (C++, Java) o ``SYNTAX_ERROR`` (Python, JavaScript), e
    l'esecuzione passa per ``io_check.py`` (file accessorio del job), che fornisce l'input e
    confronta l'output: la regola generica di ``classify`` dà PASSED, FAILED o RUNTIME_ERROR.
    Un solo caso di I/O per problema (``derived/input_output/data/<pid>``, cluster_info §8.6).

    Args:
        io_root: cartella dei casi di I/O (default: ``datasets.codenet.dirs.io_data``).
    """

    name: ClassVar[str] = "codenet"

    def __init__(
        self,
        config: ExperimentConfig,
        sandbox: Sandbox,
        language: Language,
        workdir_root: Path,
        io_root: Path | None = None,
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        if language not in CODENET_RUN:
            raise ConfigError(f"codenet executor does not support {language}")
        if io_root is None:
            io_root = config.datasets["codenet"].dirs["io_data"]
        self.io_root = io_root

    def io_case(self, problem: Problem) -> tuple[bytes, bytes]:
        """Input ed output attesi del problema.

        Raises:
            ConfigError: se il caso manca.
        """
        pid = (problem.test_ref or problem.problem_key).rsplit("/", 1)[-1]
        folder = self.io_root / pid
        files = folder / "input.txt", folder / "output.txt"
        if not all(f.is_file() for f in files):
            raise ConfigError(f"no CodeNet input/output case in {folder}")
        return files[0].read_bytes(), files[1].read_bytes()

    def canonical(self, problem: Problem) -> str:
        return problem.canonical_solution or ""

    def budget_s(self, n_samples: int) -> float:
        per_sample = self.exec_cfg.compile_timeout_s + self.exec_cfg.timeouts_s[str(self.language)]
        return n_samples * (per_sample + 5) + INVOCATION_MARGIN_S

    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        stdin, expected = self.io_case(problem)
        job = self._base_job("program")
        job.update(
            {
                "rule": f"syntax_{self.language}",
                "compile_cmd": CODENET_COMPILE[self.language],
                "run_cmd": [
                    "python3",
                    "../io_check.py",
                    "../input.txt",
                    "../expected.txt",
                    "--",
                    *CODENET_RUN[self.language],
                ],
                "samples": [{"id": str(i), "program": s.code} for i, s in enumerate(samples)],
            }
        )
        files = {
            "io_check.py": IO_CHECK.read_bytes(),
            "input.txt": stdin,
            "expected.txt": expected,
        }
        return job, files


CLASSEVAL_CHECK = Path(__file__).with_name("classeval_check.py")
CLASSEVAL_CLASS_TIMEOUT_S = 5  # func_set_timeout(5) di test_pipeline.py, per classe di test


def add_static_statement(code: str) -> str:
    """``AutoTest.add_static_statement`` di ClassEval, riga per riga.

    Toglie i ``@staticmethod`` e lo rimette davanti ai metodi (indentazione 4) senza ``self`` né
    ``cls`` nella riga della firma.
    """
    lines = [line for line in code.split("\n") if "@staticmethod" not in line]
    out: list[str] = []
    for line in lines:
        spaces = len(line) - len(line.lstrip())
        if (
            line.strip().startswith("def ")
            and "self" not in line
            and "cls" not in line
            and spaces == 4  # noqa: PLR2004 - regola dell'originale
        ):
            out.append("    @staticmethod")
        out.append(line)
    return "\n".join(out)


def classeval_program(code: str, imports: list[str], test: str) -> str:
    """Programma eseguito: import della classe + generazione + ``"\\n"`` + test.

    Come ``gen_code_list`` e ``gen_py_file`` di ``test_pipeline.py``; l'estrazione del codice è
    quella unica del benchmark (D4), non ``extract_code`` di ClassEval.
    """
    return "\n".join(imports) + "\n" + add_static_statement(code) + "\n" + test


@EXECUTORS.register("classeval")
class ClassEvalExecutor(Executor):
    """ClassEval (L3): la classe con i test ufficiali, ogni classe di test entro 5 s.

    Usa la modalità ``program`` del runner come ``codenet``: ``main.py`` (regola
    ``syntax_python``), ``py_compile`` (``SYNTAX_ERROR``), poi ``classeval_check.py`` (file
    accessorio) importa il modulo ed esegue le classi di test: PASSED solo se passano tutte (il
    ``success`` di ClassEval). Le dipendenze dei test sono nell'immagine ``sandbox_l3``.

    Args:
        tests: ``task_id`` → ``test``, ``test_classes``, ``import_statement`` (default: dal loader
            di ClassEval).
    """

    name: ClassVar[str] = "classeval"

    def __init__(
        self,
        config: ExperimentConfig,
        sandbox: Sandbox,
        language: Language,
        workdir_root: Path,
        tests: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(config, sandbox, language, workdir_root)
        if language is not Language.PYTHON:
            raise ConfigError("classeval executor is Python only")
        if tests is None:
            from bench.data.loaders.classeval import ClassEvalLoader

            tests = ClassEvalLoader(config.datasets["classeval"]).tests()
        self.tests = tests

    def test_for(self, problem: Problem) -> dict[str, Any]:
        """Test, classi di test e import della classe."""
        if problem.test_ref not in self.tests:
            raise ConfigError(f"no ClassEval test for {problem.test_ref}")
        return self.tests[problem.test_ref]

    def canonical(self, problem: Problem) -> str:
        return problem.canonical_solution or ""

    def class_timeout_s(self, problem: Problem) -> float:
        """Tempo dell'esecuzione: 5 s per classe di test più l'avvio."""
        return CLASSEVAL_CLASS_TIMEOUT_S * len(self.test_for(problem)["test_classes"]) + 10.0

    def budget_s(self, n_samples: int) -> float:
        most = max((len(t["test_classes"]) for t in self.tests.values()), default=1)
        per_sample = self.exec_cfg.compile_timeout_s + CLASSEVAL_CLASS_TIMEOUT_S * most + 10
        return n_samples * (per_sample + 5) + INVOCATION_MARGIN_S

    def job(
        self, problem: Problem, samples: list[SampleToRun]
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        entry = self.test_for(problem)
        job = self._base_job("program")
        job.update(
            {
                "rule": "syntax_python",
                "timeout_s": self.class_timeout_s(problem),
                "compile_cmd": CODENET_COMPILE[Language.PYTHON],
                "run_cmd": [
                    "python3",
                    "../classeval_check.py",
                    "main",
                    ",".join(entry["test_classes"]),
                    str(CLASSEVAL_CLASS_TIMEOUT_S),
                ],
                "samples": [
                    {
                        "id": str(i),
                        "program": classeval_program(
                            s.code, entry["import_statement"], entry["test"]
                        ),
                    }
                    for i, s in enumerate(samples)
                ],
            }
        )
        return job, {"classeval_check.py": CLASSEVAL_CHECK.read_bytes()}


def executor_name(dataset: str) -> str:
    """Executor di un dataset (SPEC §11.3)."""
    names = {
        "humanevalplus": "evalplus",
        "mbppplus": "evalplus",
        "humanevalpack": "humanevalpack",
        "codenet": "codenet",
        "classeval": "classeval",
        "codesearchnet": "syntax",
        "thestack_cpp": "syntax",
    }
    if dataset not in names:
        raise ConfigError(f"no executor for dataset '{dataset}' (yet)")
    return names[dataset]
