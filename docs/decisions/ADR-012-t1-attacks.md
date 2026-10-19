# ADR-012 — Attacchi T1: tool, configurazioni, raccolte e intensità

- **Stato:** accettata (Milestone 10, 19 ottobre 2026).
- **Contesto:**
  - SPEC §7.7 (interfaccia), §12 (attacchi), §18 (semi), §20.2 (tool), §21.3 (proprietà);
  - decisioni dell'utente del 16 ottobre 2026 (piano della M10: raccolta C++, ricette Java, densità dei commenti);
  - ADR-013 (congelamento e fase 0).

## Decisione

### 1. Infrastruttura (`src/bench/attacks/`)

- **Classi:** `Attack` (registro `ATTACKS`), `AttackContext`, `AttackOutcome` come in SPEC §7.7. `AttackChain` e `BatchAttack` arrivano con la M11, che ne ha bisogno.
- **Griglie:** `configs/attack/<file>.yaml` con `space` (prodotto cartesiano) o `points` (elenco esplicito, usato per T1.3).
- **`attack_params_hash`:** 12 caratteri dello SHA-256 di attacco, parametri e `version` dell'implementazione. Una correzione dell'attacco cambia l'hash, quindi non si mescolano risultati di versioni diverse.
- **Celle:** il `CellPlanner` espande ogni attacco sulla sua griglia (`Cell.attack_params_hash`), come lo sweep dell'HPO.
- **Seme per campione:** `derive_seed(global_seed, "attack", attack_id, params_hash, sample_id)`.
- **Errori:** un errore su un campione non ferma la cella. Il campione diventa `TOOL_ERROR` con il codice originale, e si esegue e si rileva comunque (I5).

### 2. Tool esterni (`ToolRunner`, `tools/`, `scripts/setup_tools.sh`)

Un avvio di JVM o Maven per campione costerebbe ore, quindi i tool lavorano **a lotti**:
- scrivono N file in una cartella temporanea, lanciano una sola invocazione con timeout e rileggono i file;
- se il lotto fallisce lo dividono a metà, finché l'errore resta isolato sui singoli file.

I tool si cercano nell'ambiente `bench-core` e in `tools/`.

| Attacco | Python | Java | C++ | JavaScript |
|---|---|---|---|---|
| T1.1 | `black` 24.10.0 (in processo, `Mode()` di default) | google-java-format 1.24.0 (jar `all-deps`, SHA-256 fissato) | clang-format 18, `BasedOnStyle: Google` | prettier 3.3.3 (default espliciti in `.prettierrc.json`) |
| T1.2 | `ruff check --fix --isolated` 0.7.4 (regole di default, solo correzioni sicure) | OpenRewrite `CommonStaticAnalysis` (plugin 5.45.0, `rewrite-static-analysis` 1.20.0) | clang-tidy 18 `--fix -- -std=c++17` | eslint 9.14.0 `--fix` (`js.configs.recommended`, `globals.node` 15.12.0) |

Note sui singoli tool:
- **OpenRewrite.** Le ricette di `CommonStaticAnalysis` sono elencate in `docs/attacks/t1_2_java_recipes.txt`; `setup_tools.sh` le confronta con il jar. Il progetto Maven è temporaneo, con **un package per file** (`p00001`): così classi con lo stesso nome convivono. La riga `package` si aggiunge in scrittura e si toglie in lettura. Maven gira offline (`-o`) con il repository `tools/java/m2/`, popolato dal nodo di login e condiviso dalle due persone.
- **clang-tidy.** Si usano solo controlli che si correggono da soli e non richiedono un progetto:
  - `readability-braces-around-statements`;
  - `readability-else-after-return`;
  - `readability-simplify-boolean-expr`;
  - `readability-isolate-declaration`;
  - `modernize-use-auto`;
  - `modernize-loop-convert`;
  - `modernize-use-nullptr`.

  Con errori di compilazione (es. header assenti nell'ambiente) clang-tidy non applica le correzioni e il campione risulta `TOOL_ERROR`.
- **google-java-format** con le impostazioni di default ordina gli import e toglie quelli inutilizzati: fa parte del formatter, si tiene.
- **ESLint** si lancia con `-c tools/js/eslint.config.mjs` dalla cartella dei file: con `-c` la base dei percorsi è la cartella corrente.

### 3. Attacchi

- **T1.3 commenti** (2 configurazioni).
  - **(a) Rimozione.** Una riga con solo il commento sparisce, un commento a fine riga si toglie, uno in mezzo al codice diventa uno spazio. Le docstring Python restano (SPEC §12).
  - **(b) Inserimento.**
    - Quanti: `max(1, round(density × righe di codice))`, con `density` = 0,333 (nell'hash).
    - Dove: punti scelti con l'RNG fra i **punti prima delle istruzioni**, cioè istruzioni figlie dirette di un blocco dentro una funzione che aprono la loro riga. Mai dentro stringhe, mai fra i `case` di uno `switch` senza graffe.
    - Come: sintassi di riga e indentazione del linguaggio.
    - Testi: dalla raccolta del linguaggio.
    - Per campione si salvano `n_comments` e `added_token_fraction`.
- **T1.4 rinomina** (8 configurazioni). Si rinominano `max(1, round(ratio × candidati))` coppie (funzione, nome) con l'RNG.
  - **Python:** libcst `ScopeProvider`. Esclusi `self`, `cls`, `global` e `nonlocal`, i nomi usati come argomenti con nome, le funzioni con `locals`, `vars`, `eval` o `exec`, i gruppi con `import`, `def` e `class`.
  - **Java, C++ e JS:** parametri e dichiarazioni locali delle funzioni più esterne. Mai i nomi visibili fuori dalle funzioni (campi, globali, classi, macro), i nomi di funzione, `entry_point`, né i nomi che compaiono in posizione di membro, di nome qualificato, di etichetta o di funzione chiamata.
  - **JavaScript:** `{x}` diventa `{x: nuovo}`.
  - **Nomi nuovi:** `random` = lettera + 7 caratteri alfanumerici; `natural` = dalla raccolta, con ripiego su un nome casuale (contato) dopo 50 tentativi. Mai collisioni con parole riservate o con parole presenti nel file.
- **T1.5 modifiche localizzate** (`k` ∈ {1, 3, 5}). Per ogni modifica l'RNG sceglie il tipo fra quelli con punti disponibili, poi il punto.
  - **Variabile inutilizzata.** In C++ si usa `[[maybe_unused]]`, per non dipendere dai flag di avviso.
  - **Log su stderr.** In C++ si aggiunge `#include <iostream>` se manca. Precisazione della definizione dell'attacco (versione 2, decisione dell'utente del 19 ottobre 2026): il log va solo in punti eseguiti **al massimo una volta per chiamata di funzione**, cioè:
    - fra le istruzioni del corpo diretto della funzione, mai dentro cicli o rami;
    - mai in funzioni che richiamano sé stesse (ricorsione diretta, anche come metodo).

    Motivo: il protocollo definisce T1.5 come modifiche senza effetto sul risultato, e SPEC §21.3 chiede che le canoniche continuino a passare. Un log eseguito milioni di volte viola entrambe le condizioni: nella prima verifica, `mbpp/389` e `mbpp/583` (ricorsione esponenziale) andavano in `TIMEOUT`. I punti persi con la regola sono riportati da `check_attacks_canonical.py`.
  - **Niente log in JavaScript** (D30): il valutatore di HumanEvalPack considera fallito ogni programma JavaScript che scrive su stderr. Le `k` modifiche si scelgono fra gli altri due tipi, con `k` invariato. In Java e C++ il valutatore guarda solo il codice di uscita (ADR-001), quindi lì il log resta.
  - **Estrazione di un'espressione binaria in una temporanea.** Solo da istruzioni semplici con un solo dichiaratore, che non contengono:
    - chiamate, `new`, incrementi, assegnazioni annidate, lambda;
    - `instanceof` (Java);
    - dichiarazioni `const`/`constexpr`/`static`/`final`.

    L'espressione non viene mai:
    - dal lato destro di `&&`, `||` o `??`, né dai rami di `?:`;
    - da argomenti di template;
    - da `<<` o `>>` in C++, che potrebbero essere operatori di stream.

    Non si estraggono espressioni di soli letterali.
  - **Punti esclusi:** prima di `super(...)` e `this(...)`, prima della docstring e prima di una direttiva JavaScript.
- **Verifica del parsing.** Se T1.3, T1.4 o T1.5 rendono non analizzabile un codice che prima lo era, il campione resta originale con `TOOL_ERROR`.

### 4. Raccolte di commenti e nomi (fase `attack_pools`, `data/attack_pools/<lang>.json`)

- **Fonti:**
  - Python, Java e JS: 50.000 funzioni di CodeSearchNet **train** in ordine casuale fissato dal seme;
  - C++: le funzioni dei repository del bucket `attack_pools` di The Stack (D29).
- **Filtri:** gli stessi per i quattro linguaggi, nell'ordine di `bench/data/attack_pools.py`, con il conteggio dopo ciascuno nel manifest. Si tengono solo i commenti dentro le funzioni.
- **Capienza:** al più 20.000 voci per lista.
- **Report:** `scripts/thestack_partition_report.py` stampa:
  - repository, file e funzioni di ogni bucket di The Stack, con il fabbisogno di ogni uso;
  - le voci rimaste dopo ogni filtro, per linguaggio.

### 5. Pipeline e intensità

- **Fase `attack` (CPU):** output `attacked/<attacco>/<hash>/<metodo>/<modello>/<cfg>/<L>_<lang>_<parte>.parquet`, con una riga per campione marcato (I1). Ogni riga ha:
  - `parent_id`;
  - `embed_status` del genitore (I2);
  - `attack_detail` (JSON);
  - `codebleu_distance` (1 − CodeBLEU, `codebleu==0.7.0`).
- **Manifest della cella:** riassume l'**intensità di ogni attacco**, confrontabile fra linguaggi (decisione dell'utente per T1.2, estesa a tutti):
  - stati;
  - frazione modificata;
  - distanza media;
  - medie dei conteggi;
  - versioni dei tool.
- **Fasi successive:**
  - `execute`: fonte `attacked`;
  - `detect`: soggetto `attacked/<attacco>/<hash>`, con prompt e messaggio atteso come i positivi;
  - `metrics`: con la soglia congelata calcola `tpr_at_fpr`, `auroc` e i punteggi secondari; poi `pass_at_1`, `delta_pass_at_1_pp` (rispetto al riferimento del metodo), `delta_pass_at_1_pp_vs_marked`, `changed_fraction`, `tool_error_fraction` e `codebleu_distance`.
- **Nomi dei job:** finiscono con l'attacco senza punto (`wmb-detect.stone_test_T14`).
- **Script:** `scripts/submit_attacks_l1.sh`, `scripts/check_attacks.py` (`--report`).

### 6. Verifica sulle canoniche (`scripts/check_attacks_canonical.sh`)

- Ogni configurazione T1 si applica alle canoniche di L1 (sviluppo e test, quattro linguaggi), che si eseguono con gli executor di L1. Il risultato è confrontato con l'esecuzione delle canoniche della M4.
- Per T1.2 su Java, ogni canonica rotta si riattacca **una ricetta alla volta** (`-Dwmb.recipe=…`) per trovare la ricetta colpevole. Nessuna ricetta si toglie senza la decisione dell'utente.

## Conseguenze

- **Dichiarazione nella tesi:** le intensità di T1.2 (e degli altri attacchi) sono diverse fra linguaggi. Si misurano con la frazione modificata e con 1 − CodeBLEU per cella.
- **Fattibilità:**
  - T1.3(b) e T1.4 `natural` richiedono la fase `attack_pools`;
  - T1.1 e T1.2 richiedono `setup_tools.sh`.
- **Ripresa:** la fase `attack` non ha una ripresa a metà cella; una cella T1 dura pochi minuti.
- **Test:** `tests/unit/test_attacks_t1.py`, `test_attack_tools.py`, `test_attack_pools.py`, `test_attack_stage.py`. I test dei tool non Python si saltano dove il tool manca e girano sul cluster.
