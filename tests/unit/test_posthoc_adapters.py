"""Adapter e shim di ``mcss`` e ``multimark`` (PIPELINE_metodo.md di multimark, P2 passi 1–2).

Adapter: profili di §3.11 uguali a ``configs/method/*.yaml``, valori ammessi uguali a quelli della
libreria, validazione delle chiavi, ``config_hash`` senza ``registry_seed``, messaggio di 24 bit,
``bits_correct`` da ``extra``. Shim: ``PosthocShim`` con la libreria del submodule, in processo,
su un frammento sintetico (round trip) e su codice senza siti.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from bench_contracts import WorkerItem, WorkerRequest, WorkerResult

from bench.config.schema import ExperimentConfig
from bench.domain.errors import ConfigError
from bench.domain.ids import config_hash
from bench.methods.adapters.mcss import McssAdapter
from bench.methods.adapters.multimark import (
    CHOICES,
    NATIVE_NAMES,
    MultimarkAdapter,
    PosthocMultibitAdapter,
    flatten,
    message_bits,
)
from bench.methods.base import CodeEmbedder, Detector, MethodAdapter
from bench.pipeline.stages.watermark import make_adapter
from tests.conftest import REPO_ROOT
from tests.unit.test_inventory_scripts import LIBRARY, needs_library

METHODS = ("mcss", "multimark")


@pytest.fixture(autouse=True)
def fake_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MethodAdapter, "source_commit", lambda self: "c" * 40)


def _adapter(cfg: ExperimentConfig, method: str) -> PosthocMultibitAdapter:
    adapter = make_adapter(cfg, method)
    assert isinstance(adapter, PosthocMultibitAdapter)
    return adapter


# ---------------------------------------------------------------- adapter


@pytest.mark.parametrize("method", METHODS)
def test_capabilities(cfg: ExperimentConfig, method: str) -> None:
    a = _adapter(cfg, method)
    assert isinstance(a, CodeEmbedder) and isinstance(a, Detector)
    # multimark: GPU nell'inserimento solo con il proponente neurale (embed_device)
    assert (a.gpu_for_embed, a.gpu_for_detect, a.message_bits) == (method == "multimark", False, 24)
    assert a.embed_device({}) == "cpu"
    assert a.secondary_scores == {"nreg16": "score_nreg16", "nreg1024": "score_nreg1024",
                                  "decode": "score_decode"}  # fmt: skip
    assert a.supports("python") and not a.supports("java")
    assert method not in cfg.methods  # nel catalogo, non nella lista di default
    assert a.env_name == "posthoc"
    assert a.source().pythonpath == REPO_ROOT / "build" / "patched" / "multimark"


@pytest.mark.parametrize("method", METHODS)
def test_yaml_profile_is_the_adapter_profile(cfg: ExperimentConfig, method: str) -> None:
    a = _adapter(cfg, method)
    assert flatten(a.default_hparams()) == a.PROFILE
    assert set(a.PROFILE) == set(NATIVE_NAMES)
    assert a.config_hash(a.default_hparams()) == a.config_hash({})


def test_profiles_follow_the_pipeline_table() -> None:
    mm, mc = MultimarkAdapter.PROFILE, McssAdapter.PROFILE
    assert (mm["linking"], mm["mask"], mm["canonical"], mm["names.variant"]) == (
        "function_channel", "on", "kind", "V2")  # fmt: skip
    assert (mm["names.proposer"], mm["names.include_params"], mm["channels"]) == (
        "static", False, "extended")  # fmt: skip
    assert (mm["code.matrix"], mm["names.single_letter"], mm["names.module_level"]) == (
        "dmin4", "exclude", True)  # fmt: skip
    assert (mc["linking"], mc["mask"], mc["canonical"], mc["names.variant"]) == (
        "positional", "off", "paper", "V1")  # fmt: skip
    assert (mc["names.include_params"], mc["channels"], mc["names.module_level"]) == (
        True, "mcss8", True)  # fmt: skip
    assert (mc["code.matrix"], mc["names.single_letter"]) == ("random", "extend_except_loop")


@needs_library
def test_choices_and_names_match_the_library() -> None:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark import config as mc
        from multimark.channels import (
            CANONICAL_MODES,
            CHANNEL_SET_NAMES,
            NATURAL_FORMS_MODES,
            SAME_KIND_MODES,
        )
        from multimark.codec import MATRICES
        from multimark.linking import LINKING_MODES
        from multimark.names import (
            BITS_PER_NAME,
            NAME_VARIANTS,
            SINGLE_LETTER_MODES,
            SUFFIX_BY_TYPE_MODES,
        )
    finally:
        sys.path.remove(str(LIBRARY))
    assert CHOICES["t"] == mc.T_VALUES and CHOICES["mask"] == mc.MASK_MODES
    assert CHOICES["names.proposer"] == mc.PROPOSERS and CHOICES["voting"] == mc.VOTING_MODES
    assert CHOICES["linking"] == LINKING_MODES and CHOICES["canonical"] == CANONICAL_MODES
    assert CHOICES["names.variant"] == NAME_VARIANTS
    assert CHOICES["names.single_letter"] == SINGLE_LETTER_MODES
    assert CHOICES["names.bits_per_name"] == BITS_PER_NAME
    assert CHOICES["canonical.same_kind"] == SAME_KIND_MODES
    assert CHOICES["canonical.natural_forms"] == NATURAL_FORMS_MODES
    assert CHOICES["names.suffix_by_type"] == SUFFIX_BY_TYPE_MODES
    assert CHOICES["channels"] == CHANNEL_SET_NAMES and CHOICES["code.matrix"] == MATRICES
    fields = set(mc.Config.__dataclass_fields__)
    assert set(NATIVE_NAMES.values()) | {"message_bits", "registry_seed"} == fields
    # i profili dell'adapter sono quelli della libreria (da a4e6972 anche il modulo: decisione O
    # chiusa con true)
    a_mm = {NATIVE_NAMES[k]: v for k, v in MultimarkAdapter.PROFILE.items()}
    a_mc = {NATIVE_NAMES[k]: v for k, v in McssAdapter.PROFILE.items()}
    lib_mm, lib_mc = vars(mc.multimark_base()), vars(mc.mcss_profile())
    assert {k: lib_mc[k] for k in a_mc} == a_mc
    assert {k: lib_mm[k] for k in a_mm} == a_mm


def test_native_hparams_and_validation(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")
    native = a.to_native_hparams({"names": {"variant": "V1"}, "t": 4})
    assert native["names_variant"] == "V1" and native["t"] == 4
    assert native["message_bits"] == 24 and native["registry_seed"] == cfg.global_seed
    assert native == a.to_native_hparams({"names.variant": "V1", "t": 4})
    bad: list[dict[str, Any]] = [
        {"t": 3},
        {"names.variant": "V3"},
        {"names.include_params": 1},  # bool, non intero
        {"mask": True},
        {"color": "red"},
        {"names.proposer": "neural", "names.variant": "V1"},  # il proponente serve solo a V2/V2b
        {"voting": "weighted"},  # senza channel_weights
        {"channel_weights": 3},
    ]
    for hp in bad:
        with pytest.raises(ConfigError):
            a.to_native_hparams(hp)
    a.to_native_hparams({"voting": "weighted", "channel_weights": "w.json"})
    for hp in ({"canonical.natural_forms": "const_left", "canonical": "paper"},
               {"names.suffix_by_type": "on", "canonical": "paper"},
               {"canonical.natural_forms": "zero"}, {"names.suffix_by_type": True}):  # fmt: skip
        with pytest.raises(ConfigError):
            a.to_native_hparams(hp)


def test_round4_options(cfg: ExperimentConfig) -> None:
    """``canonical.natural_forms`` e ``names.suffix_by_type`` (libreria 0664817): spente non
    entrano negli iperparametri nativi (hash invariati a parità di commit), accese sì; ``mcss``
    le rifiuta."""
    mm, mc = _adapter(cfg, "multimark"), _adapter(cfg, "mcss")
    for a in (mm, mc):
        native = a.to_native_hparams({})
        assert "canonical_natural_forms" not in native and "names_suffix_by_type" not in native
        explicit = {"canonical.natural_forms": "off", "names.suffix_by_type": "off"}
        assert a.config_hash(explicit) == a.config_hash({})
    for hp in ({"canonical.natural_forms": "const_left"},
               {"canonical.natural_forms": "const_left_zero"}, {"names.suffix_by_type": "on"},
               {"canonical.natural_forms": "off", "names.suffix_by_type": "on"}):  # fmt: skip
        native = mm.to_native_hparams(hp)
        for key, value in hp.items():
            if value != "off":
                assert native[NATIVE_NAMES[key]] == value
        assert mm.config_hash(hp) != mm.config_hash({})
        with pytest.raises(ConfigError, match="fixed"):
            mc.to_native_hparams(hp)
    both = {"canonical.natural_forms": "const_left", "names.suffix_by_type": "on"}
    assert len({mm.config_hash(both), mm.config_hash({"names.suffix_by_type": "on"}),
                mm.config_hash({"canonical.natural_forms": "const_left"})}) == 3  # fmt: skip


def test_neural_proposer(cfg: ExperimentConfig) -> None:
    """``names.proposer=neural`` (P3, MD31): solo multimark; modello e ``dtype`` negli iperparametri
    nativi e nell'hash, percorso, cache e commit no; con ``static`` gli iperparametri nativi sono
    quelli di prima; inserimento su GPU."""
    mm, mc = _adapter(cfg, "multimark"), _adapter(cfg, "mcss")
    spec = cfg.models_catalog["qwen25_coder_1p5b"]
    assert spec.hf_repo == "Qwen/Qwen2.5-Coder-1.5B-Instruct" and spec.path.name == spec.snapshot
    assert spec.snapshot == "2e1fd397ee46e1388853d2af2c993145b0f1098a"
    b = {"names.bits_per_name": 2}
    static = mm.to_native_hparams(b)
    assert not {k for k in static if k.startswith("names_neural")}
    assert mm.config_hash({**b, "names.proposer": "static"}) == mm.config_hash(b)
    native = mm.to_native_hparams({**b, "names.proposer": "neural"})
    assert native["names_proposer"] == "neural"
    assert native["names_neural_model"] == f"{spec.hf_repo}@{spec.snapshot}"
    assert native["names_neural_dtype"] == "float32"
    assert native["names_neural_path"] == str(spec.path)
    assert native["names_neural_cache"] == str(mm.neural_cache_dir())
    assert mm.neural_cache_dir() == Path(cfg.paths.artifacts) / "_cache" / "multimark_neural_names"
    assert native["names_neural_library"] == "c" * 40
    neural_hash = mm.config_hash({**b, "names.proposer": "neural"})
    assert neural_hash != mm.config_hash(b)
    hashed = {k: v for k, v in native.items()
              if k not in ("registry_seed", "names_neural_path", "names_neural_cache",
                           "names_neural_library")}  # fmt: skip
    assert neural_hash == config_hash(hashed, "c" * 40)
    # un'altra snapshot o un altro dtype cambiano l'hash; un altro percorso no
    other = spec.model_copy(update={"snapshot": "f" * 40, "path": spec.path.parent / ("f" * 40)})
    moved = spec.model_copy(update={"path": Path("/elsewhere") / spec.snapshot})
    for changed, same in ((other, False), (spec.model_copy(update={"dtype": "bfloat16"}), False),
                          (moved, True)):  # fmt: skip
        mm.neural_model = lambda changed=changed: changed  # type: ignore[method-assign,misc]
        assert (mm.config_hash({**b, "names.proposer": "neural"}) == neural_hash) is same
    del mm.neural_model
    assert mm.embed_device({**b, "names.proposer": "neural"}) == "cuda:0"
    assert mm.embed_device(b) == "cpu"
    with pytest.raises(ConfigError, match="fixed"):
        mc.to_native_hparams({"names.proposer": "neural"})
    with pytest.raises(ConfigError, match="V2/V2b"):
        mm.to_native_hparams({"names.proposer": "neural", "names.variant": "V1b"})
    mm.to_native_hparams({"names.proposer": "neural", "names.variant": "V2b"})


def test_config_hash_excludes_registry_seed(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")
    native = a.to_native_hparams({})
    expected = config_hash({k: v for k, v in native.items() if k != "registry_seed"}, "c" * 40)
    assert a.config_hash({}) == expected
    assert a.config_hash({}) != config_hash(native, "c" * 40)
    assert a.config_hash({}) != _adapter(cfg, "mcss").config_hash({})


def test_messages(cfg: ExperimentConfig) -> None:
    mm, mc = _adapter(cfg, "multimark"), _adapter(cfg, "mcss")
    model = cfg.models_catalog["qwen25_coder_7b"]
    m = mm.message_for(model, "humaneval/0", "python", 3)
    assert m is not None and len(m) == 24 and set(m) <= {"0", "1"}
    assert m == message_bits(cfg.global_seed, "multimark", "humaneval/0", "python",
                             model.model_id, 3)  # fmt: skip
    assert m == mm.message_for(model, "humaneval/0", "python", 3)
    assert m != mc.message_for(model, "humaneval/0", "python", 3)  # etichetta per metodo
    assert mm.message_for(model, "humaneval/0", "python", None) is not None  # negativi


def test_bits_correct_comes_from_extra(cfg: ExperimentConfig) -> None:
    a = _adapter(cfg, "multimark")

    def res(status: str, extra: dict[str, Any], score: float | None = 3.0) -> WorkerResult:
        return WorkerResult(item_id="x", sample_index=None, status=status, raw_output=None,
                            code=None, score=score, native_decision=None,
                            decoded_message="0" * 24, extra=extra)  # fmt: skip

    assert a.bits_correct(res("OK", {"bits_correct": 21})) == 21  # non int(score)
    assert a.bits_correct(res("OK", {"bits_correct": None})) is None
    assert a.bits_correct(res("FAILED", {"bits_correct": 21})) is None


# ---------------------------------------------------------------- shim


def _request(cfg: ExperimentConfig, method: str, op: str) -> WorkerRequest:
    a = _adapter(cfg, method)
    model = cfg.models_catalog["qwen25_coder_7b"]
    return a.build_request(op, model, {}, "k1", {}, "", REPO_ROOT, "cpu")


def _shim(cfg: ExperimentConfig, method: str, op: str) -> Any:
    sys.path.insert(0, str(LIBRARY))
    sys.path.insert(0, str(REPO_ROOT / "shims"))
    try:
        if method == "mcss":
            from bench_shims.mcss.__main__ import McssShim as Shim
        else:
            from bench_shims.multimark.__main__ import MultimarkShim as Shim
        shim = Shim()
        shim.setup(_request(cfg, method, op))
        return shim
    finally:
        sys.path.remove(str(LIBRARY))
        sys.path.remove(str(REPO_ROOT / "shims"))


def _item(code: str, message: str | None) -> WorkerItem:
    return WorkerItem(item_id="s1", language="python", seed=0, prompt_messages=None, code=code,
                      context_prompt=None, expected_message=message, n=1)  # fmt: skip


@needs_library
@pytest.mark.parametrize("method", METHODS)
def test_shim_round_trip(cfg: ExperimentConfig, method: str) -> None:
    sys.path.insert(0, str(LIBRARY))
    try:
        from multimark.synthetic import generate
    finally:
        sys.path.remove(str(LIBRARY))
    code = generate(7, n_statements=40, n_functions=2)
    message = "011010011100101011110000"
    shim = _shim(cfg, method, "embed")
    (embedded,) = shim.embed(_item(code, message))
    assert embedded.status == "OK" and embedded.code != code and embedded.error is None
    assert embedded.extra["n_sites"] >= 32 and embedded.extra["reason"] is None
    detected = shim.detect(_item(embedded.code, message))
    assert detected.status == "OK" and detected.decoded_message == message
    assert detected.extra["bits_correct"] == 24 and detected.score is not None
    assert detected.score > 5
    for field in ("n_votes", "n_sites_by_channel", "score_nreg16", "best_nreg1024", "sites",
                  "decode_status", "n_erasures", "library_version"):  # fmt: skip
        assert field in detected.extra
    assert detected.native_decision is True
    # Sul codice non marcato il punteggio è basso.
    plain = shim.detect(_item(code, message))
    assert plain.status == "OK" and plain.score < detected.score


@needs_library
def test_shim_failures(cfg: ExperimentConfig) -> None:
    shim = _shim(cfg, "multimark", "embed")
    (none,) = shim.embed(_item("def f(*args):\n    return 3\n", "0" * 24))
    assert none.status == "FAILED" and none.error == "no_sites"
    (broken,) = shim.embed(_item("def (:\n", "0" * 24))
    assert broken.status == "FAILED" and broken.error == "syntax_error"
    det = shim.detect(_item("def f(*args):\n    return 3\n", "0" * 24))
    assert det.status == "FAILED" and det.error == "no_sites" and det.score is None
    with pytest.raises(ValueError, match="expected_message"):
        shim.detect(_item("x = 1\n", None))


# ---------------------------------------------------------------- 0664817 contro 3c47a1f

PREVIOUS = "3c47a1fee86f4f37535e45aa6ca09787d56881be"  # libreria di mcss ae63f16de28f
# Inserimento in un processo a sé (le due librerie hanno lo stesso nome di pacchetto).
EMBED_SCRIPT = """
import json, sys
library, shims, method, payload = sys.argv[1:5]
sys.path[:0] = [library, shims]
from bench_contracts import WorkerItem, WorkerRequest
if method == "mcss":
    from bench_shims.mcss.__main__ import McssShim as Shim
else:
    from bench_shims.multimark.__main__ import MultimarkShim as Shim
import multimark
assert multimark.__file__.startswith(library), multimark.__file__
data = json.loads(open(payload, encoding="utf-8").read())
shim = Shim()
shim.setup(WorkerRequest.from_dict(data["request"]))
out = []
for item in data["items"]:
    (res,) = shim.embed(WorkerItem.from_dict(item))
    out.append({"status": str(res.status), "error": res.error, "code": res.code})
sys.stdout.write(json.dumps(out))
"""


def _fixture_programs() -> list[str]:
    """Programmi della fixture ``tiny``: prompt e soluzione canonica di HumanEval+."""
    path = REPO_ROOT / "tests" / "fixtures" / "tiny" / "humanevalplus.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r["prompt"] + r["canonical_solution"] for r in rows]


ROUND4_LIBRARY = "06648176cee2cf125ae8446381e4049929894cf9"  # libreria del giro 4


def _previous_library(dest: Path, commit: str = PREVIOUS) -> Path:
    """Copia della libreria a ``commit`` (``git archive`` dal submodule), o skip se manca."""
    proc = subprocess.run(["git", "-C", str(LIBRARY), "archive", "--format=tar", commit],
                          capture_output=True, check=False)  # fmt: skip
    if proc.returncode != 0:
        pytest.skip(f"multimark {commit[:7]} not available in the submodule")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def _embed_with(library: Path, cfg: ExperimentConfig, method: str, hp: dict[str, Any],
                codes: list[str], tmp: Path) -> list[dict[str, Any]]:  # fmt: skip
    a = _adapter(cfg, method)
    model = cfg.models_catalog["qwen25_coder_7b"]
    request = a.build_request("embed", model, hp, "k1", {}, "", REPO_ROOT, "cpu")
    items = [
        WorkerItem(
            item_id=f"s{i}",
            language="python",
            seed=0,
            prompt_messages=None,
            code=code,
            context_prompt=None,
            n=1,
            expected_message=message_bits(
                cfg.global_seed, method, f"p{i}", "python", model.model_id, i
            ),
        )
        for i, code in enumerate(codes)
    ]
    payload = tmp / f"{method}_{library.name}.json"
    data = {"request": request.to_dict(), "items": [it.to_dict() for it in items]}
    payload.write_text(json.dumps(data), encoding="utf-8")
    argv = [sys.executable, "-c", EMBED_SCRIPT, str(library), str(REPO_ROOT / "shims"), method,
            str(payload)]  # fmt: skip
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    result: list[dict[str, Any]] = json.loads(proc.stdout)
    return result


@needs_library
@pytest.mark.parametrize(
    ("method", "hp"), [("mcss", {}), ("multimark", {"names.bits_per_name": 2})]
)
def test_embed_is_identical_to_3c47a1f_with_options_off(
    cfg: ExperimentConfig, tmp_path: Path, method: str, hp: dict[str, Any]
) -> None:
    """Con ``canonical.natural_forms`` e ``names.suffix_by_type`` spenti l'inserimento della
    libreria 0664817 è identico byte per byte a quello di 3c47a1f (mcss e multimark B)."""
    previous = _previous_library(tmp_path / "mm_3c47a1f")
    codes = _fixture_programs()
    new = _embed_with(LIBRARY, cfg, method, hp, codes, tmp_path)
    old = _embed_with(previous, cfg, method, hp, codes, tmp_path)
    assert len(new) == len(old) == len(codes)
    changed = [r for r, c in zip(new, codes, strict=True) if r["code"] not in (None, c)]
    assert changed  # almeno un campione marcato: il confronto non è vuoto
    for a, b in zip(new, old, strict=True):
        assert (a["status"], a["error"]) == (b["status"], b["error"])
        assert (a["code"] or "").encode("utf-8") == (b["code"] or "").encode("utf-8")


_D = {"names.single_letter": "extend_except_loop", "canonical.same_kind": "binding_order",
      "names.bits_per_name": 2, "names.include_params": True}  # fmt: skip


@needs_library
@pytest.mark.parametrize(
    "hp",
    [{"names.bits_per_name": 2}, _D,
     {**_D, "names.suffix_by_type": "on", "canonical.natural_forms": "const_left_zero"}],
)  # fmt: skip
def test_static_embed_is_identical_to_round4_library(
    cfg: ExperimentConfig, tmp_path: Path, hp: dict[str, Any]
) -> None:
    """Con il proponente statico l'inserimento della libreria di P3 è identico byte per byte a
    quello di 0664817 (giro 4): r4_B, r4_D e r4_D_sfx_clz."""
    previous = _previous_library(tmp_path / "mm_0664817", ROUND4_LIBRARY)
    codes = _fixture_programs()
    new = _embed_with(LIBRARY, cfg, "multimark", hp, codes, tmp_path)
    old = _embed_with(previous, cfg, "multimark", hp, codes, tmp_path)
    assert [r for r, c in zip(new, codes, strict=True) if r["code"] not in (None, c)]
    for a, b in zip(new, old, strict=True):
        assert (a["status"], a["error"]) == (b["status"], b["error"])
        assert (a["code"] or "").encode("utf-8") == (b["code"] or "").encode("utf-8")
