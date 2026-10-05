"""Client dei worker dei metodi (SPEC §7.3, §8.3; ADR-008).

Lancia ``<python dell'ambiente> -m bench_shims.<metodo> --request <run_dir>/request.json``
con ``PYTHONPATH`` = ``shims/`` + la copia patchata del metodo, un ambiente ripulito e i
log in ``log_path``. Alla scadenza del tempo (o se il processo muore) lo rilancia **una
volta**: la ripresa del runner evita di rifare il lavoro già scritto. Poi verifica I1: i
risultati mancanti diventano ``FAILED`` con ``error="missing_result"``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult, iter_jsonl, write_jsonl
from bench_contracts.enums import DetectStatus, EmbedStatus

from bench.domain.errors import WorkerError

logger = logging.getLogger(__name__)

EXIT_SETUP = 2
EXIT_VERSION = 3
LOG_TAIL_LINES = 50
MISSING_RESULT = "missing_result"

# Variabili dell'ambiente del chiamante che passano al worker (le altre no: niente PYTHONPATH
# o variabili di bench-core). SOURCERY_TOKEN solo per ACW (SPEC §8.1), mai su disco.
PASSTHROUGH = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "LD_LIBRARY_PATH",
    "CUDA_VISIBLE_DEVICES",
    "SYSTEMROOT",  # Windows (solo test locali)
    "TEMP",
    "TMP",
)
FIXED_ENV = {
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",  # log leggibili qualunque sia la locale del nodo
}


@dataclass(frozen=True)
class MethodSource:
    """Codice del metodo: cartella da mettere nel ``PYTHONPATH`` e radice della copia patchata.

    ``root`` è ``build/patched/<metodo>``: accanto ci sono ``<metodo>.source_commit`` e
    ``<metodo>.patches.sha256``, letti dall'introspezione del worker.
    """

    pythonpath: Path
    root: Path


@dataclass
class WorkerRun:
    """Esito di un'invocazione: risultati (uno per risultato atteso) e diagnostica."""

    results: list[WorkerResult]
    missing: int = 0
    attempts: int = 0
    log_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def expected_results(request: WorkerRequest, item: WorkerItem) -> int:
    """Risultati attesi per item (SPEC §8.3): ``n`` per embed da prompt, altrimenti uno."""
    if request.op == "embed" and item.prompt_messages is not None:
        return int(item.n)
    return 1


class WorkerClient:
    """Invocazione dei worker (SPEC §8.3).

    Args:
        envs: nome dell'ambiente → interprete Python.
        shims_root: cartella ``shims/`` (contiene ``bench_shims``).
        timeout_s: tempo massimo di un tentativo.
        env: variabili da aggiungere (es. ``HF_HOME``); default: ``os.environ`` filtrato.
        on_missing: callback per i risultati mancanti (es. ``InvariantObserver``).
        extra_pythonpath: cartelle in più nel ``PYTHONPATH`` (solo test: ``bench_contracts``
            non installato).
    """

    def __init__(
        self,
        envs: Mapping[str, Path],
        shims_root: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        on_missing: Callable[[str, int], None] | None = None,
        extra_pythonpath: Sequence[Path] = (),
    ) -> None:
        self.envs = {k: Path(v) for k, v in envs.items()}
        self.shims_root = shims_root
        self.timeout_s = timeout_s
        self.extra_env = dict(env or {})
        self.on_missing = on_missing
        self.extra_pythonpath = [Path(p) for p in extra_pythonpath]

    # ------------------------------------------------------------------ ambiente
    def environment(
        self, source: MethodSource | None, secrets: Sequence[str] = ()
    ) -> dict[str, str]:
        """Ambiente ripulito del worker con ``PYTHONPATH`` = shims + copia patchata."""
        env = {k: os.environ[k] for k in PASSTHROUGH if k in os.environ}
        for key in ("HF_HOME", "HF_HUB_CACHE"):
            if key in os.environ:
                env[key] = os.environ[key]
        env.update(FIXED_ENV)
        env.update(self.extra_env)
        paths = [str(self.shims_root)]
        if source is not None:
            paths.append(str(source.pythonpath))
            env["WMB_METHOD_SOURCE"] = str(source.root)
        paths.extend(str(p) for p in self.extra_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(paths)
        for name in secrets:  # solo dall'ambiente dell'utente (SPEC §8.1)
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def _python(self, env_name: str) -> Path:
        if env_name not in self.envs:
            raise WorkerError(f"unknown environment '{env_name}'")
        return self.envs[env_name]

    # ------------------------------------------------------------------ introspezione
    def introspect(
        self, env_name: str, method: str, source: MethodSource | None = None, timeout_s: float = 600
    ) -> dict[str, Any]:
        """Versioni dell'ambiente del worker (``--introspect``), per il manifest."""
        proc = subprocess.run(
            [str(self._python(env_name)), "-m", f"bench_shims.{method}", "--introspect"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=self.environment(source),
            check=False,
        )
        if proc.returncode != 0:
            raise WorkerError(f"{method} --introspect failed: {proc.stderr.strip()[-2000:]}")
        return dict(json.loads(proc.stdout.strip().splitlines()[-1]))

    # ------------------------------------------------------------------ esecuzione
    def run(
        self,
        env_name: str,
        method: str,
        request: WorkerRequest,
        items: Sequence[WorkerItem],
        source: MethodSource | None = None,
        secrets: Sequence[str] = (),
    ) -> WorkerRun:
        """Esegue il worker su tutti gli item e restituisce i risultati verificati (I1)."""
        items_path = Path(request.items_path)
        items_path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(items_path, [it.to_dict() for it in items])
        request_path = items_path.parent / "request.json"
        request_path.write_text(
            json.dumps(request.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        cmd = [
            str(self._python(env_name)),
            "-m",
            f"bench_shims.{method}",
            "--request",
            str(request_path),
        ]
        env = self.environment(source, secrets)
        log_path = Path(request.log_path)
        attempts = 0
        for attempt in (1, 2):
            attempts = attempt
            code = self._launch(cmd, env, log_path, items_path.parent)
            if code == 0:
                break
            if code in (EXIT_SETUP, EXIT_VERSION):
                raise WorkerError(
                    f"{method} worker failed with exit code {code}:\n{self._tail(log_path)}"
                )
            if attempt == 1:
                logger.warning(
                    "%s worker attempt 1 ended with %s: relaunching once (resume)", method, code
                )
            else:
                raise WorkerError(
                    f"{method} worker failed twice (last exit {code}):\n{self._tail(log_path)}"
                )
        run = self.collect(request, items)
        run.attempts, run.log_path = attempts, log_path
        if run.missing and self.on_missing is not None:
            self.on_missing(method, run.missing)
        return run

    def _launch(self, cmd: list[str], env: dict[str, str], log_path: Path, cwd: Path) -> int | str:
        """Un tentativo: codice di uscita, oppure ``"timeout"``."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
            log.flush()
            kwargs: dict[str, Any] = {}
            if os.name == "posix":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=cwd,
                **kwargs,
            )
            try:
                return proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                log.write(f"\n===== timeout after {self.timeout_s:.0f}s: worker killed\n")
                return "timeout"

    @staticmethod
    def _kill(proc: subprocess.Popen[Any]) -> None:
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:  # solo test locali
            proc.kill()
        proc.wait()

    @staticmethod
    def _tail(log_path: Path) -> str:
        if not log_path.is_file():
            return "(no log)"
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-LOG_TAIL_LINES:])

    # ------------------------------------------------------------------ I1
    def collect(self, request: WorkerRequest, items: Sequence[WorkerItem]) -> WorkerRun:
        """Risultati in ordine di item (e di ``sample_index``), con i mancanti ``FAILED`` (I1)."""
        by_item: dict[str, dict[Any, WorkerResult]] = {}
        output = Path(request.output_path)
        if output.is_file():
            for row in iter_jsonl(output):
                result = WorkerResult.from_dict(row)
                # In caso di righe ripetute vale l'ultima.
                by_item.setdefault(result.item_id, {})[result.sample_index] = result
        status = DetectStatus.FAILED if request.op == "detect" else EmbedStatus.FAILED
        results: list[WorkerResult] = []
        missing = 0
        for item in items:
            expected = expected_results(request, item)
            got = by_item.get(item.item_id, {})
            per_prompt = request.op == "embed" and item.prompt_messages is not None
            keys: list[Any] = list(range(expected)) if per_prompt else [None]
            for key in keys:
                if key in got:
                    results.append(got[key])
                else:
                    missing += 1
                    results.append(
                        WorkerResult(
                            item_id=item.item_id,
                            sample_index=key,
                            status=status,
                            raw_output=None,
                            code=None,
                            score=None,
                            native_decision=None,
                            decoded_message=None,
                            error=MISSING_RESULT,
                        )
                    )
        if missing:
            logger.warning("%d missing worker result(s) filled as FAILED (I1)", missing)
        return WorkerRun(results=results, missing=missing)
