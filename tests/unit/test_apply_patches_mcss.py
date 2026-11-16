"""``scripts/apply_patches.sh mcss``: ``mcss`` non ha una copia sua e rifà quella di ``multimark``
(``configs/method/mcss.yaml``: ``patched_dir: build/patched/multimark``).

Lo script gira su una copia in un repository temporaneo con un finto ``third_party/multimark``
(un repository git con un commit), così ``build/patched`` del benchmark non si tocca.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

BASH = shutil.which("bash")
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(BASH is None or GIT is None, reason="bash and git required")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run([str(GIT), "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    script = (REPO_ROOT / "scripts" / "apply_patches.sh").read_bytes().replace(b"\r\n", b"\n")
    (root / "scripts" / "apply_patches.sh").write_bytes(script)
    sub = root / "third_party" / "multimark"
    (sub / "multimark").mkdir(parents=True)
    (sub / "multimark" / "__init__.py").write_text("__version__ = 'x'\n", encoding="utf-8")
    _git(sub, "init", "-q")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-q", "-m", "init")
    return root


def _run(repo: Path, *methods: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(BASH), "scripts/apply_patches.sh", *methods], cwd=repo,
                          capture_output=True, text=True, check=False)  # fmt: skip


def test_mcss_builds_the_multimark_copy(repo: Path) -> None:
    proc = _run(repo, "mcss", "multimark")
    assert proc.returncode == 0, proc.stderr
    patched = repo / "build" / "patched"
    assert (patched / "multimark" / "multimark" / "__init__.py").is_file()
    assert (patched / "multimark.source_commit").is_file()
    assert (patched / "multimark.patches.sha256").is_file()
    assert not (patched / "mcss").exists() and not (patched / "mcss.source_commit").exists()
    assert "mcss uses the copy of multimark" in proc.stderr
    assert proc.stdout.count("[OK] multimark") == 1  # una sola copia anche con i due nomi


def test_unknown_method_is_refused(repo: Path) -> None:
    proc = _run(repo, "nope")
    assert proc.returncode == 2 and "known:" in proc.stderr and "mcss" in proc.stderr
