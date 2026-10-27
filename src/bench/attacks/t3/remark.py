"""T3.1 ri-marcatura con una chiave diversa (SPEC §12; ADR-014 §3; decisioni dell'utente del 27
ottobre 2026).

Il codice finale deriva da quello marcato con k1 (interpretazione dello SPEC, ADR-014 §3.1):

- **metodi in generazione** (``PromptEmbedder``): il generatore dello stesso metodo, con la
  configurazione congelata e la chiave ``key_id`` (k2), riscrive il campione marcato con i testi
  approvati di T2.2(a) (``system.txt`` e ``conservative.txt``, SHA-256 fra i parametri). Decoding
  di generazione del metodo e del livello (la fase attack lo passa in :class:`MethodSetup`), un
  campione per campione marcato, seme dall'RNG del campione (SPEC §18); il messaggio atteso
  (MCGMark) è quello del campione marcato. Codice estratto con la regola D4;
- **metodi post-hoc** (``CodeEmbedder``, ACW): il metodo riapplicato con k2 al codice marcato.

**Stati:** ``APPLIED`` se il codice cambia; ``UNCHANGED`` se resta uguale (ACW: nessuna regola di
k2 lo modifica); ``TOOL_ERROR`` con il codice marcato se generazione o estrazione falliscono (I1,
I5); ``NOT_APPLICABLE`` per i linguaggi che il metodo non supporta. Il dettaglio registra lo stato
d'inserimento del worker (``embed_status``), le misure d'inserimento e, per ACW, le regole di k2
(``rules``) e quelle che hanno modificato il campione (``applicable_rules``), per la
sovrapposizione con il sottoinsieme di k1.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from bench_contracts import WorkerItem, WorkerResult

from bench.attacks.base import (
    Attack,
    AttackContext,
    AttackOutcome,
    MethodSetup,
    outcome,
    tool_error,
)
from bench.attacks.t2.common import load_prompt, messages, render
from bench.domain.enums import AttackStatus, Language
from bench.domain.errors import ConfigError
from bench.generation.code_extractor import FencedCodeExtractor
from bench.registry import ATTACKS

SEED_MODULUS = 2**31


@ATTACKS.register("T3.1")
class RemarkAttack(Attack):
    """T3.1: parametri in ``configs/attack/t3_1_remark.yaml``."""

    attack_id: ClassVar[str] = "T3.1"
    group: ClassVar[str] = "T3"
    requires_gpu: ClassVar[bool] = True
    needs_method: ClassVar[bool] = True

    def __init__(self, params: dict[str, Any]) -> None:
        super().__init__(params)
        missing = [k for k in ("key_id", "system_sha256", "prompt_sha256") if k not in self.params]
        if missing:
            raise ConfigError(f"T3.1: missing parameters {missing} in configs/attack")
        if self.params["key_id"] == "k1":
            raise ConfigError("T3.1: the re-marking key must differ from k1")
        self.extractor = FencedCodeExtractor()

    @property
    def key_id(self) -> str:
        return str(self.params["key_id"])

    def prompts(self) -> tuple[str, str]:
        """System prompt e template di T2.2(a) (con il controllo dello SHA-256)."""
        system = load_prompt("system", str(self.params["system_sha256"]))
        template = load_prompt("conservative", str(self.params["prompt_sha256"]))
        return system, template

    def manifest_extra(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "prompts_sha256": {
                "system.txt": self.params["system_sha256"],
                "conservative.txt": self.params["prompt_sha256"],
            },
        }

    def apply(self, code: str, language: Language, ctx: AttackContext) -> AttackOutcome:
        raise ConfigError("T3.1 needs the method generator (apply_with_method)")

    def apply_with_method(
        self,
        parents: Sequence[Any],
        language: Language,
        ctxs: Sequence[AttackContext],
        setup: MethodSetup,
    ) -> list[AttackOutcome]:
        adapter = setup.adapter
        if not adapter.supports(language):
            return [AttackOutcome(p.code, AttackStatus.NOT_APPLICABLE, {}) for p in parents]
        from bench.methods.base import CodeEmbedder

        if isinstance(adapter, CodeEmbedder):
            return self._post_hoc(parents, setup)
        return self._regenerate(parents, language, ctxs, setup)

    def _post_hoc(self, parents: Sequence[Any], setup: MethodSetup) -> list[AttackOutcome]:
        """ACW: riapplicazione con k2 al codice marcato con k1."""
        todo = [p for p in parents if p.code.strip()]
        run = setup.adapter.embed_from_code(
            todo, setup.hp, setup.model, self.key_id, setup.run_dir, setup.device
        )
        found = {r.item_id: r for r in (run.worker.results if run.worker else [])}
        out = []
        for parent in parents:
            res = found.get(parent.sample_id)
            if not parent.code.strip():
                out.append(AttackOutcome(parent.code, AttackStatus.UNCHANGED, {"empty": True}))
                continue
            if res is None:
                out.append(tool_error(parent.code, "no worker result"))
                continue
            extra = res.extra or {}
            detail = {
                "embed_status": str(res.status),
                "rules": list(extra.get("rules") or []),
                "applicable_rules": list(extra.get("applicable_rules") or []),
                "n_applicable": extra.get("n_applicable"),
            }
            if str(res.status) == "OK" and res.code is not None:
                out.append(outcome(parent.code, res.code, detail))
            elif res.error == "no transformation applied":
                out.append(outcome(parent.code, parent.code, detail))
            else:
                out.append(tool_error(parent.code, res.error or str(res.status), detail))
        return out

    def _regenerate(
        self,
        parents: Sequence[Any],
        language: Language,
        ctxs: Sequence[AttackContext],
        setup: MethodSetup,
    ) -> list[AttackOutcome]:
        """Metodi in generazione: riscrittura del campione marcato con il generatore e k2."""
        system, template = self.prompts()
        items: list[WorkerItem] = []
        for parent, ctx in zip(parents, ctxs, strict=True):
            if not parent.code.strip():
                continue
            items.append(
                WorkerItem(
                    item_id=parent.sample_id,
                    language=str(language),
                    seed=ctx.rng.randrange(SEED_MODULUS),
                    prompt_messages=messages(system, render(template, language, parent.code)),
                    code=None,
                    context_prompt=None,
                    expected_message=parent.expected_message,
                    n=1,
                )
            )
        found: dict[str, WorkerResult] = {}
        if items:
            problems = sorted(
                {p.problem_key: setup.problems[p.problem_key] for p in parents}.values(),
                key=lambda p: p.problem_key,
            )
            run = setup.adapter.rewrite_items(
                items, problems, setup.hp, setup.model, self.key_id, setup.decoding,
                system.strip(), setup.run_dir, setup.device,
            )  # fmt: skip
            found = {r.item_id: r for r in run.results}
        return [self._finish(parent, found.get(parent.sample_id), setup) for parent in parents]

    def _finish(self, parent: Any, res: WorkerResult | None, setup: MethodSetup) -> AttackOutcome:
        if not parent.code.strip():
            return AttackOutcome(parent.code, AttackStatus.UNCHANGED, {"empty": True})
        if res is None:
            return tool_error(parent.code, "no worker result")
        detail: dict[str, Any] = {
            "embed_status": str(res.status),
            **{k: v for k, v in setup.adapter.embed_metrics(res).items() if v is not None},
        }
        if str(res.status) not in ("OK", "PARTIAL") or res.raw_output is None:
            return tool_error(parent.code, res.error or f"embed {res.status}", detail)
        code, ok = self.extractor.extract(res.raw_output, setup.problems[parent.problem_key])
        detail["extraction_ok"] = ok
        if not ok:
            return tool_error(parent.code, "extraction failed", detail)
        if code.rstrip("\n") == parent.code.rstrip("\n"):
            return outcome(parent.code, parent.code, detail)
        return outcome(parent.code, code, detail)
