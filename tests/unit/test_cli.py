"""Test end-to-end della CLI ``bench`` in un sottoprocesso (Hydra reale)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT


def _bench(args: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["WMB_LOCAL_ROOT"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(REPO_ROOT / "packages" / "bench-contracts")]
    )
    return subprocess.run(
        [sys.executable, "-m", "bench.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )


def test_selftest_stage_writes_skips_and_forces(tmp_path: Path) -> None:
    root = tmp_path / "wmb"
    first = _bench(["stage=selftest", "paths=local"], root)
    assert first.returncode == 0, first.stderr
    assert "RAN      selftest [all]" in first.stdout
    assert (root / "artifacts" / "_selftest" / "selftest.parquet").is_file()
    assert (root / "artifacts" / "_selftest" / "selftest.parquet.manifest.json").is_file()

    second = _bench(["stage=selftest", "paths=local"], root)
    assert second.returncode == 0, second.stderr
    assert "SKIPPED  selftest [all]" in second.stdout

    forced = _bench(["stage=selftest", "paths=local", "force=true"], root)
    assert forced.returncode == 0, forced.stderr
    assert "RAN      selftest [all]" in forced.stdout
    # Hydra scrive la propria cartella di run sotto gli artefatti, non nella cwd.
    assert (root / "artifacts" / "_hydra").is_dir()


def test_unknown_stage_fails_cleanly(tmp_path: Path) -> None:
    result = _bench(["stage=nope", "paths=local"], tmp_path / "wmb")
    assert result.returncode == 1
    assert "unknown stage 'nope'" in result.stderr


def test_invalid_config_fails_cleanly(tmp_path: Path) -> None:
    result = _bench(["stage=selftest", "paths=local", "global_seed=7"], tmp_path / "wmb")
    assert result.returncode == 1
    assert "locked" in result.stderr


@pytest.mark.timeout(300)
def test_doctor_report_and_json(tmp_path: Path) -> None:
    out = tmp_path / "doctor.json"
    result = _bench(["doctor", "--json", str(out), "paths=local"], tmp_path / "wmb")
    # In locale mancano interpreti, modelli e dataset del cluster: il report deve dirlo.
    assert result.returncode == 1
    assert result.stdout.startswith("bench doctor")
    for section in (
        "[environments]",
        "[submodules]",
        "[patched]",
        "[apptainer]",
        "[models]",
        "[datasets]",
        "[secrets]",
        "[slurm]",
        "[paths]",
        "[gpu]",
        "[attack tools]",
    ):
        assert section in result.stdout
    assert "use 'bench doctor --gpu' on a GPU node" in result.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["exit_code"] == 1


def test_submit_dry_run(tmp_path: Path) -> None:
    result = _bench(
        [
            "submit",
            "--dry-run",
            "stage=generate_baseline",
            "paths=local",
            "levels=[L1]",
            "splits=[dev,test]",
            "languages=[python,java]",
        ],
        tmp_path / "wmb",
    )
    assert result.returncode == 0, result.stderr
    assert '"slurm_partition": "gpuq"' in result.stdout
    assert '"slurm_qos": "did_tesi_nlp_330_gpuq_qos"' in result.stdout
    assert "8 job(s) planned (dry run)" in result.stdout  # 2 modelli x 2 linguaggi x 2 parti


def test_submit_requires_stage(tmp_path: Path) -> None:
    result = _bench(["submit", "--dry-run", "paths=local"], tmp_path / "wmb")
    assert result.returncode == 2
    assert "bench submit" in result.stderr
