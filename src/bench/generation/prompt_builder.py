"""Costruzione dei messaggi chat (SPEC §2.1, §7.5; ADR-006).

- System prompt unico e fisso (``configs/prompt/system.txt``), **bloccato**: il suo SHA-256
  deve coincidere con ``prompt.system_prompt_sha256`` (decisione dell'utente).
- Messaggio utente da ``configs/prompt/user/<dataset>_<linguaggio>.j2`` (testi approvati
  dall'utente): il prompt del dataset è inserito parola per parola.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from bench.config.schema import PromptConfig
from bench.domain.errors import ConfigError
from bench.domain.models import Problem
from bench.store.hashing import sha256_bytes


def load_system_prompt(cfg: PromptConfig) -> str:
    """Legge il system prompt verificandone l'hash.

    Il file termina con un a capo, che non fa parte del prompt.

    Raises:
        ConfigError: se il file manca o il suo SHA-256 non è quello bloccato.
    """
    path = cfg.system_prompt_file
    if not path.is_file():
        raise ConfigError(f"system prompt not found: {path}")
    data = path.read_bytes()
    digest = sha256_bytes(data)
    if digest != cfg.system_prompt_sha256:
        raise ConfigError(
            f"system prompt {path} has SHA-256 {digest}, expected the locked "
            f"{cfg.system_prompt_sha256}: the system prompt must not change"
        )
    return data.decode("utf-8").rstrip("\n")


class PromptBuilder:
    """Messaggi chat ``[system, user]`` per un problema.

    Args:
        template_dir: cartella dei template utente.
        system_prompt: testo del system prompt.
        system_prompt_sha256: hash del file del system prompt (per i manifest).
    """

    def __init__(self, template_dir: Path, system_prompt: str, system_prompt_sha256: str) -> None:
        self.template_dir = template_dir
        self.system_prompt = system_prompt
        self.system_prompt_sha256 = system_prompt_sha256
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=False,
        )

    @classmethod
    def from_config(cls, cfg: PromptConfig) -> PromptBuilder:
        """Builder dalla configurazione (verifica l'hash del system prompt)."""
        return cls(cfg.template_dir, load_system_prompt(cfg), cfg.system_prompt_sha256)

    @staticmethod
    def template_name(problem: Problem) -> str:
        """Nome del template per un problema: ``<dataset>_<linguaggio>.j2``."""
        return f"{problem.dataset}_{problem.language}.j2"

    def render_user(self, problem: Problem) -> str:
        """Messaggio utente del problema.

        Raises:
            ConfigError: se il template manca o il problema non ha ``entry_point``.
        """
        if not problem.entry_point:
            raise ConfigError(f"{problem.problem_key}: entry_point is required by the prompt")
        try:
            template = self._env.get_template(self.template_name(problem))
        except TemplateNotFound as exc:
            raise ConfigError(f"no user template {exc.name} in {self.template_dir}") from exc
        prompt = problem.prompt_text
        # Il blocco di codice si chiude su una riga propria; il prompt resta invariato.
        if not prompt.endswith("\n"):
            prompt += "\n"
        return template.render(prompt=prompt, entry_point=problem.entry_point)

    def build(self, problem: Problem, system_prompt: str | None = None) -> list[dict[str, str]]:
        """Messaggi chat (SPEC §7.5); ``system_prompt`` sostituisce quello di default."""
        return [
            {
                "role": "system",
                "content": self.system_prompt if system_prompt is None else system_prompt,
            },
            {"role": "user", "content": self.render_user(problem)},
        ]

    def prompt_hashes(self) -> dict[str, str]:
        """SHA-256 del system prompt e di tutti i template utente (per i manifest)."""
        hashes = {"system.txt": self.system_prompt_sha256}
        for path in sorted(self.template_dir.glob("*.j2")):
            hashes[path.name] = sha256_bytes(path.read_bytes())
        return hashes
