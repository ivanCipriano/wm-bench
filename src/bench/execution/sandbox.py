"""Sandbox per eseguire codice generato (SPEC §11.2, ADR-001).

- ``ApptainerSandbox``: ``apptainer exec`` sull'immagine in formato **directory** derivata
  dal ``.sif`` (sul cluster manca ``squashfuse``: un ``exec`` sul ``.sif`` lo convertirebbe
  ogni volta in una sandbox temporanea, cluster_info §5), senza rete, senza home, con
  l'ambiente ripulito e la sola cartella di lavoro montata in scrittura.
- ``LocalSandbox``: sottoprocesso senza container, **solo per i test** sul PC.

Entrambe eseguono il runner del progetto (``containers/runner/wmb_runner.py``) su un
``job.json`` scritto nella cartella di lavoro.

**Disco locale del nodo** (diagnosi del 4 ottobre 2026, ADR-001): con l'immagine o la cartella
di lavoro su beegfs, ``apptainer exec`` si blocca a intermittenza (anche ``true``) e l'avvio di
Python dentro il container richiede decine di secondi. Per questo, sui nodi di calcolo,
l'immagine viene estratta dal ``.sif`` (dopo averne verificato l'hash) sul disco locale del
nodo (``$TMPDIR`` o ``/tmp``) una volta per nodo, e le cartelle di lavoro stanno lì.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
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

logger = logging.getLogger(__name__)

RUNNER_IN_IMAGE = "/opt/wmb/wmb_runner.py"
WORK_MOUNT = "/work"
KILL_AFTER_S = 5
READY_MARKER = ".wmb_ready"
EXTRACT_TIMEOUT_S = 1800


def node_local_root() -> Path:
    """Disco locale del nodo: ``$WMB_NODE_TMP``, poi ``$TMPDIR``, poi ``/tmp``."""
    return Path(os.environ.get("WMB_NODE_TMP") or os.environ.get("TMPDIR") or "/tmp")


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
    def work_path(self, workdir: Path, name: str) -> str:
        """Percorso di un file di ``workdir`` come lo vede il runner."""

    def workdir_root(self, default: Path) -> Path:
        """Cartella delle cartelle di lavoro (``default`` se la sandbox non ha preferenze)."""
        return default

    @abstractmethod
    def run(
        self, cmd: list[str], workdir: Path, timeout_s: float, mem_mb: int | None = None
    ) -> SandboxResult:
        """Esegue ``cmd`` con ``workdir`` come cartella di lavoro.

        Args:
            cmd: comando (percorsi come li vede la sandbox).
            workdir: cartella dell'host montata in scrittura.
            timeout_s: tempo massimo dell'intera invocazione.
            mem_mb: memoria virtuale massima dell'invocazione (``None`` = nessun limite).
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        self._image: Path | None = None

    def check(self) -> None:
        """Verifica Apptainer e immagine; sul disco locale estrae l'immagine se serve.

        Raises:
            SandboxError: con l'indicazione di cosa fare.
        """
        if shutil.which(self.apptainer) is None:
            raise SandboxError(
                f"{self.apptainer} not in PATH: run 'module load {self.config.apptainer_module}' "
                "(cluster_info §5)"
            )
        _ = self.image_hash
        image = self.local_image() if self.config.node_local else self.config.image_dir
        if not (image / "opt" / "wmb" / "wmb_runner.py").is_file():
            raise SandboxError(
                f"sandbox image directory {image} missing or incomplete: "
                "run scripts/build_sandbox.sh on the login node"
            )
        self._image = image

    @property
    def image_dir(self) -> Path:
        """Directory dell'immagine usata da ``exec`` (dopo ``check``)."""
        return self._image or self.config.image_dir

    @property
    def image_hash(self) -> str:
        if self._hash is None:
            sha_file = Path(f"{self.config.image_sif}.sha256")
            if not sha_file.is_file():
                raise SandboxError(f"{sha_file} not found: run scripts/build_sandbox.sh")
            self._hash = sha_file.read_text(encoding="utf-8").split()[0]
        return self._hash

    def local_image(self) -> Path:
        """Immagine estratta dal ``.sif`` sul disco locale del nodo (una volta per nodo e utente).

        Più job sullo stesso nodo si coordinano con un lock; l'estrazione avviene in una cartella
        temporanea rinominata solo a lavoro finito, con un marcatore che contiene l'hash.

        Raises:
            SandboxError: se l'hash del ``.sif`` non coincide o l'estrazione fallisce.
        """
        root = node_local_root()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"wmb_sandbox_{getpass.getuser()}_{self.image_hash[:16]}"
        marker = target / READY_MARKER
        if marker.is_file():
            return target
        with open(root / f"{target.name}.lock", "w") as lock:
            if sys.platform != "win32":  # su Windows (solo test) niente lock
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX)
            if marker.is_file():
                return target
            sif = self.config.image_sif
            if not sif.is_file():
                raise SandboxError(f"{sif} not found: run scripts/build_sandbox.sh")
            if _sha256(sif) != self.image_hash:
                raise SandboxError(f"{sif} does not match {sif}.sha256: rebuild the image")
            partial = root / f"{target.name}.partial-{os.getpid()}"
            shutil.rmtree(partial, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)
            logger.info("extracting the sandbox image to %s (once per node)", target)
            start = time.perf_counter()
            proc = subprocess.run(
                [self.apptainer, "build", "--sandbox", str(partial), str(sif)],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=EXTRACT_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                shutil.rmtree(partial, ignore_errors=True)
                raise SandboxError(f"cannot extract {sif}: {(proc.stderr or proc.stdout)[-2000:]}")
            partial.rename(target)
            marker.write_text(self.image_hash + "\n", encoding="utf-8")
            logger.info("sandbox image extracted in %.0fs", time.perf_counter() - start)
        return target

    def workdir_root(self, default: Path) -> Path:
        if not self.config.node_local:
            return default
        return node_local_root() / f"wmb_work_{getpass.getuser()}"

    def runner_command(self, workdir: Path) -> list[str]:
        return ["python3", RUNNER_IN_IMAGE, f"{WORK_MOUNT}/job.json"]

    def work_path(self, workdir: Path, name: str) -> str:
        return f"{WORK_MOUNT}/{name}"

    def command(
        self, cmd: list[str], workdir: Path, timeout_s: float, mem_mb: int | None = None
    ) -> list[str]:
        """Riga di comando completa di ``apptainer exec`` (SPEC §11.2).

        Con ``--containall`` Apptainer non monta i bind path di ``apptainer.conf`` né i file
        system dell'host (verificato con ``--debug`` sul cluster): il container vede solo
        l'immagine e ``/work``.
        """
        full = [
            self.apptainer,
            "exec",
            "--containall",
            "--cleanenv",
            "--no-home",
            "--pwd",
            WORK_MOUNT,
            "--bind",
            f"{workdir}:{WORK_MOUNT}:rw",
        ]
        if self.config.network_none:
            full += ["--net", "--network", "none"]
        full += [str(self.image_dir), "timeout", f"--kill-after={KILL_AFTER_S}"]
        full.append(f"{timeout_s:g}")
        if mem_mb is not None:
            full += ["prlimit", f"--as={mem_mb * 1024 * 1024}", "--"]
        return full + cmd

    def run(
        self, cmd: list[str], workdir: Path, timeout_s: float, mem_mb: int | None = None
    ) -> SandboxResult:
        return _run(self.command(cmd, workdir, timeout_s, mem_mb), None, timeout_s, None)


class LocalSandbox(Sandbox):
    """Sottoprocesso senza isolamento: **solo per i test** (nessun container sul PC)."""

    def __init__(self, runner: Path | None = None) -> None:
        self.runner = runner or repo_root() / "containers" / "runner" / "wmb_runner.py"

    @property
    def image_hash(self) -> str:
        return "local"

    def runner_command(self, workdir: Path) -> list[str]:
        return [sys.executable, str(self.runner), str(workdir / "job.json")]

    def work_path(self, workdir: Path, name: str) -> str:
        return str(workdir / name)

    def run(
        self, cmd: list[str], workdir: Path, timeout_s: float, mem_mb: int | None = None
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
