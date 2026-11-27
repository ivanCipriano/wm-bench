"""Catene della chiusura (``bench.chains``): definizione, dipendenze, afterok, ripresa e stato."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

from bench.chains import Chains, Report, Step, StepState, all_steps, load_state

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


def test_status_without_flag_shows_acw_jobs_already_sent(tmp_path: Path) -> None:
    """`status` senza --with-acw: HELD solo per i passi di ACW mai partiti."""
    slurm = FakeSlurm()
    steps = [Step("x/acw", "ivan", "watermark", (), acw=True),
             Step("y/acw", "ivan", "watermark", (), acw=True)]  # fmt: skip
    Chains(steps[:1], lambda s: False, slurm, tmp_path, with_acw=True).run("ivan")
    statuses = {r.key: r.status for r in Chains(steps, lambda s: False, slurm, tmp_path).status()}
    assert statuses == {"x/acw": "PENDING", "y/acw": "HELD"}


def test_reset_after_three_failures(tmp_path: Path) -> None:
    """Dopo 3 fallimenti il passo è fermo; ``reset`` azzera i tentativi e lascia la storia."""
    slurm = FakeSlurm()
    steps = STEPS[:1]
    for _ in range(3):
        Chains(steps, lambda s: False, slurm, tmp_path).run("ivan")
        slurm.finish(ok=False)
    chains = Chains(steps, lambda s: False, slurm, tmp_path)
    assert chains.run("ivan")[0].status == "FAILED"
    assert chains.reset("ivan", ["c/"], "token renewed") == ["c/a"]
    st = load_state(chains.state_path("ivan"))["c/a"]
    assert st.attempts == 0 and st.history[-1]["reset"]["attempts"] == 3
    assert Chains(steps, lambda s: False, slurm, tmp_path).status()[0].status == "TODO"
    assert Chains(steps, lambda s: False, slurm, tmp_path).run("ivan")[0].status == "SUBMITTED"
    with pytest.raises(RuntimeError, match="active"):  # job attivi: niente reset
        Chains(steps, lambda s: False, slurm, tmp_path).reset("ivan", ["c/"], "x")


def test_p7a_steps() -> None:
    """P7.a (ADR-018 §5): T1.6 e T2.5 su L1 e L2 test, solo python, per ACW, STONE e MCGMark
    (proprietario ivan), in coda; ACW con la variante ridotta e ``acw=True``; i passi di prima
    non cambiano."""
    from bench.attacks.base import grid_attacks
    from bench.chains import P7A_REDUCED, attack_steps, p7a_steps

    steps = all_steps(WMB)
    by = {s.key: s for s in steps}
    p7a = [s for m in ("acw", "stone", "mcgmark") for s in p7a_steps(m, WMB)]
    assert [s.key for s in steps[-len(p7a) :]] == [s.key for s in p7a]  # in coda
    assert len(p7a) == 64  # stone 16, mcgmark 16, acw 32 (completa e ridotta)
    stages = ("attack", "execute", "detect", "metrics")
    for m in ("acw", "stone", "mcgmark"):
        for level in ("L1", "L2"):
            for attack in ("T1.6", "T2.5"):
                suffixes = ("", "-reduced") if m == "acw" else ("",)
                for suffix in suffixes:
                    for stage in stages:
                        assert f"{m}/{level}/{attack}{suffix}/{stage}" in by
    owners = {s.key: s.owner for s in steps}
    for s in p7a:
        assert s.owner == "ivan" and "languages=[python]" in s.overrides
        assert s.acw == s.key.startswith("acw/") and s.gpu is None
        assert all(owners[n] == "ivan" for n in s.needs)  # niente di Simone
        if s.key.split("/")[1] == "L1":  # L1: niente passi senza attacco (fatti nella M10)
            assert all(n.startswith(s.key.rsplit("/", 1)[0]) for n in s.needs)
    assert by["stone/L2/T2.5/attack"].needs == ("stone/L2/watermark",)
    assert "mcgmark/L2/metrics" in by["mcgmark/L2/T1.6/metrics"].needs
    assert not any(o.startswith("attack_points") for o in by["acw/L1/T2.5/attack"].overrides)
    params = {a: {h: x.params for h, x in grid_attacks(a).items()} for a in ("T1.6", "T2.5")}
    assert params["T1.6"][P7A_REDUCED["T1.6"][0]] == {"caps": "convert"}
    assert params["T2.5"][P7A_REDUCED["T2.5"][0]] == {"n": 2, "position": "around"}
    assert "attack_points=['9528392ed687']" in by["acw/L2/T2.5-reduced/attack"].overrides
    # stesse celle (stessi hash): la completa parte solo a ridotta finita, mai insieme
    assert by["acw/L1/T1.6/attack"].needs == ("acw/L1/T1.6-reduced/metrics",)
    assert by["acw/L2/T2.5/attack"].needs == ("acw/L2/watermark", "acw/L2/T2.5-reduced/metrics")
    assert by["acw/L2/T2.5-reduced/attack"].needs == ("acw/L2/watermark",)
    assert by["stone/L1/T1.6/attack"].needs == ()
    # i default di attack_steps lasciano i passi di prima com'erano
    assert attack_steps("stone", "T2.4k", "L2", WMB) == [
        by[f"stone/L2/T2.4k/{st}"] for st in stages
    ]
    assert by["acw/L2/T2.4k/attack"].overrides[3] == "languages=[python]"
    assert by["stone/L3/T2.4k/attack"].overrides[3] == "languages=[python,java,cpp]"
    with pytest.raises(ValueError):
        p7a_steps("sweet", WMB)


# --------------------------------------------------------------------------- P7.a, 27 novembre
LINE = [
    Step("l/a", "ivan", "detect", ()),
    Step("l/b", "ivan", "metrics", (), needs=("l/a",)),
    Step("l/c", "ivan", "attack", (), needs=("l/b",)),
]


def _fail_a(slurm: FakeSlurm, state: dict[str, StepState], reason: str) -> None:
    """I job di l/a finiscono FAILED; quelli in attesa prendono il motivo dato."""
    for j in state["l/a"].jobs:
        slurm.jobs.pop(j, None)
        slurm.ended[j] = "FAILED"
    for j, (st, _) in list(slurm.jobs.items()):
        slurm.jobs[j] = (st, reason)


def test_failed_need_is_not_queued_and_costs_no_attempt(tmp_path: Path) -> None:
    """Un passo che dipende (anche indirettamente) da un passo FAILED non va in coda
    (DEP_FAILED, fermo), i suoi job in attesa si cancellano anche prima che SLURM li segni
    DependencyNeverSatisfied, e le cancellazioni non consumano tentativi."""
    slurm = FakeSlurm()

    def tick() -> dict[str, Report]:
        return {r.key: r for r in Chains(LINE, lambda s: False, slurm, tmp_path).run("ivan")}

    def state() -> dict[str, StepState]:
        return load_state(Chains([], lambda s: False, slurm, tmp_path).state_path("ivan"))

    assert {k: r.status for k, r in tick().items()} == dict.fromkeys(LINE_KEYS, "SUBMITTED")
    for _ in range(2):  # due fallimenti di l/a: a valle cancellati e rimandati
        _fail_a(slurm, state(), "DependencyNeverSatisfied")
        assert {k: r.status for k, r in tick().items()} == dict.fromkeys(LINE_KEYS, "SUBMITTED")
    assert state()["l/b"].attempts == 1 and state()["l/c"].attempts == 1  # tentativi restituiti
    # terzo fallimento: SLURM non ha ancora rivalutato le attese (motivo "Dependency"), come alle
    # 10:45 del 27 novembre
    _fail_a(slurm, state(), "Dependency")
    before = len(slurm.submitted)
    reports = tick()
    assert reports["l/a"].status == "FAILED"
    assert reports["l/b"].status == reports["l/c"].status == "DEP_FAILED"
    assert "l/a, which FAILED" in reports["l/b"].detail
    assert "l/b, which FAILED" in reports["l/c"].detail  # indiretto
    assert len(slurm.submitted) == before and not slurm.jobs  # nulla in coda, attese cancellate
    st = state()
    assert st["l/b"].attempts == 0 and st["l/c"].attempts == 0
    assert st["l/b"].history[-1]["attempt_refunded"] is True
    # giro dopo: fermi, senza invii; status li mostra come DEP_FAILED
    assert {r.status for r in tick().values()} == {"FAILED", "DEP_FAILED"}
    assert len(slurm.submitted) == before
    statuses = {r.key: r.status for r in Chains(LINE, lambda s: False, slurm, tmp_path).status()}
    assert statuses == {"l/a": "FAILED", "l/b": "DEP_FAILED", "l/c": "DEP_FAILED"}
    # dopo il reset del solo passo fallito la catena riparte
    Chains(LINE, lambda s: False, slurm, tmp_path).reset("ivan", ["l/a"], "token renewed")
    assert {k: r.status for k, r in tick().items()} == dict.fromkeys(LINE_KEYS, "SUBMITTED")


LINE_KEYS = [s.key for s in LINE]


def test_needs_still_retrying_are_not_dead(tmp_path: Path) -> None:
    """Un primo fallimento a monte non ferma nulla: il passo a valle si rimanda dietro il nuovo
    tentativo (comportamento di prima, tolto il tentativo consumato)."""
    slurm = FakeSlurm()
    Chains(LINE[:2], lambda s: False, slurm, tmp_path).run("ivan")
    path = Chains([], lambda s: False, slurm, tmp_path).state_path("ivan")
    _fail_a(slurm, load_state(path), "DependencyNeverSatisfied")
    reports = {r.key: r for r in Chains(LINE[:2], lambda s: False, slurm, tmp_path).run("ivan")}
    assert reports["l/b"].status == "SUBMITTED"
    assert slurm.submitted[-1][2] == tuple(load_state(path)["l/a"].jobs)


def test_state_is_saved_when_a_tick_breaks(tmp_path: Path) -> None:
    """Se un comando SLURM fallisce a metà giro, i job già inviati restano nello stato."""

    class Broken(FakeSlurm):
        def submit(self, step: Step, n_jobs: int, after: Sequence[str], gpu: str | None,
                   extra: Sequence[str]) -> list[str]:  # fmt: skip
            if step.key == "l/b":
                raise RuntimeError("bench submit: no answer after 1800 s")
            return super().submit(step, n_jobs, after, gpu, extra)

    slurm = Broken()
    chains = Chains(LINE, lambda s: False, slurm, tmp_path)
    with pytest.raises(RuntimeError):
        chains.run("ivan")
    assert load_state(chains.state_path("ivan"))["l/a"].jobs == ["100"]


def test_slurm_commands_have_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from bench import chains as mod

    seen: list[float] = []

    def hang(cmd: Sequence[str], **kw: object) -> None:
        seen.append(float(kw["timeout"]))  # type: ignore[arg-type]
        assert kw["stdin"] is subprocess.DEVNULL
        raise subprocess.TimeoutExpired(cmd, float(kw["timeout"]))  # type: ignore[arg-type]

    monkeypatch.setattr(mod.subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="no answer after 300"):
        mod.RealSlurm().queued()
    with pytest.raises(mod.SubmitTimeout, match="check squeue"):
        mod.RealSlurm().submit(LINE[0], 1, [], None, [])
    assert seen == [mod.QUERY_TIMEOUT_S, mod.SUBMIT_TIMEOUT_S]
    assert issubclass(mod.SubmitTimeout, RuntimeError)


def test_sourcery_token_warning(tmp_path: Path) -> None:
    import os

    from bench.chains import sourcery_token_warning

    token = tmp_path / "sourcery_token"
    assert "not found" in (sourcery_token_warning(token) or "")
    token.write_text("x", encoding="utf-8")
    now = token.stat().st_mtime
    assert sourcery_token_warning(token, now=now + 11.9 * 86400) is None
    warning = sourcery_token_warning(token, now=now + 13 * 86400)
    assert warning is not None and "13.0 days old" in warning
    os.utime(token, (now - 20 * 86400, now - 20 * 86400))
    assert "20." in (sourcery_token_warning(token, now=now) or "")


def _chains_script():  # type: ignore[no-untyped-def]
    import importlib.util
    import sys

    from tests.conftest import REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "wmb_chains_script", REPO_ROOT / "scripts" / "chains.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_loop_tick_lines_terminal_and_submit_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ciclo: riga ``tick start`` a ogni giro, DEP_FAILED fermo (il ciclo finisce con exit 1),
    ``SubmitTimeout`` ferma il ciclo (exit 3); avviso sul token solo con passi di ACW scelti."""
    import argparse

    script = _chains_script()
    steps = [Step("acw/L1/x", "ivan", "detect", ()), Step("stone/L1/x", "ivan", "detect", ())]
    monkeypatch.setattr(script, "setup", lambda smoke: (steps, [], tmp_path, []))
    monkeypatch.setattr(script, "sourcery_token_warning", lambda: "WARNING: old token")
    args = argparse.Namespace(smoke=False, owner="ivan", only=["stone/"], every=0)
    assert script.token_warning(steps, args) is None
    monkeypatch.setattr(
        script, "run_once",
        lambda a: [Report("stone/L1/x", "ivan", "DEP_FAILED", "needs y, which FAILED")],
    )  # fmt: skip
    assert script.loop(args) == 1
    lines = (tmp_path / "loop_ivan.log").read_text(encoding="utf-8").splitlines()
    assert (
        lines[0].endswith(" tick start") and "blocked by a failed step ['stone/L1/x']" in lines[-1]
    )
    assert not any("WARNING" in line for line in lines)

    def timeout(a: argparse.Namespace) -> list[Report]:
        raise script.SubmitTimeout("acw/L1/x: bench submit: no answer after 1800 s")

    monkeypatch.setattr(script, "run_once", timeout)
    args.only = []
    assert script.token_warning(steps, args) == "WARNING: old token"
    assert script.loop(args) == 3
    lines = (tmp_path / "loop_ivan.log").read_text(encoding="utf-8").splitlines()
    assert lines[-3].endswith(" tick start") and lines[-2].endswith("WARNING: old token")
    assert lines[-1].endswith("loop stopped")
