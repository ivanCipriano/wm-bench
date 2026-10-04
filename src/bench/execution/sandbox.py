"""Sandbox per eseguire codice generato (SPEC §11.2, ADR-001).

- ``ApptainerSandbox``: ``apptainer exec`` sull'immagine in formato **directory** derivata
  dal ``.sif`` (sul cluster manca ``squashfuse``: un ``exec`` sul ``.sif`` lo convertirebbe
  ogni volta in una sandbox temporanea, cluster_info §5), senza rete, senza home, con
  l'ambiente ripulito e la sola cartella di lavoro montata in scrittura.
- ``LocalSandbox``: sottoprocesso senza container, **solo per i test** sul PC.

Entrambe eseguono il runner del progetto (``containers/runner/wmb_runner.py``) su un
``job.json`` scritto nella cartella di lavoro.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from bench.config.builder import repo_root
from bench.config.schema import ExecutionConfig
from bench.domain.errors import SandboxError

RUNNER_IN_IMAGE = "/opt/wmb/wmb_runner.py"
WORK_MOUNT = "/work"
DATA_MOUNT = "/data"
KILL_AFTER_S = 5


@dataclass(frozen=True)
class SandboxResult:
    """Esito di un'invocazione della sandbox (non del codice: quello è nel ``result.json``)."""

    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


class Sandbox(ABC):
    """Esecuzione isolata del runner."""

    @property
    @abstractmethod
    def image_hash(self) -> str:
        """SHA-256 dell'immagine (``.sif``) registrato in ogni ``ExecutionRecord``."""

    @abstractmethod
    def runner_command(self, workdir: Path) -> list[str]:
        """Comando che esegue il runner sul ``job.json`` di ``workdir``."""

    @abstractmethod
    def data_path(self, host_path: Path) -> str:
        """Percorso di un file dei dataset come lo vede il runner."""

    @abstractmethod
    def run(
        self,
        cmd: list[str],
        workdir: Path,
        timeout_s: float,
        mem_mb: int | None = None,
        data_dir: Path | None = None,
    ) -> SandboxResult:
        """Esegue ``cmd`` con ``workdir`` come cartella di lavoro.

        Args:
            cmd: comando (percorsi come li vede la sandbox).
            workdir: cartella dell'host montata in scrittura.
            timeout_s: tempo massimo dell'intera invocazione.
            mem_mb: memoria virtuale massima dell'invocazione (``None`` = nessun limite).
            data_dir: cartella dei dataset da montare in sola lettura (facoltativa).
        """


def _run(
    cmd: list[str], cwd: Path | None, timeout_s: float, env: dict[str, str] | None
) -> SandboxResult:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            stdin=subprocess.DEVNULL,  # mai lo stdin del job (srun lo inoltra)
            text=True,
            errors="replace",
            timeout=timeout_s + KILL_AFTER_S + 5,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SandboxError(f"cannot run {cmd[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return SandboxResult(124, out, err, time.perf_counter() - start, True)
    # 124 = codice di uscita di ``timeout`` allo scadere del tempo.
    timed_out = proc.returncode in (124, 137)
    return SandboxResult(
        proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - start, timed_out
    )


class ApptainerSandbox(Sandbox):
    """``apptainer exec`` sull'immagine in formato directory.

    Args:
        config: configurazione dell'esecuzione.
        apptainer: eseguibile (``apptainer`` nel PATH dopo ``module load``).
    """

    def __init__(self, config: ExecutionConfig, apptainer: str = "apptainer") -> None:
        self.config = config
        self.apptainer = apptainer
        self._hash: str | None = None

    def check(self) -> None:
        """Verifica che Apptainer e l'immagine siano disponibili.

        Raises:
            SandboxError: con l'indicazione di cosa fare.
        """
        if shutil.which(self.apptainer) is None:
            raise SandboxError(
                f"{self.apptainer} not in PATH: run 'module load {self.config.apptainer_module}' "
                "(cluster_info §5)"
            )
        if not (self.config.image_dir / "opt" / "wmb" / "wmb_runner.py").is_file():
            raise SandboxError(
                f"sandbox image directory {self.config.image_dir} missing or incomplete: "
                "run scripts/build_sandbox.sh on the login node"
            )
        _ = self.image_hash

    @property
    def image_hash(self) -> str:
        if self._hash is None:
            sha_file = Path(f"{self.config.image_sif}.sha256")
            if not sha_file.is_file():
                raise SandboxError(f"{sha_file} not found: run scripts/build_sandbox.sh")
            self._hash = sha_file.read_text(encoding="utf-8").split()[0]
        return self._hash

    def runner_command(self, workdir: Path) -> list[str]:
        return ["python3", RUNNER_IN_IMAGE, f"{WORK_MOUNT}/job.json"]

    def data_path(self, host_path: Path) -> str:
        return f"{DATA_MOUNT}/{host_path.name}"

    def command(
        self,
        cmd: list[str],
        workdir: Path,
        timeout_s: float,
        mem_mb: int | None = None,
        data_dir: Path | None = None,
    ) -> list[str]:
        """Riga di comando completa di ``apptainer exec`` (SPEC §11.2)."""
        full = [
            self.apptainer,
            "exec",
            "--containall",
            "--cleanenv",
            "--no-home",
            # Niente bind path di apptainer.conf (es. /mnt/beegfs): solo /work e /data.
            "--no-mount",
            "bind-paths",
            "--pwd",
            WORK_MOUNT,
            "--bind",
            f"{workdir}:{WORK_MOUNT}:rw",
        ]
        if data_dir is not None:
            full += ["--bind", f"{data_dir}:{DATA_MOUNT}:ro"]
        if self.config.network_none:
            full += ["--net", "--network", "none"]
        full += [str(self.config.image_dir), "timeout", f"--kill-after={KILL_AFTER_S}"]
        full.append(f"{timeout_s:g}")
        if mem_mb is not None:
            full += ["prlimit", f"--as={mem_mb * 1024 * 1024}", "--"]
        return full + cmd

    def run(
        self,
        cmd: list[str],
        workdir: Path,
        timeout_s: float,
        mem_mb: int | None = None,
        data_dir: Path | None = None,
    ) -> SandboxResult:
        return _run(self.command(cmd, workdir, timeout_s, mem_mb, data_dir), None, timeout_s, None)


class LocalSandbox(Sandbox):
    """Sottoprocesso senza isolamento: **solo per i test** (nessun container sul PC)."""

    def __init__(self, runner: Path | None = None) -> None:
        self.runner = runner or repo_root() / "containers" / "runner" / "wmb_runner.py"

    @property
    def image_hash(self) -> str:
        return "local"

    def runner_command(self, workdir: Path) -> list[str]:
        return [sys.executable, str(self.runner), str(workdir / "job.json")]

    def data_path(self, host_path: Path) -> str:
        return str(host_path)

    def run(
        self,
        cmd: list[str],
        workdir: Path,
        timeout_s: float,
        mem_mb: int | None = None,
        data_dir: Path | None = None,
    ) -> SandboxResult:
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        return _run(cmd, workdir, timeout_s, env)


def make_sandbox(config: ExecutionConfig) -> Sandbox:
    """Sandbox indicata dalla configurazione (``apptainer`` sul cluster)."""
    if config.sandbox == "local":
        return LocalSandbox()
    sandbox = ApptainerSandbox(config)
    sandbox.check()
    return sandbox
