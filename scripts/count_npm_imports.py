"""T4.2: quanti campioni JavaScript di L1 importano pacchetti npm, e ``--packages=external`` cambia
qualcosa per gli altri? (decisioni dell'utente del 28 ottobre 2026; ADR-014 §4.3)

Uso (con bench-core e Node di ``tools/js``, nodo di login; qualche minuto):
    python scripts/count_npm_imports.py | tee docs/milestone_logs/M11_t4_2_npm.txt

Campioni: baseline di L1 (sviluppo e test) di ogni modello e marcati di SWEET su L1 sviluppo con la
configurazione scelta su L1+L2 (l'unico metodo in JavaScript; il test di SWEET non c'è ancora). Per
ogni gruppo: campioni, campioni con pacchetti npm (``require``/``import`` non relativi, non
``node:``, non predefiniti di Node), pacchetti e problemi coinvolti; per i campioni **senza**
pacchetti npm, quanti hanno l'output di T4.2 identico con e senza ``--packages=external`` (atteso:
tutti); per quelli **con** pacchetti, i ``TOOL_ERROR`` nelle due varianti.
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

from bench.attacks.t4.minify import node_builtins, npm_packages
from bench.attacks.tools import ToolRunner
from bench.config.builder import load_experiment, repo_root
from bench.pipeline.stages.generate_baseline import baseline_ref
from bench.pipeline.stages.tune import candidates_ref
from bench.pipeline.stages.watermark import watermarked_ref
from bench.store.artifact_store import ArtifactStore

LEVEL = "L1"
LANG = "javascript"
FINAL = ["L1", "L2"]


def groups(store: ArtifactStore, models: list[str]) -> list[tuple[str, Any]]:
    """(nome del gruppo, riferimento dei campioni) per i gruppi presenti nello store."""
    out: list[tuple[str, Any]] = []
    for model in models:
        for split in ("dev", "test"):
            ref = baseline_ref(model, LEVEL, LANG, split)
            out.append((f"baseline {model} {LEVEL}_{split}", ref))
        cand = candidates_ref("sweet", model, LANG, FINAL)
        if store.exists(cand):
            table = store.read_table(cand)
            for h in table.loc[table["selected"].astype(bool), "config_hash"]:
                ref = watermarked_ref("sweet", model, str(h), LEVEL, LANG, "dev")
                out.append((f"sweet {model} {h} {LEVEL}_dev", ref))
    return [(name, ref) for name, ref in out if store.exists(ref)]


def main() -> int:
    cfg = load_experiment(["paths=cluster", "stage=attack"])
    store = ArtifactStore(cfg.paths.artifacts)
    tools = ToolRunner(repo_root() / "tools", cfg.paths.tmp / "count_npm_imports")
    builtins = node_builtins(tools)
    sys.stdout.write(
        f"##### T4.2: pacchetti npm nei campioni JavaScript di {LEVEL} "
        f"({len(builtins)} moduli predefiniti di Node)\n"
    )
    for name, ref in groups(store, list(cfg.models)):
        rows = store.read_table(ref).to_dict("records")
        codes = [str(r.get("code") or "") for r in rows]
        npm = [npm_packages(c, builtins) for c in codes]
        with_npm = [i for i, v in enumerate(npm) if v]
        plain = [i for i, v in enumerate(npm) if not v and codes[i].strip()]
        packages = Counter(p for v in npm for p in v)
        problems = sorted({str(rows[i]["problem_key"]) for i in with_npm})
        external = tools.run("esbuild", [codes[i] for i in plain])
        bundled = tools.run("esbuild-bundled", [codes[i] for i in plain])
        same = sum(1 for a, b in zip(external, bundled, strict=True) if a.code == b.code)
        npm_ext = tools.run("esbuild", [codes[i] for i in with_npm])
        npm_bun = tools.run("esbuild-bundled", [codes[i] for i in with_npm])
        sys.stdout.write(
            f"\n{name}: {len(rows)} campioni, con pacchetti npm {len(with_npm)}\n"
            f"  pacchetti {dict(packages.most_common())}\n"
            f"  problemi {problems}\n"
            f"  senza pacchetti npm: output identico con e senza --packages=external "
            f"{same}/{len(plain)}\n"
            f"  con pacchetti npm: TOOL_ERROR con --packages=external "
            f"{sum(r.code is None for r in npm_ext)}/{len(with_npm)}, senza "
            f"{sum(r.code is None for r in npm_bun)}/{len(with_npm)}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
