# ADR-018 — Attacchi nuovi: T1.6 nomi in `snake_case`

- **Stato:** accettato per T1.6 (decisioni dell'utente del 19 novembre 2026). T2.5 (diluizione,
  PIPELINE di multimark §5.5) si aggiungerà a questo ADR in P3.
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
- **Griglia:** `caps ∈ {convert, keep}`, cioè 2 punti con `attack_params_hash` `8e1e8882fb91`
  (`convert`) e `9ad68973c317` (`keep`).
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
