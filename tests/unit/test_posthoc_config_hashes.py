"""``scripts/posthoc_config_hashes.py``: il commit della libreria entra nel ``config_hash``, così le
celle del giro di capacità (libreria a4e6972) hanno percorsi diversi da quelle dei giri precedenti
(475d4cc, 488fb5a); gli hash scritti in ``configs/hpo/multimark.yaml`` sono quelli calcolati.

Gli hash dei giri precedenti scritti nei log (per esempio 59a04735824c) non si ricalcolano più:
da a4e6972 l'adapter passa anche ``names.bits_per_name`` e ``canonical.same_kind``, che entrano
negli iperparametri nativi."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bench.methods.base import MethodAdapter
from tests.unit.test_inventory_scripts import _load

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUND2 = "475d4cc89ca5ef8a8571a0246f4eb4c0d653edf0"
ROUND3 = "a4e6972b1ee5cc1027cd4fdadf0fd41f67a67d4c"
# Hash del giro di capacità (scripts/run_p2_round3.sh li controlla).
ROUND3_HASHES = {
    "mcss": "b521002e09f5",
    "multimark": "3c1bd11758fc",
    "single_letter": "4937d9ddd78a",
    "A": "770f999adefb",
    "B": "cb79b2fcb4e6",
    "C": "cf7e4c9f106e",
    "D": "37400fdcd589",
}


def _hashes(script: object, monkeypatch: pytest.MonkeyPatch, commit: str,
            capsys: pytest.CaptureFixture[str]) -> list[list[str]]:  # fmt: skip
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: commit)
    assert script.main(["paths=local"]) == 0  # type: ignore[attr-defined]
    return [line.split(" ", 2) for line in capsys.readouterr().out.splitlines()]


def test_round3_hashes_differ_and_match_the_grid_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    script = _load("posthoc_config_hashes")
    old = _hashes(script, monkeypatch, ROUND2, capsys)
    new = _hashes(script, monkeypatch, ROUND3, capsys)
    assert [label for *_, label in new] == [
        "default", "default", 'grid:{"names.single_letter":"extend_except_loop"}',
        'grid:{"canonical.same_kind":"binding_order"}', 'grid:{"names.bits_per_name":2}',
        'grid:{"canonical.same_kind":"binding_order","names.bits_per_name":2,'
        '"names.single_letter":"extend_except_loop"}',
        'grid:{"canonical.same_kind":"binding_order","names.bits_per_name":2,'
        '"names.include_params":true,"names.single_letter":"extend_except_loop"}',
    ]  # fmt: skip
    hashes = [h for _, h, _ in new]
    assert hashes == list(ROUND3_HASHES.values())
    assert len(set(hashes)) == len(hashes) and not set(hashes) & {h for _, h, _ in old}
    text = (REPO_ROOT / "configs" / "hpo" / "multimark.yaml").read_text(encoding="utf-8")
    written = re.findall(r"^  - \{.*\}\s+# ([0-9a-f]{12})$", text, flags=re.M)
    assert written == hashes[1:]
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: ROUND2)
    assert script.main(["--expect-commit", ROUND3, "paths=local"]) == 1
    assert "apply_patches.sh multimark" in capsys.readouterr().err


@pytest.mark.parametrize("script_name", ["run_p2_round3.sh", "run_p2_snake_dev.sh"])
def test_round3_script_checks_the_same_hashes(script_name: str) -> None:
    """``scripts/run_p2_round3.sh`` e ``scripts/run_p2_snake_dev.sh`` (T1.6 sulle stesse
    configurazioni) controllano il commit e gli hash di questo test."""
    text = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
    assert f"EXPECTED={ROUND3}" in text
    for name, var in (("mcss", "MCSS"), ("multimark", "MM"), ("single_letter", "MM_SL"),
                      ("A", "MM_A"), ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text
