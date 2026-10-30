"""Catene di lavoro della chiusura (M13, M14, SWEET): passi, dipendenze, stato e invio.

Decisioni dell'utente del 30 ottobre 2026 (``docs/milestone_logs/M13_M14_plan.md``):

- un **passo** è un invio di ``bench submit`` (fase + override Hydra) con un proprietario
  (``ivan`` o ``simone``): ognuno lancia solo i propri passi, con i suoi 10 posti (MaxSubmit);
- un passo è **finito** quando esistono tutti gli output delle sue celle (gli stessi che la fase
  salterebbe): rilanciare non rifà il lavoro completo. I passi ``once`` (scelta rifatta con
  ``force=true``) sono finiti quando un loro invio è terminato con successo;
- un passo parte quando i passi da cui dipende sono finiti, oppure subito con
  ``--dependency=afterok`` sui job ancora attivi di quei passi; un job in attesa con
  ``DependencyNeverSatisfied`` (un passo precedente fallito) si cancella e il passo si ritenta;
- la rilevazione resta su ``gpuq`` (``bench.pipeline.submit.DETECT_STAGES``); la generazione può
  andare su ``aiq`` (``gpu="auto"``: la partizione con meno job in attesa), e una volta scelta la
  GPU di un passo resta la stessa nei tentativi successivi (una cella tutta su un tipo di GPU);
- i passi di ACW su L2 test e L3 partono solo con ``--with-acw`` (licenza di Sourcery confermata
  dall'utente).

Ordine obbligatorio di SWEET: estensione D31 su L1 e L2 dev → soglie e scelta su L1+L2 →
congelamento → fase 0 sul test → attacchi T1-T4 → M12 → L2 test e L3; nessun passo sul test prima
del congelamento (i passi di test dipendono da ``sweet/freeze``).
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
}
METHOD_OWNER = {"sweet": "simone", "promptmark": "simone", "stone": "ivan", "mcgmark": "ivan",
                "acw": "ivan"}  # fmt: skip
SWEET_ATTACKS = ("T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T2.1", "T2.2", "T2.4", "T4.1", "T4.2")
ACTIVE = ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "REQUEUED", "RESIZING", "SUSPENDED")
FAILED = ("FAILED", "TIMEOUT", "CANCELLED", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL", "DEADLINE",
          "PREEMPTED")  # fmt: skip


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
def _test(method: str, level: str, attacks: str = "[]") -> tuple[str, ...]:
    return (f"methods=[{method}]", f"levels=[{level}]", "splits=[test]",
            f"languages={LANGS[method]}", f"attacks={attacks}")  # fmt: skip


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
    gpu = None if method == "acw" else "auto"
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
        Step(f"{prefix}/detect", owner, "detect", test, jobs=2, needs=marked, acw=acw),
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


def attack_steps(method: str, attack: str, level: str, wmb: str) -> list[Step]:
    """Un attacco sul test di un livello: attack, execute, detect, metrics (M10, M11)."""
    owner, prefix = METHOD_OWNER[method], f"{method}/{level}/{attack}"
    test = _test(method, level, f"[{attack}]")
    image = _image(wmb) if level == "L3" else ()
    acw = method == "acw" and level in ("L2", "L3")
    gpu_attack = "auto" if attack in ("T2.2", "T2.3", "T3.1") else None
    clean = f"{method}/{level}"
    return [
        Step(
            f"{prefix}/attack",
            owner,
            "attack",
            test,
            jobs=4,
            needs=(f"{clean}/watermark",),
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
            f"{prefix}/detect", owner, "detect", test, jobs=2, needs=(f"{prefix}/attack",), acw=acw
        ),  # fmt: skip
        Step(
            f"{prefix}/metrics",
            owner,
            "metrics",
            (*test, "detection.threshold_levels=[L1,L2]"),
            needs=(f"{prefix}/execute", f"{prefix}/detect", f"{clean}/metrics"),
            acw=acw,
        ),  # fmt: skip
    ]


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


def t3_1_steps(method: str, wmb: str, after: Sequence[str]) -> list[Step]:
    """T3.1 con la seconda chiave (ADR-014 §3), nell'ordine di ``submit_attacks_l1.sh``."""
    o, langs, p = METHOD_OWNER[method], LANGS[method], f"{method}/L1/T3.1"
    k2 = (f"methods=[{method}]", f"languages={langs}", "attacks=[]")
    test = _test(method, "L1", "[T3.1]")
    return [
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
            needs=(f"{p}/k2_freeze", f"{method}/L1/watermark"),
        ),  # fmt: skip
        Step(
            f"{p}/attack",
            o,
            "attack",
            test,
            jobs=4,
            gpu="auto",
            needs=(f"{p}/k2_freeze", f"{method}/L1/watermark"),
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


def m12_steps(method: str, after: Sequence[str]) -> list[Step]:
    """Impercettibilità (M12, ADR-015) sul test di L1."""
    o, p = METHOD_OWNER[method], f"{method}/M12"
    test = _test(method, "L1")
    return [
        Step(
            f"{p}/perplexity",
            o,
            "perplexity",
            (*test, "execution.sources=[llm_baseline,llm_watermarked,baseline_twin]"),
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


def all_steps(wmb: str) -> list[Step]:
    """Tutti i passi, in ordine di priorità (SWEET per primo sulle GPU)."""
    steps = sweet_steps(wmb) + k_curve_steps("sweet", wmb) + baseline_steps(wmb)
    for method in ("promptmark", "stone", "mcgmark", "acw"):
        for level in ("L2", "L3"):
            steps += test_phase(method, level, wmb)
        steps += k_curve_steps(method, wmb)
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


def _run(cmd: Sequence[str], env: dict[str, str] | None = None) -> str:
    out = subprocess.run(list(cmd), capture_output=True, text=True, check=False, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: exit {out.returncode}: {out.stderr.strip()[-800:]}")
    return out.stdout


class RealSlurm:
    """``squeue``, ``sacct``, ``scancel`` e ``bench submit``."""

    def active(self, job_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        ids = [j for j in job_ids if j]
        if not ids:
            return {}
        out = _run(["squeue", "-h", "-j", ",".join(ids), "-o", "%i|%T|%r"])
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
        return re.findall(r"^SLURM job (\S+) ", _run(cmd, env), flags=re.M)


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
    status: str  # DONE, RUNNING, PENDING, SUBMITTED, READY, WAITING, BLOCKED, FAILED, HELD
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
                 extra: Sequence[str] = (), with_acw: bool = False) -> None:  # fmt: skip
        self.steps = list(steps)
        self.by_key = {s.key: s for s in steps}
        self.done = done
        self.slurm = slurm
        self.state_dir = state_dir
        self.extra = tuple(extra)
        self.with_acw = with_acw
        self._done: dict[str, bool | None] = {}

    def state_path(self, owner: str) -> Path:
        return self.state_dir / f"state_{owner}.json"

    def is_done(self, step: Step, state: dict[str, StepState]) -> bool | None:
        if step.key not in self._done:
            if step.once:
                st = state.get(step.key)
                self._done[step.key] = bool(st and st.succeeded)
            else:
                self._done[step.key] = self.done(step)
        return self._done[step.key]

    def _all_state(self) -> dict[str, StepState]:
        merged: dict[str, StepState] = {}
        for owner in OWNERS:
            merged.update(load_state(self.state_path(owner)))
        return merged

    def _refresh(self, step: Step, st: StepState) -> tuple[str, str]:
        """Stato dei job registrati del passo: ACTIVE, FAILED, ENDED o NONE."""
        if not st.jobs:
            return "NONE", ""
        active = self.slurm.active(st.jobs)
        never = [j for j, (_, reason) in active.items() if "DependencyNeverSatisfied" in reason]
        if never:
            self.slurm.cancel(active)
            st.history.append({"at": _now(), "jobs": st.jobs, "end": "DependencyNeverSatisfied"})
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

    def run(self, owner: str, dry_run: bool = False) -> list[Report]:
        """Un giro: aggiorna lo stato, cancella le attese impossibili, invia i passi pronti."""
        path = self.state_path(owner)
        state = load_state(path)
        everyone = {**self._all_state(), **state}
        free = MAX_JOBS - self.slurm.queued()
        reports: list[Report] = []
        for step in self.steps:
            if step.owner != owner:
                continue
            st = state.setdefault(step.key, StepState())
            reports.append(self._advance(step, st, everyone, free, dry_run))
            if reports[-1].status == "SUBMITTED":
                free -= len(st.jobs)
            everyone[step.key] = st
        if not dry_run:
            save_state(path, state)
        return reports

    def _advance(  # noqa: PLR0911 - un esito per stato del passo
        self, step: Step, st: StepState, everyone: dict[str, StepState], free: int, dry_run: bool
    ) -> Report:
        if step.acw and not self.with_acw:
            return Report(step.key, step.owner, "HELD", "ACW on new data: needs --with-acw")
        job_state, info = self._refresh(step, st) if not dry_run else ("NONE", "")
        if job_state == "ACTIVE":
            return Report(step.key, step.owner, "RUNNING", info)
        done = self.is_done(step, everyone)
        if done:
            return Report(step.key, step.owner, "DONE")
        if st.attempts >= MAX_ATTEMPTS:  # i tentativi si contano all'invio
            last = st.history[-1] if st.history else "-"
            return Report(step.key, step.owner, "FAILED", f"{st.attempts} attempts; last: {last}")
        wait: list[str] = []
        for need in step.needs:
            other = self.by_key[need]
            if self.is_done(other, everyone):
                continue
            jobs = everyone.get(need, StepState()).jobs
            if not jobs:
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
        gpu = st.gpu or self._gpu(step)
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
        reports = []
        for step in self.steps:
            st = everyone.get(step.key, StepState())
            active = self.slurm.active(st.jobs) if st.jobs else {}
            if step.acw and not self.with_acw and not self.is_done(step, everyone):
                reports.append(Report(step.key, step.owner, "HELD", "needs --with-acw"))
            elif active:
                states = sorted({s for s, _ in active.values()})
                reports.append(Report(step.key, step.owner, "RUNNING" if "RUNNING" in states
                                      else "PENDING", ",".join(states)))  # fmt: skip
            elif self.is_done(step, everyone):
                reports.append(Report(step.key, step.owner, "DONE"))
            elif st.attempts >= MAX_ATTEMPTS:
                reports.append(Report(step.key, step.owner, "FAILED", f"{st.attempts} attempts"))
            elif st.history and st.history[-1].get("end") not in (None, ["COMPLETED"]):
                reports.append(Report(step.key, step.owner, "FAILED", str(st.history[-1]["end"])))
            else:
                reports.append(Report(step.key, step.owner, "TODO"))
        return reports


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
