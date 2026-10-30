"""Script principale delle catene (M13, M14, SWEET; ``bench.chains``).

Uso (con bench-core, nodo di login; ognuno lancia solo i propri passi):
    python scripts/chains.py status                      # stato di tutte le catene (nessun invio)
    python scripts/chains.py run --owner ivan [--dry-run] [--with-acw]
    python scripts/chains.py loop --owner ivan [--with-acw]   # in tmux: un giro ogni 30 minuti

``run`` fa un giro: aggiorna lo stato dei job, cancella le attese impossibili
(``DependencyNeverSatisfied``) e invia i passi pronti fino ai posti liberi (10 meno i job in coda),
con ``afterok`` sui passi ancora in corso. È riprendibile: un passo finito (output tutti presenti)
non si rilancia, uno interrotto si rilancia e le fasi riprendono dalle celle complete. ``loop``
ripete ``run`` ogni ``--every`` secondi, scrive una riga per passo cambiato in
``$WMB/logs/chains/loop_<owner>.log`` e si ferma da solo quando i passi del proprietario sono tutti
finiti (o fermi: ``HELD`` senza ``--with-acw``, ``FAILED`` dopo 3 tentativi, da guardare).

Con ``--smoke`` lavora nella cartella separata ``$WMB/smoke/`` (artefatti, stati, job con un job
per passo) preparata da ``scripts/smoke_seed.py``: lo store dei job ha ``WMB_WRITE_ROOT`` e si
ferma con un errore se prova a scrivere fuori. Nello smoke test i passi di SWEET prima del
congelamento non girano: il congelamento finto è nella cartella dello smoke test.
Nodi guasti: ``export WMB_EXCLUDE_NODES=gnode13,gnode14`` prima del comando.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from bench.chains import OWNERS, Chains, RealSlurm, Report, Step, all_steps, artifact_done
from bench.config.builder import load_experiment
from bench.store.artifact_store import WRITE_ROOT_ENV

SMOKE_SKIP = ("sweet/d31/", "sweet/freeze")  # congelamento finto già nello smoke test
TERMINAL = {"DONE", "HELD", "FAILED"}


def setup(smoke: bool) -> tuple[list[Step], list[str], Path, list[str]]:
    """Passi, override di base, cartella degli stati e override in più degli invii."""
    wmb = str(load_experiment(["paths=cluster", "stage=selftest"]).paths.wmb)
    base = ["paths=cluster"]
    extra: list[str] = []
    state_dir = Path(wmb) / "logs" / "chains"
    steps = all_steps(wmb)
    if smoke:
        root = Path(wmb) / "smoke"
        extra = [f"paths.artifacts={root}/artifacts", f"paths.tmp={root}/tmp"]
        base += extra
        state_dir = root / "chains"
        os.environ[WRITE_ROOT_ENV] = str(root)
        steps = [dataclasses.replace(s, jobs=1) for s in steps]
    return steps, base, state_dir, extra


def make(args: argparse.Namespace) -> Chains:
    steps, base, state_dir, extra = setup(args.smoke)
    real_done = artifact_done(base)

    def skip(step: Step) -> bool:
        # anche i passi ``once`` (tune con force): il congelamento finto c'è già
        return bool(args.smoke and step.key.startswith(SMOKE_SKIP))

    return Chains(steps, real_done, RealSlurm(), state_dir, extra, with_acw=args.with_acw,
                  skip=skip)  # fmt: skip


def print_reports(reports: list[Report], verbose: bool = True) -> None:
    by_chain: dict[str, list[Report]] = defaultdict(list)
    for r in reports:
        by_chain[r.key.rsplit("/", 1)[0]].append(r)
    for chain, items in by_chain.items():
        counts = Counter(r.status for r in items)
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        sys.stdout.write(f"{chain:<34} {items[0].owner:<7} {summary}\n")
        if verbose:
            for r in items:
                if r.status != "DONE":
                    name = r.key.rsplit("/", 1)[1]
                    sys.stdout.write(f"    {name:<20} {r.status:<9} {r.detail}\n")


@contextlib.contextmanager
def lock(state_dir: Path, owner: str):  # type: ignore[no-untyped-def]
    """Un solo giro alla volta per proprietario (un lucchetto più vecchio di 3 ore è scaduto)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"lock_{owner}"
    if path.exists() and time.time() - path.stat().st_mtime > 3 * 3600:
        path.unlink()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"another run for {owner} is in progress ({path})") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def failed_logs(chains: Chains, reports: list[Report], lines: int = 15) -> None:
    """Ultime righe dei log (submitit) dei job dei passi falliti."""
    states = {}
    states.update(chains._all_state())
    base = load_experiment([*setup(chains.state_dir.parent.name == "smoke")[1],
                            "stage=selftest"]).paths.artifacts  # fmt: skip
    for r in reports:
        if r.status != "FAILED":
            continue
        st = states.get(r.key)
        jobs = [j for h in (st.history if st else []) for j in h.get("submitted", [])][-2:]
        stage = chains.by_key[r.key].stage
        for job in jobs:
            for log in sorted((Path(base) / "_slurm" / stage / job).glob("*_log.err")):
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
                sys.stdout.write(f"\n----- {r.key} job {job}: {log}\n")
                sys.stdout.write("\n".join(tail) + "\n")


def run_once(args: argparse.Namespace) -> list[Report]:
    chains = make(args)
    owners = OWNERS if args.owner == "all" else (args.owner,)
    reports: list[Report] = []
    for owner in owners:  # "all": un solo account per tutte le catene (smoke test)
        with lock(chains.state_dir, owner):
            reports += chains.run(owner, dry_run=args.dry_run, only=args.only)
    return reports


def loop(args: argparse.Namespace) -> int:
    _, _, state_dir, _ = setup(args.smoke)
    log = state_dir / f"loop_{args.owner}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    previous: dict[str, str] = {}
    while True:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            reports = run_once(args)
        except (RuntimeError, OSError, SystemExit) as exc:  # un giro fallito non ferma il ciclo
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} ERROR {exc}\n")
            time.sleep(args.every)
            continue
        changed = [r for r in reports if previous.get(r.key) != r.status]
        previous = {r.key: r.status for r in reports}
        counts = Counter(r.status for r in reports)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {dict(sorted(counts.items()))}\n")
            for r in changed:
                handle.write(f"{stamp}   {r.key} {r.status} {r.detail}\n")
        sys.stdout.write(f"{stamp} {dict(sorted(counts.items()))} (log: {log})\n")
        if all(r.status in TERMINAL for r in reports):
            failed = [r.key for r in reports if r.status == "FAILED"]
            held = [r.key for r in reports if r.status == "HELD"]
            message = f"{stamp} all chains of {args.owner} ended; failed {failed}; held {held}\n"
            with log.open("a", encoding="utf-8") as handle:
                handle.write(message)
            sys.stdout.write(message)
            return 1 if failed else 0
        time.sleep(args.every)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["status", "run", "loop"])
    parser.add_argument("--owner", choices=[*OWNERS, "all"])
    parser.add_argument("--logs", action="store_true", help="status: log tails of failed steps")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="run/loop: only steps whose name starts with this prefix (repeatable)",
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--with-acw", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--every", type=int, default=1800)
    parser.add_argument("--brief", action="store_true", help="status: one line per chain")
    args = parser.parse_args()
    if args.command == "status":
        chains = make(args)
        reports = chains.status()
        print_reports(reports, verbose=not args.brief)
        if args.logs:
            failed_logs(chains, reports)
        return 0
    if args.owner is None:
        parser.error(f"{args.command} needs --owner")
    if args.command == "run":
        print_reports(run_once(args))
        return 0
    return loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
