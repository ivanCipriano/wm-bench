"""Controlli della Milestone 7 su L1 dev.

Uso (con bench-core):
    python scripts/check_m7.py [--artifacts DIR]

1. Celle mancanti: per ogni metodo, modello e linguaggio previsto dice quali artefatti delle fasi
   watermark, execute, detect, calibrate e metrics esistono e sono completi.
2. MCGMark: stati di esecuzione dei campioni marcati e della baseline gemella (per spiegare il
   calo di Pass@1), con il numero di siti marcati per stato d'inserimento.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ARTIFACTS = "/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench/artifacts"
MODELS = ["qwen25_coder_7b", "deepseek_coder_6p7b"]
LANGS = {
    "sweet": ["python", "java", "cpp", "javascript"],
    "stone": ["python", "java", "cpp"],
    "mcgmark": ["python"],
    "promptmark": ["python"],
    "acw": ["python"],
}
TWIN = {"mcgmark"}


def _status(path: Path) -> str:
    manifest = Path(f"{path}.manifest.json")
    if not manifest.is_file():
        return "MISSING"
    return str(json.loads(manifest.read_text(encoding="utf-8")).get("status", "?"))


def _cfg(root: Path, method: str, model: str) -> str | None:
    folder = root / "watermarked" / method / model
    hashes = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    return hashes[0] if len(hashes) == 1 else (",".join(hashes) or None)


def cells(root: Path) -> None:
    sys.stdout.write("##### artefatti per cella (L1 dev)\n")
    for method, langs in LANGS.items():
        for model in MODELS:
            cfg = _cfg(root, method, model)
            if cfg is None or "," in cfg:
                sys.stdout.write(f"{method} {model}: watermarked config {cfg}\n")
                continue
            for lang in langs:
                name = f"L1_{lang}_dev.parquet"
                refs = {
                    "watermark": root / "watermarked" / method / model / cfg / name,
                    "exec_marked": root / "execution/llm_watermarked" / method / model / cfg / name,
                    "det_pos": root / "detection" / method / model / cfg / "positives" / name,
                    "det_human": root / "detection" / method / model / cfg / "neg_human" / name,
                    "det_llm": root / "detection" / method / model / cfg / "neg_llm" / name,
                    "threshold": root / "thresholds" / method / model / lang / f"{cfg}.json",
                    "metrics": root / "metrics" / method / model / cfg / name,
                }
                if method in TWIN:
                    refs["exec_twin"] = (
                        root / "execution/baseline_twin" / method / model / cfg / name
                    )
                    refs["det_twin"] = (
                        root / "detection" / method / model / cfg / "neg_llm_twin" / name
                    )
                states = {k: _status(v) for k, v in refs.items()}
                bad = {k: v for k, v in states.items() if v != "complete"}
                verdict = "ok" if not bad else " ".join(f"{k}={v}" for k, v in bad.items())
                sys.stdout.write(f"{method:<10} {model:<20} {lang:<10} {verdict}\n")


def mcgmark(root: Path) -> None:
    sys.stdout.write("\n##### MCGMark: stati di esecuzione (marcati e gemella)\n")
    for model in MODELS:
        cfg = _cfg(root, "mcgmark", model)
        if cfg is None or "," in cfg:
            continue
        name = "L1_python_dev.parquet"
        marked = root / "execution/llm_watermarked/mcgmark" / model / cfg / name
        twin = root / "execution/baseline_twin/mcgmark" / model / cfg / name
        samples = root / "watermarked/mcgmark" / model / cfg / name
        for label, path in (("marked", marked), ("twin", twin)):
            if path.is_file():
                counts = pd.read_parquet(path)["status"].value_counts().to_dict()
                sys.stdout.write(f"{model} {label}: {counts}\n")
        if marked.is_file() and samples.is_file():
            ex = pd.read_parquet(marked)[["sample_id", "status"]]
            wm = pd.read_parquet(samples)
            cols = [c for c in ("sample_id", "embed_status", "n_sites", "extraction_ok") if c in wm]
            df = ex.merge(wm[cols], on="sample_id", how="left")
            table = pd.crosstab(df["embed_status"], df["status"])
            sys.stdout.write(f"{model} marked, embed_status x exec status:\n{table}\n")
            if "n_sites" in df:
                means = df.groupby("status")["n_sites"].mean().round(2).to_dict()
                sys.stdout.write(f"{model} marked, mean n_sites by exec status: {means}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", default=DEFAULT_ARTIFACTS)
    args = parser.parse_args()
    root = Path(args.artifacts)
    cells(root)
    mcgmark(root)


if __name__ == "__main__":
    main()
