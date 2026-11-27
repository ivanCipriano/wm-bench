"""Catene di lavoro della chiusura (M13, M14, SWEET): passi, dipendenze, stato e invio.

Decisioni dell'utente del 30 ottobre 2026 (``docs/milestone_logs/M13_M14_plan.md``):

- un **passo** è un invio di ``bench submit`` (fase + override Hydra) con un proprietario
  (``ivan`` o ``simone``): ognuno lancia solo i propri passi, con i suoi 10 posti (MaxSubmit);
- un passo è **finito** quando esistono tutti gli output delle sue celle (gli stessi che la fase
  salterebbe): rilanciare non rifà il lavoro completo. I passi ``once`` (scelta rifatta con
  ``force=true``) sono finiti quando un loro invio è terminato con successo;
- un passo parte quando i passi da cui dipende sono finiti, oppure subito con
  ``--dependency=afterok`` sui job ancora attivi di quei passi; un job in attesa con
  ``DependencyNeverSatisfied`` (un passo precedente fallito) si cancella e il passo si ritenta,
  senza che la cancellazione consumi uno dei suoi tentativi (il fallimento è a monte);
- un passo che dipende da un passo ``FAILED`` (3 tentativi, nessun job attivo), anche
  indirettamente, non va in coda: è ``DEP_FAILED`` (fermo, come ``FAILED``), e i suoi job ancora in
  attesa di quella dipendenza si cancellano senza contare un tentativo (decisione dell'utente del
  27 novembre 2026, P7.a: prima finivano in coda dietro job che non sarebbero mai partiti);
- la rilevazione resta su ``gpuq`` (``bench.pipeline.submit.DETECT_STAGES``); la generazione può
  andare su ``aiq`` (``gpu="auto"``: la partizione con meno job in attesa), e una volta scelta la
  GPU di un passo resta la stessa nei tentativi successivi (una cella tutta su un tipo di GPU);
- i passi di ACW su L2 test e L3 partono solo con ``--with-acw`` (licenza di Sourcery confermata
  dall'utente).

Ordine obbligatorio di SWEET: estensione D31 su L1 e L2 dev → soglie e scelta su L1+L2 →
congelamento → fase 0 sul test → attacchi T1-T4 → M12 → L2 test e L3; nessun passo sul test prima
del congelamento (i passi di test dipendono da ``sweet/freeze``).

Metodi post-hoc ``mcss`` e ``multimark`` (P6, PIPELINE di multimark v1.3.16, decisioni dell'utente
del 24 novembre 2026; :func:`posthoc_steps`): solo i passi sul test, che restano non pianificabili
(«held») finché il metodo non è congelato (il ``CellPlanner`` li rifiuta, ADR-019). Congelamento,
seconda chiave su dev e ``freeze_k2`` sono fuori dalle catene (``scripts/run_p6.sh``). Tre
configurazioni (``mcss`` main, ``multimark`` prudent e high_capacity: un passo di ``multimark``
copre le due), tutto su CPU salvo perplexity, ``adversarial_unixcoder`` e T2.2; L1 test con la
suite completa, L2 test con l'insieme S più T1.6, T2.4k e T2.5, L3 = solo ClassEval
(``posthoc_datasets=[classeval]``) con T2.4k, T2.5, T1.4 natural r = 1,0 e T1.6 convert.

P7.a (v1.3.18, ADR-018 §5; :func:`p7a_steps`): T1.6 e T2.5 su L1 e L2 test, solo python, per ACW,
STONE e MCGMark (proprietario ivan), in coda a :func:`all_steps`; si lanciano con ``--only``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

OWNERS = ("ivan", "simone")
MAX_JOBS = 10  # MaxSubmit per utente (cluster_info §4)
MAX_ATTEMPTS = 3
LANGS = {
    "sweet": "[python,java,cpp,javascript]",
    "stone": "[python,java,cpp]",
    "mcgmark": "[python]",
    "promptmark": "[python]",
    "acw": "[python]",
    "mcss": "[python]",
    "multimark": "[python]",
}
METHOD_OWNER = {"sweet": "simone", "promptmark": "simone", "stone": "ivan", "mcgmark": "ivan",
                "acw": "ivan", "mcss": "ivan", "multimark": "ivan"}  # fmt: skip
# job di rilevazione per passo: la rilevazione di SWEET (con Qwen) è il collo di bottiglia
# delle catene (stima complessiva, M13_closing_cost_4.txt); al più i posti liberi
DETECT_JOBS = {"sweet": 8}
POSTHOC = ("mcss", "multimark")
# Attacchi dei post-hoc sul test per livello (v1.3.16, punto 5); T3.1 a parte (t3_1_steps).
POSTHOC_ATTACKS = {
    "L1": ("T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T1.6", "T2.1", "T2.2", "T2.4", "T2.5",
           "T4.1"),
    "L2": ("T1.4", "T1.5", "T1.6", "T2.1", "T2.4", "T2.4k", "T2.5", "T4.1"),
    "L3": ("T1.4", "T1.6", "T2.4k", "T2.5"),
}  # fmt: skip
# Punti scelti (attack_params_hash, ``attack_points``) degli attacchi che non girano con tutta la
# griglia; tests/unit/test_chains.py ricontrolla i parametri.
POSTHOC_POINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "L2": {
        "T1.4": ("23f6be2801c3", "e2770ff72bae"),  # natural r = 1,0; random r = 0,5
        "T1.5": ("705d07d343b2",),  # k = 5
        "T2.1": ("f39d410e0723",),  # b = all
        "T2.4": ("214ddb2cb794",),  # p = 0,5
        "T4.1": ("fe396d74371a",),  # rename = true
    },
    "L3": {
        "T1.4": ("23f6be2801c3",),  # natural r = 1,0
        "T1.6": ("9ad68973c317",),  # convert
    },
}
POSTHOC_L3_DATASETS = "[classeval]"
# P7.a (PIPELINE di multimark v1.3.18, decisioni dell'utente del 26 novembre 2026; ADR-018 §5):
# T1.6 e T2.5 sul test dei metodi Python del proprietario ivan, solo python, L1 e L2, con le
# configurazioni e le soglie congelate. Variante ridotta di ACW (passi ``<attacco>-reduced``):
# T1.6 convert e T2.5 n = 2 around.
P7A_METHODS = ("acw", "stone", "mcgmark")
P7A_ATTACKS = ("T1.6", "T2.5")
P7A_LEVELS = ("L1", "L2")
P7A_REDUCED: dict[str, tuple[str, ...]] = {
    "T1.6": ("9ad68973c317",),  # caps = convert
    "T2.5": ("9528392ed687",),  # n = 2, position = around
}
SWEET_ATTACKS = ("T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T2.1", "T2.2", "T2.4", "T4.1", "T4.2")
ACTIVE = ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "REQUEUED", "RESIZING", "SUSPENDED")
FAILED = ("FAILED", "TIMEOUT", "CANCELLED", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL", "DEADLINE",
          "PREEMPTED")  # fmt: skip
# Limiti di tempo dei comandi SLURM (P7.a, 27 novembre 2026: un giro del ciclo si è bloccato senza
# errori). Interrogazioni e cancellazioni: un errore come gli altri, il ciclo riprova al giro dopo.
# ``bench submit``: limite largo e, se scade, :class:`SubmitTimeout` (il ciclo si ferma: il job
# potrebbe essere stato inviato senza che lo stato lo sappia, va guardato ``squeue``).
QUERY_TIMEOUT_S = 300
SUBMIT_TIMEOUT_S = 1800
# Token di Sourcery (ACW): le prove sono scadute dopo circa 15 giorni (26 ottobre → 10 novembre;
# 10 novembre → fallimenti del 27 novembre); oltre questa età la stima e i giri lo segnalano.
SOURCERY_TOKEN_FILE = Path.home() / ".config" / "wmb" / "sourcery_token"
SOURCERY_TOKEN_MAX_DAYS = 12


class SubmitTimeout(RuntimeError):
    """``bench submit`` non ha risposto entro :data:`SUBMIT_TIMEOUT_S`."""


def sourcery_token_warning(
    path: Path | None = None, now: float | None = None, max_days: float = SOURCERY_TOKEN_MAX_DAYS
) -> str | None:
    """Avviso se il file del token di Sourcery manca o ha più di ``max_days`` giorni (dalla data
    di modifica, cioè dall'ultimo ``update_sourcery_token.sh``); ``None`` se è recente.
    Non legge mai il contenuto del file."""
    import time

    path = SOURCERY_TOKEN_FILE if path is None else path
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return f"WARNING: Sourcery token file {path} not found (ACW detect needs it)"
    age = ((time.time() if now is None else now) - mtime) / 86400
    if age > max_days:
        return (
            f"WARNING: Sourcery token file {path} is {age:.1f} days old (> {max_days:g}): the "
            "trial may have expired; check the canary or renew with "
            "scripts/update_sourcery_token.sh before launching ACW"
        )
    return None


@dataclass(frozen=True)
class Step:
    """Un invio di ``bench submit``.

    Attributes:
        key: identificativo unico (``<catena>/<passo>``).
        owner: chi lo lancia (``ivan`` o ``simone``).
        stage: fase della pipeline.
        overrides: override Hydra (oltre a ``stage=``).
        jobs: job paralleli al più.
        needs: passi da cui dipende.
        gpu: ``None`` (CPU o rilevazione), ``"a100"`` o ``"auto"`` (generazione: gpuq o aiq).
        once: finito quando un invio è terminato con successo (scelta rifatta con ``force``).
        acw: passo di ACW su dati nuovi (solo con ``--with-acw``).
    """

    key: str
    owner: str
    stage: str
    overrides: tuple[str, ...]
    jobs: int = 1
    needs: tuple[str, ...] = ()
    gpu: str | None = None
    once: bool = False
    acw: bool = False

    @property
    def chain(self) -> str:
        return self.key.rsplit("/", 1)[0]


DoneCheck = Callable[[Step], bool | None]  # None: non pianificabile (es. metodo non congelato)


# --------------------------------------------------------------------------- definizione
def _test(
    method: str, level: str, attacks: str = "[]", languages: str | None = None
) -> tuple[str, ...]:
    posthoc_l3 = method in POSTHOC and level == "L3"
    only = (f"posthoc_datasets={POSTHOC_L3_DATASETS}",) if posthoc_l3 else ()
    langs = languages or LANGS[method]
    return (f"methods=[{method}]", f"levels=[{level}]", "splits=[test]",
            f"languages={langs}", f"attacks={attacks}", *only)  # fmt: skip


def _image(wmb: str) -> tuple[str, ...]:
    return (f"execution.image_sif={wmb}/containers/sandbox_l3.sif",
            f"execution.image_dir={wmb}/containers/sandbox_l3_dir")  # fmt: skip


def baseline_steps(wmb: str) -> list[Step]:
    """Baseline e canoniche di L2 test (già fatte nella M9) e di L3 (M13): prerequisiti."""
    l3 = ("levels=[L3]", "splits=[test]")
    l2 = ("levels=[L2]", "splits=[test]")
    l1 = ("levels=[L1]", "splits=[test]")
    return [
        Step("base/L1/baseline", "ivan", "generate_baseline", l1, jobs=8, gpu="auto"),
        Step(
            "base/L1/execute",
            "ivan",
            "execute",
            (*l1, "execution.sources=[llm_baseline]"),
            jobs=2,
            needs=("base/L1/baseline",),
        ),  # fmt: skip
        # controllo negativo di T2.2(b) (D34): baseline di L1 test riscritta, una volta per tutti
        Step(
            "base/L1/T2.2/control_attack",
            "simone",
            "attack_baseline",
            (*l1, "languages=[python,java,cpp,javascript]", "attacks=[T2.2]"),
            jobs=2,
            gpu="auto",
            needs=("base/L1/baseline",),
        ),  # fmt: skip
        Step("base/L2/baseline", "ivan", "generate_baseline", l2, jobs=8, gpu="auto"),
        Step(
            "base/L2/execute",
            "ivan",
            "execute",
            (*l2, "execution.sources=[llm_baseline]"),
            jobs=2,
            needs=("base/L2/baseline",),
        ),  # fmt: skip
        Step(
            "base/L3/canonical",
            "ivan",
            "execute",
            (*l3, *_image(wmb), "execution.sources=[canonical]"),
        ),  # fmt: skip
        Step("base/L3/baseline", "ivan", "generate_baseline", l3, jobs=8, gpu="auto"),
        Step(
            "base/L3/execute",
            "ivan",
            "execute",
            (*l3, *_image(wmb), "execution.sources=[llm_baseline]"),
            jobs=2,
            needs=("base/L3/baseline",),
        ),  # fmt: skip
    ]


def test_phase(method: str, level: str, wmb: str, after: Sequence[str] = ()) -> list[Step]:
    """Fase 0 di un metodo congelato su un livello (ADR-013): [gemella], watermark, execute,
    detect, metrics; ``after`` = passi da cui dipende tutto (es. il congelamento di SWEET)."""
    owner, prefix = METHOD_OWNER[method], f"{method}/{level}"
    test = _test(method, level)
    base = (f"base/{level}/baseline",)
    base_exec = (f"base/{level}/execute",)
    image = _image(wmb) if level == "L3" else ()
    acw = method == "acw" and level in ("L2", "L3")
    gpu = None if method == "acw" or method in POSTHOC else "auto"  # post-hoc: proponente statico
    steps: list[Step] = []
    first = (*after, *base)
    sources = "[llm_watermarked]"
    marked: tuple[str, ...] = (f"{prefix}/watermark",)
    if method == "mcgmark":
        steps.append(Step(f"{prefix}/twin", owner, "generate_baseline_twin", test, jobs=2,
                          needs=first, gpu=gpu))  # fmt: skip
        sources = "[llm_watermarked,baseline_twin]"
        marked = (*marked, f"{prefix}/twin")
    steps += [
        Step(
            f"{prefix}/watermark", owner, "watermark", test, jobs=4, needs=first, gpu=gpu, acw=acw
        ),  # fmt: skip
        Step(
            f"{prefix}/execute",
            owner,
            "execute",
            (*test, *image, f"execution.sources={sources}"),
            jobs=2,
            needs=marked,
            acw=acw,
        ),  # fmt: skip
        Step(
            f"{prefix}/detect",
            owner,
            "detect",
            test,
            jobs=DETECT_JOBS.get(method, 2),
            needs=marked,
            acw=acw,
        ),  # fmt: skip
        Step(
            f"{prefix}/metrics",
            owner,
            "metrics",
            (*test, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{prefix}/execute", f"{prefix}/detect", *base_exec),
            acw=acw,
        ),  # fmt: skip
    ]
    return steps


def attack_steps(
    method: str,
    attack: str,
    level: str,
    wmb: str,
    points: Sequence[str] = (),
    languages: str | None = None,
    clean_needs: bool = True,
    suffix: str = "",
    acw: bool | None = None,
    after: Sequence[str] = (),
) -> list[Step]:
    """Un attacco sul test di un livello: attack, execute, detect, metrics (M10, M11); con
    ``points`` solo quei punti dell'attacco (``attack_points``, P6).

    Argomenti di P7.a (i default lasciano i passi come prima): ``languages`` al posto di quelli
    del metodo; ``clean_needs=False`` senza dipendenze dai passi senza attacco del livello (su L1
    i cinque metodi li hanno fatti fuori dalle catene, nella M10: gli artefatti ci sono e le fasi
    li leggono); ``suffix`` nel nome della catena (``<attacco><suffix>``, es. la variante ridotta
    di ACW); ``acw`` al posto della regola di ACW (L2 e L3); ``after`` passi in più da cui dipende
    l'attacco (P7.a: la variante completa di ACW dopo la ridotta)."""
    owner, prefix = METHOD_OWNER[method], f"{method}/{level}/{attack}{suffix}"
    chosen = (f"attack_points=[{','.join(repr(h) for h in points)}]",) if points else ()
    test = (*_test(method, level, f"[{attack}]", languages), *chosen)
    image = _image(wmb) if level == "L3" else ()
    if acw is None:
        acw = method == "acw" and level in ("L2", "L3")
    gpu_attack = "auto" if attack in ("T2.2", "T2.3", "T3.1") else None
    clean = f"{method}/{level}"
    marked = ((f"{clean}/watermark",) if clean_needs else ()) + tuple(after)
    clean_metrics = (f"{clean}/metrics",) if clean_needs else ()
    return [
        Step(
            f"{prefix}/attack",
            owner,
            "attack",
            test,
            jobs=4,
            needs=marked,
            gpu=gpu_attack,
            acw=acw,
        ),  # fmt: skip
        Step(
            f"{prefix}/execute",
            owner,
            "execute",
            (*test, *image, "execution.sources=[attacked]"),
            jobs=2,
            needs=(f"{prefix}/attack",),
            acw=acw,
        ),  # fmt: skip
        Step(
            f"{prefix}/detect",
            owner,
            "detect",
            test,
            jobs=DETECT_JOBS.get(method, 2),
            needs=(f"{prefix}/attack",),
            acw=acw,
        ),  # fmt: skip
        Step(
            f"{prefix}/metrics",
            owner,
            "metrics",
            (*test, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{prefix}/execute", f"{prefix}/detect", *clean_metrics),
            acw=acw,
        ),  # fmt: skip
    ]


def p7a_steps(method: str, wmb: str) -> list[Step]:
    """P7.a (ADR-018 §5): T1.6 e T2.5 con tutti i loro punti su L1 e L2 test, solo python.

    Su L1 nessuna dipendenza dai passi senza attacco (fatti fuori dalle catene, M10); su L2 da
    quelli delle catene (:func:`test_phase`). I passi di ACW hanno ``acw=True`` anche su L1 (la
    rilevazione usa Sourcery: partono solo con ``--with-acw``); per ACW anche la variante ridotta
    :data:`P7A_REDUCED` (catene ``acw/<livello>/<attacco>-reduced``).

    Le celle della variante ridotta sono un sottoinsieme di quelle della completa: stessi
    ``attack_params_hash``, quindi stessi artefatti e stesse cartelle di lavoro del worker. Perché
    le due varianti non scrivano mai insieme le stesse celle, l'attacco della completa dipende dalle
    metriche della ridotta: la completa parte solo a ridotta finita (e rifà solo i punti che
    mancano). Lanciare la completa vuol dire quindi dare ``--only`` per tutte e due."""
    if method not in P7A_METHODS:
        raise ValueError(f"P7.a: {method} is not among {P7A_METHODS}")
    is_acw = method == "acw"
    steps: list[Step] = []
    for level in P7A_LEVELS:
        for attack in P7A_ATTACKS:
            common: dict[str, Any] = {"languages": "[python]", "clean_needs": level != "L1",
                                      "acw": is_acw}  # fmt: skip
            after: tuple[str, ...] = ()
            if is_acw:
                reduced = attack_steps(method, attack, level, wmb, P7A_REDUCED[attack],
                                       suffix="-reduced", **common)  # fmt: skip
                steps += reduced
                after = (reduced[-1].key,)  # metriche della ridotta
            steps += attack_steps(method, attack, level, wmb, after=after, **common)
    return steps


def sweet_steps(wmb: str) -> list[Step]:
    """SWEET in pari con gli altri metodi, nell'ordine obbligatorio."""
    o, langs = "simone", LANGS["sweet"]
    sweep = ("hpo.grid=sweet",)
    dev = {lv: ("methods=[sweet]", f"levels=[{lv}]", "splits=[dev]", f"languages={langs}")
           for lv in ("L1", "L2")}  # fmt: skip
    final = ("methods=[sweet]", "levels=[L1,L2]", "splits=[dev]", f"languages={langs}")
    steps: list[Step] = []
    for lv in ("L1", "L2"):  # estensione D31 (punti `local` di configs/hpo/sweet.yaml)
        p = f"sweet/d31/{lv}"
        steps += [
            Step(f"{p}/watermark", o, "watermark", (*sweep, *dev[lv]), jobs=6, gpu="a100"),
            Step(
                f"{p}/execute",
                o,
                "execute",
                (*sweep, *dev[lv], "execution.sources=[llm_watermarked]"),
                jobs=2,
                needs=(f"{p}/watermark",),
            ),  # fmt: skip
            Step(f"{p}/detect", o, "detect", (*sweep, *dev[lv]), jobs=2, needs=(f"{p}/watermark",)),
        ]
    steps += [
        Step(
            "sweet/d31/calibrate",
            o,
            "calibrate",
            (*sweep, *final),
            needs=("sweet/d31/L1/detect", "sweet/d31/L2/detect"),
        ),  # fmt: skip
        Step(
            "sweet/d31/tune",
            o,
            "tune",
            (*final, "force=true"),
            once=True,
            needs=("sweet/d31/calibrate", "sweet/d31/L1/execute", "sweet/d31/L2/execute"),
        ),
        Step(
            "sweet/freeze",
            o,
            "freeze",
            ("methods=[sweet]", f"languages={langs}"),
            needs=("sweet/d31/tune",),
        ),  # fmt: skip
    ]
    frozen = ("sweet/freeze",)
    steps += test_phase("sweet", "L1", wmb, after=frozen)
    for attack in SWEET_ATTACKS:
        steps += attack_steps("sweet", attack, "L1", wmb)
    # controllo negativo di T2.2(b) (D34): la baseline riscritta esiste già (control_attack all)
    t22 = _test("sweet", "L1", "[T2.2]")
    steps += [
        Step(
            "sweet/L1/T2.2/control_detect",
            o,
            "detect_attack_control",
            t22,
            jobs=2,
            needs=(*frozen, "base/L1/T2.2/control_attack"),
        ),  # fmt: skip
        Step(
            "sweet/L1/T2.2/control_metrics",
            o,
            "metrics_attack_control",
            (*t22, "detection.threshold_levels=[L1,L2]"),
            needs=("sweet/L1/T2.2/control_detect", "sweet/L1/T2.2/metrics"),
        ),  # fmt: skip
    ]
    steps += t3_1_steps("sweet", wmb, after=frozen)
    steps += m12_steps("sweet", after=("sweet/L1/metrics",))
    for level in ("L2", "L3"):
        steps += test_phase("sweet", level, wmb, after=frozen)
    return steps


def t3_1_steps(
    method: str, wmb: str, after: Sequence[str], dev: bool = True, gpu: str | None = "auto"
) -> list[Step]:
    """T3.1 con la seconda chiave (ADR-014 §3), nell'ordine di ``submit_attacks_l1.sh``. Con
    ``dev=False`` senza i passi su dev e il congelamento di k2 (post-hoc, P6: fatti prima del
    congelamento da ``scripts/run_p6.sh``); ``gpu=None``: ri-marcatura su CPU (post-hoc)."""
    o, langs, p = METHOD_OWNER[method], LANGS[method], f"{method}/L1/T3.1"
    k2 = (f"methods=[{method}]", f"languages={langs}", "attacks=[]")
    test = _test(method, "L1", "[T3.1]")
    frozen_k2 = (f"{p}/k2_freeze",) if dev else tuple(after)
    steps = [
        Step(
            f"{p}/k2_detect_dev",
            o,
            "detect_k2",
            ("levels=[L1,L2]", "splits=[dev]", *k2),
            jobs=2,
            needs=tuple(after),
        ),  # fmt: skip
        Step(
            f"{p}/k2_calibrate",
            o,
            "calibrate_k2",
            ("levels=[L1,L2]", *k2),
            needs=(f"{p}/k2_detect_dev",),
        ),  # fmt: skip
        Step(
            f"{p}/k2_freeze", o, "freeze_k2", ("levels=[L1,L2]", *k2), needs=(f"{p}/k2_calibrate",)
        ),
        Step(
            f"{p}/k2_detect_test",
            o,
            "detect_k2",
            ("levels=[L1]", "splits=[test]", *k2),
            jobs=2,
            needs=(*frozen_k2, f"{method}/L1/watermark"),
        ),  # fmt: skip
        Step(
            f"{p}/attack",
            o,
            "attack",
            test,
            jobs=4,
            gpu=gpu,
            needs=(*frozen_k2, f"{method}/L1/watermark"),
        ),  # fmt: skip
        Step(
            f"{p}/execute",
            o,
            "execute",
            (*test, "execution.sources=[attacked]"),
            jobs=2,
            needs=(f"{p}/attack",),
        ),  # fmt: skip
        Step(f"{p}/detect", o, "detect", test, jobs=2, needs=(f"{p}/attack",)),
        Step(f"{p}/k2_detect", o, "detect_k2", test, jobs=2, needs=(f"{p}/attack",)),
        Step(
            f"{p}/metrics",
            o,
            "metrics",
            (*test, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{p}/execute", f"{p}/detect", f"{method}/L1/metrics"),
        ),  # fmt: skip
        Step(
            f"{p}/k2_metrics",
            o,
            "metrics_remark",
            (*test, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{p}/metrics", f"{p}/k2_detect", f"{p}/k2_detect_test"),
        ),  # fmt: skip
    ]
    return steps if dev else steps[3:]


def m12_steps(
    method: str, after: Sequence[str], sources: str = "[llm_baseline,llm_watermarked,baseline_twin]"
) -> list[Step]:
    """Impercettibilità (M12, ADR-015) sul test di L1 (post-hoc: senza gemella)."""
    o, p = METHOD_OWNER[method], f"{method}/M12"
    test = _test(method, "L1")
    return [
        Step(
            f"{p}/perplexity",
            o,
            "perplexity",
            (*test, f"execution.sources={sources}"),
            jobs=2,
            gpu="auto",
            needs=tuple(after),
        ),  # fmt: skip
        Step(f"{p}/logreg", o, "adversarial_logreg", test, needs=tuple(after)),
        Step(f"{p}/unixcoder", o, "adversarial_unixcoder", test, gpu="auto", needs=tuple(after)),
        Step(
            f"{p}/metrics",
            o,
            "metrics_imperceptibility",
            test,
            needs=(f"{p}/perplexity", f"{p}/logreg", f"{p}/unixcoder"),
        ),  # fmt: skip
    ]


def k_curve_steps(method: str, wmb: str) -> list[Step]:
    """T2.4 in k (ADR-014 §2.3) su ClassEval (L3) e sui programmi CodeNet di L2 test."""
    steps: list[Step] = []
    for level in ("L2", "L3"):
        steps += attack_steps(method, "T2.4k", level, wmb)
    return steps


def posthoc_steps(method: str, wmb: str) -> list[Step]:
    """Parte di test di un metodo post-hoc (P6, v1.3.16): fase 0 su L1, L2 e ClassEval, attacchi
    per livello (:data:`POSTHOC_ATTACKS`, punti di :data:`POSTHOC_POINTS`), controllo negativo di
    T2.2(b), T3.1 su CPU e impercettibilità su L1. Nessun passo su dev né di congelamento."""
    o = METHOD_OWNER[method]
    steps: list[Step] = []
    for level, attacks in POSTHOC_ATTACKS.items():
        steps += test_phase(method, level, wmb)
        for attack in attacks:
            points = POSTHOC_POINTS.get(level, {}).get(attack, ())
            steps += attack_steps(method, attack, level, wmb, points)
    t22 = _test(method, "L1", "[T2.2]")
    steps += [
        Step(
            f"{method}/L1/T2.2/control_detect",
            o,
            "detect_attack_control",
            t22,
            jobs=2,
            needs=("base/L1/T2.2/control_attack", f"{method}/L1/metrics"),
        ),  # fmt: skip
        Step(
            f"{method}/L1/T2.2/control_metrics",
            o,
            "metrics_attack_control",
            (*t22, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{method}/L1/T2.2/control_detect", f"{method}/L1/T2.2/metrics"),
        ),  # fmt: skip
    ]
    steps += t3_1_steps(method, wmb, after=(f"{method}/L1/metrics",), dev=False, gpu=None)
    steps += m12_steps(method, after=(f"{method}/L1/metrics",),
                       sources="[llm_baseline,llm_watermarked]")  # fmt: skip
    return steps


def all_steps(wmb: str) -> list[Step]:
    """Tutti i passi, in ordine di priorità (SWEET per primo sulle GPU)."""
    steps = sweet_steps(wmb) + k_curve_steps("sweet", wmb) + baseline_steps(wmb)
    for method in ("promptmark", "stone", "mcgmark", "acw"):
        for level in ("L2", "L3"):
            steps += test_phase(method, level, wmb)
        steps += k_curve_steps(method, wmb)
    for method in POSTHOC:
        steps += posthoc_steps(method, wmb)
    for method in P7A_METHODS:  # P7.a: in coda, lanciati solo con --only
        steps += p7a_steps(method, wmb)
    keys = [s.key for s in steps]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ValueError(f"duplicate steps: {duplicates}")
    missing = sorted({n for s in steps for n in s.needs} - set(keys))
    if missing:
        raise ValueError(f"unknown dependencies: {missing}")
    return steps


# --------------------------------------------------------------------------- completamento
def artifact_done(base: Sequence[str]) -> DoneCheck:
    """Controllo sugli artefatti: un passo è finito se esistono tutti gli output delle sue celle.

    ``None`` se le celle non si possono ancora pianificare (es. metodo non congelato).
    ``base``: override comuni (percorsi del cluster o dello smoke test).
    """
    from bench.config.builder import load_experiment
    from bench.domain.errors import BenchError
    from bench.pipeline.stage import CellPlanner
    from bench.registry import STAGES
    from bench.store.artifact_store import ArtifactStore

    def done(step: Step) -> bool | None:
        import bench.pipeline.stages  # noqa: F401  (popola STAGES)

        try:
            cfg = load_experiment([*base, f"stage={step.stage}", *step.overrides])
            stage_cls = STAGES.get(step.stage)
            stage = stage_cls.create(cfg)
            store = ArtifactStore(cfg.paths.artifacts)
            for cell in CellPlanner(cfg).cells_for(stage_cls):
                if not all(store.exists(ref) for ref in stage.outputs(cell)):
                    return False
        except BenchError as exc:
            logger.info("%s: not plannable yet: %s", step.key, exc)
            return None
        return True

    return done


# --------------------------------------------------------------------------- SLURM
class Slurm(Protocol):
    """Interfaccia verso SLURM (sostituibile nei test)."""

    def active(self, job_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        """Job attivi fra quelli dati: id → (stato, motivo)."""
        ...

    def final(self, job_ids: Iterable[str]) -> dict[str, str]:
        """Stato finale (``sacct``) dei job dati."""
        ...

    def queued(self) -> int:
        """Job dell'utente in coda o in esecuzione."""
        ...

    def pending_on(self, partition: str) -> int:
        """Job in attesa su una partizione (tutti gli utenti)."""
        ...

    def cancel(self, job_ids: Iterable[str]) -> None: ...

    def submit(
        self, step: Step, n_jobs: int, after: Sequence[str], gpu: str | None, extra: Sequence[str]
    ) -> list[str]: ...  # fmt: skip


def _run(
    cmd: Sequence[str], env: dict[str, str] | None = None, timeout: float = QUERY_TIMEOUT_S
) -> str:
    """Esegue un comando SLURM; stdin chiuso (nessuna attesa sul terminale).

    Raises:
        RuntimeError: exit code diverso da zero o tempo scaduto.
    """
    try:
        out = subprocess.run(list(cmd), capture_output=True, text=True, check=False, env=env,
                             timeout=timeout, stdin=subprocess.DEVNULL)  # fmt: skip
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{' '.join(cmd[:2])}: no answer after {timeout:g} s") from None
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: exit {out.returncode}: {out.stderr.strip()[-800:]}")
    return out.stdout


class RealSlurm:
    """``squeue``, ``sacct``, ``scancel`` e ``bench submit``."""

    def active(self, job_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        ids = [j for j in job_ids if j]
        if not ids:
            return {}
        try:
            out = _run(["squeue", "-h", "-j", ",".join(ids), "-o", "%i|%T|%r"])
        except RuntimeError as exc:
            # un job finito da tempo esce dalla memoria del controller: con un solo id squeue
            # risponde "Invalid job id specified" invece di una lista vuota
            if "Invalid job id" in str(exc):
                return {}
            raise
        found: dict[str, tuple[str, str]] = {}
        for line in out.splitlines():
            job, state, reason = [*line.split("|"), "", ""][:3]
            found[job.strip()] = (state.strip(), reason.strip())
        return found

    def final(self, job_ids: Iterable[str]) -> dict[str, str]:
        ids = [j for j in job_ids if j]
        if not ids:
            return {}
        out = _run(["sacct", "-n", "-P", "-X", "-j", ",".join(ids), "-o", "JobID,State"])
        found = {}
        for line in out.splitlines():
            job, _, state = line.partition("|")
            found[job.strip()] = state.split()[0] if state.strip() else "UNKNOWN"
        return found

    def queued(self) -> int:
        return len(_run(["squeue", "-h", "-u", os.environ.get("USER", "")]).splitlines())

    def pending_on(self, partition: str) -> int:
        return len(_run(["squeue", "-h", "-p", partition, "-t", "PD"]).splitlines())

    def cancel(self, job_ids: Iterable[str]) -> None:
        ids = [j for j in job_ids if j]
        if ids:
            _run(["scancel", *ids])

    def submit(self, step: Step, n_jobs: int, after: Sequence[str], gpu: str | None,
               extra: Sequence[str]) -> list[str]:  # fmt: skip
        cmd = ["bench", "submit", "--jobs", str(n_jobs), "--allow-concurrent"]
        if after:
            cmd += ["--after", ":".join(after), "--after-ok"]
        cmd += [f"stage={step.stage}", *step.overrides, *extra]
        env = dict(os.environ)
        if gpu:
            env["WMB_GPU"] = gpu
        try:
            out = _run(cmd, env, timeout=SUBMIT_TIMEOUT_S)
        except RuntimeError as exc:
            if "no answer after" in str(exc):
                raise SubmitTimeout(
                    f"{step.key}: {exc}; jobs may have been submitted without being recorded: "
                    "check squeue before restarting the loop"
                ) from None
            raise
        return re.findall(r"^SLURM job (\S+) ", out, flags=re.M)


# --------------------------------------------------------------------------- stato
@dataclass
class StepState:
    """Stato salvato di un passo (``<cartella>/state_<proprietario>.json``)."""

    jobs: list[str] = field(default_factory=list)
    gpu: str | None = None
    attempts: int = 0
    succeeded: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)


def load_state(path: Path) -> dict[str, StepState]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: StepState(**v) for k, v in data.items()}


def save_state(path: Path, state: dict[str, StepState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.__dict__ for k, v in state.items()}, indent=1), "utf-8")
    os.replace(tmp, path)


@dataclass
class Report:
    """Esito di un passo dopo un giro."""

    key: str
    owner: str
    # DONE, RUNNING, PENDING, SUBMITTED, READY, WAITING, BLOCKED, FAILED, DEP_FAILED, HELD
    # (BLOCKED = non ancora pianificabile; DEP_FAILED = dipende da un passo FAILED)
    status: str
    detail: str = ""


class Chains:
    """Avanza le catene di un proprietario (un giro di ``run``).

    Args:
        steps: tutti i passi (``all_steps``).
        done: controllo di completamento sugli artefatti.
        slurm: interfaccia verso SLURM.
        state_dir: cartella degli stati (una per proprietario).
        extra: override in più per ogni invio (smoke test: percorsi della cartella separata).
        with_acw: lancia anche i passi di ACW su dati nuovi.
    """

    def __init__(self, steps: Sequence[Step], done: DoneCheck, slurm: Slurm, state_dir: Path,
                 extra: Sequence[str] = (), with_acw: bool = False,
                 skip: Callable[[Step], bool] | None = None,
                 runner: str | None = None) -> None:  # fmt: skip
        self.steps = list(steps)
        # chi lancia (account): ogni account ha il suo file di stato per proprietario, così due
        # account possono avanzare catene dello stesso proprietario (con --only disgiunti)
        self.runner = re.sub(r"[^A-Za-z0-9_.-]", "_", runner or os.environ.get("USER", "") or "me")
        self.skip = skip
        self.by_key = {s.key: s for s in steps}
        self.done = done
        self.slurm = slurm
        self.state_dir = state_dir
        self.extra = tuple(extra)
        self.with_acw = with_acw
        self._done: dict[str, bool | None] = {}
        self._others: dict[str, StepState] = {}  # stati degli altri account (giro corrente)
        self._dead: dict[str, bool] = {}  # dipendenze fallite (memoria dentro un passo)

    def state_path(self, owner: str) -> Path:
        return self.state_dir / f"state_{owner}__{self.runner}.json"

    def is_done(self, step: Step, state: dict[str, StepState]) -> bool | None:
        if step.key not in self._done:
            if self.skip is not None and self.skip(step):  # smoke test: passi già "fatti"
                self._done[step.key] = True
            elif step.once:
                st = state.get(step.key)
                self._done[step.key] = bool(st and st.succeeded)
            else:
                self._done[step.key] = self.done(step)
        return self._done[step.key]

    def _all_state(self, exclude: Path | None = None) -> dict[str, StepState]:
        """Stati di tutti gli account (``state_*.json``, anche quelli senza account del primo
        formato), uniti per passo: job e storia di tutti, successo se uno è riuscito."""
        merged: dict[str, StepState] = {}
        for path in sorted(self.state_dir.glob("state_*.json")):
            if exclude is not None and path == exclude:
                continue
            for key, st in load_state(path).items():
                if key not in merged:
                    merged[key] = StepState(list(st.jobs), st.gpu, st.attempts, st.succeeded,
                                            list(st.history))  # fmt: skip
                    continue
                m = merged[key]
                m.jobs += [j for j in st.jobs if j not in m.jobs]
                m.gpu = m.gpu or st.gpu
                m.attempts = max(m.attempts, st.attempts)
                m.succeeded = m.succeeded or st.succeeded
                m.history += st.history
        return merged

    def _refresh(self, step: Step, st: StepState) -> tuple[str, str]:
        """Stato dei job registrati del passo: ACTIVE, FAILED, ENDED o NONE."""
        if not st.jobs:
            return "NONE", ""
        active = self.slurm.active(st.jobs)
        never = [j for j, (_, reason) in active.items() if "DependencyNeverSatisfied" in reason]
        if never:
            self.slurm.cancel(active)
            # il job non è mai partito: il tentativo si restituisce (fallimento a monte)
            st.attempts = max(0, st.attempts - 1)
            st.history.append({"at": _now(), "jobs": st.jobs, "end": "DependencyNeverSatisfied",
                               "attempt_refunded": True})  # fmt: skip
            st.jobs = []
            return "FAILED", "a previous step failed (DependencyNeverSatisfied): cancelled"
        if active:
            states = sorted({s for s, _ in active.values()})
            return "ACTIVE", ",".join(states)
        final = self.slurm.final(st.jobs)
        bad = sorted({s for s in final.values() if s.split()[0] in FAILED})
        st.history.append({"at": _now(), "jobs": st.jobs, "end": bad or ["COMPLETED"]})
        if step.once and not bad:
            st.succeeded = True
            self._done[step.key] = True
        st.jobs = []
        return ("FAILED", ",".join(bad)) if bad else ("ENDED", "")

    def run(self, owner: str, dry_run: bool = False, only: Sequence[str] = ()) -> list[Report]:
        """Un giro: aggiorna lo stato, cancella le attese impossibili, invia i passi pronti.

        ``only``: solo i passi il cui nome inizia con uno dei prefissi (es. ``promptmark/``), per
        dividere le catene di un proprietario fra due account senza sovrapposizioni.
        """
        path = self.state_path(owner)
        state = load_state(path)
        self._others = self._all_state(exclude=path)
        everyone = {**self._others, **state}
        free = MAX_JOBS - self.slurm.queued()
        reports: list[Report] = []
        try:
            for step in self.steps:
                if step.owner != owner or (only and not step.key.startswith(tuple(only))):
                    continue
                st = state.setdefault(step.key, StepState())
                self._dead = {}
                reports.append(self._advance(step, st, everyone, free, dry_run))
                if reports[-1].status == "SUBMITTED":
                    free -= len(st.jobs)
                if st.jobs or step.key not in everyone:  # i job dell'altro account restano visibili
                    everyone[step.key] = st
        finally:
            # anche se il giro si interrompe (comando SLURM in errore o scaduto): i job già
            # inviati restano nello stato, così il giro dopo non li rimanda
            if not dry_run:
                save_state(path, state)
        return reports

    def _is_dead(self, key: str, everyone: dict[str, StepState]) -> bool:
        """``True`` se il passo è ``FAILED`` (non finito, tentativi esauriti, nessun job attivo)
        o dipende, anche indirettamente, da un passo così: non finirà senza un ``reset``."""
        if key not in self._dead:
            self._dead[key] = False  # nessun ciclo nelle dipendenze; protegge comunque
            step = self.by_key[key]
            st = everyone.get(key, StepState())
            other = self._others.get(key)
            alive = bool(self.is_done(step, everyone)) or any(
                s is not None and s.jobs and self.slurm.active(s.jobs) for s in (st, other)
            )
            if alive:
                dead = False
            elif st.attempts >= MAX_ATTEMPTS:
                dead = True
            else:
                dead = any(self._is_dead(need, everyone) for need in step.needs)
            self._dead[key] = dead
        return self._dead[key]

    def _failed_need(self, step: Step, everyone: dict[str, StepState]) -> str | None:
        """Prima dipendenza non finita che non finirà (:meth:`_is_dead`), o ``None``."""
        return next((n for n in step.needs if self._is_dead(n, everyone)), None)

    def _cancel_behind_failure(
        self, step: Step, st: StepState, everyone: dict[str, StepState]
    ) -> Report | None:
        """Job tutti in attesa (afterok) di una dipendenza che non partirà più: si cancellano
        subito, senza aspettare che SLURM li segni ``DependencyNeverSatisfied`` e senza contare
        il tentativo. ``None`` se nessuna dipendenza è fallita."""
        failed = self._failed_need(step, everyone)
        if failed is None:
            return None
        self.slurm.cancel(st.jobs)
        st.attempts = max(0, st.attempts - 1)
        st.history.append({"at": _now(), "jobs": st.jobs, "end": f"cancelled: {failed} FAILED",
                           "attempt_refunded": True})  # fmt: skip
        st.jobs = []
        return Report(step.key, step.owner, "DEP_FAILED",
                      f"needs {failed}, which FAILED: pending jobs cancelled")  # fmt: skip

    def _advance(  # noqa: PLR0911, PLR0912 - un esito per stato del passo
        self, step: Step, st: StepState, everyone: dict[str, StepState], free: int, dry_run: bool
    ) -> Report:
        if step.acw and not self.with_acw:
            return Report(step.key, step.owner, "HELD", "ACW on new data: needs --with-acw")
        job_state, info = self._refresh(step, st) if not dry_run else ("NONE", "")
        if job_state == "ACTIVE":
            cancelled = (
                self._cancel_behind_failure(step, st, everyone) if info == "PENDING" else None
            )
            return cancelled or Report(step.key, step.owner, "RUNNING", info)
        other = self._others.get(step.key)
        if other is not None and other.jobs and self.slurm.active(other.jobs):
            return Report(step.key, step.owner, "RUNNING", "jobs of another account")
        done = self.is_done(step, everyone)
        if done:
            return Report(step.key, step.owner, "DONE")
        if st.attempts >= MAX_ATTEMPTS:  # i tentativi si contano all'invio
            last = st.history[-1] if st.history else "-"
            return Report(step.key, step.owner, "FAILED", f"{st.attempts} attempts; last: {last}")
        wait: list[str] = []
        for need in step.needs:
            if self.is_done(self.by_key[need], everyone):
                continue
            jobs = list(self.slurm.active(everyone.get(need, StepState()).jobs))
            if not jobs:
                if self._is_dead(need, everyone):
                    return Report(step.key, step.owner, "DEP_FAILED",
                                  f"needs {need}, which FAILED: not submitted")  # fmt: skip
                return Report(step.key, step.owner, "WAITING", f"needs {need}")
            wait += jobs
        if done is None and not wait:
            return Report(step.key, step.owner, "BLOCKED", "cannot be planned yet")
        if done is None:
            return Report(step.key, step.owner, "WAITING", "planned after its dependencies")
        if free < 1:
            return Report(step.key, step.owner, "READY", "no free slot")
        if dry_run:
            return Report(
                step.key, step.owner, "READY", f"would submit {min(step.jobs, free)} job(s)"
            )
        merged = everyone.get(step.key)
        gpu = st.gpu or (merged.gpu if merged is not None else None) or self._gpu(step)
        jobs = self.slurm.submit(step, min(step.jobs, free), wait, gpu, self.extra)
        st.jobs, st.gpu, st.attempts = jobs, gpu, st.attempts + 1
        st.history.append({"at": _now(), "submitted": jobs, "gpu": gpu, "after": wait})
        return Report(step.key, step.owner, "SUBMITTED", f"{jobs} gpu={gpu} after={wait}")

    def _gpu(self, step: Step) -> str | None:
        if step.gpu == "auto":
            return (
                "h100" if self.slurm.pending_on("aiq") < self.slurm.pending_on("gpuq") else "a100"
            )
        return step.gpu

    def status(self) -> list[Report]:
        """Stato di tutte le catene (entrambi i proprietari), senza inviare nulla."""
        everyone = self._all_state()
        self._others = {}
        reports = []
        for step in self.steps:
            self._dead = {}
            st = everyone.get(step.key, StepState())
            active = self.slurm.active(st.jobs) if st.jobs else {}
            # HELD solo se il passo non è mai partito: inviato da un ciclo con --with-acw, il suo
            # stato vero (in coda, in esecuzione, fallito) si vede anche da `status` senza opzione
            never_sent = not st.jobs and not st.history
            if step.acw and not self.with_acw and never_sent and not self.is_done(step, everyone):
                reports.append(Report(step.key, step.owner, "HELD", "needs --with-acw"))
            elif active:
                states = sorted({s for s, _ in active.values()})
                reports.append(Report(step.key, step.owner, "RUNNING" if "RUNNING" in states
                                      else "PENDING", ",".join(states)))  # fmt: skip
            elif self.is_done(step, everyone):
                reports.append(Report(step.key, step.owner, "DONE"))
            elif st.attempts >= MAX_ATTEMPTS:
                reports.append(Report(step.key, step.owner, "FAILED", f"{st.attempts} attempts"))
            elif (failed := self._failed_need(step, everyone)) is not None:
                reports.append(Report(step.key, step.owner, "DEP_FAILED", f"needs {failed}"))
            elif (
                st.history
                and st.history[-1].get("end") not in (None, ["COMPLETED"])
                and not st.history[-1].get("attempt_refunded")
            ):
                reports.append(Report(step.key, step.owner, "FAILED", str(st.history[-1]["end"])))
            else:
                reports.append(Report(step.key, step.owner, "TODO"))
        return reports

    def reset(self, owner: str, prefixes: Sequence[str], reason: str) -> list[str]:
        """Azzera i tentativi dei passi del proprietario che iniziano con uno dei prefissi, dopo
        aver tolto la causa dei fallimenti (es. token scaduto); la storia resta, con una riga
        ``reset``. Rifiuta i passi con job ancora attivi.

        Raises:
            RuntimeError: se un passo scelto ha job attivi.
        """
        path = self.state_path(owner)
        state = load_state(path)
        chosen = [k for k in state if k.startswith(tuple(prefixes))]
        busy = [k for k in chosen if state[k].jobs and self.slurm.active(state[k].jobs)]
        if busy:
            raise RuntimeError(f"steps with active jobs, not reset: {busy}")
        for key in chosen:
            st = state[key]
            st.history.append({"at": _now(), "reset": {"attempts": st.attempts, "reason": reason}})
            st.attempts, st.jobs = 0, []
        save_state(path, state)
        return chosen


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
