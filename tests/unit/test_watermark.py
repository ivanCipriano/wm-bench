"""Adapter di STONE e fase ``watermark`` senza GPU.

Il worker reale è sostituito dallo shim di prova ``echo`` (stesso protocollo, nessun modello):
si verificano iperparametri, chiave, ``NOT_APPLICABLE`` per JavaScript senza worker, I1,
ripresa e manifest della fase.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from bench_contracts import WorkerItem, WorkerRequest

import bench.pipeline.stages.watermark as wm
from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.methods.adapters.stone import StoneAdapter
from bench.methods.base import MethodAdapter
from bench.methods.worker_client import WorkerClient, WorkerRun
from bench.pipeline.facade import BenchmarkFacade
from bench.pipeline.stage import Cell, CellPlanner
from tests.conftest import REPO_ROOT, rebuild
from tests.datafix import HE_DEV, MBPP_DEV, build_tiny_datasets, tiny_config

MODEL = "qwen25_coder_7b"


class EchoClient(WorkerClient):
    """Client reale che però lancia sempre lo shim ``echo`` con l'interprete dei test."""

    calls: ClassVar[list[str]] = []

    def run(
        self,
        env_name: str,
        method: str,
        request: WorkerRequest,
        items: Any,
        source: Any = None,
        secrets: Any = (),
    ) -> WorkerRun:  # type: ignore[override]
        EchoClient.calls.append(method)
        return super().run("test", "echo", request, items, None, secrets)

    def introspect(
        self, env_name: str, method: str, source: Any = None, timeout_s: float = 600
    ) -> dict[str, Any]:  # type: ignore[override]
        return {"method": method, "python": "test", "packages_sha256": "0" * 64}


def echo_client(cfg: ExperimentConfig, timeout_s: float) -> WorkerClient:
    return EchoClient(
        envs={"test": Path(sys.executable)},
        shims_root=REPO_ROOT / "shims",
        timeout_s=timeout_s,
        extra_pythonpath=[REPO_ROOT / "packages" / "bench-contracts"],
    )


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "b" * 40)


def adapter(cfg: ExperimentConfig) -> StoneAdapter:
    return StoneAdapter(cfg.methods_catalog["stone"], cfg, echo_client(cfg, 60))


def test_stone_hparams_and_key(cfg: ExperimentConfig) -> None:
    a = adapter(cfg)
    assert a.default_hparams() == {"gamma": 0.5, "delta": 1.0}
    native = a.to_native_hparams(a.default_hparams())
    assert native == {
        "gamma": 0.5,
        "delta": 1.0,
        "skipping_rule": "all_pl",
        "watermark_on_pl": "False",
        "prefix_length": 0,
        "z_threshold": 10.0,
        "vocab_size": None,
    }
    with pytest.raises(ConfigError, match="unknown"):
        a.to_native_hparams({"gamma": 0.5, "delta": 1.0, "hash_key": 3})
    with pytest.raises(ConfigError, match="gamma"):
        a.to_native_hparams({"gamma": 1.5, "delta": 1.0})
    assert a.key("k1") == a.key("k1") != a.key("k2") and 0 <= a.key("k1") < 2**31
    assert a.supports("python") and a.supports("cpp") and not a.supports("javascript")
    h = a.config_hash(a.default_hparams())
    assert len(h) == 12 and h != a.config_hash({"gamma": 0.5, "delta": 0.5})
    assert a.source().pythonpath.name == "stone_implementation"


@pytest.fixture
def facade(cfg: ExperimentConfig, monkeypatch: pytest.MonkeyPatch) -> BenchmarkFacade:
    build_tiny_datasets(cfg.paths.datasets)
    tcfg = tiny_config(cfg)
    BenchmarkFacade(tcfg).run_stage("prepare_data")
    tcfg = rebuild(
        tcfg, methods=["stone"], models=[MODEL], languages=["python", "javascript"], splits=["dev"]
    )
    monkeypatch.setattr(wm, "WORKER_FACTORY", echo_client)
    EchoClient.calls = []
    return BenchmarkFacade(tcfg)


def test_cells_of_watermark(facade: BenchmarkFacade) -> None:
    cells = CellPlanner(facade.cfg).cells_for(wm.WatermarkStage)
    assert {(c.method, c.language) for c in cells} == {("stone", "python"), ("stone", "javascript")}


def test_watermark_stage_rows_statuses_and_resume(facade: BenchmarkFacade) -> None:
    report = facade.run_stage("watermark")
    assert report.ran == 2
    stage = wm.WatermarkStage(facade.cfg)
    n = facade.cfg.decoding["level1"].n
    cfg_hash = stage.config_hash("stone")
    py = facade.store.read_table(
        wm.watermarked_ref("stone", MODEL, cfg_hash, "L1", "python", "dev")
    )
    js = facade.store.read_table(
        wm.watermarked_ref("stone", MODEL, cfg_hash, "L1", "javascript", "dev")
    )
    assert len(py) == (HE_DEV + MBPP_DEV) * n  # I1
    assert set(py["embed_status"]) == {"OK"} and set(py["source"]) == {"llm_watermarked"}
    assert set(py["method"]) == {"stone"} and set(py["config_hash"]) == {cfg_hash}
    assert set(py["key_id"]) == {"k1"} and py["sample_id"].is_unique
    for _, group in py.groupby("problem_key"):
        assert sorted(group["sample_index"]) == list(range(n)) and group["seed"].nunique() == 1
    # JavaScript: N righe NOT_APPLICABLE per problema, senza invocare il worker.
    assert len(js) == HE_DEV * n and set(js["embed_status"]) == {"NOT_APPLICABLE"}
    assert not js["extraction_ok"].any() and EchoClient.calls == ["stone"]
    manifest = facade.store.read_manifest(
        wm.watermarked_ref("stone", MODEL, cfg_hash, "L1", "python", "dev")
    )
    assert manifest is not None and manifest.config_hash == cfg_hash
    extra = manifest.extra
    assert extra["native_hparams"]["delta"] == 1.0 and extra["key_id"] == "k1"
    assert extra["seed_scheme"] == "per_problem"  # STONE: seme della baseline (D9)
    assert extra["embed_status_counts"] == {"OK": len(py)}
    assert manifest.worker_env is not None and manifest.worker_packages_sha256 == "0" * 64
    assert manifest.n_rows_expected == manifest.n_rows_out
    # Seconda esecuzione: tutto saltato.
    assert facade.run_stage("watermark").ran == 0


def test_same_seed_as_the_baseline(facade: BenchmarkFacade) -> None:
    from bench.generation.hf_generator import generation_seed

    facade.run_stage(
        "watermark",
        cells=[Cell(method="stone", model_id=MODEL, level="L1", language="python", split="dev")],
    )
    stage = wm.WatermarkStage(facade.cfg)
    df = facade.store.read_table(
        wm.watermarked_ref("stone", MODEL, stage.config_hash("stone"), "L1", "python", "dev")
    )
    for row in df.itertuples(index=False):
        assert row.seed == generation_seed(20261001, MODEL, row.problem_key, "python")


def test_worker_items_carry_the_prompt_and_the_neutral_decoding(facade: BenchmarkFacade) -> None:
    import json

    cell = Cell(method="stone", model_id=MODEL, level="L1", language="python", split="dev")
    facade.run_stage("watermark", cells=[cell])
    stage = wm.WatermarkStage(facade.cfg)
    run_dir = (
        facade.store.root
        / "_runs"
        / "watermark"
        / "stone"
        / MODEL
        / stage.config_hash("stone")
        / "L1_python_dev"
    )
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    assert request["decoding"]["temperature"] == 0.2 and request["decoding"]["top_k"] == 0
    assert request["decoding"]["num_return_sequences"] == facade.cfg.decoding["level1"].n
    assert request["decoding"]["torch_dtype"] == "bfloat16"
    assert request["hparams"]["vocab_size"] is None and request["key_id"] == "k1"
    first = WorkerItem.from_dict(
        json.loads((run_dir / "items.jsonl").read_text(encoding="utf-8").splitlines()[0])
    )
    assert first.prompt_messages is not None and first.prompt_messages[0]["role"] == "system"


def test_shims_are_python39_compatible() -> None:
    stone_env_scripts = [
        REPO_ROOT / "tests" / "oracle" / f"stone_{n}.py" for n in ("original", "rowwise")
    ]
    for path in sorted((REPO_ROOT / "shims").rglob("*.py")) + stone_env_scripts:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 9))  # niente match, ecc.
        if path.name != "__init__.py":
            assert "from __future__ import annotations" in source, path
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                assert node.arg != "strict", f"{path}: zip(strict=) needs Python 3.10"
            if isinstance(node, ast.Import | ast.ImportFrom):
                module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                assert not (module or "").startswith("bench."), f"{path} imports {module}"
