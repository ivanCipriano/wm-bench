"""Comando di ``ApptainerSandbox`` (SPEC §11.2), estrazione locale dell'immagine, controlli."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bench.config.schema import ExecutionConfig, ExperimentConfig
from bench.domain.errors import SandboxError
from bench.execution import sandbox as sb


def _execution(cfg: ExperimentConfig, **update: Any) -> ExecutionConfig:
    return cfg.execution.model_copy(update=update)


def _fake_image(cfg: ExperimentConfig, content: bytes = b"fake image") -> None:
    sif = cfg.execution.image_sif
    sif.parent.mkdir(parents=True, exist_ok=True)
    sif.write_bytes(content)
    Path(f"{sif}.sha256").write_text(hashlib.sha256(content).hexdigest() + "\n", encoding="utf-8")


def test_command_matches_the_spec(cfg: ExperimentConfig, tmp_path: Path) -> None:
    box = sb.ApptainerSandbox(cfg.execution)
    cmd = box.command(["python3", "/opt/wmb/wmb_runner.py", "/work/job.json"], tmp_path, 120)
    assert cmd[:9] == [
        "apptainer",
        "exec",
        "--containall",
        "--cleanenv",
        "--no-home",
        "--pwd",
        "/work",
        "--bind",
        f"{tmp_path}:/work:rw",
    ]
    assert cmd[9:12] == ["--net", "--network", "none"]
    i = cmd.index(str(cfg.execution.image_dir))
    assert cmd[i + 1 : i + 4] == ["timeout", "--kill-after=5", "120"]
    assert cmd[-3:] == ["python3", "/opt/wmb/wmb_runner.py", "/work/job.json"]
    assert "prlimit" not in cmd and "--no-mount" not in cmd
    limited = box.command(["true"], tmp_path, 10, 2048)
    assert limited[-4:] == ["prlimit", f"--as={2048 * 1024 * 1024}", "--", "true"]


def test_check_explains_what_is_missing(
    cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = sb.ApptainerSandbox(_execution(cfg, node_local=False))
    monkeypatch.setattr(sb.shutil, "which", lambda name: None)
    with pytest.raises(SandboxError, match="module load apptainer/apptainer.module"):
        box.check()
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/apptainer")
    with pytest.raises(SandboxError, match="sha256"):
        box.check()
    Path(f"{cfg.execution.image_sif}.sha256").parent.mkdir(parents=True, exist_ok=True)
    Path(f"{cfg.execution.image_sif}.sha256").write_text("ab" * 32 + "\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="build_sandbox.sh"):
        box.check()
    runner = cfg.execution.image_dir / "opt" / "wmb" / "wmb_runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("", encoding="utf-8")
    box.check()
    assert box.image_hash == "ab" * 32
    assert box.image_dir == cfg.execution.image_dir


def test_image_is_extracted_once_on_the_node_disk(
    cfg: ExperimentConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = tmp_path / "node_tmp"
    monkeypatch.setenv("WMB_NODE_TMP", str(node))
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/apptainer")
    _fake_image(cfg)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        assert cmd[:3] == ["apptainer", "build", "--sandbox"]
        target = Path(cmd[3])
        (target / "opt" / "wmb").mkdir(parents=True)
        (target / "opt" / "wmb" / "wmb_runner.py").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    box = sb.ApptainerSandbox(cfg.execution)
    box.check()
    assert box.image_dir.parent == node
    assert (box.image_dir / sb.READY_MARKER).read_text(encoding="utf-8").strip() == box.image_hash
    assert box.workdir_root(Path("unused")).parent == node
    assert str(box.image_dir) in box.command(["true"], tmp_path, 1)
    sb.ApptainerSandbox(cfg.execution).check()  # seconda volta: riusa l'estrazione
    assert len(calls) == 1


def test_extraction_refuses_a_modified_sif(
    cfg: ExperimentConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WMB_NODE_TMP", str(tmp_path / "node_tmp"))
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/apptainer")
    _fake_image(cfg)
    cfg.execution.image_sif.write_bytes(b"tampered")
    with pytest.raises(SandboxError, match="does not match"):
        sb.ApptainerSandbox(cfg.execution).check()


def test_local_sandbox_runs_the_runner(tmp_path: Path) -> None:
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
    assert box.work_path(tmp_path, "a.jsonl") == str(tmp_path / "a.jsonl")
