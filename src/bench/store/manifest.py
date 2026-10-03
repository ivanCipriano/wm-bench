"""Manifest degli artefatti e raccolta della provenienza (SPEC §7.11).

Accanto a ogni file di dati c'è ``<file>.manifest.json``. Un file senza manifest con
``status=complete`` è considerato inesistente (SPEC §15.2).
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import platform
import socket
import subprocess
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bench.store.hashing import sha256_bytes

logger = logging.getLogger(__name__)

MANIFEST_VERSION = "1"
ManifestStatus = Literal["running", "complete", "failed"]
_STATUS_MIN_FIELDS = 2  # "<sha> <path> [(describe)]" in git submodule status
# Cartelle versionate che non sono codice: modificarle non rende "sporco" il repository
# (i log delle milestone vengono scritti con tee mentre i job girano).
NON_CODE_PREFIXES = ("docs/milestone_logs/",)
_PORCELAIN_PREFIX = 3  # "XY " prima del percorso


def dirty_paths(porcelain: str) -> list[str]:
    """File modificati da ``git status --porcelain``, esclusi quelli in ``NON_CODE_PREFIXES``."""
    paths = []
    for line in porcelain.splitlines():
        if len(line) <= _PORCELAIN_PREFIX:
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if not path.startswith(NON_CODE_PREFIXES):
            paths.append(path)
    return sorted(paths)


class Provenance(BaseModel):
    """Provenienza dell'esecuzione: codice, ambiente e nodo."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    repo_commit: str | None
    repo_dirty: bool | None
    repo_dirty_paths: list[str] = Field(default_factory=list)
    submodules: dict[str, str] = Field(default_factory=dict)
    patches: dict[str, dict[str, str]] = Field(default_factory=dict)
    patched_source_commits: dict[str, str] = Field(default_factory=dict)
    core_python: str
    core_packages: list[str] = Field(default_factory=list)
    core_packages_sha256: str
    parser_versions: dict[str, str] = Field(default_factory=dict)
    slurm_job_id: str | None
    node: str
    gpu: str | None


class Manifest(BaseModel):
    """Manifest di un artefatto."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    manifest_version: str = MANIFEST_VERSION
    kind: str
    stage: str
    cell: dict[str, str | None]
    config: dict[str, Any]
    config_sha256: str
    config_hash: str | None = None
    global_seed: int
    provenance: Provenance
    worker_env: dict[str, Any] | None = None
    worker_packages_sha256: str | None = None
    sandbox_image_hash: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(
        default_factory=dict
    )  # diagnostica della fase (es. campionamento)
    started_at: datetime
    finished_at: datetime | None = None
    n_rows_in: int | None = None
    n_rows_out: int | None = None
    n_rows_expected: int | None = None
    data_sha256: str | None = None
    data_size: int | None = None
    status: ManifestStatus = "running"


# Pacchetti da cui dipendono i conteggi di righe e nodi (ADR-005): registrati esplicitamente.
PARSER_PACKAGES = (
    "tree-sitter",
    "tree-sitter-python",
    "tree-sitter-java",
    "tree-sitter-cpp",
    "tree-sitter-javascript",
)


def parser_versions() -> dict[str, str]:
    """Versioni installate di tree-sitter e delle 4 grammatiche (``"missing"`` se assenti)."""
    versions: dict[str, str] = {}
    for name in PARSER_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


class ProvenanceCollector:
    """Raccoglie la provenienza una sola volta per processo.

    Args:
        repo_root: radice del repository Git del progetto.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def _git(self, *args: str, cwd: Path | None = None) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd or self.repo_root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("git %s failed: %s", " ".join(args), exc)
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _submodules(self) -> dict[str, str]:
        out = self._git("submodule", "status")
        result: dict[str, str] = {}
        for line in (out or "").splitlines():
            parts = line[1:].split()
            if len(parts) >= _STATUS_MIN_FIELDS:
                # Il primo carattere è lo stato: ' ' allineato, '+' commit diverso, '-' assente.
                result[parts[1]] = parts[0] if line[0] == " " else f"{line[0]}{parts[0]}"
        return result

    def _patches(self) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        patched = self.repo_root / "build" / "patched"
        hashes: dict[str, dict[str, str]] = {}
        commits: dict[str, str] = {}
        if not patched.is_dir():
            return hashes, commits
        for sha_file in sorted(patched.glob("*.patches.sha256")):
            method = sha_file.name.removesuffix(".patches.sha256")
            entries: dict[str, str] = {}
            for line in sha_file.read_text(encoding="utf-8").splitlines():
                digest, _, name = line.partition(" ")
                if digest:
                    entries[name.strip().lstrip("*")] = digest
            hashes[method] = entries
        for commit_file in sorted(patched.glob("*.source_commit")):
            method = commit_file.name.removesuffix(".source_commit")
            commits[method] = commit_file.read_text(encoding="utf-8").strip()
        return hashes, commits

    @staticmethod
    def _packages() -> list[str]:
        names = {
            f"{dist.metadata['Name']}=={dist.version}"
            for dist in importlib.metadata.distributions()
            if dist.metadata["Name"]
        }
        return sorted(names, key=str.lower)

    @cached_property
    def provenance(self) -> Provenance:
        """Provenienza del processo corrente (calcolata alla prima richiesta)."""
        head = self._git("rev-parse", "HEAD")
        status = self._git("status", "--porcelain", "--untracked-files=no")
        patches, patched_commits = self._patches()
        packages = self._packages()
        return Provenance(
            repo_commit=head.strip() if head else None,
            repo_dirty=None if status is None else bool(dirty_paths(status)),
            repo_dirty_paths=[] if status is None else dirty_paths(status),
            submodules=self._submodules(),
            patches=patches,
            patched_source_commits=patched_commits,
            core_python=platform.python_version(),
            core_packages=packages,
            core_packages_sha256=sha256_bytes("\n".join(packages).encode("utf-8")),
            parser_versions=parser_versions(),
            slurm_job_id=os.environ.get("SLURM_JOB_ID"),
            node=os.environ.get("SLURMD_NODENAME") or socket.gethostname(),
            gpu=os.environ.get("CUDA_VISIBLE_DEVICES"),
        )
