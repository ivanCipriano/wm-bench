"""Oracle di PromptMark (SPEC §21.4; audit ``docs/audit/promptmark.md``). Cluster, nodo NVIDIA.

Prerequisiti (``scripts/oracle_method.sh promptmark``, anche in parti parallele):
1. fase ``promptmark_freq`` (lista di frequenza, D8);
2. ``make_oracle_inputs.py promptmark`` → ``inputs.json`` (5 HumanEval, L1);
3. ``promptmark_original.py`` (percorso diretto senza shim e senza limiti) → ``original.json``;
4. ``promptmark_patch_check.py`` (CPU) → ``patch_check.json``.

Verifiche:
- lo shim riproduce il percorso diretto: stessa risposta scelta e stesse iterazioni (codice, esiti
  degli esempi, p). Il percorso diretto esegue gli esempi **senza** i limiti dello shim:
  l'uguaglianza mostra che i limiti non cambiano gli esiti (decisione dell'utente). Decoding
  effettivo neutro;
- per campione: iterazioni, ripetizioni per watermark e per correttezza, esempi eseguiti, e se
  il codice della regola D4 coincide con quello scelto dal metodo (ultimo blocco ``python``);
- rilevazione: stessi punteggi −log10 p (tolleranza 1e-9), stessi conteggi, stessi casi falliti;
- patch 0002 identica all'originale con i default; identificatori della lista di frequenza uguali a
  quelli della procedura degli autori;
- sovrapposizione fra gli esempi del ciclo e i test di EvalPlus (base e plus) su L1 Python.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.methods.base import DetectInput, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter

from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.oracle, pytest.mark.gpu]

METHOD = "promptmark"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle" / METHOD
FILES = ("inputs", "original", "patch_check")
TOLERANCE = 1e-9


def _require(path: Path) -> None:
    if path.is_file():
        return
    message = f"{path} not found: run scripts/oracle_method.sh {METHOD}"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    for name in FILES:
        _require(FIXTURES / f"{name}.json")
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")) for name in FILES
    }


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(["paths=cluster", "stage=watermark"])


@pytest.fixture(scope="module")
def adapter(cfg: ExperimentConfig) -> MethodAdapter:
    _require(cfg.envs[METHOD].python)  # interprete dell'ambiente del metodo (solo sul cluster)
    return make_adapter(cfg, METHOD)


ITERATION_FIELDS = ("correctness", "tests_passed", "tests_failed", "meets_z", "code", "response")


def test_generation_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    from bench.domain.models import Problem
    from bench.generation.code_extractor import FencedCodeExtractor
    from bench_contracts import WorkerItem

    inputs, original = data["inputs"], data["original"]
    model = cfg.models_catalog[inputs["model_id"]]
    request = adapter.build_request(
        "embed",
        model,
        adapter.default_hparams(),
        "k1",
        inputs["decoding"],
        inputs["prompts"][0]["messages"][0]["content"],
        tmp_path / "embed",
        "cuda:0",
    )
    assert request.key == inputs["key"] and request.hparams == inputs["native_hparams"]
    items = [
        WorkerItem(
            item_id=f"{p['problem_key']}#0",
            language="python",
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
    by_key = {r.item_id.rpartition("#")[0]: r for r in run.results}
    problems = {p["problem_key"]: p for p in inputs["prompts"]}
    extractor = FencedCodeExtractor()
    print(
        "\nproblem         iters  retry wm  retry corr  examples  selected  D4 == method"
        "  marked  free ids  distinct seeds/responses/codes"
    )
    marked: dict[str, list[bool]] = {}
    for gen in original["generations"]:
        result = by_key[gen["problem_key"]]
        extra = result.extra
        assert result.raw_output == gen["raw_output"], gen["problem_key"]
        assert extra["example_tests"] == gen["examples"]
        assert len(extra["iterations"]) == len(gen["iterations"]), gen["problem_key"]
        for mine, theirs in zip(extra["iterations"], gen["iterations"], strict=True):
            for field in ITERATION_FIELDS:
                assert mine[field] == theirs[field], (gen["problem_key"], field)
            assert abs(mine["p_exact"] - theirs["p_exact"]) <= TOLERANCE
        assert extra["selected_iteration"] == gen["selected_iteration"]
        # Semi: quelli effettivamente usati, diversi a ogni iterazione e uguali al percorso diretto.
        assert extra["seeds"] == gen["seeds"] and len(set(gen["seeds"])) == len(gen["seeds"])
        assert len(gen["seeds"]) == len(gen["iterations"])
        assert extra["n_free_identifiers"] == gen["n_free_identifiers"]
        assert extra["watermarked"] == gen["watermarked"]
        marked.setdefault(gen["problem_key"].split("/")[0], []).append(gen["watermarked"])
        its = gen["iterations"]
        distinct = (
            f"{len(set(gen['seeds']))}/{len({i['response'] for i in its})}"
            f"/{len({i['code'] for i in its})}"
        )
        problem = Problem.model_validate(_problem_of(cfg, gen["problem_key"]))
        code, ok = extractor.extract(result.raw_output, problem)
        same = ok and code.strip() == gen["selected_code"].strip()
        print(
            f"{gen['problem_key']:15s} {extra['n_iterations']:5d}  "
            f"{extra['n_retries_watermark']:8d}  "
            f"{extra['n_retries_correctness']:10d}  {extra['n_example_tests']:8d}  "
            f"{extra['selected_iteration']!s:>8s}  {same!s:12s}  {gen['watermarked']!s:6s}  "
            f"{gen['n_free_identifiers']!s:>8s}  {distinct}"
        )
        assert problems[gen["problem_key"]]["seed"] == gen["seed"]
    effective = next(
        r.extra["generation_config"] for r in run.results if "generation_config" in r.extra
    )
    assert effective["temperature"] == 0.2 and effective["top_p"] == 0.95
    assert effective["max_new_tokens"] == inputs["decoding"]["max_new_tokens"] == 512
    assert effective["do_sample"] is True and effective["num_return_sequences"] == 1
    for family, flags in marked.items():
        print(f"watermark embedded ({family}): {sum(flags)}/{len(flags)}")
    green, size, gamma = original["green_letters"], original["green_size"], original["gamma"]
    print(f"green list: {green} (size {size}), gamma {gamma:.4f}")


def _problem_of(cfg: ExperimentConfig, key: str) -> dict[str, Any]:
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.store.artifact_store import ArtifactStore

    table = ArtifactStore(cfg.paths.artifacts).read_table(problems_ref("L1", "python"))
    row = table[table["problem_key"] == key].iloc[0].to_dict()
    return row


def test_detection_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    assert isinstance(adapter, Detector)
    inputs, detections = data["inputs"], data["original"]["detections"]
    results = adapter.detect_codes(
        [DetectInput(d["id"], d["language"], d["code"]) for d in detections],
        adapter.default_hparams(),
        cfg.models_catalog[inputs["model_id"]],
        "k1",
        inputs["decoding"],
        tmp_path / "detect",
        "cpu",
    )
    print("\nid                          score  tokens  green  status")
    for d, result in zip(detections, results, strict=True):
        assert result.item_id == d["id"]
        if d["error"] or d["token_count"] == 0:
            assert result.status == "FAILED", d["id"]
            print(f"{d['id']:26s}      -       -      -  FAILED")
            continue
        assert result.status == "OK", (d["id"], result.error)
        assert result.score is not None and abs(result.score - d["score"]) <= TOLERANCE, d["id"]
        assert result.extra["token_count"] == d["token_count"]
        assert result.extra["green_count"] == d["green_count"]
        assert result.native_decision == d["is_watermarked"]
        print(
            f"{d['id']:26s} {result.score:6.2f}  {d['token_count']:6d}  {d['green_count']:5d}  OK"
        )


def test_patch_0002_and_frequency_procedure(data: dict[str, Any]) -> None:
    from bench.data.promptmark_freq import identifiers

    check = data["patch_check"]
    assert check["mismatches"] == [], check["mismatches"]
    differences = []
    for item in data["inputs"]["codes"]:
        theirs = check["identifiers"][item["id"]]
        mine = identifiers(item["code"])
        if (theirs is None) != (mine is None) or (mine is not None and set(theirs) != mine):
            differences.append(item["id"])
    print(f"\nidentifier sets compared: {len(data['inputs']['codes'])}, differences: {differences}")
    assert differences == []


def _call_inputs(test: str, entry_point: str) -> list[Any]:
    """Argomenti (valori letterali) delle chiamate a ``entry_point`` in un'asserzione."""
    found = []
    try:
        tree = ast.parse(test)
    except SyntaxError:  # non dovrebbe accadere: gli esempi estratti sono validi
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == entry_point:
            try:
                found.append([ast.literal_eval(arg) for arg in node.args])
            except (ValueError, SyntaxError):
                continue
    return found


def test_example_overlap_with_evalplus(cfg: ExperimentConfig) -> None:
    """Quanti esempi del ciclo coincidono con input dei test di EvalPlus (audit §8)."""
    for name in ("humanevalplus", "mbppplus"):
        _require(cfg.datasets[name].file("data"))  # dataset del cluster
    from bench.domain.models import Problem
    from bench.generation.prompt_builder import PromptBuilder
    from bench.pipeline.stages.generate_baseline import problems_ref
    from bench.store.artifact_store import ArtifactStore

    sys.path.insert(0, str(REPO_ROOT / "shims"))
    from bench_shims.promptmark.examples import prompt_examples

    store = ArtifactStore(cfg.paths.artifacts)
    table = store.read_table(problems_ref("L1", "python"))
    builder = PromptBuilder.from_config(cfg.prompt)
    evalplus: dict[str, dict[str, Any]] = {}
    for name in ("humanevalplus", "mbppplus"):
        with cfg.datasets[name].file("data").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                evalplus[row["task_id"]] = row
    print(
        "\ndataset        split  problems  with ex.  examples  in base  in plus  problems overlap"
    )
    for (dataset, split), group in table.groupby(["dataset", "split"]):
        n_prob = with_ex = n_ex = in_base = in_plus = overlap = 0
        for row in group.to_dict(orient="records"):
            problem = Problem.model_validate(row)
            n_prob += 1
            examples = prompt_examples(builder.render_user(problem))
            ref = evalplus[str(problem.test_ref)]
            base = [list(x) for x in ref.get("base_input") or []]
            plus = [list(x) for x in ref.get("plus_input") or []]
            hit = False
            if examples:
                with_ex += 1
            for test in examples:
                n_ex += 1
                calls = _call_inputs(test, str(problem.entry_point))
                b = any(c in base for c in calls)
                p = any(c in plus for c in calls)
                in_base += b
                in_plus += p
                hit = hit or b or p
            overlap += hit
        print(
            f"{dataset:14s} {split:5s} {n_prob:9d} {with_ex:9d} {n_ex:9d} {in_base:8d} {in_plus:8d}"
            f" {overlap:17d}"
        )
