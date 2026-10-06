"""Oracle di SWEET (SPEC §21.4): lo shim riproduce il percorso originale. Cluster, nodo NVIDIA.

Prerequisiti (``scripts/oracle_method.sh sweet``):
1. ``tests/oracle/make_oracle_inputs.py sweet`` (bench-core) → ``inputs.json``;
2. ``tests/oracle/sweet_original.py`` (ambiente ``sweet``) → ``original.json``.

Verifiche:
- generazione a una sequenza: stesso testo dello shim e del percorso originale;
- rilevazione su codice fissato (con e senza contesto): stessi z-score (tolleranza 1e-6), stessi
  conteggi di token valutati e verdi, stessi casi non valutabili;
- decoding effettivo dello shim (temperature 0,2, top_p 0,95, ``max_new_tokens`` del request);
- equivalenza per riga del processor con 6 sequenze, su CPU e su GPU.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.methods.base import DetectInput, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter

from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.oracle, pytest.mark.gpu]

METHOD = "sweet"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle" / METHOD
TOLERANCE = 1e-6


def _require(path: Path) -> None:
    if path.is_file():
        return
    message = f"{path} not found: run scripts/oracle_method.sh {METHOD}"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    _require(FIXTURES / "inputs.json")
    _require(FIXTURES / "original.json")
    return {
        "inputs": json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8")),
        "original": json.loads((FIXTURES / "original.json").read_text(encoding="utf-8")),
    }


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(["paths=cluster", "stage=watermark"])


@pytest.fixture(scope="module")
def adapter(cfg: ExperimentConfig) -> MethodAdapter:
    _require(cfg.envs[METHOD].python)  # interprete dell'ambiente del metodo (solo sul cluster)
    return make_adapter(cfg, METHOD)


def test_generation_matches_the_original(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    from bench_contracts import WorkerItem

    inputs, original = data["inputs"], data["original"]
    model = cfg.models_catalog[inputs["model_id"]]
    request = adapter.build_request(
        "embed",
        model,
        adapter.default_hparams(),
        "k1",
        inputs["decoding"],
        "",
        tmp_path / "embed",
        "cuda:0",
    )
    assert request.key == inputs["key"] and request.hparams == inputs["native_hparams"]
    items = [
        WorkerItem(
            item_id=p["problem_key"],
            language=p["language"],
            seed=p["seed"],
            prompt_messages=p["messages"],
            code=None,
            context_prompt=None,
            expected_message=None,
            n=1,
        )
        for p in inputs["prompts"]
    ]
    run = adapter.run_worker(request, items)
    assert run.missing == 0 and {r.status for r in run.results} == {"OK"}
    by_key = {r.item_id: r for r in run.results}
    for gen in original["generations"]:
        assert by_key[gen["problem_key"]].raw_output == gen["new_text"], gen["problem_key"]
    effective = next(
        r.extra["generation_config"] for r in run.results if "generation_config" in r.extra
    )
    assert effective["temperature"] == 0.2 and effective["top_p"] == 0.95
    assert effective["max_new_tokens"] == inputs["decoding"]["max_new_tokens"] == 512
    assert effective["top_k"] == 0 and effective["repetition_penalty"] == 1.0
    assert effective["do_sample"] is True and effective["no_repeat_ngram_size"] == 0
    assert run.results[0].extra["vocab_size"] == original["vocab_size"]


def test_detection_matches_the_original(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    assert isinstance(adapter, Detector)
    inputs, original = data["inputs"], data["original"]
    model = cfg.models_catalog[inputs["model_id"]]
    detections = original["detections"]
    results = adapter.detect_codes(
        [
            DetectInput(d["id"], d["language"], d["code"], prompt_messages=d["messages"])
            for d in detections
        ],
        adapter.default_hparams(),
        model,
        "k1",
        inputs["decoding"],
        tmp_path / "detect",
        "cuda:0",
    )
    assert any(d["id"].startswith("generated:") for d in detections)
    assert any(d["messages"] is None for d in detections)  # caso senza contesto (D3)
    print("\nid                          z (shim)   scored  green")
    for d, result in zip(detections, results, strict=True):
        assert result.item_id == d["id"]
        if d["invalid"] or d["num_tokens_scored"] == 0:
            assert result.status == "FAILED", d["id"]
            continue
        assert result.status == "OK", (d["id"], result.error)
        assert result.score is not None and abs(result.score - d["z_score"]) <= TOLERANCE, d["id"]
        assert result.extra["num_tokens_scored"] == d["num_tokens_scored"]
        assert result.extra["num_green_tokens"] == d["num_green_tokens"]
        assert result.native_decision == d["prediction"]
        print(
            f"{d['id']:26s} {result.score:9.3f}  "
            f"{d['num_tokens_scored']:6d}  {d['num_green_tokens']:5d}"
        )


def test_rowwise_equivalence_of_the_processor(
    adapter: MethodAdapter, cfg: ExperimentConfig
) -> None:
    _require(FIXTURES / "inputs.json")
    model_path = json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8"))["model_path"]
    config = json.loads((Path(model_path) / "config.json").read_text(encoding="utf-8"))
    env = adapter.worker.environment(adapter.source())
    proc = subprocess.run(
        [
            str(cfg.envs[METHOD].python),
            str(REPO_ROOT / "tests" / "oracle" / "sweet_rowwise.py"),
            "--tokenizer",
            model_path,
            "--vocab-width",
            str(config["vocab_size"]),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=1800,
        check=False,
    )
    assert proc.stdout.strip(), proc.stderr[-3000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    for device, result in report["devices"].items():
        assert result["rows_equal"], (device, result)
        assert result["biased"] == result["expected_biased"], (device, result)
    assert report["ok"] and proc.returncode == 0
