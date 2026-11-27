# ADR-018 — Attacchi nuovi: T1.6 nomi in `snake_case` e T2.5 diluizione

- **Stato:** accettato per T1.6 (decisioni dell'utente del 19 novembre 2026) e per T2.5
  (diluizione, PIPELINE di multimark §5.5; decisioni dell'utente del 23 novembre 2026, §4).
- **Contesto:** il canale dei nomi di `mcss` (V1) scrive 2 bit per variabile nello **stile** del
  nome (`snake_case` 00, `camelCase` 01, `PascalCase` 10, `ALL_CAPS` 11). Portare tutti i nomi
  in `snake_case`, come chiede PEP 8, è un attacco realistico: lo fa chi uniforma lo stile di un
  file. Nessun attacco del benchmark lo faceva. T1.4 rinomina con nomi casuali o della raccolta
  CodeSearchNet, cioè con uno stile casuale. Le regole di ruff di T1.2 non comprendono quelle sui
  nomi di PEP 8, che comunque ruff non corregge da solo. T4.1 `rename=true` accorcia i nomi.
  Il canale dei nomi V2 di `multimark` legge i bit da `norm(n)`, quindi è invariante allo stile.
- **Vincolo:** è un'aggiunta. Un nuovo `attack_id` con il suo file
  (`src/bench/attacks/t1/snake_case.py`, `configs/attack/t1_6_snake_case.yaml`). `rename.py` e
  gli altri attacchi non cambiano, e così le celle già calcolate.
  `tests/unit/test_attacks_t1_snake_case.py::test_existing_attack_hashes_are_unchanged` fissa gli
  `attack_params_hash` di tutte le altre griglie (quelli di S si ritrovano in `P2_round3.txt`).
  Lo smoke test `test_posthoc_smoke.py::test_t16_adds_cells_without_touching_the_others` verifica
  che le uscite di T1.5 già calcolate restino identiche, byte per byte, dopo T1.6.

## 1. Regole

- **Linguaggi:** solo Python; sugli altri `NOT_APPLICABLE`. Lo stile dei nomi del canale V1
  esiste solo nei metodi post-hoc Python.
- **Determinismo:** l'attacco non usa l'RNG. Lo stesso codice dà sempre lo stesso risultato.
- **Griglia:** `caps ∈ {convert, keep}`, cioè 2 punti con `attack_params_hash` `9ad68973c317`
  (`convert`) e `8e1e8882fb91` (`keep`). Correzione del 26 novembre 2026: la prima stesura di
  questo ADR aveva le due etichette invertite; il codice (`grid_attacks("T1.6")`),
  `bench.chains.POSTHOC_POINTS` e i log di P6 hanno sempre usato gli hash giusti.
  - `convert`: tutto in `snake_case`, costanti di modulo comprese. È la regola di §5.5.
  - `keep`: i nomi **di modulo** in `ALL_CAPS` restano invariati, come chiede PEP 8 per le
    costanti. `ALL_CAPS` vuol dire un nucleo con almeno una lettera e nessuna minuscola (`MOD`,
    `N`, `MAX_N`). I nomi in `ALL_CAPS` dentro le funzioni si convertono comunque.
  - Perché due punti: con il solo `convert` l'attacco favorirebbe `multimark` rispetto a `mcss`,
    perché cancella anche lo stile 11 di `mcss` a livello di modulo, dove un attaccante che segue
    PEP 8 lo lascerebbe. Con due punti il confronto è dichiarato, e il report li mette in righe
    separate.
- **Candidati negli scope di funzione** (lambda comprese): gli stessi di T1.4, cioè
  `rename.python_candidates`. Sono le variabili locali e i parametri, con le stesse esclusioni:
  `self`, `cls`, dunder, `entry_point`, nomi in `global` o `nonlocal`, nomi usati come argomento
  con nome, funzioni che usano `locals`, `vars`, `eval` o `exec`.
- **Candidati di modulo: scostamento da §5.5.** §5.5 dice «candidati come T1.4», e T1.4 non tocca
  i nomi di modulo (`docs/deviations.md`). Ma `mcss` e `multimark` (decisione O) marcano anche le
  variabili di modulo. Su L2, dove i programmi CodeNet sono script, una parte del canale
  resterebbe quindi fuori portata. La voce di `deviations.md` del 15 novembre lo aveva già
  previsto, e §3.6.1 dice che T1.6 «usa esattamente la regola di questa sezione».
  - Si prendono i nomi legati a livello di modulo solo da nomi (niente `def`, `class`, `import`).
  - Si applicano le esclusioni (h) e (i) di §3.6.1. (h) esclude i nomi in un'istruzione `global`
    o `nonlocal`, in `__all__`, i nomi usati come argomento con nome, `self`, `cls`, i dunder e
    `entry_point`. (i) esclude tutti i nomi di modulo se il file usa `globals`, `locals`, `vars`,
    `eval` o `exec`. La regola (i) vale in qualunque punto del file, non solo a livello di
    modulo: è più prudente di §3.6.1.
  - La rinomina copre tutte le occorrenze, comprese le letture dentro funzioni e classi
    (`ScopeProvider` di LibCST).
- **Mai rinominati:** variabili delle comprehension, attributi di classe, nomi di funzioni, classi
  e import, attributi (`.n`).
- **Conversione:** `norm(n)` di §3.6.2, cioè `multimark.names.normalize_name`; un test la
  confronta con la libreria. I segmenti si spezzano su `_`, sui cambi da minuscolo a maiuscolo e
  sulle sequenze di maiuscole seguite da minuscola, poi vanno in minuscolo e si uniscono con `_`.
  - Gli `_` iniziali e finali si conservano: `_tmpVal` diventa `_tmp_val`, `list_` resta `list_`.
  - Restano invariati i nomi già in `snake_case`, i nuclei vuoti e i nuclei con caratteri fuori
    da `[A-Za-z0-9_]`.
- **Collisioni:**
  - Un nome originale riceve un solo nome nuovo in tutto il file, quindi la stessa rinomina in
    ogni scope. Due nomi originali diversi non ricevono mai lo stesso nome nuovo. La rinomina
    resta coerente e non cambia le relazioni fra i nomi.
  - Se `norm(n)` è già una parola del file (anche in stringhe e commenti, come in T1.4), una
    parola riservata, un builtin o il nome nuovo di un altro nome, si prova `norm(n)_1`, poi
    `_2` e così via.
  - I nomi si assegnano nell'ordine della prima occorrenza. Con `maxVal` e `MaxVal`, il primo
    diventa `max_val` e il secondo `max_val_1`. Con `N` e `n`, `n` resta e `N` diventa `n_1`.
- **Esiti:**
  - `APPLIED` se c'è almeno una rinomina, altrimenti `UNCHANGED`.
  - `TOOL_ERROR`, con il codice originale, se l'ingresso o l'uscita non passano `ast.parse`, o se
    due rinomine toccano lo stesso intervallo.
  - `attack_detail` contiene: `n_candidates`, `n_module`, `n_renamed`, `n_module_renamed`,
    `n_collisions`, `n_caps_kept` (nomi di modulo in `ALL_CAPS` lasciati con `keep` che sarebbero
    cambiati), `n_occurrences`.

## 2. Verifiche

- **Unitarie**, in `tests/unit/test_attacks_t1_snake_case.py`:
  - conversione, confrontata con la libreria;
  - underscore e `ALL_CAPS`;
  - rinomine con `convert` e `keep`, con la stessa uscita del programma;
  - collisioni (nome già presente, nome di un altro, parola riservata, builtin, parola in una
    stringa);
  - stesso nome in due funzioni;
  - esclusioni;
  - nomi di modulo letti in funzioni e classi;
  - `UNCHANGED`, `NOT_APPLICABLE`, `TOOL_ERROR` e determinismo;
  - griglia e hash degli altri attacchi.
- **Canoniche di L1** (sviluppo e test) ancora corrette dopo l'attacco: con
  `scripts/check_attacks_canonical.sh --languages python --attacks T1.6` sul cluster, in un job
  defq con Apptainer. Lo script è generico, quindi non serve modificarlo.

## 3. Uso in P2 (solo sviluppo)

Si usa con `scripts/run_p2_snake_dev.sh` e `scripts/report_snake_dev.py`, log `P2_snake_dev.txt`.
Il giro riguarda il codice marcato del giro di capacità (libreria `a4e6972`) delle stesse 7
configurazioni: `mcss` e i 6 punti di `configs/hpo/multimark.yaml`, compresa B, la provvisoria.
Gira solo su L1 e L2 dev, con le soglie del giro di capacità e senza ricalibrare. La deviazione
dal protocollo, che prevede gli attacchi sul test con la configurazione congelata, è in
`docs/deviations.md`. Sul test T1.6 entrerà con gli altri attacchi in P3 e P6.

## 4. T2.5 diluizione (23 novembre 2026)

- **Contesto:** §5.5 di PIPELINE: un attaccante aggiunge al file funzioni umane che non vengono
  chiamate. Il codice originale resta intatto, ma i metodi che leggono il messaggio da tutto il
  file (`mcss`, `multimark` con legame `positional`) ricevono voti da siti che non portano bit. È
  l'attacco del fattore «legame» dell'ablazione (§6.3) e una delle quattro celle del criterio di
  §6.4.
- **Vincolo:** come per T1.6, è un'aggiunta: un nuovo `attack_id` (`src/bench/attacks/t2/dilution.py`,
  `configs/attack/t2_5_dilution.yaml`) e una fase nuova (`function_pools`). Gli altri attacchi e le
  celle già calcolate non cambiano: `tests/unit/test_attacks_t2_dilution.py::test_grid_and_other_hashes`
  fissa gli `attack_params_hash` di tutte le altre griglie, T1.6 compreso.
- **Fase `function_pools`** (`src/bench/pipeline/stages/function_pools.py`, dati in
  `src/bench/data/function_pools.py`): una cella, solo Python, uscita
  `data/function_pools/python.parquet`. Le funzioni di CodeSearchNet **train** si leggono in ordine
  casuale con il seme `derive_seed(global_seed, "function_pool", "python")` e si tengono le prime
  5.000 che passano i filtri, con il conteggio dopo ciascuno nel manifest:
  - i filtri delle raccolte di ADR-012 (righe oltre 200 caratteri, marcatori di codice generato);
  - quelli di §5.5: `ast.parse` riuscito (più `compile`, che scarta i `nonlocal` senza funzione
    esterna), una sola `def`, 3–40 righe di codice (non vuote e non di solo commento), nessun
    `import`, nessun decoratore, non ricorsive, senza `global`;
  - **un filtro in più, `definition_time`**: niente annotazioni e solo default letterali.
    Annotazioni e default si valutano quando la `def` viene eseguita, quindi una funzione con
    `x: np.ndarray` o `timeout=DEFAULT_TIMEOUT` farebbe fallire il programma al caricamento, e le
    canoniche non resterebbero verdi come chiede §5.5;
  - i duplicati esatti.
- **Attacco:** solo Python, livello di file, `n` funzioni distinte estratte con l'RNG dell'attacco
  e messe a livello di modulo.
  - `before`: dopo la parte iniziale fatta di docstring e `import`. È il punto «dopo le `import`
    iniziali e prima della prima definizione» di §5.5 quando fra le due non c'è altro; altrimenti
    vale «dopo le `import`».
  - `after`: in coda.
  - `around`: le prime `ceil(n/2)` prima, le altre dopo.
  - Due righe vuote separano ogni funzione aggiunta.
- **Collisioni:** se il nome di una funzione aggiunta è già una parola del file, una parola
  riservata, un **builtin** o il nome di un'altra funzione aggiunta, la `def` diventa
  `<nome>_ext<k>`. I builtin non sono in §5.5: una `def sorted` messa prima del codice ne cambierebbe
  il comportamento. Basta rinominare la `def`, perché le funzioni della raccolta non sono
  ricorsive.
- **Esiti:**
  - `APPLIED`;
  - codice vuoto `NOT_APPLICABLE`, come chiede §5.5 (gli altri attacchi lo trattano come
    `UNCHANGED`);
  - `TOOL_ERROR` con il codice originale se l'ingresso o l'uscita non si analizzano.
  - `attack_detail` = `{n_inserted, names, position, added_loc, function_ids, n_renamed}`.
- **Griglia:** 3 celle, `(n, position)` ∈ `{(1, before), (2, around), (4, around)}`, con
  `attack_params_hash` `c32f7d55a266`, `9528392ed687` e `a2bc6db7f357`. È la riduzione della
  decisione G di §7, scelta dall'utente il 23 novembre 2026; (2, around) è la cella di §6.3 e §6.4.
- **Uso:** giro P4 su sviluppo (`scripts/run_p4_dev.sh`), solo sui punti di `mcss` e `multimark`.
  Il perimetro sul test (tutti i metodi Python secondo §5.5) si decide dopo.
- **Verifiche:**
  - unitarie in `tests/unit/test_attacks_t2_dilution.py`: filtri, raccolta, posizioni,
    collisioni, programma invariato (le funzioni aggiunte non cambiano i test), determinismo,
    esiti;
  - canoniche di L1 sul cluster: `LOG=P4_t2_5_canonical.txt bash scripts/check_attacks_canonical.sh
    --languages python --attacks T2.5` (fase `canonical` di `run_p4_dev.sh`). Lo script legge la
    raccolta di funzioni se la fase `function_pools` è stata lanciata e altrimenti salta T2.5.

## 5. P7.a: T1.6 e T2.5 su ACW, STONE e MCGMark (26 novembre 2026)

- **Decisione** (PIPELINE di multimark v1.3.18, P7 ridotta, punto P7.a; decisioni dell'utente del
  26 novembre 2026): T1.6 (`convert`, `keep`) e T2.5 (i tre punti) sul test di L1 e L2, solo
  python, per i metodi Python del proprietario `ivan` (ACW, STONE, MCGMark), con le loro
  configurazioni e soglie congelate. Niente per SWEET e PromptMark (proprietario `simone`). È il
  perimetro sul test lasciato aperto in §4 («tutti i metodi Python secondo §5.5»), ristretto ai
  metodi di `ivan`.
- **Cosa non cambia:** gli attacchi, le griglie e quindi gli `attack_params_hash` (§1 e §4); le
  configurazioni e le soglie congelate; le celle già calcolate. Le celle di P7.a sono nuove (questi
  hash non sono mai stati calcolati per questi metodi). La raccolta di funzioni di T2.5
  (`data/function_pools/python.parquet`, una per linguaggio, indipendente dal metodo) è quella di
  P4 e P6.
- **Catene** (`bench.chains.p7a_steps`, in coda a `all_steps`; si lanciano solo con `--only`):
  per metodo, livello e attacco i passi attack, execute, detect e metrics, con `languages=[python]`
  (STONE ha anche java e cpp: nessuna cella `NOT_APPLICABLE` in più). Su L1 nessuna dipendenza dai
  passi senza attacco: per questi metodi L1 test è stato fatto nella M10 fuori dalle catene
  (`submit_attacks_l1.sh`) e le fasi leggono gli artefatti; su L2 le dipendenze di sempre
  (`<metodo>/L2/watermark` e `<metodo>/L2/metrics`). ACW ha `acw=True` anche su L1 (la rilevazione
  usa Sourcery: parte solo con `--with-acw`) e una variante ridotta, catene
  `acw/<livello>/<attacco>-reduced` con `attack_points` T1.6 `convert` (`9ad68973c317`) e T2.5
  `n = 2, around` (`9528392ed687`). Le sue celle sono un sottoinsieme di quelle della variante
  completa: stessi `attack_params_hash`, quindi **stessi artefatti e stesse cartelle di lavoro del
  worker** (non ci sono uscite separate, e non servono: è lo stesso calcolo). Perché le due
  varianti non scrivano mai insieme le stesse celle, l'attacco della completa dipende dalle
  metriche della ridotta: la completa parte solo a ridotta finita e rifà soltanto i punti che
  mancano; la ridotta lanciata dopo la completa risulta già finita. I passi di prima sono identici
  (`tests/unit/test_chains.py::test_p7a_steps`).
- **Risorse:** attacco, esecuzione e metriche su CPU; la rilevazione segue
  `resources.gpu_methods_by_stage.detect` (STONE e MCGMark su GPU, ACW su CPU con Sourcery).
  `scripts/estimate_closing_cost.py` usa ora la stessa regola per metodo (prima contava la fase,
  quindi la rilevazione di ACW come GPU) e ha `--only` come le catene.
- **Controlli e report:** `scripts/check_attacks.py` ammette `--level L2` per questi tre metodi
  solo con T1.6 e T2.5; per il resto l'uscita non cambia. `src/bench/report.py` non cambia: il
  report di default dei cinque metodi resta lo stesso byte per byte e le celle di P7.a non ci
  entrano (includerle è un passo successivo, da decidere).
