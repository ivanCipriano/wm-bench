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

## 0001-inprocess-hf-provider.patch

- `src/llm_providers.py`: classe `InProcessHFProvider`, registrata nella factory come `inprocess_hf`
  (SPEC §9.4). Usa modello e tokenizer già caricati dal worker, `apply_chat_template` con il system prompt
  fisso del framework (o quello passato dal chiamante) e il messaggio di PromptMark come utente, la
  `generation_config` neutra del framework (`max_tokens` e `temperature` dei chiamanti sono ignorati) e,
  se indicato, un seme impostato prima di ogni generazione (`seed_fn`).
- Nessun'altra parte del codice cambia; il fallback a Bedrock di `generate_response` resta (senza
  credenziali né rete fallisce).

## 0002-parametri-soglia-green-list.patch

Parametri dichiarati che il codice non rispetta, necessari alla griglia della M8 (decisione dell'utente del
7 ottobre 2026). Con i valori di default il comportamento è **identico** all'originale (verificato
dall'oracle, `promptmark_patch_check.py`).

| File | Modifica |
|---|---|
| `src/shared_utils.py` | `detect_watermark(..., z_threshold=2.12)`: la soglia era cablata (`norm.sf(2.12)`) |
| `src/shared_utils.py` | `build_green_set`: dimensione `g_min + h % (g_max − g_min + 1)` invece di `g_min + h % 11`, che ignora `g_max` (con i default 8 e 18 è la stessa formula); con `g_min = g_max` la dimensione è fissa |
| `src/watermarking/exp_iterative_wm.py` | costanti `G_MIN = 8`, `G_MAX = 18` passate a `get_red_green_sets` nelle tre chiamate; `Z_THRESHOLD` (già presente, 2,12) passata a `detect_watermark` |

## Verifica

- `scripts/apply_patches.sh promptmark` applica la patch a `build/patched/promptmark/` sul commit fissato: verificato in M0.
