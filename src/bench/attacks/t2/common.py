"""Parti comuni degli attacchi con LLM (T2.2, T2.3): prompt bloccati, segnaposto, estrazione.

I testi stanno in ``configs/prompt/attacks/``. Ogni cella porta fra i parametri lo SHA-256 atteso
di ogni file che usa (``configs/attack/t2_*.yaml``): il file si confronta con quel valore al
caricamento (``ConfigError`` se diverso) e, stando nei parametri, l'hash entra in
``attack_params_hash``. Cambiare un prompt richiede quindi di aggiornare il valore e produce celle
nuove, senza mescolare risultati di testi diversi (come ``system_prompt_sha256`` della
generazione).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from functools import cache
from typing import Any

from bench.attacks.base import AttackContext, AttackOutcome, outcome, tool_error
from bench.attacks.llm import Completion
from bench.config.builder import config_dir
from bench.domain.enums import Language
from bench.domain.errors import ConfigError
from bench.generation.code_extractor import FencedCodeExtractor

LANGUAGE_NAMES: dict[Language, str] = {
    Language.PYTHON: "Python",
    Language.JAVA: "Java",
    Language.CPP: "C++",
    Language.JAVASCRIPT: "JavaScript",
}
LLM_PARAMS = ("attacker", "max_new_tokens", "batch_size")


def prompt_path_sha256(name: str) -> str:
    """SHA-256 di ``configs/prompt/attacks/<name>.txt``."""
    path = config_dir() / "prompt" / "attacks" / f"{name}.txt"
    return hashlib.sha256(path.read_bytes()).hexdigest()


@cache
def load_prompt(name: str, expected_sha256: str) -> str:
    """Testo di ``configs/prompt/attacks/<name>.txt``, se lo SHA-256 è quello atteso.

    Raises:
        ConfigError: file mancante o SHA-256 diverso.
    """
    path = config_dir() / "prompt" / "attacks" / f"{name}.txt"
    if not path.is_file():
        raise ConfigError(f"missing attack prompt {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ConfigError(
            f"{path}: sha256 {actual} != {expected_sha256} in configs/attack (approved text)"
        )
    return path.read_text(encoding="utf-8")


def render(template: str, language: Language, code: str, target: Language | None = None) -> str:
    """Segnaposto: ``{{ language }}``, ``{{ tag }}``, ``{{ code }}`` e ``{{ target_language }}``."""
    text = template.replace("{{ language }}", LANGUAGE_NAMES[language])
    if target is not None:
        text = text.replace("{{ target_language }}", LANGUAGE_NAMES[target])
    return text.replace("{{ tag }}", str(language)).replace("{{ code }}", code.rstrip("\n"))


def messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system.strip()}, {"role": "user", "content": user}]


def require_llm_params(attack_id: str, params: Mapping[str, Any], extra: tuple[str, ...]) -> None:
    """Parametri obbligatori della cella (decoding e SHA-256 dei prompt).

    Raises:
        ConfigError: parametro mancante.
    """
    missing = [k for k in (*LLM_PARAMS, *extra) if k not in params]
    if missing:
        raise ConfigError(f"{attack_id}: missing parameters {missing} in configs/attack")


def completion_detail(prefix: str, completion: Completion) -> dict[str, Any]:
    return {
        f"{prefix}new_tokens": completion.new_tokens,
        f"{prefix}truncated": completion.truncated,
    }


def final_outcome(
    original: str,
    completion: Completion,
    ctx: AttackContext,
    language: Language,
    extractor: FencedCodeExtractor,
    detail: dict[str, Any],
) -> AttackOutcome:
    """Codice estratto con la regola D4 dalla risposta finale.

    Estrazione fallita: ``TOOL_ERROR`` con il codice originale (il campione resta nelle metriche,
    I1 e I5). Codice uguale all'originale a meno degli a capo finali: ``UNCHANGED``.
    """
    if ctx.problem is not None:
        code, ok = extractor.extract(completion.text, ctx.problem)
    else:
        selected = extractor.select(completion.text, language)
        code, ok = (selected or "", selected is not None)
    detail = {**detail, **completion_detail("", completion), "extraction_ok": ok}
    if not ok:
        return tool_error(original, "extraction failed", detail)
    if code.rstrip("\n") == original.rstrip("\n"):
        return outcome(original, original, detail)
    return outcome(original, code, detail)
