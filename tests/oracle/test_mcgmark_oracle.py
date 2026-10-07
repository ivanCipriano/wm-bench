"""Oracle di MCGMark (SPEC §21.4; audit ``docs/audit/mcgmark.md``). Cluster, nodo NVIDIA.

Prerequisiti (``scripts/oracle_method.sh mcgmark``, anche in parti parallele):
1. ``make_oracle_inputs.py mcgmark`` → ``inputs.json`` (5 HumanEval, L1) e
   ``make_mcgmark_long_inputs.py`` → ``inputs_long.json`` (3 CodeNet esclusi dalla M9 e 6 classi
   ClassEval, ``max_new_tokens`` 1024; solo verifica, nessuna metrica);
2. ``mcgmark_original.py``: su L1 codice originale (sola 0000, γ dinamico) → ``original.json`` e
   codice del framework (0000-0003) → ``patched.json``; sul codice lungo 0000-0002 →
   ``nospeed_long.json`` e 0000-0003 → ``patched_long.json``.

Verifiche:
- lo shim riproduce il percorso diretto con le patch: stesso testo, stesse posizioni e bit
  inseriti; decoding effettivo neutro;
- isolamento (decisione dell'utente): il campione generato da solo o dopo altri è identico,
  quindi lo stato globale di modulo viene azzerato davvero;
- rilevazione: stessi bit e stessi messaggi del percorso diretto; l'estrazione dal codice
  estratto con D4 ritrova le posizioni e i bit registrati dal processor in generazione (primo
  ciclo), con il tasso di inserimento riuscito;
- generazione con il prefill della fence (D20) e baseline gemella senza processor;
- catena completa su codice lungo: siti per campione, ciclo di 24 completo, bit recuperati dal
  codice D4; messaggio noto recuperato per intero su almeno un campione; errori per bit di
  correzione (audit §5.3);
- patch 0003: testo identico carattere per carattere a 0000-0002 con lo stesso seme, speed-up;
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


FILES = ("inputs", "original", "patched", "inputs_long", "nospeed_long", "patched_long")


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


def _replay(
    inputs: dict[str, Any],
    generations: list[dict[str, Any]],
    adapter: MethodAdapter,
    cfg: ExperimentConfig,
    run_dir: Path,
) -> list[Any]:
    """Rilevazione dello shim sul codice estratto con la regola D4, come nella pipeline."""
    from bench.domain.models import Problem
    from bench.generation.code_extractor import FencedCodeExtractor

    assert isinstance(adapter, Detector)
    problems = {p["problem_key"]: Problem.model_validate(p["problem"]) for p in inputs["prompts"]}
    extractor = FencedCodeExtractor()
    items = []
    for gen in generations:
        code, ok = extractor.extract(gen["new_text"], problems[gen["problem_key"]])
        assert ok, gen["problem_key"]
        items.append(
            DetectInput(gen["problem_key"], "python", code, expected_message=gen["message"])
        )
    return adapter.detect_codes(
        items,
        adapter.default_hparams(),
        cfg.models_catalog[inputs["model_id"]],
        "k1",
        inputs["decoding"],
        run_dir,
        "cuda:0",
    )


def _prefix(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _chain_report(generations: list[dict[str, Any]], results: list[Any]) -> list[dict[str, Any]]:
    """Per campione: siti, ciclo completo, allineamento della rilevazione e bit recuperati."""
    rows = []
    print(
        "\nproblem                 sites  round  replayed  aligned  bits (generation)  "
        "bits (code, D4)  status"
    )
    for gen, result in zip(generations, results, strict=True):
        internal = gen["internal"]
        sites = len(internal["tokens"])
        replay = result.extra["tokens"]
        aligned = _prefix(internal["tokens"], replay)
        rounds = internal["rounds"]
        bits_gen = _matches(rounds[0], gen["message"]) if rounds else None
        bits_code = int(result.score) if result.status == "OK" else None
        row = {
            "key": gen["problem_key"],
            "sites": sites,
            "round": sites >= ROUND,
            "aligned_round": aligned >= ROUND,
            "bits_gen": bits_gen,
            "bits_code": bits_code,
            "status": result.status,
        }
        rows.append(row)
        print(
            f"{row['key']:23s} {sites:5d}  {'yes' if row['round'] else 'no':>5s}  "
            f"{len(replay):8d}  {aligned:7d}  "
            f"{'-' if bits_gen is None else f'{bits_gen}/12':>17s}  "
            f"{'-' if bits_code is None else f'{bits_code}/12':>15s}  {result.status}"
        )
    n = len(rows)
    complete = sum(r["round"] for r in rows)
    print(f"embedding with a complete round (OK): {complete}/{n} ({complete / n:.0%})")
    return rows


def test_l1_extraction_from_code_matches_the_embedding(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """L1 (HumanEval): la rilevazione sul codice D4 ritrova le posizioni della generazione.

    Su L1 nessun campione arriva a un ciclo completo (decisione dell'utente: PARTIAL e rilevazione
    FAILED per costruzione); si verifica l'allineamento sui siti presenti.
    """
    generations = data["patched"]["generations"]
    results = _replay(data["inputs"], generations, adapter, cfg, tmp_path / "detect")
    rows = _chain_report(generations, results)
    aligned = sum(
        _prefix(g["internal"]["tokens"], r.extra["tokens"]) == len(g["internal"]["tokens"])
        for g, r in zip(generations, results, strict=True)
    )
    print(f"fully aligned samples: {aligned}/{len(rows)}")
    assert aligned >= len(rows) - 1  # al più una differenza di ritokenizzazione
    assert all(r["status"] == "FAILED" for r in rows if not r["round"])


def test_long_code_chain_recovers_the_message(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Codice lungo (3 CodeNet + 6 ClassEval): inserimento → D4 → rilevazione → messaggio.

    Verifica dell'integrazione prima della M9 (decisione dell'utente del 7 ottobre 2026); i
    campioni non entrano in nessuna metrica.
    """
    long = data["patched_long"]
    results = _replay(data["inputs_long"], long["generations"], adapter, cfg, tmp_path / "long")
    rows = _chain_report(long["generations"], results)
    _print_error_breakdown(long["generations"])
    for row in rows:
        if row["round"] and row["aligned_round"]:
            # La rilevazione dal codice legge gli stessi bit della generazione.
            assert row["bits_code"] == row["bits_gen"], row["key"]
    assert any(r["round"] for r in rows), "no long sample reaches a complete round"
    best = max((r["bits_code"] or 0) for r in rows)
    assert best == INFO, "no long sample recovers the full 12-bit message"


def test_long_human_code_scores(
    data: dict[str, Any], adapter: MethodAdapter, cfg: ExperimentConfig, tmp_path: Path
) -> None:
    """Soluzioni canoniche di ClassEval (non marcate): siti e punteggio, solo diagnostica."""
    assert isinstance(adapter, Detector)
    inputs = data["inputs_long"]
    results = adapter.detect_codes(
        [
            DetectInput(c["id"], "python", c["code"], expected_message=c["message"])
            for c in inputs["codes"]
        ],
        adapter.default_hparams(),
        cfg.models_catalog[inputs["model_id"]],
        "k1",
        inputs["decoding"],
        tmp_path / "human",
        "cuda:0",
    )
    print("\nid                                  sites  status  score")
    for c, r in zip(inputs["codes"], results, strict=True):
        score = "-" if r.score is None else f"{r.score:.0f}/12"
        print(f"{c['id']:35s} {r.extra['n_eligible']:5d}  {r.status:6s}  {score}")


def test_patch_0003_keeps_the_text_identical_and_is_faster(data: dict[str, Any]) -> None:
    """Patch 0003: stesso seme → stesso testo carattere per carattere; speed-up misurato."""
    before, after = data["nospeed_long"], data["patched_long"]
    assert before["variant"] == "nospeed" and after["variant"] == "patched"
    total_before = total_after = 0.0
    print("\nproblem                 tokens  0000-0002 s  0000-0003 s  speed-up")
    for b, a in zip(before["generations"], after["generations"], strict=True):
        assert b["problem_key"] == a["problem_key"]
        assert a["new_text"] == b["new_text"], b["problem_key"]
        assert a["new_ids"] == b["new_ids"], b["problem_key"]
        assert a["internal"] == b["internal"], b["problem_key"]
        total_before += b["elapsed_s"]
        total_after += a["elapsed_s"]
        print(
            f"{b['problem_key']:23s} {len(b['new_ids']):6d}  {b['elapsed_s']:11.1f}  "
            f"{a['elapsed_s']:11.1f}  {b['elapsed_s'] / a['elapsed_s']:7.2f}x"
        )
    print(f"total: {total_before:.1f} s -> {total_after:.1f} s ({total_before / total_after:.2f}x)")


def test_l1_messages_report(data: dict[str, Any]) -> None:
    """L1: siti per campione e bit del primo ciclo con originale e patch (nessun ciclo su L1)."""
    print("\nproblem         message       sites  original  patched")
    for variant in ("original", "patched"):
        assert data[variant]["variant"] == variant
    pairs = zip(data["original"]["generations"], data["patched"]["generations"], strict=True)
    for orig, patc in pairs:
        cells = []
        for gen in (orig, patc):
            rounds = gen["internal"]["rounds"]
            cells.append(f"{_matches(rounds[0], gen['message']):2d}/12" if rounds else "  -  ")
        sites = len(patc["internal"]["tokens"])
        print(
            f"{orig['problem_key']:15s} {orig['message']}  {sites:5d}  {cells[0]:>8s}  "
            f"{cells[1]:>7s}"
        )
        # Testo identico fra codice originale e del framework se γ = 0,25 non compare (D18, 0003).
        if all(s["gamma"] == 0.5 for s in orig["steps"]):
            assert orig["new_text"] == patc["new_text"], orig["problem_key"]


def _print_error_breakdown(generations: list[dict[str, Any]]) -> None:
    """Diagnostica (audit §5): errori del primo ciclo per bit del messaggio e bit di correzione.

    Bit del messaggio: quello atteso (il registro ``First_watermark_token`` annota l'ultima
    posizione d'informazione con il bit di correzione, audit §5.3). Bit di correzione: posizioni
    12-23 del registro (``robust_list``: 1 se il bit è 1 e il token più probabile era fuori dalla
    green list).
    """
    counts: dict[tuple[str, str], list[int]] = {}
    for gen in generations:
        embedded, bits = gen["internal"]["embedded_bits"], gen["internal"]["bits"]
        if len(embedded) < ROUND or len(bits) < ROUND:
            continue
        for j in range(INFO):
            m, r = gen["message"][j], embedded[INFO + j]
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
