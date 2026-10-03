"""Criteri di accettazione della Milestone 2 sugli artefatti reali di ``prepare_data``.

Richiede ``bench stage=prepare_data levels=[L1]`` già eseguito. I conteggi sono quelli
della SPEC §10.2 e di cluster_info §10.1 (D7), scritti qui e non letti dalla configurazione.
"""

from __future__ import annotations

import statistics

import pandas as pd
import pytest

from bench.config.schema import ExperimentConfig
from bench.domain.enums import Language
from bench.pipeline.stages.prepare_data import (
    integration_ref,
    native_ref,
    problems_ref,
    split_ref,
)
from bench.store.artifact_store import ArtifactStore
from bench.store.refs import ArtifactRef
from tests.integration.conftest import languages_under_test, require

pytestmark = pytest.mark.data

LANGS = (Language.PYTHON, *[lang for lang in languages_under_test() if lang is not Language.PYTHON])
HEP = tuple(lang for lang in LANGS if lang is not Language.PYTHON)


@pytest.fixture(scope="module")
def store(data_cfg: ExperimentConfig) -> ArtifactStore:
    store = ArtifactStore(data_cfg.paths.artifacts)
    require(
        store.manifest_of(split_ref()),
        "prepare_data artifacts (run 'bench stage=prepare_data levels=[L1]')",
    )
    return store


def _table(store: ArtifactStore, ref: ArtifactRef) -> pd.DataFrame:
    assert store.verify(ref), f"artifact missing or corrupted: {ref.path}"
    return store.read_table(ref)


def _splits(df: pd.DataFrame) -> tuple[int, int]:
    return int((df["split"] == "dev").sum()), int((df["split"] == "test").sum())


def test_problem_counts(store: ArtifactStore) -> None:
    py = _table(store, problems_ref(Language.PYTHON))
    assert len(py) == 542 and _splits(py) == (96, 446)
    assert py["dataset"].value_counts().to_dict() == {"mbppplus": 378, "humanevalplus": 164}
    for lang in HEP:
        df = _table(store, problems_ref(lang))
        assert len(df) == 164 and _splits(df) == (24, 140)


def test_native_negative_counts_and_d7(store: ArtifactStore) -> None:
    py = _table(store, native_ref(Language.PYTHON))
    assert len(py) == 1138 and _splits(py) == (210, 928)
    assert py["dataset"].value_counts().to_dict() == {"mbpp_original": 974, "humanevalplus": 164}
    split = _table(store, split_ref())
    extra = split[split["role"] == "negative_only"]
    assert len(extra) == 596 and _splits(extra) == (114, 482)
    for lang in HEP:
        df = _table(store, native_ref(lang))
        assert len(df) == 164 and _splits(df) == (24, 140)


def test_integration_counts(store: ArtifactStore) -> None:
    assert len(_table(store, integration_ref(Language.PYTHON, "dev"))) == 2000 - 210
    for lang in HEP:
        assert len(_table(store, integration_ref(lang, "dev"))) == 2000 - 24
        # Decisione dell'utente: negativi di test per linguaggio pari a quelli di L1 Python (928).
        assert len(_table(store, integration_ref(lang, "test"))) == 928 - 140


def test_i4_single_split_per_problem_key(store: ArtifactStore) -> None:
    frames = [_table(store, split_ref())]
    for lang in LANGS:
        frames += [_table(store, problems_ref(lang)), _table(store, native_ref(lang))]
    rows = pd.concat([f[["problem_key", "split"]] for f in frames])
    assert (rows.groupby("problem_key")["split"].nunique() == 1).all()
    reference = _table(store, problems_ref(Language.PYTHON)).set_index("problem_key")["split"]
    reference = reference[reference.index.str.startswith("humaneval/")].sort_index()
    for lang in HEP:
        other = _table(store, problems_ref(lang)).set_index("problem_key")["split"].sort_index()
        assert other.equals(reference)


def test_loc_statistics_of_problems(store: ArtifactStore) -> None:
    py = _table(store, problems_ref(Language.PYTHON))
    for dataset, (mean, std) in {"humanevalplus": (5.1, 4.4), "mbppplus": (4.0, 3.7)}.items():
        locs = py.loc[py["dataset"] == dataset, "loc_to_generate"].tolist()
        assert abs(statistics.fmean(locs) - mean) <= 0.1
        assert abs(statistics.pstdev(locs) - std) <= 0.1


def test_integration_is_disjoint_and_in_range(store: ArtifactStore) -> None:
    for lang in HEP:
        dev = _table(store, integration_ref(lang, "dev"))
        test = _table(store, integration_ref(lang, "test"))
        assert not set(dev["repo"]) & set(test["repo"]), f"{lang}: shared repositories"
        assert not set(dev["problem_key"]) & set(test["problem_key"])
        for part, df in (("dev", dev), ("test", test)):
            manifest = store.read_manifest(integration_ref(lang, part))
            assert manifest is not None
            lo, hi = manifest.extra["reference_range"]
            assert df["loc"].between(lo, hi).all()
            assert df["contamination_risk"].all()
            assert "moved_between_bins" in manifest.extra
