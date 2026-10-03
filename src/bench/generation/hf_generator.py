"""Generazione della baseline senza watermark con transformers (SPEC §7.5, §10.5; ADR-006).

Un prompt alla volta: prima di ogni ``generate`` si fissano ``torch.manual_seed(seed)`` e
``torch.cuda.manual_seed_all(seed)`` con ``seed = derive_seed(global_seed, "gen", model_id,
problem_key, language)``, lo stesso seme passato ai worker dei metodi; poi si generano gli N
campioni con ``num_return_sequences``. Il backend è iniettabile per i test senza GPU.
"""

from __future__ import annotations

import logging
import platform
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from bench.config.schema import DecodingConfig, ModelSpec
from bench.domain.enums import Source
from bench.domain.ids import derive_seed, sample_id
from bench.domain.models import CodeSample, Problem
from bench.generation.code_extractor import CodeExtractor, FencedCodeExtractor
from bench.generation.decoding import effective_config, neutral_generation_config, special_token_ids
from bench.generation.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


def generation_seed(global_seed: int, model_id: str, problem_key: str, language: str) -> int:
    """Seme di generazione di un problema (SPEC §10.5, §18)."""
    return derive_seed(global_seed, "gen", model_id, problem_key, language)


class TextGenerator(Protocol):
    """Backend di generazione: N completamenti per una chat, dato un seme."""

    def generate(self, messages: list[dict[str, str]], n: int, seed: int) -> list[str]:
        """N testi generati (solo la parte nuova, senza prompt)."""
        ...

    def info(self) -> dict[str, Any]:
        """Diagnostica: configurazione effettiva, versioni, dispositivo."""
        ...


def sample_sequences(model: Any, input_ids: Any, generation_config: Any, n: int, seed: int) -> Any:
    """Esegue ``generate`` con il seme fissato e restituisce solo i token nuovi (``n`` righe)."""
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    attention_mask = torch.ones_like(input_ids)
    with torch.no_grad():
        output = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            generation_config=generation_config,
            num_return_sequences=n,
        )
    return output[:, input_ids.shape[1] :]


class HFTextGenerator:
    """Backend reale: modello e tokenizer locali dalla snapshot (offline).

    Args:
        model: modello (``path`` = snapshot della cache HF).
        decoding: parametri di decoding (SPEC §2.1).
        device: dispositivo (default: ``cuda`` se disponibile).
    """

    def __init__(
        self, model: ModelSpec, decoding: DecodingConfig, device: str | None = None
    ) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.spec = model
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device == "cpu":
            logger.warning("no CUDA device: generation on CPU will be very slow")
        dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[
            model.dtype
        ]
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(model.tokenizer_path), local_files_only=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            str(model.path), torch_dtype=dtype, local_files_only=True
        ).to(self.device)
        self.model.eval()
        eos, pad = special_token_ids(
            self.tokenizer.eos_token_id,
            self.tokenizer.pad_token_id,
            getattr(self.model.generation_config, "eos_token_id", None),
        )
        self.generation_config = neutral_generation_config(decoding, eos, pad)
        # Il generation_config.json del modello viene sostituito: niente ricadute sui suoi default.
        self.model.generation_config = self.generation_config

    def generate(self, messages: list[dict[str, str]], n: int, seed: int) -> list[str]:
        input_ids = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        ).to(self.device)
        new_tokens = sample_sequences(self.model, input_ids, self.generation_config, n, seed)
        return [str(t) for t in self.tokenizer.batch_decode(new_tokens, skip_special_tokens=True)]

    def info(self) -> dict[str, Any]:
        import torch
        import transformers

        return {
            "generation_config": effective_config(self.generation_config),
            "model_path": str(self.spec.path),
            "snapshot": self.spec.snapshot,
            "dtype": self.spec.dtype,
            "device": self.device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "transformers": transformers.__version__,
            "python": platform.python_version(),
        }


class HFBaselineGenerator:
    """Baseline senza watermark (SPEC §7.5).

    Args:
        model: modello generatore.
        decoding: parametri di decoding.
        prompts: costruttore dei messaggi chat.
        global_seed: seme globale.
        backend: backend di generazione (default: ``HFTextGenerator``).
        extractor: estrattore del codice (default: regola unica D4).
    """

    def __init__(
        self,
        model: ModelSpec,
        decoding: DecodingConfig,
        *,
        prompts: PromptBuilder,
        global_seed: int,
        backend: TextGenerator | None = None,
        extractor: CodeExtractor | None = None,
    ) -> None:
        self.model = model
        self.decoding = decoding
        self.prompts = prompts
        self.global_seed = global_seed
        self.backend: TextGenerator = backend or HFTextGenerator(model, decoding)
        self.extractor = extractor or FencedCodeExtractor()

    def seed_for(self, problem: Problem) -> int:
        """Seme del problema (uguale a quello dei worker dei metodi)."""
        return generation_seed(
            self.global_seed, self.model.model_id, problem.problem_key, problem.language
        )

    def generate_problem(self, problem: Problem, seed: int | None = None) -> list[CodeSample]:
        """Gli N campioni di un problema, con codice estratto."""
        seed = self.seed_for(problem) if seed is None else seed
        outputs = self.backend.generate(self.prompts.build(problem), self.decoding.n, seed)
        if len(outputs) != self.decoding.n:
            raise RuntimeError(
                f"{problem.problem_key}: {len(outputs)} outputs, expected {self.decoding.n}"
            )
        samples = []
        for index, raw in enumerate(outputs):
            code, ok = self.extractor.extract(raw, problem)
            samples.append(
                CodeSample(
                    sample_id=sample_id(
                        source=Source.LLM_BASELINE,
                        problem_key=problem.problem_key,
                        language=problem.language,
                        model_id=self.model.model_id,
                        sample_index=index,
                    ),
                    problem_key=problem.problem_key,
                    dataset=problem.dataset,
                    language=problem.language,
                    level=problem.level,
                    split=problem.split,
                    source=Source.LLM_BASELINE,
                    model_id=self.model.model_id,
                    method=None,
                    config_hash=None,
                    key_id=None,
                    sample_index=index,
                    seed=seed,
                    raw_output=raw,
                    code=code,
                    extraction_ok=ok,
                    embed_status=None,
                    expected_message=None,
                    parent_id=None,
                    attack_id=None,
                    attack_params_hash=None,
                    attack_status=None,
                    contamination_risk=problem.contamination_risk,
                )
            )
        return samples

    def generate(self, problems: Sequence[Problem], seeds: Mapping[str, int]) -> list[CodeSample]:
        """Campioni di tutti i problemi (SPEC §7.5), con i semi dati per ``problem_key``."""
        out: list[CodeSample] = []
        for problem in problems:
            out.extend(self.generate_problem(problem, seeds[problem.problem_key]))
        return out
