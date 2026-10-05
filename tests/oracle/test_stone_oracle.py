"""Oracle di STONE (SPEC §21.4): lo shim riproduce il percorso originale. Cluster, nodo NVIDIA.

Prerequisiti (``scripts/oracle_stone.sh``):
1. ``tests/oracle/make_stone_inputs.py`` (bench-core) → ``inputs.json``;
2. ``tests/oracle/stone_original.py`` (ambiente ``stone``) → ``original.json``.

Verifiche:
- generazione a una sequenza: stesso testo dello shim e del percorso originale;
- rilevazione su codice fissato: stessi z-score (tolleranza 1e-6) e stessi casi falliti;
- decoding effettivo dello shim: temperature 0,2, top_p 0,95, ``max_new_tokens`` del request;
- equivalenza per riga del processor con 6 sequenze (patch 0001).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bench_contracts import WorkerItem

from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.methods.adapters.stone import StoneAdapter
from bench.pipeline.stages.watermark import make_worker_client
from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.oracle, pytest.mark.gpu]

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle" / "stone"
TOLERANCE = 1e-6


def _require(path: Path) -> None:
    if path.is_file():
        return
    message = f"{path} not found: run scripts/oracle_stone.sh"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    _require(FIXTURES / "inputs.json")
    _require(FIXTURES / "original.json")
    inputs = json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8"))
    original = json.loads((FIXTURES / "original.json").read_text(encoding="utf-8"))
    return {"inputs": inputs, "original": original}


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(["paths=cluster", "stage=watermark"])


@pytest.fixture(scope="module")
def adapter(cfg: ExperimentConfig) -> StoneAdapter:
    method_cfg = cfg.methods_catalog["stone"]
    return StoneAdapter(method_cfg, cfg, make_worker_client(cfg, method_cfg.worker_timeout_s))


def _request(
    adapter: StoneAdapter, cfg: ExperimentConfig, inputs: dict[str, Any], op: str, run_dir: Path
) -> Any:
    model = cfg.models_catalog[inputs["model_id"]]
    request = adapter.build_request(
        op, model, adapter.default_hparams(), "k1", inputs["decoding"], "", run_dir, "cuda:0"
    )
    assert request.key == inputs["key"] and request.hparams == inputs["native_hparams"]
    return request


def test_generation_matches_the_original(
    data: dict[str, Any], adapter: StoneAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    inputs, original = data["inputs"], data["original"]
    request = _request(adapter, cfg, inputs, "embed", tmp_path / "embed")
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
        assert gen["prompt_ids_match"], gen["problem_key"]
        assert gen["new_text"] is not None, gen["problem_key"]
        assert by_key[gen["problem_key"]].raw_output == gen["new_text"], gen["problem_key"]
    # Decoding effettivo (SPEC §21.4 punto 4).
    effective = next(
        r.extra["generation_config"] for r in run.results if "generation_config" in r.extra
    )
    assert effective["temperature"] == 0.2 and effective["top_p"] == 0.95
    assert effective["max_new_tokens"] == inputs["decoding"]["max_new_tokens"] == 512
    assert effective["top_k"] == 0 and effective["repetition_penalty"] == 1.0
    assert effective["do_sample"] is True and effective["no_repeat_ngram_size"] == 0
    assert run.results[0].extra["vocab_size"] == original["vocab_size"]


def test_detection_matches_the_original(
    data: dict[str, Any], adapter: StoneAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    inputs, original = data["inputs"], data["original"]
    request = _request(adapter, cfg, inputs, "detect", tmp_path / "detect")
    detections = original["detections"]
    items = [
        WorkerItem(
            item_id=d["id"],
            language=d["language"],
            seed=0,
            prompt_messages=None,
            code=d["code"],
            context_prompt=None,
            expected_message=None,
            n=1,
        )
        for d in detections
    ]
    run = adapter.run_worker(request, items)
    by_id = {r.item_id: r for r in run.results}
    assert any(d["id"].startswith("generated:") for d in detections)
    for d in detections:
        result = by_id[d["id"]]
        if d["error"] is not None:
            assert result.status == "FAILED", d["id"]
            continue
        assert result.status == "OK", (d["id"], result.error)
        assert result.score is not None and abs(result.score - d["score"]) <= TOLERANCE, d["id"]
        assert result.native_decision == d["is_watermarked"]
        extra = result.extra
        assert extra["n_syntax"] + extra["n_nonsyntax"] == extra["n_tokens"]
        if extra["n_nonsyntax"] > 0:
            assert extra["z_nonsyntax"] is not None
    # Diagnostica, non criterio di fedeltà (SPEC §21.4): i due punteggi per tipo di codice.
    # Con lo z-score ufficiale (denominatore sui token sintattici, audit §5) la separazione
    # fra codice marcato e codice umano non è garantita; si confrontano i due punteggi in M7.
    print("\nid                          official   z_nonsyntax  n_syntax  n_nonsyntax")
    for d in detections:
        result = by_id[d["id"]]
        z2 = result.extra.get("z_nonsyntax")
        official = f"{result.score:9.3f}" if result.score is not None else "   FAILED"
        corrected = f"{z2:12.3f}" if z2 is not None else "        None"
        print(
            f"{d['id']:26s} {official}  {corrected}  {result.extra.get('n_syntax', '-')!s:>8}"
            f"  {result.extra.get('n_nonsyntax', '-')!s:>11}"
        )


def test_rowwise_equivalence_of_the_processor(adapter: StoneAdapter, cfg: ExperimentConfig) -> None:
    inputs_path = FIXTURES / "inputs.json"
    _require(inputs_path)
    model_path = json.loads(inputs_path.read_text(encoding="utf-8"))["model_path"]
    config = json.loads((Path(model_path) / "config.json").read_text(encoding="utf-8"))
    env = adapter.worker.environment(adapter.source())
    proc = subprocess.run(
        [
            str(cfg.envs["stone"].python),
            str(REPO_ROOT / "tests" / "oracle" / "stone_rowwise.py"),
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
    for language, result in report["languages"].items():
        assert result["rows_equal"], (language, result)
        assert result["biased"] == result["expected_biased"], (language, result)
    assert report["ok"] and proc.returncode == 0
