"""Adapter di ``multimark`` e base comune con ``mcss`` (PIPELINE_metodo.md di multimark, P2).

Entrambi i metodi sono profili della stessa libreria ``multimark`` (submodule
``third_party/multimark``, copia ``build/patched/multimark`` senza patch). Sono post-hoc
(``CodeEmbedder``): marcano il codice estratto dei campioni della baseline e rilevano con il
messaggio atteso. Audit: ``docs/audit/multimark.md`` e ``docs/audit/mcss.md``.

- **Iperparametri** (§3.11, nomi del protocollo → nomi della libreria, 1:1): ``t``, ``linking``,
  ``mask``, ``canonical``, ``names.variant``, ``names.proposer``, ``names.include_params``,
  ``channels``, ``names.module_level``, ``code.matrix``, ``names.single_letter``,
  ``names.bits_per_name``, ``canonical.same_kind`` (libreria a4e6972, v1.3.2),
  ``canonical.natural_forms``, ``names.suffix_by_type`` (libreria 0664817, v1.3.9, MD29 e MD30),
  ``voting``, ``channel_weights``.
  - ``canonical.natural_forms`` e ``names.suffix_by_type`` entrano negli iperparametri nativi (e
    nel ``config_hash``) solo se diversi da ``off``: con le opzioni spente gli hash calcolati con
    lo stesso commit della libreria restano quelli di prima e la libreria usa i suoi default.
  - Solo per ``multimark``: ``mcss`` le fissa a ``off`` (``FIXED``) e rifiuta ogni altro valore.
  - Una configurazione indica solo le chiavi che cambiano rispetto al profilo del metodo
    (``PROFILE``); le altre restano quelle del profilo.
  - ``message_bits`` = 24 lo fissa l'adapter.
  - ``registry_seed`` = ``global_seed`` va allo shim per le esche del registro (§3.10.1) ma
    **non** entra nel ``config_hash`` (§3.11).
  - ``names.proposer=neural`` (libreria b2b6c18, P3, MD31): solo per ``multimark``
    (``mcss`` lo fissa a ``static``) e solo con ``names.variant`` V2 o V2b. Il modello dei nomi è
    ``NEURAL_MODEL`` del catalogo dei modelli (``Qwen/Qwen2.5-Coder-1.5B-Instruct``, decisione J
    di v1.3.13). Solo con il proponente neurale gli iperparametri nativi hanno in più
    ``names_neural_model`` (repository e snapshot) e ``names_neural_dtype``, che entrano nel
    ``config_hash``, e ``names_neural_path`` (snapshot locale passata a ``NeuralProposer``),
    ``names_neural_cache`` (cache su disco delle risposte, condivisa fra le configurazioni) e
    ``names_neural_library`` (commit della libreria, per la chiave della cache), che non ci
    entrano: con ``static`` gli hash restano quelli di prima. ``names.proposer`` c'era già.
  - Con il proponente neurale l'inserimento va su GPU (``gpu_for_embed``, ``embed_device``); la
    rilevazione no (il bit si legge dal nome).
- **Messaggio** (§3.7): 24 bit, ``derive_seed(global_seed, "<metodo>-msg", problem_key,
  language, model_id, sample_index)``, come ``mcgmark.message_bits``.
  - Per i positivi il messaggio va allo shim nell'item dell'inserimento e resta nel campione
    marcato (``expected_message``).
  - Per i negativi viene dagli identificativi (``message_for``).
- **Stati dell'inserimento** (§3.9): ``OK`` se N ≥ B, ``PARTIAL`` se 0 < N < B, ``FAILED``
  altrimenti.
  - ``PARTIAL`` è un positivo a tutti gli effetti, quindi il codice marcato è quello dello shim.
  - Con ``FAILED`` il codice resta quello della baseline, come per ACW.
- **Rilevazione** (§3.10):
  - punteggio principale: ``-log10 P[Bin(N, 1/2) ≥ A]`` con ``N_reg = 1``;
  - secondari ``nreg16``, ``nreg1024`` (registro di esche) e ``decode`` (``bits_correct / 24``);
  - ``bits_correct`` da ``extra`` (ADR-017 §1);
  - ``decoded_message`` = decodifica aperta.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from bench_contracts import WorkerItem, WorkerRequest, WorkerResult
from bench_contracts.enums import EmbedStatus, WorkerOp

from bench.config.schema import ModelSpec
from bench.domain.enums import MethodFamily
from bench.domain.errors import ConfigError
from bench.domain.ids import config_hash, derive_seed
from bench.domain.models import CodeSample
from bench.methods.base import CodeEmbedder, Detector, EmbedRun
from bench.methods.worker_client import WorkerRun
from bench.registry import METHODS

MESSAGE_BITS = 24  # §3.7, §0.3: 24 bit contro i 12 di MCGMark (D1)
# Valori ammessi (§3.11), copiati dalla libreria (``multimark.config``) per non importarla in
# bench-core; ``tests/unit/test_posthoc_adapters.py`` li confronta con quelli della libreria.
CHOICES: dict[str, tuple[Any, ...]] = {
    "t": (0, 2, 4, 8),
    "linking": ("function_channel", "function_single_run", "positional"),
    "mask": ("on", "off"),
    "canonical": ("kind", "paper"),
    "names.variant": ("V0", "V1", "V1b", "V2", "V2b"),
    "names.proposer": ("static", "neural"),
    "names.include_params": (False, True),
    "channels": ("mcss8", "extended", "extended+str"),
    "names.module_level": (False, True),
    "code.matrix": ("random", "dmin4"),
    "names.single_letter": ("exclude", "extend_except_loop", "extend_all"),
    "names.bits_per_name": (1, 2),
    "canonical.same_kind": ("off", "binding_order"),
    "canonical.natural_forms": ("off", "const_left", "const_left_zero"),
    "names.suffix_by_type": ("off", "on"),
    "voting": ("simple", "weighted", "weighted_erasure"),
}
# Nomi del protocollo → campi di ``multimark.config.Config`` (1:1, §3.11).
NATIVE_NAMES: dict[str, str] = {
    "t": "t",
    "linking": "linking",
    "mask": "mask",
    "canonical": "canonical",
    "names.variant": "names_variant",
    "names.proposer": "names_proposer",
    "names.include_params": "names_include_params",
    "channels": "channels",
    "names.module_level": "names_module_level",
    "code.matrix": "code_matrix",
    "names.single_letter": "names_single_letter",
    "names.bits_per_name": "names_bits_per_name",
    "canonical.same_kind": "canonical_same_kind",
    "canonical.natural_forms": "canonical_natural_forms",
    "names.suffix_by_type": "names_suffix_by_type",
    "voting": "voting",
    "channel_weights": "channel_weights",
}
# Chiavi native che non entrano nel config_hash (§3.11): il global_seed è costante; percorso del
# modello dei nomi, cartella della cache e commit della libreria dipendono dalla macchina o sono già
# nell'hash (il commit).
NOT_HASHED = ("registry_seed", "names_neural_path", "names_neural_cache", "names_neural_library")
# Modello del proponente neurale (P3, decisione J di v1.3.13): chiave di ``models_catalog``.
NEURAL_MODEL = "qwen25_coder_1p5b"
# Varianti dei nomi che usano il proponente (con V0, V1 e V1b non si usa).
PROPOSER_VARIANTS = ("V2", "V2b")
# Opzioni della libreria 0664817 (MD29, MD30): negli iperparametri nativi solo se non ``off``.
OMITTED_WHEN_OFF = ("canonical.natural_forms", "names.suffix_by_type")


def flatten(hp: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """``{"names": {"variant": "V2"}}`` → ``{"names.variant": "V2"}`` (le configurazioni YAML
    possono usare chiavi annidate o puntate)."""
    out: dict[str, Any] = {}
    for key, value in hp.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.update(flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def message_bits(
    global_seed: int,
    method: str,
    problem_key: str,
    language: str,
    model_id: str,
    index: int | None,
) -> str:
    """Messaggio atteso di 24 bit (§3.7), anche per i negativi, dai loro identificativi."""
    value = derive_seed(global_seed, f"{method}-msg", problem_key, language, model_id, index)
    return format(value % (2**MESSAGE_BITS), f"0{MESSAGE_BITS}b")


class PosthocMultibitAdapter(CodeEmbedder, Detector):
    """Base di ``mcss`` e ``multimark``: stessa libreria, profili diversi (``PROFILE``)."""

    family: ClassVar[MethodFamily] = MethodFamily.POST_HOC
    # Se l'inserimento può usare la GPU: solo con names.proposer=neural (``embed_device``).
    gpu_for_embed: ClassVar[bool] = False
    gpu_for_detect: ClassVar[bool] = False
    message_bits: ClassVar[int | None] = MESSAGE_BITS
    secondary_scores: ClassVar[dict[str, str]] = {
        "nreg16": "score_nreg16",
        "nreg1024": "score_nreg1024",
        "decode": "score_decode",
    }
    PROFILE: ClassVar[dict[str, Any]] = {}
    # Chiavi che il metodo non lascia cambiare (valore obbligato = quello del profilo).
    FIXED: ClassVar[tuple[str, ...]] = ()

    _embed_messages: dict[str, str | None] | None = None

    # ------------------------------------------------------------------ iperparametri
    def to_native_hparams(self, hp: dict[str, Any]) -> dict[str, Any]:
        """Profilo del metodo con le chiavi di ``hp`` al posto delle sue (§3.11), validato."""
        given = flatten(hp)
        unknown = sorted(set(given) - set(NATIVE_NAMES))
        if unknown:
            raise ConfigError(f"{self.name}: unknown hyperparameters {unknown}")
        merged = {**self.PROFILE, **given}
        self._check_choices(merged)
        neural = merged["names.proposer"] == "neural"
        weights = merged.get("channel_weights")
        if merged["voting"] != "simple" and not weights:
            raise ConfigError(f"{self.name}: voting={merged['voting']} needs channel_weights")
        if weights is not None and not isinstance(weights, str):
            raise ConfigError(f"{self.name}: channel_weights must be a JSON file path")
        native: dict[str, Any] = {
            NATIVE_NAMES[k]: merged.get(k)
            for k in NATIVE_NAMES
            if not (k in OMITTED_WHEN_OFF and merged[k] == "off")
        }
        native["message_bits"] = MESSAGE_BITS
        native["registry_seed"] = int(self.cfg.global_seed)
        if neural:
            spec = self.neural_model()
            native["names_neural_model"] = f"{spec.hf_repo}@{spec.snapshot}"
            native["names_neural_dtype"] = spec.dtype
            native["names_neural_path"] = str(spec.path)
            native["names_neural_cache"] = str(self.neural_cache_dir())
            native["names_neural_library"] = self.source_commit()
        return native

    def _check_choices(self, merged: dict[str, Any]) -> None:
        """Valori ammessi e vincoli fra le chiavi (§3.11, MD27–MD31)."""
        for key, allowed in CHOICES.items():
            value = merged[key]
            ok = value in allowed and (isinstance(value, bool) == isinstance(allowed[0], bool))
            if not ok:
                raise ConfigError(f"{self.name}: {key} must be one of {list(allowed)}: {value!r}")
        if merged["names.bits_per_name"] != 1 and merged["names.variant"] != "V2":
            raise ConfigError(f"{self.name}: names.bits_per_name=2 requires names.variant=V2")
        if merged["canonical.same_kind"] != "off" and merged["canonical"] != "kind":
            raise ConfigError(f"{self.name}: canonical.same_kind requires canonical=kind")
        for key in self.FIXED:
            if merged[key] != self.PROFILE[key]:
                raise ConfigError(f"{self.name}: {key} is fixed to {self.PROFILE[key]!r}")
        for key in OMITTED_WHEN_OFF:
            if merged[key] != "off" and merged["canonical"] != "kind":
                raise ConfigError(f"{self.name}: {key} requires canonical=kind")
        if (
            merged["names.proposer"] == "neural"
            and merged["names.variant"] not in PROPOSER_VARIANTS
        ):
            raise ConfigError(f"{self.name}: names.proposer=neural requires names.variant V2/V2b")

    def neural_model(self) -> ModelSpec:
        """Modello del proponente neurale dal catalogo (``NEURAL_MODEL``)."""
        spec = self.cfg.models_catalog.get(NEURAL_MODEL)
        if spec is None:
            raise ConfigError(
                f"{self.name}: names.proposer=neural needs {NEURAL_MODEL} in models_catalog"
            )
        return spec

    def neural_cache_dir(self) -> Path:
        """Cache su disco delle risposte del modello dei nomi (shim, ``neural_cache.py``): una
        sola per tutte le configurazioni, perché la chiave contiene lotto, modello, prompt e
        parametri di generazione."""
        return Path(self.cfg.paths.artifacts) / "_cache" / "multimark_neural_names"

    def embed_device(self, hp: dict[str, Any]) -> str:
        """Dispositivo dell'inserimento: ``cuda:0`` con il proponente neurale, altrimenti CPU."""
        neural = {**self.PROFILE, **flatten(hp)}["names.proposer"] == "neural"
        return "cuda:0" if neural else "cpu"

    def config_hash(self, hp: dict[str, Any]) -> str:
        """Hash degli iperparametri nativi senza ``registry_seed`` (§3.11) + contratto e commit."""
        native = {k: v for k, v in self.to_native_hparams(hp).items() if k not in NOT_HASHED}
        return config_hash(native, self.source_commit())

    # ------------------------------------------------------------------ messaggi
    def message_for(
        self, model: ModelSpec, problem_key: str, language: str, index: int | None
    ) -> str | None:
        return message_bits(
            self.cfg.global_seed, self.name, problem_key, language, model.model_id, index
        )

    def _parent_message(self, model: ModelSpec, parent: CodeSample) -> str | None:
        return self.message_for(
            model, parent.problem_key, str(parent.language), parent.sample_index
        )

    # ------------------------------------------------------------------ inserimento
    def run_worker(self, request: WorkerRequest, items: Sequence[WorkerItem]) -> WorkerRun:
        """Negli item dell'inserimento il messaggio del campione (``CodeEmbedder`` non lo mette)."""
        if request.op == WorkerOp.EMBED and self._embed_messages is not None:
            messages = self._embed_messages
            items = [dataclasses.replace(it, expected_message=messages[it.item_id]) for it in items]
        return super().run_worker(request, items)

    def embed_from_code(
        self,
        samples: Sequence[CodeSample],
        hp: dict[str, Any],
        model: ModelSpec,
        key_id: str,
        run_dir: Path,
        device: str = "cpu",
    ) -> EmbedRun:
        """Come ``CodeEmbedder.embed_from_code`` con il messaggio atteso nei campioni marcati e il
        codice dello shim anche per gli inserimenti ``PARTIAL`` (§3.9: sono positivi). Con il
        proponente neurale il worker usa la GPU (``embed_device``)."""
        if self.embed_device(hp) != "cpu":
            device = self.embed_device(hp)
        self._embed_messages = {s.sample_id: self._parent_message(model, s) for s in samples}
        try:
            run = super().embed_from_code(samples, hp, model, key_id, run_dir, device)
        finally:
            self._embed_messages = None
        found = {r.item_id: r for r in run.worker.results} if run.worker is not None else {}
        out: list[CodeSample] = []
        for parent, sample in zip(samples, run.samples, strict=True):
            result = found.get(parent.sample_id)
            if result is None:  # linguaggio non supportato: NOT_APPLICABLE, nessun messaggio
                out.append(sample)
                continue
            update: dict[str, Any] = {"expected_message": self._parent_message(model, parent)}
            if result.status == EmbedStatus.PARTIAL:
                update["code"] = result.code if result.code is not None else parent.code
            out.append(sample.model_copy(update=update))
        return dataclasses.replace(run, samples=out)

    # ------------------------------------------------------------------ rilevazione
    def bits_correct(self, result: WorkerResult) -> int | None:
        """Bit del messaggio giusti dalla decodifica aperta (``extra["bits_correct"]``, §3.10;
        con decodifica ambigua i bit decisi dal voto, decisione L di §7)."""
        if result.status != "OK":
            return None
        value = (result.extra or {}).get("bits_correct")
        return None if value is None else int(value)


@METHODS.register("multimark")
class MultimarkAdapter(PosthocMultibitAdapter):
    """MultiMark, configurazione di base (§0.1, §3.11; proponente statico, neurale da P3)."""

    name: ClassVar[str] = "multimark"
    gpu_for_embed: ClassVar[bool] = True  # solo con names.proposer=neural (embed_device)
    PROFILE: ClassVar[dict[str, Any]] = {
        "t": 2,
        "linking": "function_channel",
        "mask": "on",
        "canonical": "kind",
        "names.variant": "V2",
        "names.proposer": "static",  # neural in P3 (MD31): due punti del giro P3
        "names.include_params": False,  # decisione C (chiusa il 13 novembre)
        "channels": "extended",
        "names.module_level": True,  # decisione O, chiusa con true (v1.3.1)
        "code.matrix": "dmin4",  # MD18, superato il controllo di §3.8
        "names.single_letter": "exclude",  # decisione R in P5
        "names.bits_per_name": 1,  # MD27: 2 solo nell'ablazione (v1.3.2)
        "canonical.same_kind": "off",  # MD28: binding_order solo nell'ablazione (v1.3.2)
        "canonical.natural_forms": "off",  # MD29: const_left / const_left_zero nel giro 4 (v1.3.9)
        "names.suffix_by_type": "off",  # MD30 (v1.3.9)
        "voting": "simple",
        "channel_weights": None,
    }
