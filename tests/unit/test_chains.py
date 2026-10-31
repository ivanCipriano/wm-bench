"""Catene della chiusura (``bench.chains``): definizione, dipendenze, afterok, ripresa e stato."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

from bench.chains import Chains, Step, StepState, all_steps, load_state

WMB = "/wmb"


class FakeSlurm:
    def __init__(self) -> None:
        self.next_id = 100
        self.jobs: dict[str, tuple[str, str]] = {}  # attivi: id -> (stato, motivo)
        self.ended: dict[str, str] = {}
        self.submitted: list[tuple[str, int, tuple[str, ...], str | None]] = []
        self.pending = {"aiq": 0, "gpuq": 5}

    def active(self, job_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        return {j: self.jobs[j] for j in job_ids if j in self.jobs}

    def final(self, job_ids: Iterable[str]) -> dict[str, str]:
        return {j: self.ended.get(j, "COMPLETED") for j in job_ids}

    def queued(self) -> int:
        return len(self.jobs)

    def pending_on(self, partition: str) -> int:
        return self.pending[partition]

    def cancel(self, job_ids: Iterable[str]) -> None:
        for j in list(job_ids):
            self.jobs.pop(j, None)
            self.ended[j] = "CANCELLED"

    def submit(self, step: Step, n_jobs: int, after: Sequence[str], gpu: str | None,
               extra: Sequence[str]) -> list[str]:  # fmt: skip
        ids = [str(self.next_id + i) for i in range(n_jobs)]
        self.next_id += n_jobs
        for j in ids:
            self.jobs[j] = ("PENDING", "Dependency" if after else "Priority")
        self.submitted.append((step.key, n_jobs, tuple(after), gpu))
        return ids

    def finish(self, ok: bool = True) -> None:
        for j in list(self.jobs):
            self.ended[j] = "COMPLETED" if ok else "FAILED"
        self.jobs.clear()


STEPS = [
    Step("c/a", "ivan", "watermark", (), jobs=2, gpu="auto"),
    Step("c/b", "ivan", "detect", (), needs=("c/a",)),
    Step("c/c", "simone", "metrics", (), needs=("c/b",)),
    Step("c/t", "ivan", "tune", (), once=True),
]


def test_definition_is_consistent() -> None:
    steps = all_steps(WMB)
    keys = {s.key for s in steps}
    assert "sweet/freeze" in keys and "stone/L3/metrics" in keys and "acw/L3/T2.4k/metrics" in keys
    by = {s.key: s for s in steps}
    # nessun passo di SWEET sul test prima del congelamento
    for s in steps:
        if s.key.startswith("sweet/L") and s.stage == "watermark":
            assert "sweet/freeze" in s.needs
    assert by["sweet/d31/tune"].once and "force=true" in by["sweet/d31/tune"].overrides
    assert (
        by["mcgmark/L3/execute"].overrides[-1]
        == "execution.sources=[llm_watermarked,baseline_twin]"
    )
    assert any("sandbox_l3" in o for o in by["stone/L3/execute"].overrides)
    assert not any("sandbox_l3" in o for o in by["stone/L2/execute"].overrides)
    assert all(s.owner == "simone" for s in steps if s.key.startswith(("sweet/", "promptmark/")))
    assert by["acw/L3/watermark"].acw and not by["stone/L3/watermark"].acw
    assert by["sweet/d31/L1/watermark"].gpu == "a100"  # sviluppo di SWEET sullo stesso hardware


def test_chain_advances_with_afterok_and_resumes(tmp_path: Path) -> None:
    slurm = FakeSlurm()
    done: dict[str, bool] = {}
    chains = Chains(STEPS, lambda s: done.get(s.key, False), slurm, tmp_path)
    reports = {r.key: r.status for r in chains.run("ivan")}
    assert reports == {"c/a": "SUBMITTED", "c/b": "SUBMITTED", "c/t": "SUBMITTED"}
    # c/b parte dopo c/a con afterok; la generazione va su aiq (coda più corta)
    assert slurm.submitted[0] == ("c/a", 2, (), "h100")
    assert slurm.submitted[1][0] == "c/b" and slurm.submitted[1][2] == ("100", "101")
    # secondo giro con i job ancora attivi: nessun nuovo invio
    chains = Chains(STEPS, lambda s: done.get(s.key, False), slurm, tmp_path)
    assert {r.status for r in chains.run("ivan")} == {"RUNNING"}
    # c/a fallisce: c/b resta in attesa impossibile, si cancella; al giro dopo si ritenta c/a
    slurm.ended.update({"100": "FAILED", "101": "COMPLETED"})
    del slurm.jobs["100"], slurm.jobs["101"]
    slurm.jobs["102"] = ("PENDING", "DependencyNeverSatisfied")
    chains = Chains(STEPS, lambda s: done.get(s.key, False), slurm, tmp_path)
    reports = {r.key: r for r in chains.run("ivan")}
    assert reports["c/a"].status == "SUBMITTED"
    state = load_state(Chains([], lambda s: False, slurm, tmp_path).state_path("ivan"))
    assert state["c/a"].attempts == 2 and state["c/a"].gpu == "h100"  # stessa GPU al tentativo
    assert "102" not in slurm.jobs
    # tune (once): finito quando un invio termina bene
    slurm.finish()
    done.update({"c/a": True, "c/b": True})
    chains = Chains(STEPS, lambda s: done.get(s.key, False), slurm, tmp_path)
    assert {r.key: r.status for r in chains.run("ivan")}["c/t"] == "DONE"
    statuses = {r.key: r.status for r in Chains(STEPS, lambda s: done.get(s.key, False), slurm,
                                                 tmp_path).status()}  # fmt: skip
    assert statuses["c/c"] == "TODO" and statuses["c/a"] == "DONE"


def test_owner_slots_held_and_blocked(tmp_path: Path) -> None:
    slurm = FakeSlurm()
    for i in range(10):
        slurm.jobs[str(i)] = ("RUNNING", "")
    steps = [*STEPS, Step("x/acw", "ivan", "watermark", (), acw=True)]
    chains = Chains(steps, lambda s: None if s.key == "c/b" else False, slurm, tmp_path)
    reports = {r.key: r.status for r in chains.run("ivan")}
    assert reports["c/a"] == "READY" and reports["x/acw"] == "HELD"  # nessun posto libero
    # simone: c/c aspetta c/b, di ivan e senza job
    assert {r.key: r.status for r in chains.run("simone")} == {"c/c": "WAITING"}


def test_max_attempts(tmp_path: Path) -> None:
    slurm = FakeSlurm()
    chains = Chains(STEPS[:1], lambda s: False, slurm, tmp_path)
    for _ in range(3):
        chains = Chains(STEPS[:1], lambda s: False, slurm, tmp_path)
        chains.run("ivan")
        slurm.finish(ok=False)
    reports = Chains(STEPS[:1], lambda s: False, slurm, tmp_path).run("ivan")
    assert reports[0].status == "FAILED"
    assert isinstance(
        load_state(Chains([], lambda s: False, slurm, tmp_path).state_path("ivan"))["c/a"],
        StepState,
    )


@pytest.mark.parametrize("owner", ["ivan", "simone"])
def test_dry_run_submits_nothing(tmp_path: Path, owner: str) -> None:
    slurm = FakeSlurm()
    Chains(STEPS, lambda s: False, slurm, tmp_path).run(owner, dry_run=True)
    assert slurm.submitted == [] and not list(tmp_path.glob("state_*.json"))


def test_artifact_done_on_an_empty_store(local_root: Path) -> None:
    from bench.chains import artifact_done, test_phase

    done = artifact_done(["paths=local"])
    base = Step("base/L1/baseline", "ivan", "generate_baseline", ("levels=[L1]", "splits=[test]"))
    assert done(base) is False
    watermark = next(s for s in test_phase("stone", "L3", WMB) if s.key.endswith("/watermark"))
    assert done(watermark) is None  # STONE non congelato in uno store vuoto: non pianificabile


def test_skip_also_covers_once_steps(tmp_path: Path) -> None:
    """Smoke test: i passi saltati (anche ``once``, come tune) sono finiti e non si inviano."""
    slurm = FakeSlurm()
    chains = Chains(STEPS, lambda s: False, slurm, tmp_path, skip=lambda s: s.key == "c/t")
    reports = {r.key: r.status for r in chains.run("ivan")}
    assert reports["c/t"] == "DONE"
    assert "c/t" not in [k for k, *_ in slurm.submitted]


def test_two_accounts_split_one_owner_without_duplicates(tmp_path: Path) -> None:
    """Due account sullo stesso proprietario: ognuno ha il suo stato e vede i job dell'altro."""
    slurm = FakeSlurm()
    a = Chains(STEPS, lambda s: False, slurm, tmp_path, runner="a")
    reports = {r.key: r.status for r in a.run("ivan", only=["c/a"])}
    assert reports == {"c/a": "SUBMITTED"}
    b = Chains(STEPS, lambda s: False, slurm, tmp_path, runner="b")
    reports = {r.key: r.status for r in b.run("ivan")}
    assert reports["c/a"] == "RUNNING"  # job dell'altro account: nessun doppione
    assert reports["c/b"] == "SUBMITTED"  # afterok sui job di a
    assert next(x for x in slurm.submitted if x[0] == "c/b")[2] == ("100", "101")
    assert {p.name for p in tmp_path.glob("state_*.json")} == {
        "state_ivan__a.json",
        "state_ivan__b.json",
    }


def test_purged_job_is_not_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un job uscito dalla memoria di SLURM non è attivo (squeue -j risponde con un errore)."""
    from bench import chains as mod

    def fake_run(cmd: Sequence[str], env: dict[str, str] | None = None) -> str:
        raise RuntimeError("squeue: exit 1: slurm_load_jobs error: Invalid job id specified")

    monkeypatch.setattr(mod, "_run", fake_run)
    assert mod.RealSlurm().active(["808795"]) == {}
