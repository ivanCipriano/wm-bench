# ADR-004 — Dati del Livello 1: loader, divisione, conteggio delle righe, negativi

- **Stato:** accettata (Milestone 2, 3 ottobre 2026)
- **Contesto:** SPEC §7.4, §10, §15.2, §18; cluster_info §8, §10.1 (D7); decisioni dell'utente del 3 ottobre 2026.

## Decisioni

1. **Artefatti di `prepare_data`** (una sola cella, CPU):

   | Percorso | Righe attese (SPEC §10.2) |
   |---|---|
   | `data/splits/split.parquet` | 1.138 chiavi (164 HumanEval + 974 MBPP) |
   | `data/problems/L1_python.parquet` | 542 (96 / 446) |
   | `data/problems/L1_<java\|cpp\|javascript>.parquet` | 164 (24 / 140) |
   | `data/negatives/L1_python_native.parquet` | 1.138 (210 / 928) |
   | `data/negatives/L1_<java\|cpp\|javascript>_native.parquet` | 164 (24 / 140) |
   | `data/negatives/L1_python_integration_dev.parquet` | 1.790 |
   | `data/negatives/L1_<java\|cpp\|javascript>_integration_dev.parquet` | 1.976 |
   | `data/negatives/L1_<java\|cpp\|javascript>_integration_test.parquet` | 788 |

   - Ogni manifest dichiara `n_rows_expected` ricavato dalla configurazione, quindi I1 fa rispettare i conteggi.
   - Le tabelle dei negativi hanno i campi di `CodeSample` più `loc` (righe secondo `function_loc`) e, per l'integrazione, `repo`.
   - I livelli diversi da L1 danno un `ConfigError` finché non arrivano i loro loader (L2 in M9, L3/L4 in M13).

2. **Loader.**
   - Leggono i file della §8 direttamente: EvalPlus come JSONL, mai dalla cache in home.
   - Controllano colonne e righe attese (`expected_rows` in `configs/dataset/*.yaml`, da cluster_info).
   - Restituiscono `Problem` e `CodeSample` con `split=None`. `split` è opzionale solo prima dell'assegnazione: negli artefatti c'è sempre.
   - Mappature degli ID: `HumanEval/N`, `Java/N`, `CPP/N`, `JavaScript/N` → `humaneval/N`; `Mbpp/N` e il `task_id` di MBPP → `mbpp/N`. La cartella di JavaScript è `js`.
   - Negativi nativi:
     - HumanEval+ e HumanEvalPack: prompt + soluzione canonica;
     - MBPP: la colonna `code` dei 4 file `full` (verificata sui dati pubblici).
   - Il codice umano viene normalizzato a fine riga `\n` (MBPP contiene `\r\n`).

3. **Divisione** (`ProblemSplitter`).
   - Per famiglia si ordinano le chiavi per `derive_seed(global_seed, "split", key)` (a parità, per chiave) e i primi `dev` vanno in sviluppo: HumanEval 24/164 (vale per HE+ e per i 3 linguaggi di HEP), MBPP+ 72/378.
   - Extra di MBPP (D7): stessa regola con `round(596 × 72 / 378) = 114` (arrotondamento per eccesso alla metà).
   - La divisione dipende solo dalla chiave (I4).

4. **Conteggio delle righe** (`lang/line_counter.py`, SPEC §10.3).
   - Conta le righe con almeno una foglia tree-sitter non commento. Le foglie su più righe contano per tutte le righe coperte.
   - In Python si escludono le docstring: una stringa come prima istruzione di modulo, classe o funzione.
   - "Parte da generare":
     - HumanEval+ e HumanEvalPack: le righe della soluzione, analizzando prompt + soluzione insieme;
     - MBPP+: l'intera soluzione canonica (il prompt è solo testo).
   - Taratura sui file EvalPlus pubblici: HumanEval+ **5,116 ± 4,441**, MBPP+ **4,011 ± 3,675** (deviazione standard di popolazione), contro 5,1 ± 4,4 e 4,0 ± 3,7 del protocollo.

5. **Integrazione dei negativi** (decisioni dell'utente).
   - Quantità:
     - test L1 Java, C++ e JS: totale 928 per linguaggio, cioè 140 canoniche più 788 di integrazione (pari ai negativi umani di test di L1 Python);
     - sviluppo: fino a `min_dev_negatives = 2000` per linguaggio.
   - Sorgenti:
     - CodeSearchNet, `test` per il test e `validation` per lo sviluppo; per C++, The Stack;
     - si usa `whole_func_string`, che nei file pubblici coincide con `func_code_string` nel 100% delle righe (la frazione è registrata nel manifest);
     - si tengono solo le funzioni che si analizzano senza errori, eventualmente dentro un involucro di classe o struttura.
   - **Riferimento della stratificazione:** le righe (`function_loc`) dei negativi umani **nativi** della stessa parte e dello stesso linguaggio.
   - **Campionamento** per decili:
     - i gruppi sono delimitati dai quantili e, agli estremi, da minimo e massimo del riferimento; i candidati fuori campo si escludono (`out_of_range`);
     - quote assegnate con il metodo dei resti maggiori;
     - se un decile non ha abbastanza candidati si prende dal decile adiacente più vicino (a parità, quello inferiore) e il numero va in `moved_between_bins` nel manifest;
     - seme `derive_seed(global_seed, "sample", <sorgente>, <linguaggio>, "l1_<parte>_negatives")`.
   - `function_loc` conta solo le righe dentro le funzioni più esterne, così import, `class Solution` o `#include` delle soluzioni canoniche non falsano il confronto con le funzioni isolate di CodeSearchNet e The Stack.
   - Deduplicazione per impronta del codice senza spazi, contro i nativi e dentro il campione.
   - **Disgiunzione:**
     - CodeSearchNet: dai candidati di una parte si tolgono i repository presenti nello split usato dall'altra parte;
     - The Stack: partizione fissa per repository (`max_stars_repo_name`, `derive_seed(global_seed, "sample", "thestack_cpp", "repo", repo)`): `l3_test` 40%, `l1_test` 20%, `dev` 20%, `promptmark` 20%. Resta stabile per le milestone successive (cluster_info §8.5).
   - Tutta l'integrazione ha `contamination_risk=True` (I6).

6. **Rinvii.**
   - Le liste di frequenza di PromptMark (§15.2) si producono in M6, dopo l'audit di PromptMark (D8). La partizione di The Stack le prevede già.
   - **M9:** quando si aggiungono le sottomissioni CodeNet di sviluppo, l'integrazione di sviluppo va ricalcolata sulla distribuzione combinata L1+L2 (annotato anche in cluster_info §11).

## Conseguenze

- Cambiare regole di divisione, conteggio delle righe, partizione di The Stack o semi invalida gli artefatti di `prepare_data`: va fatto solo con un nuovo ADR.
- `tests/integration/test_l1_data.py` e `test_loc_oracle.py` (marker `data`) verificano i criteri della M2 sui dati reali del cluster.
