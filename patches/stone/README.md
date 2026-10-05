# Patch di STONE

Submodule: `third_party/STONE-watermarking` @ `bb5d809c0c494a219411e861f2313cca2b9fd7b4` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente).

| File | Modifica | Impatto |
|---|---|---|
| `stone_implementation/run.py` | i `task_id` di MBPP+ e HumanEval+ non vengono più prefissati con `Mbpp/` / `HumanEval/` | solo formato dei file di output dello script originale (i `task_id` di EvalPlus contengono già il prefisso). Nessun effetto su inserimento e rilevazione |
| `stone_implementation/evaluation/stem.py` | `correctness = round(pass_k, 3)` invece di `round(pass_k / 2, 3)` | cambia solo la metrica aggregata STEM del repository, che il framework **non** usa (metriche proprie, SPEC §9.3, §13) |

`git apply` segnala una riga con spazi finali: avviso innocuo.

## 0001-rowwise-syntax-mask.patch

Scritta dall'agente in Milestone 5 (decisione dell'utente del 5 ottobre 2026; audit `docs/audit/stone.md` §9).

| File | Modifica | Impatto |
|---|---|---|
| `stone_implementation/watermark/stone/stone.py` (`STONELogitsProcessor.__call__`) | il token "previsto" usato per decidere se il passo è sintattico si calcola con l'argmax **di ogni riga** (`raw_probs[b_idx]`) e la maschera `pl_mask` ha forma `[B, 1]`, una decisione per riga | nessuno con una sola sequenza per volta |

**Il difetto dell'originale compare solo con più sequenze per volta.** L'originale fa `torch.argmax(raw_probs)` sull'intero
tensore `[B, V]`: con B > 1 ottiene un indice "appiattito" (riga × V + colonna) che non appartiene a nessuna riga, lo
decodifica come se fosse un token e applica la stessa decisione `[[bool]]` a tutte le righe. **Con una sola sequenza
(B = 1) l'indice appiattito coincide con quello della riga e la patch dà un comportamento identico all'originale.**
Il framework genera i 6 campioni di un problema in una sola chiamata (`num_return_sequences=6`, stesso seme della
baseline), quindi la patch è necessaria.

Verifica (SPEC §21.4, `tests/oracle/`):
- fedeltà a una sequenza: testo generato e z-score identici al percorso originale;
- equivalenza per riga: con un blocco di 6 sequenze, decisione sintattica e logit modificati di ogni riga identici a
  quelli del processor applicato alla riga da sola.

## Verifica

- `scripts/apply_patches.sh stone` applica la patch a `build/patched/stone/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di STONE (SPEC §21.4, M5).
