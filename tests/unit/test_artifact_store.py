"""Test di ArtifactStore: scrittura atomica, validità, letture (SPEC §7.11)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from bench.domain.errors import MissingInputError
from bench.domain.models import MetricValue
from bench.store.artifact_store import ArtifactStore
from bench.store.manifest import Manifest, Provenance
from bench.store.refs import ArtifactRef

REF = ArtifactRef.of("baseline", "baseline/qwen/L1_python_dev.parquet")


def make_manifest(kind: str = "baseline", rows: int | None = 3) -> Manifest:
    return Manifest(
        kind=kind,
        stage="test",
        cell={"method": None},
        config={},
        config_sha256="0" * 64,
        global_seed=20261001,
        provenance=Provenance(
            repo_commit=None,
            repo_dirty=None,
            core_python="3.11",
            core_packages_sha256="x",
            slurm_job_id=None,
            node="test",
            gpu=None,
        ),
        started_at=datetime.now(UTC),
        n_rows_out=rows,
        n_rows_expected=rows,
    )


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts", producers={"baseline": "generate_baseline"})


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


def test_write_then_read(store: ArtifactStore, df: pd.DataFrame) -> None:
    final = store.write_table(REF, df, make_manifest())
    assert store.exists(REF)
    assert store.verify(REF)
    assert final.status == "complete"
    assert final.data_size == store.path_of(REF).stat().st_size
    assert final.kind == "baseline"
    pd.testing.assert_frame_equal(store.read_table(REF), df)
    assert store.count_rows(REF) == 3
    assert store.manifest_of(REF).name == "L1_python_dev.parquet.manifest.json"


def test_no_temporary_files_left(store: ArtifactStore, df: pd.DataFrame) -> None:
    store.write_table(REF, df, make_manifest())
    names = sorted(p.name for p in store.path_of(REF).parent.iterdir())
    assert names == ["L1_python_dev.parquet", "L1_python_dev.parquet.manifest.json"]


def test_crash_before_manifest_leaves_no_artifact(
    store: ArtifactStore, df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ArtifactStore, "_write_text_atomic", staticmethod(boom))
    with pytest.raises(OSError):
        store.write_table(REF, df, make_manifest())
    assert store.path_of(REF).is_file()
    assert not store.exists(REF)


def test_failed_overwrite_invalidates_old_artifact(
    store: ArtifactStore, df: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.write_table(REF, df, make_manifest())
    assert store.exists(REF)

    def broken(self: pd.DataFrame, *args: object, **kwargs: object) -> None:
        raise RuntimeError("crash while writing")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", broken)
    with pytest.raises(RuntimeError):
        store.write_table(REF, df, make_manifest())
    # Il manifest vecchio è stato rimosso prima di scrivere: niente "complete" incoerente.
    assert not store.exists(REF)
    assert [p for p in store.path_of(REF).parent.iterdir() if ".tmp-" in p.name] == []


def test_incomplete_manifest_means_missing(store: ArtifactStore, df: pd.DataFrame) -> None:
    final = store.write_table(REF, df, make_manifest())
    running = final.model_copy(update={"status": "running"})
    store.manifest_of(REF).write_text(running.model_dump_json(), encoding="utf-8")
    assert not store.exists(REF)


def test_size_mismatch_means_missing(store: ArtifactStore, df: pd.DataFrame) -> None:
    store.write_table(REF, df, make_manifest())
    with open(store.path_of(REF), "ab") as handle:
        handle.write(b"garbage")
    assert not store.exists(REF)


def test_verify_detects_same_size_corruption(store: ArtifactStore, df: pd.DataFrame) -> None:
    store.write_table(REF, df, make_manifest())
    path = store.path_of(REF)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert store.exists(REF)
    assert not store.verify(REF)


def test_unreadable_manifest_means_missing(store: ArtifactStore, df: pd.DataFrame) -> None:
    store.write_table(REF, df, make_manifest())
    store.manifest_of(REF).write_text("{not json", encoding="utf-8")
    assert not store.exists(REF)


def test_read_missing_raises_with_producer(store: ArtifactStore) -> None:
    with pytest.raises(MissingInputError, match="generate_baseline"):
        store.read_table(REF)


def test_model_roundtrip(store: ArtifactStore) -> None:
    ref = ArtifactRef.of("metrics", "metrics/one.json")
    value = MetricValue(
        name="auroc",
        value=0.9,
        ci_low=0.8,
        ci_high=0.95,
        ci_method="bootstrap",
        n=10,
        cell={"method": "sweet"},
    )
    store.write_model(ref, value, make_manifest("metrics", rows=None))
    assert store.read_model(ref, MetricValue) == value
    assert store.count_rows(ref) is None


@pytest.mark.parametrize("bad", ["/abs/x.parquet", "../x.parquet", "a/x.parquet.manifest.json", ""])
def test_artifact_ref_validation(bad: str) -> None:
    with pytest.raises(ValueError):
        ArtifactRef.of("k", bad)
