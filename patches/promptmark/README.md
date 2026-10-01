# Patch di PromptMark

Submodule: `third_party/PromptMark` (`ahmedfahad04/promptmark`) @ `c04c8f61db1f0ec7ba2f213f4aa1a15787af484e` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente). Esclusi: notebook `*.ipynb`, `datasets/core`, `datasets/humaneval_164.json`, `datasets/sanitized-mbpp-sample-100.json`, `output/`, `__pycache__`.

| File | Modifica | Impatto |
|---|---|---|
| `requirements.txt` | `dark>=22.0.0` → `black>=22.0.0` | solo installazione |
| `scripts/evals/*` (7 file) | percorsi assoluti degli autori sostituiti con quelli del clone di riferimento sul cluster (`wm_bench/repos/shared/promptmark`); riferimento MBPP `.json` → `sanitized-mbpp-sample-100.jsonl` | solo script di valutazione originali, **non** usati dal framework |
| `scripts/robustness/program_perturb_rename_comments.py` | percorso del CSV adattato al cluster | idem |
| `src/run_experiments.sh` | provider letto da `$LLM_PROVIDER` (default `claude`); nomi dei file di output con il provider; dataset `.jsonl` | solo script degli esperimenti; il framework userà il provider `inprocess_hf` (patch futura, SPEC §9.5) |

Nota: i percorsi assoluti puntano al clone di riferimento in `repos/shared/`, non a `build/patched/promptmark/`. Sono irrilevanti per il framework, ma vanno tenuti presenti se si rilanciano gli script originali per i test oracle.

## Verifica

- `scripts/apply_patches.sh promptmark` applica la patch a `build/patched/promptmark/` sul commit fissato: verificato in M0.
