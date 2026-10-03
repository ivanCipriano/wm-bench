"""Runner dei test dentro la sandbox (ADR-001, SPEC §11).

Gira **dentro** il container (``/opt/wmb/wmb_runner.py``) con il Python dell'immagine e non
importa nulla del framework: legge ``job.json``, esegue i campioni e scrive ``result.json``
nella stessa cartella. Un'invocazione del container = un problema (fino a 6 campioni); ogni
campione gira in un sottoprocesso proprio, in una cartella propria, con timeout e limite di
memoria.

Modalità (campo ``mode``):

- ``samples`` con ``kind``:
  - ``program``: programma completo già assemblato dall'host (HumanEvalPack, ``syntax``);
    compilazione ``compile_cmd`` (facoltativa), esecuzione ``run_cmd``, classificazione con
    la regola ``rule`` (``cpp``, ``java``, ``javascript``, ``syntax``);
  - ``evalplus``: funzioni di EvalPlus (``untrusted_check``) su test base+plus, con la ground
    truth del problema in ``payload`` (pickle prodotto dalla modalità ``groundtruth``);
- ``groundtruth``: output attesi e tempi delle soluzioni canoniche di HumanEval+ e MBPP+ con
  ``evalplus.evaluate.get_groundtruth``; un pickle per problema in ``out_dir``.

Gli stati sono quelli di ``bench.domain.enums.ExecStatus``. I percorsi relativi in
``job.json`` sono relativi alla cartella del job.
"""

from __future__ import annotations

import contextlib
import json
import os
import pickle
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PASSED = "PASSED"
FAILED = "FAILED"
SYNTAX_ERROR = "SYNTAX_ERROR"
COMPILE_ERROR = "COMPILE_ERROR"
RUNTIME_ERROR = "RUNTIME_ERROR"
TIMEOUT = "TIMEOUT"
SANDBOX_ERROR = "SANDBOX_ERROR"

# Nome del file sorgente per regola (come nell'harness: test.cpp, Main.java, test.js).
SOURCE_FILES = {
    "cpp": "test.cpp",
    "java": "Main.java",
    "javascript": "test.js",
    "syntax_python": "main.py",
    "syntax_java": "Main.java",
    "syntax_cpp": "main.cpp",
    "syntax_javascript": "main.js",
}


# --------------------------------------------------------------------------- sottoprocessi
def _limits(mem_mb: int | None) -> Any:
    """``preexec_fn`` che imposta il limite di memoria virtuale (solo POSIX)."""
    if mem_mb is None or os.name != "posix":
        return None

    def apply() -> None:
        import resource

        limit = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply


def run_process(
    cmd: list[str], cwd: Path, timeout_s: float, mem_mb: int | None
) -> tuple[int | None, str, str, float]:
    """Esegue ``cmd``; restituisce (codice di uscita o ``None`` se scade il tempo, stdout,
    stderr, durata). Allo scadere uccide l'intero gruppo di processi."""
    start = time.perf_counter()
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _limits(mem_mb)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    except OSError as exc:
        return 127, "", f"cannot start {cmd[0]}: {exc}", time.perf_counter() - start
    try:
        out, err = proc.communicate(timeout=timeout_s)
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        out, err = proc.communicate()
        code = None
    return (
        code,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
        time.perf_counter() - start,
    )


def _tail(text: str, n: int) -> str | None:
    text = text.strip()
    return text[-n:] if text else None


# --------------------------------------------------------------------------- programmi completi
def classify(rule: str, code: int | None, out: str, err: str) -> str:
    """Stato di un'esecuzione riuscita a partire (compilazione già superata).

    PASSED coincide con la regola di ``code_eval_octopack`` (harness HumanEvalPack):
    C++ e Java passano con codice di uscita 0; JavaScript passa se non scrive nulla né su
    stderr né su stdout (``console.assert`` non cambia il codice di uscita). Gli stati di
    fallimento distinguono soltanto il motivo.
    """
    if code is None:
        return TIMEOUT
    if rule == "javascript":
        if err.strip():
            if "Assertion failed" in err:
                return FAILED
            return SYNTAX_ERROR if "SyntaxError" in err else RUNTIME_ERROR
        return FAILED if out.strip() else PASSED
    if code == 0:
        return PASSED
    marker = "AssertionError" if rule == "java" else "Assertion"
    return FAILED if marker in err else RUNTIME_ERROR


def run_program(job: dict[str, Any], sample: dict[str, Any], root: Path) -> dict[str, Any]:
    """Compila ed esegue un programma completo in ``root/<id>``."""
    rule = job["rule"]
    tail_n = int(job.get("stderr_tail_chars", 2000))
    work = root / f"s_{sample['id']}"
    work.mkdir(parents=True, exist_ok=True)
    (work / SOURCE_FILES[rule]).write_text(sample["program"], encoding="utf-8")
    total = 0.0
    try:
        compile_cmd = job.get("compile_cmd")
        if compile_cmd:
            code, out, err, elapsed = run_process(
                list(compile_cmd), work, float(job["compile_timeout_s"]), job.get("mem_mb")
            )
            total += elapsed
            if code is None:
                return _record(sample, TIMEOUT, total, "compilation timed out")
            if code != 0:
                # py_compile e node --check controllano solo la sintassi.
                syntax_only = rule in ("syntax_python", "syntax_javascript")
                status = SYNTAX_ERROR if syntax_only else COMPILE_ERROR
                return _record(sample, status, total, _tail(err or out, tail_n))
        run_cmd = job.get("run_cmd")
        if not run_cmd:
            return _record(sample, PASSED, total, None)
        cmd = list(run_cmd)
        if cmd[0] == "./a.out" and not (work / "a.out").exists() and (work / "a.exe").exists():
            cmd[0] = str(work / "a.exe")  # solo per i test su Windows (MinGW)
        code, out, err, elapsed = run_process(cmd, work, float(job["timeout_s"]), job.get("mem_mb"))
        total += elapsed
        status = classify(rule, code, out, err)
        detail = err or out
        if status == FAILED and rule == "javascript" and not err.strip():
            detail = "stdout not empty (counted as failure by the harness): " + out
        return _record(sample, status, total, _tail(detail, tail_n) if status != PASSED else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _record(
    sample: dict[str, Any],
    status: str,
    duration: float,
    stderr_tail: str | None,
    n_tests: int | None = None,
    n_passed: int | None = None,
) -> dict[str, Any]:
    return {
        "id": sample["id"],
        "status": status,
        "n_tests": n_tests,
        "n_passed": n_passed,
        "duration_s": round(duration, 4),
        "stderr_tail": stderr_tail,
    }


# --------------------------------------------------------------------------- EvalPlus
def _prepare_evalplus_env(root: Path, mem_mb: int | None) -> None:
    """Variabili lette da EvalPlus all'import: vanno impostate prima di importarlo."""
    os.environ.setdefault("HOME", str(root))
    os.environ["XDG_CACHE_HOME"] = str(root / "_cache")
    os.environ["EVALPLUS_MAX_MEMORY_BYTES"] = str(-1 if mem_mb is None else mem_mb * 1024 * 1024)


def run_evalplus(job: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Test base+plus di EvalPlus sui campioni di un problema."""
    _prepare_evalplus_env(root, job.get("mem_mb"))
    from evalplus.eval import PASS, untrusted_check
    from evalplus.eval import TIMEOUT as EP_TIMEOUT

    conf = job["evalplus"]
    with open(root / conf["payload"], "rb") as fh:
        payload = pickle.load(fh)
    problem, oracle = payload["problem"], payload["oracle"]
    n_tests = len(problem["base_input"]) + len(problem["plus_input"])
    tail_n = int(job.get("stderr_tail_chars", 2000))
    results = []
    for sample in job["samples"]:
        start = time.perf_counter()
        code = sample["program"]
        try:
            compile(code, "<sample>", "exec")
        except (SyntaxError, ValueError) as exc:
            results.append(
                _record(sample, SYNTAX_ERROR, time.perf_counter() - start, _tail(repr(exc), tail_n))
            )
            continue
        status = PASSED
        detail = None
        # Come evalplus.evaluate.check_correctness: prima i test base, poi i plus.
        for part in ("base", "plus"):
            stat, _details = untrusted_check(
                conf["dataset"],
                code,
                problem[f"{part}_input"],
                problem["entry_point"],
                expected=oracle[part],
                atol=problem["atol"],
                ref_time=oracle[f"{part}_time"],
                fast_check=True,
                min_time_limit=float(conf["min_time_limit"]),
                gt_time_limit_factor=float(conf["gt_time_limit_factor"]),
            )
            if stat != PASS:
                status = TIMEOUT if stat == EP_TIMEOUT else FAILED
                detail = f"{part} tests: {stat}"
                break
        results.append(
            _record(
                sample,
                status,
                time.perf_counter() - start,
                detail,
                n_tests=n_tests,
                n_passed=n_tests if status == PASSED else None,
            )
        )
    return results


def run_groundtruth(job: dict[str, Any], root: Path) -> dict[str, Any]:
    """Ground truth di EvalPlus (output attesi e tempi), un pickle per problema."""
    datasets = job["datasets"]
    os.environ["HUMANEVAL_OVERRIDE_PATH"] = datasets["humaneval"]
    os.environ["MBPP_OVERRIDE_PATH"] = datasets["mbpp"]
    _prepare_evalplus_env(root, job.get("mem_mb"))
    from evalplus.data import (
        get_human_eval_plus,
        get_human_eval_plus_hash,
        get_mbpp_plus,
        get_mbpp_plus_hash,
    )
    from evalplus.eval._special_oracle import MBPP_OUTPUT_NOT_NONE_TASKS
    from evalplus.evaluate import get_groundtruth

    out_dir = root / job["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    hashes = {}
    for name, load, hasher, not_none in (
        ("humaneval", get_human_eval_plus, get_human_eval_plus_hash, []),
        ("mbpp", get_mbpp_plus, get_mbpp_plus_hash, MBPP_OUTPUT_NOT_NONE_TASKS),
    ):
        start = time.perf_counter()
        problems = load()
        hashes[name] = hasher()
        oracle = get_groundtruth(problems, hashes[name], not_none)
        seconds = time.perf_counter() - start
        for task_id, problem in problems.items():
            fname = f"{name}_{task_id.replace('/', '_')}.pkl"
            with open(out_dir / fname, "wb") as fh:
                pickle.dump({"problem": problem, "oracle": oracle[task_id]}, fh)
            entries.append(
                {
                    "dataset": name,
                    "task_id": task_id,
                    "file": f"{job['out_dir']}/{fname}",
                    "canonical_program": problem["prompt"] + problem["canonical_solution"],
                    "n_base": len(problem["base_input"]),
                    "n_plus": len(problem["plus_input"]),
                }
            )
        print(f"groundtruth {name}: {len(problems)} problems in {seconds:.1f}s", flush=True)
    return {"problems": entries, "dataset_hash": hashes}


# --------------------------------------------------------------------------- versioni e main
def versions() -> dict[str, str]:
    """Versioni degli strumenti dell'immagine (scritte dalla build in /opt/wmb/versions.json)."""
    path = Path("/opt/wmb/versions.json")
    if path.is_file():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return {"python": platform.python_version(), "image": "none (local run)"}


def main(argv: list[str]) -> int:
    job_path = Path(argv[1])
    root = job_path.parent
    job = json.loads(job_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"versions": versions()}
    if job["mode"] == "groundtruth":
        result.update(run_groundtruth(job, root))
    elif job["kind"] == "evalplus":
        result["results"] = run_evalplus(job, root)
    else:
        result["results"] = [run_program(job, s, root) for s in job["samples"]]
    tmp = root / "result.json.tmp"
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(root / "result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
