"""Prova della rimessa in coda automatica al timeout (job CPU su defq, circa 10-15 minuti).

Uso (con bench-core, nodo di login):
    python scripts/test_requeue.py submit     # invia il job di prova e stampa l'ID
    python scripts/test_requeue.py check ID   # a job finito: esito

Il job usa gli stessi parametri di ``bench submit`` (``executor_parameters``, quindi anche
``--requeue``), ma con 3 minuti di tempo e il segnale 60 s prima: le prime 2 esecuzioni dormono
oltre il limite e devono essere rimesse in coda da submitit; la terza termina subito. Esito atteso:
``Restarts=2`` e risultato ``done after 2 requeues``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from bench.config.builder import load_experiment
from bench.pipeline.submit import executor_parameters

WMB = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench"
FOLDER = Path(WMB) / "tmp" / "requeue_test"
REQUEUES = 2


class SleepyJob:
    """Dorme oltre il limite finché non è stato rimesso in coda ``REQUEUES`` volte."""

    def __call__(self, counter: str) -> str:
        path = Path(counter)
        runs = int(path.read_text()) if path.is_file() else 0
        path.write_text(str(runs + 1))
        if runs < REQUEUES:
            time.sleep(600)  # oltre i 3 minuti: arriva il segnale di timeout
            return f"not requeued (run {runs + 1})"
        return f"done after {runs} requeues"

    def checkpoint(self, counter: str) -> object:
        from submitit.helpers import DelayedSubmission

        return DelayedSubmission(self, counter)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("submit", "check"):  # noqa: PLR2004
        sys.stderr.write(__doc__ or "")
        return 2
    import submitit

    if sys.argv[1] == "submit":
        cfg = load_experiment(["paths=cluster", "stage=execute", "cluster=slurm_cpu"])
        profile = cfg.cluster.model_copy(update={"timeout_min": 3, "cpus_per_task": 1, "mem_gb": 2})
        params = executor_parameters(cfg, profile, "requeue_test", name="wmb-requeue-test")
        FOLDER.mkdir(parents=True, exist_ok=True)
        counter = FOLDER / "counter.txt"
        counter.unlink(missing_ok=True)
        executor = submitit.AutoExecutor(
            folder=str(FOLDER / "%j"), cluster="slurm", slurm_max_num_timeout=REQUEUES + 1
        )
        executor.update_parameters(**params, slurm_signal_delay_s=60)
        job = executor.submit(SleepyJob(), str(counter))
        sys.stdout.write(f"submitted {job.job_id}\n")
        return 0
    job_id = sys.argv[2]
    job = submitit.SlurmJob(folder=str(FOLDER / "%j"), job_id=job_id)
    try:
        sys.stdout.write(f"result: {job.result()}\n")
    except Exception as exc:
        sys.stdout.write(f"error: {type(exc).__name__}: {exc}\n")
    counter = FOLDER / "counter.txt"
    sys.stdout.write(f"runs: {counter.read_text() if counter.is_file() else 0}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
