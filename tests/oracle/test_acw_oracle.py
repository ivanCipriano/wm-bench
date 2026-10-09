"""Oracle di ACW (SPEC §21.4; audit ``docs/audit/acw.md``). Cluster, nodo ``defq`` (solo CPU).

Prerequisiti (``scripts/oracle_method.sh acw``, con ``SOURCERY_TOKEN`` nell'ambiente):
1. ``make_oracle_inputs.py acw`` → ``inputs.json`` (codici di 5 HumanEval e 5 MBPP+: baseline
   e soluzioni canoniche; chiavi k1 e k2);
2. ``acw_original.py`` (ambiente ``acw``) → ``original.json``.

Verifiche:
- inserimento: lo shim (a lotti) produce lo stesso codice del percorso diretto, con gli stessi
  esiti;
- rilevazione: stesso esito per regola e congiunto del percorso diretto, sul codice marcato,
  sulla baseline e sul codice umano; punteggio = frazione delle regole invariate;
- ordine e chiave con tutte le regole, prova dei limiti di Sourcery, chiamate a Sourcery per
  campione.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any

import pytest
from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.methods.base import DetectInput, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter

from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.oracle]

METHOD = "acw"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle" / METHOD


def _require(path: Path) -> None:
    if path.is_file():
        return
    message = f"{path} not found: run scripts/oracle_method.sh {METHOD}"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    for name in ("inputs", "original"):
        _require(FIXTURES / f"{name}.json")
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("inputs", "original")
    }


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(["paths=cluster", "stage=watermark"])


@pytest.fixture(scope="module")
def adapter(cfg: ExperimentConfig) -> MethodAdapter:
    _require(cfg.envs[METHOD].python)  # interprete dell'ambiente del metodo (solo sul cluster)
    return make_adapter(cfg, METHOD)


def test_embedding_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    from bench_contracts import WorkerItem

    inputs, original = data["inputs"], data["original"]
    request = adapter.build_request(
        "embed",
        cfg.models_catalog[inputs["model_id"]],
        adapter.default_hparams(),
        "k1",
        {},
        "",
        tmp_path / "embed",
        "cpu",
    )
    assert request.key == inputs["key"] and request.hparams == inputs["native_hparams"]
    items = [
        WorkerItem(
            item_id=e["id"],
            language="python",
            seed=0,
            prompt_messages=None,
            code=e["code"],
            context_prompt=None,
            expected_message=None,
            n=1,
        )
        for e in original["embed"]
    ]
    run = adapter.run_worker(request, items)
    assert run.missing == 0
    by_id = {r.item_id: r for r in run.results}
    print("\nid                          status  applicable rules  sourcery calls/sample")
    for e in original["embed"]:
        result = by_id[e["id"]]
        assert result.extra["rules"] == original["rules"]
        if e["changed"]:
            assert result.status == "OK" and result.code == e["marked"], e["id"]
        else:
            assert result.status == "FAILED", e["id"]
        print(
            f"{e['id']:27s} {result.status:6s}  {result.extra['n_applicable']:16d}  "
            f"{result.extra['sourcery_calls_per_sample']:.2f}"
        )


def test_detection_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    assert isinstance(adapter, Detector)
    inputs, original = data["inputs"], data["original"]
    detections = original["detections"]
    results = adapter.detect_codes(
        [DetectInput(d["id"], "python", d["code"]) for d in detections],
        adapter.default_hparams(),
        cfg.models_catalog[inputs["model_id"]],
        "k1",
        {},
        tmp_path / "detect",
        "cpu",
    )
    scores: dict[str, list[float]] = {}
    print("\nid                          kind      score  rules changed  joint unchanged")
    for d, result in zip(detections, results, strict=True):
        assert result.item_id == d["id"] and result.status == "OK", (d["id"], result.error)
        assert result.extra["rule_unchanged"] == d["rule_unchanged"], d["id"]
        assert result.native_decision == d["joint_unchanged"], d["id"]
        expected = sum(d["rule_unchanged"].values()) / len(d["rule_unchanged"])
        assert result.score == pytest.approx(expected)
        scores.setdefault(d["kind"], []).append(float(result.score))
        print(
            f"{d['id']:27s} {d['kind']:8s} {result.score:6.3f}  "
            f"{result.extra['n_rules_changed']:13d}  {d['joint_unchanged']}"
        )
    for kind, values in scores.items():
        print(f"mean score {kind}: {statistics.fmean(values):.3f} (n={len(values)})")


def test_order_key_and_sourcery_probe(data: dict[str, Any]) -> None:
    """Diagnostica (richieste dell'utente): ordine delle regole, chiave, limiti di Sourcery."""
    original = data["original"]
    order = original["order_check"]
    print(f"\nsourcery {original['sourcery_version']}; rules k1 = {original['rules']}")
    print(f"rules k2 = {original['rules_k2']}")
    print(
        f"same set: {order['same_rule_set']}, same order: {order['same_order']}, "
        f"outputs differing between k1 and k2: {order['differing_outputs']}/{order['n']}"
    )
    timings = original["sourcery_timings"]
    print(
        f"sourcery calls in the direct path: {len(timings)}, "
        f"seconds per call: median {statistics.median(timings):.2f}, max {max(timings):.2f}"
    )
    probe = original["probe"]
    seconds = [p["seconds"] for p in probe]
    codes = sorted({p["returncode"] for p in probe})
    print(
        f"probe: {len(probe)} consecutive calls, seconds first/median/last "
        f"{seconds[0]:.2f}/{statistics.median(seconds):.2f}/{seconds[-1]:.2f}, return codes {codes}"
    )
    suspicious = [
        p["output_tail"]
        for p in probe
        if any(w in p["output_tail"].lower() for w in ("limit", "quota", "rate", "exceeded"))
    ]
    print(f"messages mentioning limits: {len(suspicious)}")
    for tail in suspicious[:3]:
        print("  ", tail.replace("\n", " ")[:200])
    assert codes == [0], "sourcery returned errors during the probe"
