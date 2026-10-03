"""Comando di ``ApptainerSandbox`` (SPEC §11.2) e controlli preliminari."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.errors import SandboxError
from bench.execution import sandbox as sb


def test_command_matches_the_spec(cfg: ExperimentConfig, tmp_path: Path) -> None:
    box = sb.ApptainerSandbox(cfg.execution)
    cmd = box.command(
        ["python3", "/opt/wmb/wmb_runner.py", "/work/job.json"],
        tmp_path,
        120,
        None,
        tmp_path / "data",
    )
    assert cmd[:7] == [
        "apptainer",
        "exec",
        "--containall",
        "--cleanenv",
        "--no-home",
        "--pwd",
        "/work",
    ]
    assert ["--bind", f"{tmp_path}:/work:rw"] == cmd[7:9]
    assert ["--bind", f"{tmp_path / 'data'}:/data:ro"] == cmd[9:11]
    assert cmd[11:14] == ["--net", "--network", "none"]
    i = cmd.index(str(cfg.execution.image_dir))
    assert cmd[i + 1 : i + 4] == ["timeout", "--kill-after=5", "120"]
    assert cmd[-3:] == ["python3", "/opt/wmb/wmb_runner.py", "/work/job.json"]
    assert "prlimit" not in cmd
    limited = box.command(["true"], tmp_path, 10, 2048)
    assert limited[-4:] == ["prlimit", f"--as={2048 * 1024 * 1024}", "--", "true"]


def test_check_explains_what_is_missing(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = sb.ApptainerSandbox(cfg.execution)
    monkeypatch.setattr(sb.shutil, "which", lambda name: None)
    with pytest.raises(SandboxError, match="module load apptainer/apptainer.module"):
        box.check()
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/apptainer")
    with pytest.raises(SandboxError, match="build_sandbox.sh"):
        box.check()
    runner = cfg.execution.image_dir / "opt" / "wmb" / "wmb_runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("", encoding="utf-8")
    with pytest.raises(SandboxError, match="sha256"):
        box.check()
    Path(f"{cfg.execution.image_sif}.sha256").write_text("ab" * 32 + "\n", encoding="utf-8")
    box.check()
    assert box.image_hash == "ab" * 32


def test_local_sandbox_runs_the_runner(tmp_path: Path) -> None:
    import json
    import sys

    box = sb.LocalSandbox()
    job = {
        "mode": "samples",
        "kind": "program",
        "rule": "javascript",
        "compile_cmd": None,
        "run_cmd": [sys.executable, "test.js"],
        "timeout_s": 20,
        "compile_timeout_s": 20,
        "mem_mb": None,
        "samples": [{"id": "0", "program": "x = 1\n"}],
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    result = box.run(box.runner_command(tmp_path), tmp_path, 60)
    assert result.returncode == 0, result.stderr
    out = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert out["results"][0]["status"] == "PASSED"
    assert box.image_hash == "local"
