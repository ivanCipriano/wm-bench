"""Esclusioni di CodeNet (decisione dell'utente del 7 ottobre 2026): i problemi dell'oracle di
MCGMark non devono entrare nella selezione della Milestone 9."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.config.schema import ExperimentConfig
from bench.data.codenet_exclusions import check_not_excluded, load_excluded
from bench.domain.errors import ConfigError, DataError
from tests.conftest import REPO_ROOT

EXCLUDED = REPO_ROOT / "configs" / "dataset" / "codenet_excluded.yaml"


def test_versioned_file_is_valid_and_wired(cfg: ExperimentConfig) -> None:
    excluded = load_excluded(EXCLUDED)
    # Vuoto fino alla selezione sul cluster (oracle_method.sh mcgmark select), poi 3 problemi.
    assert len(excluded) in (0, 3)
    assert set(excluded.values()) <= {"oracle_mcgmark"}
    assert cfg.datasets["codenet"].file("excluded").resolve() == EXCLUDED.resolve()


def test_selection_must_not_contain_excluded_problems() -> None:
    excluded = {"codenet/p00001": "oracle_mcgmark"}
    check_not_excluded(["codenet/p00002", "codenet/p00003"], excluded)
    with pytest.raises(DataError, match="p00001"):
        check_not_excluded(["codenet/p00002", "codenet/p00001"], excluded)


def test_selected_l2_problems_exclude_the_oracle_ones(cfg: ExperimentConfig) -> None:
    """Dalla M9: i problemi L2 preparati non contengono quelli esclusi."""
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.store.artifact_store import ArtifactStore

    store = ArtifactStore(cfg.paths.artifacts)
    ref = problems_ref("L2", "python")
    if not store.exists(ref):
        pytest.skip("L2 problems not prepared yet (Milestone 9)")
    keys = set(store.read_table(ref)["problem_key"])
    check_not_excluded(keys, load_excluded(EXCLUDED))


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("excluded: {}", "must be a list"),
        ("excluded:\n  - {problem_key: p1, use: oracle_mcgmark, reason: x}", "invalid"),
        ("excluded:\n  - {problem_key: codenet/p1, use: other, reason: x}", "unknown use"),
        ("excluded:\n  - {problem_key: codenet/p1, use: oracle_mcgmark}", "missing reason"),
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, body: str, match: str) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        load_excluded(path)
