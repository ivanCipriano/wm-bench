# Patch di STONE

Submodule: `third_party/STONE-watermarking` @ `bb5d809c0c494a219411e861f2313cca2b9fd7b4` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente).

| File | Modifica | Impatto |
|---|---|---|
| `stone_implementation/run.py` | i `task_id` di MBPP+ e HumanEval+ non vengono più prefissati con `Mbpp/` / `HumanEval/` | solo formato dei file di output dello script originale (i `task_id` di EvalPlus contengono già il prefisso). Nessun effetto su inserimento e rilevazione |
| `stone_implementation/evaluation/stem.py` | `correctness = round(pass_k, 3)` invece di `round(pass_k / 2, 3)` | cambia solo la metrica aggregata STEM del repository, che il framework **non** usa (metriche proprie, SPEC §9.3, §13) |

`git apply` segnala una riga con spazi finali: avviso innocuo.

## Verifica

- `scripts/apply_patches.sh stone` applica la patch a `build/patched/stone/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di STONE (SPEC §21.4, M5).
