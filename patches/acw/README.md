# Patch di ACW

Submodule: `third_party/ACW` @ `2236dc304478a31fe7cc7cc393527375024428db` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente). Esclusi dalla patch: `source/G/` (codice generato dagli autori), `source/apps/` (copia del dataset APPS), `__pycache__`.

La patch ha fine riga **misti** (alcune righe CRLF, come nei file originali): va conservata byte per byte (`.gitattributes`: `patches/** -text`).

| File | Modifica | Impatto |
|---|---|---|
| `source/.sourcery.yaml` (nuovo) | elenco delle regole Sourcery abilitate | definisce il catalogo di trasformazioni: **rilevante** per l'inserimento; da confrontare con il paper nell'audit (M6) |
| `source/refactor.py` | `apply_pep8_rule` importato da `one_rule_format.reformatting_process` invece che da `formatting_pep8` | cambia la funzione di riformattazione usata; motivazione e impatto da documentare nell'audit (M6) |
| `source/one_rule_format.py` | le funzioni `transform_*` sono chiamate una volta con il solo percorso del file invece che per ogni nodo dell'AST | adegua le chiamate alla firma `transform_operations_add(file_path)` di `base_ast.py`; da verificare nell'audit |
| `source/folder_list.py` | rimosse le cartelle `H/APPS_H`, `H/HE_H`, `H/MBPP_H` | cartelle assenti nel repository; solo script degli esperimenti originali |
| `source/folder_to_jsonl.py` | `HumanEvalN` → `HumanEval/N` nei `task_id` | solo formato degli ID negli script originali |
| `source/RQ4-get-results.py` | aggiunto entry point `fire` (`folder_process --strength`) | solo invocazione da riga di comando |

## Verifica

- `scripts/apply_patches.sh acw` applica la patch a `build/patched/acw/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di ACW (SPEC §21.4, M6).
