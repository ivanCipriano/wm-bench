"""``scripts/check_stage_done.py``: una fase è finita solo se ogni uscita di ogni cella c'è."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.methods.base import MethodAdapter
from bench.store.artifact_store import ArtifactStore
from tests.unit.test_inventory_scripts import _load

ARGS = ["stage=watermark", "methods=[mcss,multimark]", "levels=[L1,L2]", "splits=[dev]",
        "languages=[python]", "paths=local"]  # fmt: skip


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path / "wmb"))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "d" * 40)
    return _load("check_stage_done")


def test_missing_outputs_mean_not_done(
    script: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert script.main(["--show", "2", *ARGS]) == 1  # type: ignore[attr-defined]
    out = capsys.readouterr().out
    assert out.count("[MISSING]") == 2 and "... and 6 more" in out
    assert "watermark: 0/8 cells complete: NOT DONE" in out
    # sweep di multimark: base e i due punti di sviluppo, per 2 modelli e 2 livelli
    sweep = [a for a in ARGS if not a.startswith("methods=")]
    assert script.main(["methods=[multimark]", "hpo.grid=multimark", *sweep]) == 1  # type: ignore[attr-defined]
    assert "watermark: 0/12 cells complete" in capsys.readouterr().out
    monkeypatch.setattr(ArtifactStore, "exists", lambda self, ref: True)
    assert script.main(ARGS) == 0  # type: ignore[attr-defined]
    assert "8/8 cells complete: DONE" in capsys.readouterr().out
    assert script.main(["methods=[mcss]"]) == 2  # type: ignore[attr-defined]
