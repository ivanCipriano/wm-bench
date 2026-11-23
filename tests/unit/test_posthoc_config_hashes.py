"""``scripts/posthoc_config_hashes.py``: il commit della libreria entra nel ``config_hash``, così le
celle del giro di capacità (libreria a4e6972) hanno percorsi diversi da quelle dei giri precedenti
(475d4cc, 488fb5a), da quelle del giro 4 (0664817) e da quelle del giro P3; gli hash scritti in
``configs/hpo/multimark.yaml`` (i 2 punti neurali di P3) sono quelli calcolati, e quelli del giro 4
si ricalcolano dai loro iperparametri (``ROUND4_POINTS``).

Gli hash dei giri precedenti scritti nei log (per esempio 59a04735824c) non si ricalcolano più:
da a4e6972 l'adapter passa anche ``names.bits_per_name`` e ``canonical.same_kind``, che entrano
negli iperparametri nativi."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from bench.methods.base import MethodAdapter
from tests.unit.test_inventory_scripts import _load

REPO_ROOT = Path(__file__).resolve().parents[2]

ROUND2 = "475d4cc89ca5ef8a8571a0246f4eb4c0d653edf0"
ROUND3 = "a4e6972b1ee5cc1027cd4fdadf0fd41f67a67d4c"
# Hash del giro di capacità (scripts/run_p2_round3.sh li controlla).
ROUND3_HASHES = {
    "mcss": "b521002e09f5",
    "multimark": "3c1bd11758fc",
    "single_letter": "4937d9ddd78a",
    "A": "770f999adefb",
    "B": "cb79b2fcb4e6",
    "C": "cf7e4c9f106e",
    "D": "37400fdcd589",
}


def _hashes(script: object, monkeypatch: pytest.MonkeyPatch, commit: str,
            capsys: pytest.CaptureFixture[str]) -> list[list[str]]:  # fmt: skip
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: commit)
    assert script.main(["paths=local"]) == 0  # type: ignore[attr-defined]
    return [line.split(" ", 2) for line in capsys.readouterr().out.splitlines()]


# Punti del giro di capacità (non più nella griglia, che dal giro 4 ha solo i suoi 12 punti).
ROUND3_POINTS = {
    "single_letter": {"names.single_letter": "extend_except_loop"},
    "A": {"canonical.same_kind": "binding_order"},
    "B": {"names.bits_per_name": 2},
    "C": {"names.single_letter": "extend_except_loop", "canonical.same_kind": "binding_order",
          "names.bits_per_name": 2},
    "D": {"names.single_letter": "extend_except_loop", "canonical.same_kind": "binding_order",
          "names.bits_per_name": 2, "names.include_params": True},
}  # fmt: skip


def _adapters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, commit: str) -> tuple[Any, Any]:
    from bench.config.builder import load_experiment
    from bench.pipeline.stages.watermark import make_adapter

    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: commit)
    cfg = load_experiment(["paths=local", "stage=watermark"])
    return make_adapter(cfg, "mcss"), make_adapter(cfg, "multimark")


def test_round3_hashes_are_recomputed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Gli hash del giro di capacità si ricalcolano identici con la libreria a4e6972 (anche dopo le
    chiavi di 0664817, che spente non entrano negli iperparametri nativi) e sono diversi da quelli
    del secondo giro."""
    mc, mm = _adapters(monkeypatch, tmp_path, ROUND3)
    got = {"mcss": mc.config_hash({}), "multimark": mm.config_hash({})}
    got.update({k: mm.config_hash(hp) for k, hp in ROUND3_POINTS.items()})
    assert got == ROUND3_HASHES
    old = {mm.config_hash(hp) for hp in ({}, *ROUND3_POINTS.values())}
    _, mm2 = _adapters(monkeypatch, tmp_path, ROUND2)
    assert not old & {mm2.config_hash(hp) for hp in ({}, *ROUND3_POINTS.values())}


def test_script_checks_the_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    script = _load("posthoc_config_hashes")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: ROUND2)
    assert script.main(["--expect-commit", ROUND3, "paths=local"]) == 1
    assert "apply_patches.sh multimark" in capsys.readouterr().err


@pytest.mark.parametrize("script_name", ["run_p2_round3.sh", "run_p2_snake_dev.sh"])
def test_round3_script_checks_the_same_hashes(script_name: str) -> None:
    """``scripts/run_p2_round3.sh`` e ``scripts/run_p2_snake_dev.sh`` (T1.6 sulle stesse
    configurazioni) controllano il commit e gli hash di questo test."""
    text = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
    assert f"EXPECTED={ROUND3}" in text
    for name, var in (("mcss", "MCSS"), ("multimark", "MM"), ("single_letter", "MM_SL"),
                      ("A", "MM_A"), ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text


# Rifacimento di mcss con la libreria corretta (scripts/run_p2_mcssfix.sh li controlla).
MCSSFIX = "3c47a1fee86f4f37535e45aa6ca09787d56881be"
MCSSFIX_HASHES = {"mcss": "ae63f16de28f", "multimark": "614c0c80c5c5"}
MCSS_ROUND2 = "1d0c64c5124e"


def test_mcssfix_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Con 3c47a1f cambiano gli hash di mcss **e** di multimark: multimark non si rilancia (il
    suo nuovo default non deve comparire negli script), mcss sì."""
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    script = _load("posthoc_config_hashes")
    rows = _hashes(script, monkeypatch, MCSSFIX, capsys)
    assert [h for _, h, _ in rows[:2]] == list(MCSSFIX_HASHES.values())
    assert not {h for _, h, _ in rows} & set(ROUND3_HASHES.values())


def test_mcssfix_script_launches_only_mcss() -> None:
    """``scripts/run_p2_mcssfix.sh``: libreria e hash giusti, configurazioni lette con i vecchi
    hash, ogni invio solo per mcss (``methods=[mcss]``, mai ``hpo.grid``) e solo su dev."""
    text = (REPO_ROOT / "scripts" / "run_p2_mcssfix.sh").read_text(encoding="utf-8")
    assert f"EXPECTED={MCSSFIX}" in text and f"MCSS_NEW={MCSSFIX_HASHES['mcss']}" in text
    assert f"MCSS={ROUND3_HASHES['mcss']}" in text and f"MCSS_R2={MCSS_ROUND2}" in text
    for name, var in (("multimark", "MM"), ("single_letter", "MM_SL"), ("A", "MM_A"),
                      ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert re.findall(r'"methods=\[[^\]]*\]"', code) == ['"methods=[mcss]"']
    assert "hpo.grid" not in code and MCSSFIX_HASHES["multimark"] not in text
    assert re.findall(r'"splits=\[[^\]]*\]"', code) == ['"splits=[dev]"']
    # ogni invio parte da CLEAN, ATTACKED o IMP (tutti derivati da CLEAN)
    submits = [ln for ln in code.splitlines() if ln.strip().startswith("submit --jobs")]
    assert submits and all(re.search(r'"\$\{(CLEAN|ATTACKED|IMP)\[@\]\}"', s) for s in submits)
    assert 'ATTACKED=("${CLEAN[@]}"' in code and 'IMP=("${CLEAN[@]}"' in code


# Giro 4 (pipeline v1.3.9 e v1.3.10, libreria 0664817; scripts/run_p2_round4.sh li controlla).
ROUND4 = "06648176cee2cf125ae8446381e4049929894cf9"
ROUND4_HASHES = {
    "B": "a8a296957377", "B_SFX": "042968dc17df", "B_SFX_CL": "317adaa840ee",
    "B_SFX_CLZ": "a2d195001771", "C": "19e744a3b406", "C_SFX": "71d9d5e6160d",
    "C_SFX_CL": "a8fadfc4757a", "C_SFX_CLZ": "fb24f51f5d15", "D": "b4cd11f13a07",
    "D_SFX": "f4fb209f9226", "D_SFX_CL": "f765c166de34", "D_SFX_CLZ": "cb29e1d7b6b8",
}  # fmt: skip
# Default con la libreria 0664817: non si lanciano nel giro 4.
ROUND4_DEFAULTS = {"mcss": "ef3ec0dfdb26", "multimark": "9f4d71ebe223"}


# Punti del giro 4 (non più nella griglia, che dal giro P3 ha solo i suoi 2 punti).
_C = {"names.single_letter": "extend_except_loop", "canonical.same_kind": "binding_order",
      "names.bits_per_name": 2}  # fmt: skip
_BASES = {"B": ROUND3_POINTS["B"], "C": _C, "D": {**_C, "names.include_params": True}}
_VARIANTS = {"": {}, "_SFX": {"names.suffix_by_type": "on"},
             "_SFX_CL": {"names.suffix_by_type": "on", "canonical.natural_forms": "const_left"},
             "_SFX_CLZ": {"names.suffix_by_type": "on",
                          "canonical.natural_forms": "const_left_zero"}}  # fmt: skip
ROUND4_POINTS = {f"{b}{v}": {**hp, **opt} for b, hp in _BASES.items()
                 for v, opt in _VARIANTS.items()}  # fmt: skip


def test_round4_hashes_are_recomputed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Gli hash del giro 4 si ricalcolano identici con la libreria 0664817 (anche dopo le chiavi
    del proponente neurale, che con ``static`` non entrano negli iperparametri nativi); B, C e D
    con le opzioni spente sono i punti del giro di capacità."""
    mc, mm = _adapters(monkeypatch, tmp_path, ROUND4)
    assert {k: mm.config_hash(hp) for k, hp in ROUND4_POINTS.items()} == ROUND4_HASHES
    assert {"mcss": mc.config_hash({}), "multimark": mm.config_hash({})} == ROUND4_DEFAULTS
    assert [ROUND4_POINTS[k] for k in ("B", "C", "D")] == [ROUND3_POINTS[k] for k in "BCD"]
    explicit = {**ROUND4_POINTS["B"], "names.proposer": "static"}
    assert mm.config_hash(explicit) == ROUND4_HASHES["B"]


def test_round4_script_launches_only_the_grid() -> None:
    """``scripts/run_p2_round4.sh``: libreria e hash giusti, solo lo sweep di multimark (mai il
    default, né mcss), solo su dev; riferimenti letti con gli hash già calcolati."""
    text = (REPO_ROOT / "scripts" / "run_p2_round4.sh").read_text(encoding="utf-8")
    assert f"EXPECTED={ROUND4}" in text
    for name, h in ROUND4_HASHES.items():
        assert f"R4_{name}={h}" in text
    assert f"MCSS_NEW={MCSSFIX_HASHES['mcss']}" in text
    for name, var in (("mcss", "MCSS"), ("multimark", "MM"), ("single_letter", "MM_SL"),
                      ("A", "MM_A"), ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert re.findall(r'"methods=\[[^\]]*\]"', code) == ['"methods=[multimark]"']
    assert code.count('"hpo.grid=multimark"') == 1
    assert re.findall(r'"splits=\[[^\]]*\]"', code) == ['"splits=[dev]"']
    assert not set(ROUND4_DEFAULTS.values()) & set(re.findall(r"[0-9a-f]{12}", text))
    submits = [ln for ln in code.splitlines() if ln.strip().startswith("submit --jobs")]
    assert submits and all(re.search(r'"\$\{(CLEAN|ATTACKED|IMP)\[@\]\}"', s) for s in submits)
    assert 'ATTACKED=("${CLEAN[@]}"' in code and 'IMP=("${CLEAN[@]}"' in code
    assert 'ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1,T1.6]"' in code


# Giro P3 (pipeline v1.3.13, libreria con il proponente neurale; scripts/run_p2_p3.sh li controlla).
P3 = "b2b6c18ea5303ab134f84f11d8b1de1c2bb5ec5a"
P3_HASHES = {"B": "d54101565a0b", "D": "e80cde8924cb"}
# Default e B, D statici con la stessa libreria: non si lanciano nel giro P3.
P3_STATIC = {"mcss": "1d3087c73439", "multimark": "36a0251d191f", "B": "e52b040770df",
             "D": "3fed1f1c1e24"}  # fmt: skip


def test_p3_hashes_are_recomputed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """I 2 punti di P3 (B e D del giro 4 con ``names.proposer=neural``, non più nella griglia dal
    giro P4) si ricalcolano identici con la libreria b2b6c18, come i default."""
    mc, mm = _adapters(monkeypatch, tmp_path, P3)
    neural = {"names.proposer": "neural"}
    grid = [mm.config_hash({**ROUND4_POINTS[k], **neural}) for k in ("B", "D")]
    assert grid == list(P3_HASHES.values())
    assert [mc.config_hash({}), mm.config_hash({})] == [P3_STATIC["mcss"], P3_STATIC["multimark"]]
    assert {k: mm.config_hash(ROUND4_POINTS[k]) for k in ("B", "D")} == {
        k: P3_STATIC[k] for k in ("B", "D")}  # fmt: skip
    old = set(ROUND3_HASHES.values()) | set(MCSSFIX_HASHES.values()) | set(ROUND4_HASHES.values())
    assert not (set(grid) | set(P3_STATIC.values())) & old


def test_p3_neural_keys_in_the_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Modello (repository e snapshot) e ``dtype`` entrano nell'hash; percorso, cache e commit
    passato allo shim no (il commit c'è già)."""
    from bench.domain.ids import config_hash as raw_hash

    _, mm = _adapters(monkeypatch, tmp_path, P3)
    hp = {**ROUND4_POINTS["B"], "names.proposer": "neural"}
    native = mm.to_native_hparams(hp)
    hashed = {k: v for k, v in native.items()
              if k not in ("registry_seed", "names_neural_path", "names_neural_cache",
                           "names_neural_library")}  # fmt: skip
    assert hashed["names_neural_model"] == (
        "Qwen/Qwen2.5-Coder-1.5B-Instruct@2e1fd397ee46e1388853d2af2c993145b0f1098a"
    )
    assert hashed["names_neural_dtype"] == "float32"
    assert mm.config_hash(hp) == raw_hash(hashed, P3) == P3_HASHES["B"]


def test_p3_script_launches_only_the_grid() -> None:
    """``scripts/run_p2_p3.sh``: libreria e hash giusti, solo lo sweep di multimark (mai il
    default, né mcss, né B e D statici con la libreria nuova), solo su dev, inserimento su GPU in
    un solo job; riferimenti letti con gli hash già calcolati."""
    text = (REPO_ROOT / "scripts" / "run_p2_p3.sh").read_text(encoding="utf-8")
    assert f"EXPECTED={P3}" in text
    assert f"P3_B={P3_HASHES['B']}" in text and f"P3_D={P3_HASHES['D']}" in text
    for name, h in ROUND4_HASHES.items():
        assert f"R4_{name}={h}" in text
    assert f"MCSS_NEW={MCSSFIX_HASHES['mcss']}" in text
    for name, var in (("mcss", "MCSS"), ("multimark", "MM"), ("single_letter", "MM_SL"),
                      ("A", "MM_A"), ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert re.findall(r'"methods=\[[^\]]*\]"', code) == ['"methods=[multimark]"']
    assert code.count('"hpo.grid=multimark"') == 1
    assert re.findall(r'"splits=\[[^\]]*\]"', code) == ['"splits=[dev]"']
    assert not set(P3_STATIC.values()) & set(re.findall(r"[0-9a-f]{12}", text))
    submits = [ln for ln in code.splitlines() if ln.strip().startswith("submit --jobs")]
    assert submits and all(re.search(r'"\$\{(CLEAN|ATTACKED|IMP)\[@\]\}"', s) for s in submits)
    assert 'ATTACKED=("${CLEAN[@]}"' in code and 'IMP=("${CLEAN[@]}"' in code
    assert 'ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1,T1.6]"' in code
    # inserimento: un solo job GPU (B e D in sequenza per cella), regola delle risorse solo lì
    gpu = "resources.gpu_methods_by_stage.watermark.multimark=true"
    assert code.count(gpu) == 1
    (wm,) = (s for s in submits if "stage=watermark" in s)
    assert wm.strip().startswith("submit --jobs 1 ") and '"$GPU_EMBED"' in wm
    assert f'GPU_EMBED="{gpu}"' in code


# Giro P4 su dev (P4+P5 ridotte, v1.3.11 e v1.3.14; scripts/run_p4_dev.sh li controlla), libreria
# b2b6c18 con il proponente statico: B e D da congelare e 5 ablazioni rispetto a B (§6.3).
P4_POINTS = {
    "B": ROUND4_POINTS["B"],
    "D": ROUND4_POINTS["D"],
    "V0": {"names.variant": "V0"},
    "V1": {"names.variant": "V1"},
    "POS": {**ROUND4_POINTS["B"], "linking": "positional"},
    "MASK": {**ROUND4_POINTS["B"], "mask": "off"},
    "PAPER": {**ROUND4_POINTS["B"], "canonical": "paper"},
}
P4_HASHES = {"B": "e52b040770df", "D": "3fed1f1c1e24", "V0": "ebeb5a0753cd",
             "V1": "40e5e9455cea", "POS": "271087ba99b6", "MASK": "16de27361bad",
             "PAPER": "65eb9730301a"}  # fmt: skip
P4_MCSS = "1d3087c73439"
# Celle già misurate con lo stesso inserimento (impercettibilità non rimisurata).
P4_SAME = {"mcss": MCSSFIX_HASHES["mcss"], "B": ROUND4_HASHES["B"], "D": ROUND4_HASHES["D"]}


def test_p4_grid_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La griglia ha B, D e le 5 ablazioni nell'ordine, con gli hash nei commenti; mcss, B e D
    sono quelli dati dall'utente; le ablazioni cambiano un solo fattore rispetto a B (V0 e V1
    tornano a 1 bit per nome, l'unico valore ammesso con quelle varianti)."""
    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    script = _load("posthoc_config_hashes")
    rows = _hashes(script, monkeypatch, P3, capsys)
    assert [h for _, h, _ in rows[:2]] == [P4_MCSS, P3_STATIC["multimark"]]
    grid = [h for _, h, label in rows if label.startswith("grid:")]
    assert grid == list(P4_HASHES.values())
    assert [P4_HASHES["B"], P4_HASHES["D"]] == [P3_STATIC["B"], P3_STATIC["D"]]
    text = (REPO_ROOT / "configs" / "hpo" / "multimark.yaml").read_text(encoding="utf-8")
    assert re.findall(r"^  - \{.*\}\s+# ([0-9a-f]{12})$", text, flags=re.M) == grid
    from bench.hpo.grid import load_grid

    assert [p.hparams for p in load_grid("multimark").points] == list(P4_POINTS.values())
    _, mm = _adapters(monkeypatch, tmp_path, P3)
    with pytest.raises(Exception, match="bits_per_name=2 requires names.variant=V2"):
        mm.config_hash({**ROUND4_POINTS["B"], "names.variant": "V0"})
    old = (set(ROUND3_HASHES.values()) | set(MCSSFIX_HASHES.values())
           | set(ROUND4_HASHES.values()) | set(P3_HASHES.values()))  # fmt: skip
    assert not set(grid) & old


def test_hpo_points_filters_the_sweep(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``hpo.points`` (stringhe, anche da riga di comando) tiene solo quegli hash dello sweep; un
    hash che non è nella griglia ferma la pianificazione."""
    from bench.config.builder import load_experiment
    from bench.domain.errors import ConfigError
    from bench.pipeline.stage import CellPlanner
    from bench.pipeline.stages.imperceptibility import AdversarialLogregStage

    monkeypatch.setenv("WMB_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("SOURCERY_TOKEN", "fake-token-for-tests")
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: P3)
    ablations = [P4_HASHES[k] for k in ("V0", "V1", "POS", "MASK", "PAPER")]
    base = ["paths=local", "stage=adversarial_logreg", "methods=[multimark]",
            "hpo.grid=multimark", "hpo.use_selected=false", "levels=[L1]", "splits=[dev]",
            "languages=[python]", "models=[qwen25_coder_7b]"]  # fmt: skip
    every = CellPlanner(load_experiment(base)).cells_for(AdversarialLogregStage)
    assert [c.config_hash for c in every] == list(P4_HASHES.values())
    quoted = "hpo.points=[" + ",".join(f"'{h}'" for h in ablations) + "]"
    some = CellPlanner(load_experiment([*base, quoted])).cells_for(AdversarialLogregStage)
    assert [c.config_hash for c in some] == ablations
    bad = load_experiment([*base, "hpo.points=['000000000000']"])
    with pytest.raises(ConfigError, match="hpo.points not in the multimark grid"):
        CellPlanner(bad).cells_for(AdversarialLogregStage)


def test_p4_script() -> None:
    """``scripts/run_p4_dev.sh``: libreria e hash giusti, multimark solo come sweep della griglia
    e mcss solo come default del suo metodo, solo su dev, impercettibilità solo per le ablazioni,
    attacchi con T1.6 e T2.5, riferimenti letti con gli hash già calcolati."""
    text = (REPO_ROOT / "scripts" / "run_p4_dev.sh").read_text(encoding="utf-8")
    assert f"EXPECTED={P3}" in text and f"P4_MCSS={P4_MCSS}" in text
    for name, h in P4_HASHES.items():
        assert f"P4_{name}={h}" in text
    for name, h in ROUND4_HASHES.items():
        assert f"R4_{name}={h}" in text
    assert f"P3_B={P3_HASHES['B']}" in text and f"P3_D={P3_HASHES['D']}" in text
    assert f"MCSS_NEW={MCSSFIX_HASHES['mcss']}" in text
    for name, var in (("mcss", "MCSS"), ("multimark", "MM"), ("single_letter", "MM_SL"),
                      ("A", "MM_A"), ("B", "MM_B"), ("C", "MM_C"), ("D", "MM_D")):  # fmt: skip
        assert f"{var}={ROUND3_HASHES[name]}" in text
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert sorted(re.findall(r'"methods=\[[^\]]*\]"', code)) == [
        '"methods=[mcss]"',
        '"methods=[multimark]"',
    ]
    assert code.count('"hpo.grid=multimark"') == 1
    assert re.findall(r'"splits=\[[^\]]*\]"', code) == ['"splits=[dev]"']
    assert 'ATTACKS="attacks=[T1.4,T1.5,T2.1,T2.4,T4.1,T1.6,T2.5]"' in code
    submits = [ln for ln in code.splitlines() if ln.strip().startswith("submit --jobs")]
    allowed = r'"\$\{(CLEAN|CLEAN_MCSS|ATTACKED|ATTACKED_MCSS|IMP)\[@\]\}"'
    others = [s for s in submits if not re.search(allowed, s)]
    assert others == ['        submit --jobs 1 stage=function_pools "languages=[python]"']
    # impercettibilità solo per le 5 ablazioni, mai per mcss
    imp = [s for s in submits if "perplexity" in s or "adversarial" in s or "imperceptibility" in s]
    assert len(imp) == 4 and all('"${IMP[@]}"' in s for s in imp)
    assert 'IMP=("${CLEAN[@]}" "attacks=[]" "hpo.points=[$ABLATIONS]")' in code
    assert "ABLATIONS=\"'$P4_V0','$P4_V1','$P4_POS','$P4_MASK','$P4_PAPER'\"" in code
    # mcss, B e D: impercettibilità dalle celle con lo stesso inserimento, identità controllata
    assert "--same p4_mcss=mcss_new_3c47a1f p4_B=r4_B p4_D=r4_D" in code
    assert P4_SAME == {"mcss": "ae63f16de28f", "B": "a8a296957377", "D": "b4cd11f13a07"}
    assert '--pair "mcss:$P4_MCSS:$MCSS_NEW:p4_mcss"' in code
    assert '--pair "multimark:$P4_B:$R4_B:p4_B"' in code
    assert '--pair "multimark:$P4_D:$R4_D:p4_D"' in code
    # nessun job di più del limite: ogni fase controlla i posti prima di inviare
    phases = re.findall(r"^    ([a-z0-9_]+)\)$", code, flags=re.M)
    assert phases[:5] == ["function_pools", "canonical", "table5", "watermark", "identity"]
    assert 'MAX_JOBS="${MAX_JOBS:-10}"' in code
