"""Pilota di Tests4Py (Livello 4) con la misura dei criteri di fattibilità (cluster_info §8.7).

Uso (nodo di login, serve la rete; vedi scripts/tests4py_pilot.sh, che prepara l'ambiente):
    python scripts/tests4py_pilot.py run      # riprendibile: rifà solo i passi mancanti
    python scripts/tests4py_pilot.py report   # tabella e verdetto dei tre criteri

Decisioni dell'utente (29 e 30 ottobre 2026): 5 progetti, **i più piccoli**, scelti dallo script e
riportati nel log; ci si ferma al pilota documentato. Procedura, con ``t4p`` (Tests4Py):

1. per ognuno dei progetti di ``t4p info`` si fa il checkout del bug 1 (``t4p checkout -p P -i 1
   -w DIR``) e se ne misurano spazio (byte) e righe Python del sorgente; si scelgono i 5 più
   piccoli per righe Python (a parità, per spazio);
2. per i 5: ``t4p build -w DIR`` (ambiente con la versione di Python del progetto, via pyenv) e
   ``t4p test -w DIR -a`` (tutta la suite) con un limite di 9 h, il limite di ``defq``;
3. si registrano per passo durata, esito e spazio totale (``$HOME`` di Tests4Py, cache comprese).

Criteri (fissati prima del pilota, cluster_info §8.7): L4 è **non fattibile** se la preparazione dei
5 ambienti richiede più di una giornata di lavoro (qui: più di 8 h di durata complessiva di
checkout e build), se lo spazio supera la quota (``--quota-gb``, da ``lfs quota`` o
dall'amministratore), o se la suite di un progetto non sta nei limiti dei job SLURM (oltre 9 h, o
interrotta al limite).
Stato in ``$WMB/tests4py/pilot.json``; log dei comandi in ``$WMB/tests4py/logs/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

N_PROJECTS = 5
WORKDAY_S = 8 * 3600
SLURM_LIMIT_S = 9 * 3600  # defq (cluster_info §4)
INFO_TIMEOUT_S, CHECKOUT_TIMEOUT_S, BUILD_TIMEOUT_S = 600, 3600, 3 * 3600


def root() -> Path:
    return (
        Path(os.environ.get("WMB", "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench")) / "tests4py"
    )


def run_cmd(name: str, cmd: list[str], timeout: int) -> dict[str, Any]:
    """Esegue un comando, ne salva l'output in ``logs/<name>.log``, misura la durata."""
    logs = root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        code, text = out.returncode, out.stdout + out.stderr
    except subprocess.TimeoutExpired as exc:
        partial = [
            x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
            for x in (exc.stdout, exc.stderr)
        ]
        code, text = None, f"TIMEOUT after {timeout} s\n{partial[0]}{partial[1]}"
    elapsed = time.perf_counter() - start
    (logs / f"{name}.log").write_text(f"$ {' '.join(cmd)}\n{text}", encoding="utf-8")
    return {"cmd": cmd, "returncode": code, "seconds": round(elapsed, 1), "tail": text[-1500:]}


def du(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            p = Path(dirpath, f)
            if not p.is_symlink():
                total += p.stat().st_size
    return total


def py_lines(path: Path) -> int:
    lines = 0
    for p in path.rglob("*.py"):
        if "tests4py" not in p.parts:
            lines += sum(1 for line in p.read_text(errors="replace").splitlines() if line.strip())
    return lines


def projects() -> list[str]:
    info = run_cmd("info", ["t4p", "info"], INFO_TIMEOUT_S)
    names = sorted(
        set(
            re.findall(
                r"^\s*([a-z_]+)\b", info["tail"] if info["returncode"] == 0 else "", flags=re.M
            )
        )
    )
    known = [
        "ansible",
        "black",
        "calculator",
        "cookiecutter",
        "expression",
        "fastapi",
        "httpie",
        "keras",
        "luigi",
        "markup",
        "matplotlib",
        "middle",
        "pandas",
        "pysnooper",
        "sanic",
        "scrapy",
        "spacy",
        "thefuck",
        "tornado",
        "tqdm",
        "youtubedl",
    ]
    found = [n for n in names if n in known]
    return found or known  # elenco del repository se l'uscita di `t4p info` non si legge


def load() -> dict[str, Any]:
    path = root() / "pilot.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"projects": {}}


def save(state: dict[str, Any]) -> None:
    path = root() / "pilot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def run() -> None:
    state = load()
    work = root() / "work"
    for name in projects():  # 1) misura
        entry = state["projects"].setdefault(name, {})
        if "checkout" in entry:
            continue
        entry["checkout"] = run_cmd(
            f"checkout_{name}",
            ["t4p", "checkout", "-p", name, "-i", "1", "-w", str(work)],
            CHECKOUT_TIMEOUT_S,
        )
        target = next((p for p in work.glob(f"{name}_1*") if p.is_dir()), None)
        if target is not None:
            entry["dir"], entry["bytes"], entry["py_lines"] = (
                str(target),
                du(target),
                py_lines(target),
            )
        save(state)
    measured = [
        (v["py_lines"], v["bytes"], k)
        for k, v in state["projects"].items()
        if v.get("checkout", {}).get("returncode") == 0 and "py_lines" in v
    ]
    state["selected"] = [k for _, _, k in sorted(measured)[:N_PROJECTS]]
    save(state)
    for name in state["selected"]:  # 2) ambienti e suite
        entry = state["projects"][name]
        if "build" not in entry:
            entry["build"] = run_cmd(
                f"build_{name}", ["t4p", "build", "-w", entry["dir"]], BUILD_TIMEOUT_S
            )
            save(state)
        if "test" not in entry and entry["build"]["returncode"] == 0:
            entry["test"] = run_cmd(
                f"test_{name}", ["t4p", "test", "-w", entry["dir"], "-a"], SLURM_LIMIT_S
            )
            save(state)
    state["home_bytes"] = du(Path(os.environ.get("HOME", "~")).expanduser())
    save(state)


def report(quota_gb: float | None) -> None:
    state = load()
    w = sys.stdout.write
    w("##### Tests4Py pilot (L4), criteria of cluster_info §8.7\n")
    w(f"{'project':<14} {'py lines':>9} {'MB':>8} {'checkout s':>10} {'build':>14} {'tests':>14}\n")
    for name, v in sorted(state["projects"].items(), key=lambda kv: kv[1].get("py_lines", 1e12)):
        mark = "*" if name in state.get("selected", []) else " "
        b, t = v.get("build", {}), v.get("test", {})
        w(
            f"{mark}{name:<13} {v.get('py_lines', '-'):>9} {v.get('bytes', 0) / 1e6:>8.1f} "
            f"{v.get('checkout', {}).get('seconds', '-'):>10} "
            f"{_res(b):>14} {_res(t):>14}\n"
        )
    selected = [state["projects"][n] for n in state.get("selected", [])]
    prep = sum(
        v.get("checkout", {}).get("seconds", 0) + v.get("build", {}).get("seconds", 0)
        for v in selected
    )
    space = state.get("home_bytes", 0) + sum(v.get("bytes", 0) for v in state["projects"].values())
    slow = [
        n
        for n in state.get("selected", [])
        if state["projects"][n].get("test", {}).get("returncode") is None
        or state["projects"][n].get("test", {}).get("seconds", 0) > SLURM_LIMIT_S
    ]
    failed_build = [
        n
        for n in state.get("selected", [])
        if state["projects"][n].get("build", {}).get("returncode") != 0
    ]
    c1 = prep > WORKDAY_S or bool(failed_build)
    c2 = quota_gb is not None and space / 1e9 > quota_gb
    c3 = bool(slow)
    w(f"\nselected (5 smallest by Python lines): {state.get('selected')}\n")
    w(
        f"1) preparation of the 5 environments: {prep / 3600:.1f} h (limit 8 h)"
        f"{'; builds failed: ' + str(failed_build) if failed_build else ''} -> "
        f"{'NOT FEASIBLE' if c1 else 'ok'}\n"
    )
    w(
        f"2) space: {space / 1e9:.1f} GB (quota {quota_gb} GB) -> "
        f"{'NOT FEASIBLE' if c2 else 'ok' if quota_gb is not None else 'quota not given'}\n"
    )
    w(f"3) test suites within 9 h: {'NOT FEASIBLE, ' + str(slow) if c3 else 'ok'}\n")
    w(f"\nverdict: L4 {'NOT FEASIBLE' if (c1 or c2 or c3) else 'feasible'}\n")


def _res(r: dict[str, Any]) -> str:
    if not r:
        return "-"
    code = "TIMEOUT" if r.get("returncode") is None else f"rc {r['returncode']}"
    return f"{code} {r.get('seconds', 0):.0f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["run", "report"])
    parser.add_argument("--quota-gb", type=float, default=None)
    args = parser.parse_args()
    if args.mode == "run":
        run()
    else:
        report(args.quota_gb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
