"""Oracle di MCGMark (SPEC §21.4; audit ``docs/audit/mcgmark.md``). Cluster, nodo NVIDIA.

Prerequisiti (``scripts/oracle_method.sh mcgmark``):
1. ``tests/oracle/make_oracle_inputs.py mcgmark`` (bench-core) → ``inputs.json``;
2. ``tests/oracle/mcgmark_original.py --variant original`` sul codice con la sola patch 0000
   → ``original.json`` (γ dinamico del repository);
3. ``tests/oracle/mcgmark_original.py --variant patched`` sul codice del framework
   (patch 0000-0002) → ``patched.json``.

Verifiche:
- lo shim riproduce il percorso diretto con le patch: stesso testo, stesse posizioni e bit
  inseriti; decoding effettivo neutro;
- isolamento (decisione dell'utente): il campione generato da solo o dopo altri è identico,
  quindi lo stato globale di modulo viene azzerato davvero;
- rilevazione: stessi bit e stessi messaggi del percorso diretto; l'estrazione dal codice
  estratto con D4 ritrova le posizioni e i bit registrati dal processor in generazione (primo
  ciclo), con il tasso di inserimento riuscito;
- generazione con il prefill della fence (D20) e baseline gemella senza processor;
- messaggio noto: su almeno un campione si recuperano tutti i 12 bit;
- patch 0001 (D18): con lo stesso testo forzato e gli stessi logit, i logit restituiti sono
  identici sui passi con γ = 0,5 nel codice originale; le differenze stanno solo sui passi con
  γ = 0,25 e sui passi di correzione che ne dipendono (bit di correzione del ciclo). Il test
  stampa anche la quota di passi con γ = 0,25 e i bit recuperati con originale e patch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from bench.config.builder import load_experiment
from bench.config.schema import ExperimentConfig
from bench.methods.base import DetectInput, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter

from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.oracle, pytest.mark.gpu]

METHOD = "mcgmark"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle" / METHOD
ROUND, INFO = 24, 12


def _require(path: Path) -> None:
    if path.is_file():
        return
    message = f"{path} not found: run scripts/oracle_method.sh {METHOD}"
    if os.environ.get("WMB_REQUIRE_DATA") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _matches(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b, strict=False) if x == y)


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    for name in ("inputs", "original", "patched"):
        _require(FIXTURES / f"{name}.json")
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("inputs", "original", "patched")
    }


@pytest.fixture(scope="module")
def cfg() -> ExperimentConfig:
    return load_experiment(["paths=cluster", "stage=watermark"])


@pytest.fixture(scope="module")
def adapter(cfg: ExperimentConfig) -> MethodAdapter:
    _require(cfg.envs[METHOD].python)  # interprete dell'ambiente del metodo (solo sul cluster)
    return make_adapter(cfg, METHOD)


def _embed(
    adapter: MethodAdapter,
    cfg: ExperimentConfig,
    inputs: dict[str, Any],
    prompts: list[dict[str, Any]],
    run_dir: Path,
) -> Any:
    from bench_contracts import WorkerItem

    model = cfg.models_catalog[inputs["model_id"]]
    request = adapter.build_request(
        "embed", model, adapter.default_hparams(), "k1", inputs["decoding"], "", run_dir, "cuda:0"
    )
    assert request.key == inputs["key"] and request.hparams == inputs["native_hparams"]
    items = [
        WorkerItem(
            item_id=f"{p['problem_key']}#0",
            language=p["language"],
            seed=p["seed"],
            prompt_messages=p["messages"],
            code=None,
            context_prompt=None,
            expected_message=p["message"],
            n=1,
        )
        for p in prompts
    ]
    run = adapter.run_worker(request, items)
    assert run.missing == 0, run
    return {r.item_id.rpartition("#")[0]: r for r in run.results}, run


def test_generation_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    inputs, patched = data["inputs"], data["patched"]
    by_key, run = _embed(adapter, cfg, inputs, inputs["prompts"], tmp_path / "embed")
    print("\nproblem         status   embedded  generated")
    for gen in patched["generations"]:
        result = by_key[gen["problem_key"]]
        assert result.raw_output == gen["new_text"], gen["problem_key"]
        assert result.extra["embedded_bits"] == gen["internal"]["embedded_bits"]
        assert result.extra["embedded_tokens"] == gen["internal"]["tokens"]
        expected = "OK" if len(gen["internal"]["embedded_bits"]) >= ROUND else "PARTIAL"
        assert result.status == expected, gen["problem_key"]
        print(
            f"{gen['problem_key']:15s} {result.status:8s} {result.extra['n_embedded']:8d} "
            f"{result.extra['n_generated_tokens']:10d}"
        )
    effective = next(
        r.extra["generation_config"] for r in run.results if "generation_config" in r.extra
    )
    assert effective["temperature"] == 0.2 and effective["top_p"] == 0.95
    assert effective["max_new_tokens"] == inputs["decoding"]["max_new_tokens"] == 512
    assert effective["top_k"] == 0 and effective["repetition_penalty"] == 1.0
    assert effective["do_sample"] is True and effective["num_return_sequences"] == 1


def test_samples_are_isolated(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Il campione generato da solo, o dopo altri in ordine inverso, è identico (stato azzerato)."""
    inputs, patched = data["inputs"], data["patched"]
    expected = {g["problem_key"]: g["new_text"] for g in patched["generations"]}
    reversed_run, _ = _embed(
        adapter, cfg, inputs, list(reversed(inputs["prompts"])), tmp_path / "reversed"
    )
    alone, _ = _embed(adapter, cfg, inputs, inputs["prompts"][-1:], tmp_path / "alone")
    for key, text in expected.items():
        assert reversed_run[key].raw_output == text, key
    last = inputs["prompts"][-1]["problem_key"]
    assert alone[last].raw_output == expected[last]


def test_detection_matches_the_direct_path(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    assert isinstance(adapter, Detector)
    inputs, patched = data["inputs"], data["patched"]
    model = cfg.models_catalog[inputs["model_id"]]
    detections = patched["detections"]
    results = adapter.detect_codes(
        [
            DetectInput(
                d["id"],
                d["language"],
                d["code"],
                prompt_messages=d["messages"],
                expected_message=d["message"],
            )
            for d in detections
        ],
        adapter.default_hparams(),
        model,
        "k1",
        inputs["decoding"],
        tmp_path / "detect",
        "cuda:0",
    )
    print("\nid                          eligible rounds  score  first round  message")
    for d, result in zip(detections, results, strict=True):
        assert result.item_id == d["id"]
        assert result.extra["bits"] == d["bits"] and result.extra["rounds"] == d["rounds"]
        if not d["rounds"]:
            assert result.status == "FAILED", d["id"]
            print(f"{d['id']:26s} {len(d['tokens']):8d}      0      -")
            continue
        assert result.status == "OK", (d["id"], result.error)
        assert result.decoded_message == d["rounds"][0]
        assert result.score == float(_matches(d["rounds"][0], d["message"]))
        assert result.native_decision == (d["rounds"][0] == d["message"])
        print(
            f"{d['id']:26s} {len(d['tokens']):8d} {len(d['rounds']):6d} {result.score:6.0f}  "
            f"{d['rounds'][0]}  {d['message']}"
        )


def test_extraction_from_code_recovers_the_embedding(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Sul codice estratto con la regola D4 (come nella pipeline) la macchina a stati rigiocata
    ritrova le posizioni e i bit registrati dal processor in generazione."""
    from bench.domain.models import Problem
    from bench.generation.code_extractor import FencedCodeExtractor

    assert isinstance(adapter, Detector)
    inputs, patched = data["inputs"], data["patched"]
    problems = {p["problem_key"]: Problem.model_validate(p["problem"]) for p in inputs["prompts"]}
    extractor = FencedCodeExtractor()
    codes = {}
    for gen in patched["generations"]:
        code, ok = extractor.extract(gen["new_text"], problems[gen["problem_key"]])
        assert ok, gen["problem_key"]
        codes[gen["problem_key"]] = code
    results = adapter.detect_codes(
        [
            DetectInput(key, "python", code, expected_message=gen["message"])
            for (key, code), gen in zip(codes.items(), patched["generations"], strict=True)
        ],
        adapter.default_hparams(),
        cfg.models_catalog[inputs["model_id"]],
        "k1",
        inputs["decoding"],
        tmp_path / "detect",
        "cuda:0",
    )
    checked = embedded_ok = 0
    print("\nproblem         embedded  replayed  equal prefix  equal bits (first round)")
    for gen, result in zip(patched["generations"], results, strict=True):
        internal = gen["internal"]
        replay_tokens, replay_bits = result.extra["tokens"], result.extra["bits"]
        prefix = 0
        for a, b in zip(internal["tokens"], replay_tokens, strict=False):
            if a != b:
                break
            prefix += 1
        same_bits = internal["bits"][:ROUND] == replay_bits[:ROUND]
        print(
            f"{gen['problem_key']:15s} {len(internal['tokens']):8d} {len(replay_tokens):9d} "
            f"{prefix:13d}  {same_bits}"
        )
        if len(internal["tokens"]) >= ROUND:
            checked += 1
            embedded_ok += 1
            assert internal["tokens"][:ROUND] == replay_tokens[:ROUND], gen["problem_key"]
            assert same_bits, gen["problem_key"]
    n = len(patched["generations"])
    print(f"embedding with a complete round (OK): {embedded_ok}/{n} ({embedded_ok / n:.0%})")
    assert checked > 0, "no sample with a complete round"


def test_twin_baseline_differs_only_by_the_watermark(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Baseline gemella (D20): stessa pipeline senza processor; parte con il prefill."""
    import dataclasses

    from bench_contracts import WorkerItem

    inputs = data["inputs"]
    model = cfg.models_catalog[inputs["model_id"]]
    request = adapter.build_request(
        "embed", model, {}, "k1", inputs["decoding"], "", tmp_path / "twin", "cuda:0"
    )
    request = dataclasses.replace(request, hparams={**request.hparams, "watermark": False})
    prompt = inputs["prompts"][0]
    item = WorkerItem(
        item_id=f"{prompt['problem_key']}#0",
        language="python",
        seed=prompt["seed"],
        prompt_messages=prompt["messages"],
        code=None,
        context_prompt=None,
        expected_message=prompt["message"],
        n=1,
    )
    run = adapter.run_worker(request, [item])
    result = run.results[0]
    assert result.status == "OK" and result.extra["watermark"] is False
    assert result.extra["n_embedded"] == 0
    assert result.raw_output.startswith(inputs["native_hparams"]["assistant_prefill"])
    marked = data["patched"]["generations"][0]["new_text"]
    print(f"\ntwin == watermarked: {result.raw_output == marked}")


def test_known_message_is_recovered(data: dict[str, Any]) -> None:
    """Bit recuperati sul primo ciclo: codice originale (γ dinamico) e codice con patch 0001."""
    print("\nproblem         message       original  patched")
    best = 0
    for variant in ("original", "patched"):
        assert data[variant]["variant"] == variant
    pairs = zip(data["original"]["generations"], data["patched"]["generations"], strict=True)
    for orig, patc in pairs:
        cells = []
        for gen in (orig, patc):
            rounds = gen["internal"]["rounds"]
            cells.append(f"{_matches(rounds[0], gen['message']):2d}/12" if rounds else "  -  ")
        if patc["internal"]["rounds"]:
            best = max(best, _matches(patc["internal"]["rounds"][0], patc["message"]))
        print(f"{orig['problem_key']:15s} {orig['message']}  {cells[0]:>8s}  {cells[1]:>7s}")
    _print_error_breakdown(data["patched"]["generations"])
    assert best == INFO, "no sample recovers the full 12-bit message"


def _print_error_breakdown(generations: list[dict[str, Any]]) -> None:
    """Diagnostica (audit §5): errori del primo ciclo per bit del messaggio e bit di correzione.

    I bit inseriti del ciclo sono il messaggio (posizioni 0-11) e i bit di correzione del
    repository (12-23, ``robust_list``: 1 se il token più probabile era fuori dalla green list).
    """
    counts: dict[tuple[str, str], list[int]] = {}
    for gen in generations:
        embedded, bits = gen["internal"]["embedded_bits"], gen["internal"]["bits"]
        if len(embedded) < ROUND or len(bits) < ROUND:
            continue
        for j in range(INFO):
            m, r = embedded[j], embedded[INFO + j]
            decoded = "1" if bits[j] != bits[INFO + j] else "0"
            cell = counts.setdefault((m, r), [0, 0])
            cell[0] += 1
            cell[1] += int(decoded != m)
    print("message bit  correction bit  positions  wrong")
    for (m, r), (n, wrong) in sorted(counts.items()):
        print(f"{m:>11s}  {r:>14s}  {n:9d}  {wrong:5d}")


def _allowed_differences(steps: list[dict[str, Any]]) -> set[int]:
    """Passi in cui la patch 0001 può cambiare i logit: γ = 0,25, o correzione dipendente."""
    allowed: set[int] = set()
    last_marking: dict[int, int] = {}  # posizione → ultimo passo che l'ha marcata
    for i, step in enumerate(steps):
        if step["gamma"] != 0.5:
            allowed.add(i)
        position = step["tele_before"]
        if step["tele_after"] == position + 1:
            if position % ROUND >= INFO:
                partner = last_marking.get(position - INFO)
                if partner is not None and steps[partner]["gamma"] != 0.5:
                    allowed.add(i)
            last_marking[position] = i
    return allowed


def test_fixed_gamma_patch_changes_only_the_dynamic_gamma_steps(data: dict[str, Any]) -> None:
    total_steps = total_low = marking_steps = marking_low = 0
    print("\nproblem         steps  gamma=0.25  differing  allowed")
    pairs = zip(data["original"]["generations"], data["patched"]["generations"], strict=True)
    for orig, patc in pairs:
        a, b = orig["forced_steps"], patc["forced_steps"]
        assert len(a) == len(b) and len(a) > 0, orig["problem_key"]
        assert all(s["gamma"] == 0.5 for s in b)
        # La marcatura dipende solo dai token forzati, non da γ.
        assert [(s["tele_before"], s["tele_after"]) for s in a] == [
            (s["tele_before"], s["tele_after"]) for s in b
        ]
        differing = {
            i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x["digest"] != y["digest"]
        }
        allowed = _allowed_differences(a)
        assert differing <= allowed, (orig["problem_key"], sorted(differing - allowed)[:10])
        print(
            f"{orig['problem_key']:15s} {len(a):5d} {sum(s['gamma'] != 0.5 for s in a):11d} "
            f"{len(differing):10d} {len(allowed):8d}"
        )
        # Quota di γ = 0,25 nella generazione del codice originale (D18).
        gen = orig["steps"]
        total_steps += len(gen)
        total_low += sum(s["gamma"] != 0.5 for s in gen)
        marking = [s for s in gen if s["tele_after"] == s["tele_before"] + 1]
        marking_steps += len(marking)
        marking_low += sum(s["gamma"] != 0.5 for s in marking)
    print(
        f"original generation: gamma=0.25 on {total_low}/{total_steps} steps "
        f"({total_low / max(total_steps, 1):.1%}), on {marking_low}/{marking_steps} marking steps "
        f"({marking_low / max(marking_steps, 1):.1%})"
    )
