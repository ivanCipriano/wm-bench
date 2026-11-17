"""``scripts/posthoc_config_hashes.py``: il commit della libreria entra nel ``config_hash``, così le
celle del secondo giro di P2 (libreria 475d4cc) hanno percorsi diversi da quelle del primo
(488fb5a); gli hash scritti in ``configs/hpo/multimark.yaml`` sono quelli calcolati."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bench.methods.base import MethodAdapter
from tests.unit.test_inventory_scripts import _load

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUND1 = "488fb5a769241e7887be2ad7f6596e21495ece82"
ROUND2 = "475d4cc89ca5ef8a8571a0246f4eb4c0d653edf0"


def _hashes(script: object, monkeypatch: pytest.MonkeyPatch, commit: str,
            capsys: pytest.CaptureFixture[str]) -> list[list[str]]:  # fmt: skip
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: commit)
    assert script.main(["paths=local"]) == 0  # type: ignore[attr-defined]
    return [line.split(" ", 2) for line in capsys.readouterr().out.splitlines()]


def test_round2_hashes_differ_and_match_the_grid_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    script = _load("posthoc_config_hashes")
    old = _hashes(script, monkeypatch, ROUND1, capsys)
    new = _hashes(script, monkeypatch, ROUND2, capsys)
    # primo giro: gli hash dei percorsi di P2_dev.txt
    assert [h for m, h, label in old if label == "default"] == ["d68986489777", "194b0aa80f9f"]
    assert [label for *_, label in new] == [
        "default", "default", 'grid:{"names.single_letter":"extend_except_loop"}',
        'grid:{"names.include_params":true}',
    ]  # fmt: skip
    hashes = [h for _, h, _ in new]
    assert len(set(hashes)) == 4 and not set(hashes) & {h for _, h, _ in old}
    text = (REPO_ROOT / "configs" / "hpo" / "multimark.yaml").read_text(encoding="utf-8")
    written = re.findall(r"^  - \{.*\}\s+# ([0-9a-f]{12})$", text, flags=re.M)
    assert written == hashes[1:]
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: ROUND1)
    assert script.main(["--expect-commit", ROUND2, "paths=local"]) == 1
    assert "apply_patches.sh multimark" in capsys.readouterr().err
