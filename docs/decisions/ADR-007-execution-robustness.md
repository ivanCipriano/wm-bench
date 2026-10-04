# ADR-007 — Esecuzione dei test: bug di EvalPlus, memoria, carico e ripetizione dei timeout

- **Stato:** accettata (Milestone 4, 4 ottobre 2026; decisioni dell'utente sui tre casi)
- **Contesto:** prima esecuzione di L1 (`M4_cluster.txt`). Le canoniche C++, Java e JavaScript passano al 100%;
  in Python ne falliscono 3 su 542: `humaneval/32`, `humaneval/139`, `mbpp/255`.
  `scripts/debug_canonical.sh` ha chiarito le cause (nodo tnode05).

## Diagnosi

| Problema | Esito della diagnosi | Causa |
|---|---|---|
| HumanEval/32 (`find_zero`) | fallisce sempre, da solo e sotto carico, con 0 test contati | **bug di EvalPlus 0.3.1**: dopo l'oracolo speciale di `find_zero` il codice fa `continue` senza segnare il test come superato né far avanzare `progress`. `untrusted_check` vede quindi meno test controllati di quelli attesi e dichiara FAIL qualunque soluzione |
| MBPP/255 | fallisce sempre sul test plus 85; rieseguito fuori dalla guardia dà l'output giusto in 1,2 s (limite 4,8 s) | **limite di memoria** di 4 GB (default di EvalPlus) sul processo controllato: l'output della canonica è una lista molto grande |
| HumanEval/139 | nella diagnosi passa sempre, da solo e con 16 copie in parallelo; nell'esecuzione vera è fallito | **carico**: due job sullo stesso nodo con 16 processi ciascuno. EvalPlus conta come "fail" un test che supera il proprio limite di tempo (max(1 s, 4 × tempo della ground truth)) |

## Decisioni (dell'utente)

1. **HumanEval/32 — bug corretto nel runner.**
   - Il runner usa una copia di `unsafe_execute` e `untrusted_check` di EvalPlus 0.3.1
     (`containers/runner/wmb_runner.py`, licenza Apache-2.0), identica all'originale tranne due punti.
   - Il test di `find_zero` superato viene contato.
   - Viene registrato il motivo del primo test fallito (punto 3).
   - Oracoli speciali, tolleranze, guardia e limiti restano invariati.
   - Deviazione D14.

2. **Memoria di Python a 8 GB per campione** (`execution.mem_mb.python`, passata a EvalPlus come
   `EVALPLUS_MAX_MEMORY_BYTES`). Da confermare con `debug_canonical.sh --mem-gb 8` prima della riesecuzione.
   Deviazione D13.

3. **Carico e ripetizione dei timeout.**
   - **Worker in base alle CPU del job:** `SLURM_CPUS_PER_TASK`, altrimenti `len(os.sched_getaffinity(0))`, mai
     `os.cpu_count()`, che conta tutti i core del nodo. Le celle Python usano **la metà** di quelle CPU, come il
     default di EvalPlus (`cpu_count // 2`).
   - **Stato TIMEOUT anche per il limite di tempo di un singolo test di EvalPlus.** EvalPlus lo conta come "fail";
     l'insieme dei PASSED non cambia.
   - **Ripetizione mirata.** Dopo l'esecuzione di una cella, i campioni in `TIMEOUT` vengono rieseguiti **una
     volta, da soli** (un campione per invocazione, un solo worker). Il **risultato finale è quello della
     ripetizione**. Risposte sbagliate ed errori non si ripetono.
   - La regola è nella fase `execute`, quindi vale **identica** per baseline, metodi e attacchi, e per tutti i
     linguaggi (anche i timeout di HumanEvalPack).
   - **Tracciabilità.** `ExecutionRecord` ha due campi nuovi: `first_attempt_status` (esito del primo tentativo)
     e `retry_status` (esito della ripetizione, `None` se non ripetuto). `status` è l'esito finale. Il manifest
     riporta i conteggi del primo tentativo, i campioni ripetuti e quelli recuperati (TIMEOUT → PASSED).
   - Le ripetizioni sono salvate nel file parziale: un job interrotto non le rifà.

4. **Canoniche eseguite 3 volte** (`execution.canonical_repeats: 3`, tutti i linguaggi; `sample_index` =
   ripetizione). Il criterio M4 richiede che passino **tutte e tre le volte**: i test `data` controllano lo
   stato finale; il report mostra anche il primo tentativo.

## Conseguenze

- Il runner cambia, quindi l'immagine va ricostruita (nuovo hash) e tutta l'esecuzione di L1 va rifatta,
  ground truth compresa: tutti gli artefatti devono avere lo stesso hash. I risultati precedenti si conservano a
  parte (`execution_v1`) solo come riferimento.
- I Pass@1 Python di questa versione non sono confrontabili uno a uno con quelli pubblicati per EvalPlus 0.3.1
  su HumanEval/32, MBPP/255 e su campioni lenti. Le differenze sono tutte in `docs/deviations.md`.
- Il riepilogo della M4 riporta, per modello e linguaggio, quanti campioni della baseline la ripetizione ha
  portato da TIMEOUT a PASSED (`scripts/report_execution.py`).
