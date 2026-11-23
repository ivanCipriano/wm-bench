"""Test della raccolta della provenienza per i manifest (SPEC §7.11)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bench.store.manifest import GIT_READ_ONLY, ProvenanceCollector


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_clean_repo_provenance(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    prov = ProvenanceCollector(git_repo).provenance
    assert prov.repo_commit is not None and len(prov.repo_commit) == 40
    assert prov.repo_dirty is False
    assert prov.slurm_job_id == "12345"
    assert prov.gpu == "0"
    assert any(p.lower().startswith("pydantic==") for p in prov.core_packages)
    assert len(prov.core_packages_sha256) == 64


def test_dirty_repo_and_patch_hashes(git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("changed", encoding="utf-8")
    patched = git_repo / "build" / "patched"
    patched.mkdir(parents=True)
    (patched / "stone.patches.sha256").write_text(
        "abc123  patches/stone/0000-x.patch\n", encoding="utf-8"
    )
    (patched / "stone.source_commit").write_text("deadbeef\n", encoding="utf-8")
    prov = ProvenanceCollector(git_repo).provenance
    assert prov.repo_dirty is True
    assert prov.patches == {"stone": {"patches/stone/0000-x.patch": "abc123"}}
    assert prov.patched_source_commits == {"stone": "deadbeef"}


def test_not_a_repository(tmp_path: Path) -> None:
    prov = ProvenanceCollector(tmp_path).provenance
    assert prov.repo_commit is None
    assert prov.submodules == {}


def test_provenance_is_cached(git_repo: Path) -> None:
    collector = ProvenanceCollector(git_repo)
    assert collector.provenance is collector.provenance


def test_dirty_paths_ignore_milestone_logs() -> None:
    from bench.store.manifest import dirty_paths

    porcelain = " M docs/milestone_logs/M3_cluster.txt\n M src/bench/cli.py\nR  a.py -> b.py\n"
    assert dirty_paths(porcelain) == ["b.py", "src/bench/cli.py"]
    assert dirty_paths(" M docs/milestone_logs/M4_cluster.txt\n") == []


def _make_index_stale(repo: Path) -> bytes:
    """Cambia il mtime di un file tracciato senza cambiarne il contenuto: il prossimo ``git status``
    con i lock opzionali riscrive l'indice per aggiornare le informazioni di stat."""
    st = (repo / "a.txt").stat()
    os.utime(repo / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    return (repo / ".git" / "index").read_bytes()


def test_plain_git_status_rewrites_the_index(git_repo: Path) -> None:
    """Premessa del test sotto: senza ``--no-optional-locks`` ``git status`` scrive l'indice."""
    before = _make_index_stale(git_repo)
    _git(git_repo, "status", "--porcelain", "--untracked-files=no")
    assert (git_repo / ".git" / "index").read_bytes() != before


def test_provenance_never_writes_the_index(git_repo: Path) -> None:
    """La provenienza non scrive ``.git/index`` (né prende ``.git/index.lock``): un job ucciso
    durante ``git status`` non lascia il lock che fa fallire il commit dei log."""
    before = _make_index_stale(git_repo)
    prov = ProvenanceCollector(git_repo).provenance
    assert prov.repo_dirty is False
    assert (git_repo / ".git" / "index").read_bytes() == before
    assert not (git_repo / ".git" / "index.lock").exists()


def test_provenance_works_with_a_stale_lock(git_repo: Path) -> None:
    """Con un ``index.lock`` lasciato da un altro processo la provenienza si legge lo stesso."""
    (git_repo / ".git" / "index.lock").write_bytes(b"")
    prov = ProvenanceCollector(git_repo).provenance
    assert prov.repo_commit is not None and prov.repo_dirty is False


def test_every_provenance_git_call_is_read_only(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(cmd: list[str], *args: object, **kwargs: object) -> object:
        calls.append(list(cmd))
        return real(cmd, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", spy)
    ProvenanceCollector(git_repo).provenance  # noqa: B018
    git_calls = [c for c in calls if c and c[0] == "git"]
    assert git_calls and all(tuple(c[:2]) == GIT_READ_ONLY for c in git_calls)
