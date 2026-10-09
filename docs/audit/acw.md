# Audit di ACW (SPEC §9.0)

- **Stato:** decisioni dell'utente del 9 ottobre 2026 applicate (§3 punteggio, §4 iperparametri).
  Oracle verde il 9 ottobre 2026 (3 test, `tnode01`). **Aperto:** la licenza di Sourcery usata è una
  prova "Code Quality - Team" che scade il 10 ottobre 2026 (§6).

## 0. Esiti dell'oracle (9 ottobre 2026)

- Inserimento: lo shim (a lotti) produce lo stesso codice del percorso diretto su 10 campioni della baseline
  (5 HumanEval, 5 MBPP+), tutti `OK`; 1-4 regole applicabili per campione (siti idonei).
- Rilevazione: esito per regola e congiunto identico al percorso diretto su 30 codici. Punteggi: codice
  marcato 1,000 in 10 casi su 10; baseline 0,907-0,977 (media 0,951); codice umano 0,907-0,977 (media
  0,951). Decisione congiunta: vera per tutti i marcati, falsa per tutti gli altri.
- Chiave e ordine: con 43 regole k1 e k2 danno 0 codici diversi su 10.
- Sourcery 1.33.0: nessun errore né limite in 30 chiamate consecutive (7,8-7,9 s l'una) e in 104 chiamate del
  percorso diretto (mediana 8,4 s); 3,4 chiamate per campione con lotti da 10.
- **Repository:** `Noelle1831-k/ACW`; paper "Efficient and Universal Watermarking for LLM-Generated Code Detection"
  (arXiv 2402.07518v5, 10 luglio 2026, IEEE Transactions on Software Engineering).

## 1. Commit e codice

- Commit `2236dc30` (28 febbraio 2026, submodule `third_party/ACW`); copia `build/patched/acw/` con la patch
  preesistente `0000`. Codice in `source/` (import assoluti: `patched_subdir: source`).
- Ambiente `acw` (Python 3.10.21, **senza torch**): metodo solo CPU. Dipendenze da `source/requirements.txt`
  (`sourcery==1.33.0`, `autopep8`, `libcst` usato da `base_ast.py`, `chardet`, `fire`).
- Punto d'innesto: `refactor.py::WatermarkInjector` (variante con selezione delle regole per seme) e il confronto
  per hash di `ExperimentEvaluator`; `RQ1-get-results.py` è la versione degli esperimenti del paper (tutte le
  regole, nessuna selezione).

### 1.1 Patch preesistente 0000 (chiude il punto 2 di `cluster_info` §11 per ACW)

| File | Modifica | Motivo verificato | Caso |
|---|---|---|---|
| `refactor.py` | `apply_pep8_rule` da `one_rule_format.reformatting_process` invece che da `formatting_pep8` | `formatting_pep8.py` **non definisce** `apply_pep8_rule` (ha solo funzioni per cartelle che rinominano i file): l'originale fallisce all'import | codice incoerente con sé stesso → patch minima |
| `one_rule_format.py` | `transform_*(file_path)` chiamate una volta per file | le funzioni di `base_ast.py` prendono solo il percorso; l'originale le chiamava con `(node, lines, file_path, tree)` per ogni nodo: `TypeError` catturato, quindi **le regole 36-39 non facevano nulla** | idem |
| `.sourcery.yaml` | elenco delle 35 regole Sourcery | coincide con `refact_list.py`; serve a `RQ1` | configurazione |
| altri script | percorsi, ID, `fire` | solo esperimenti originali | — |

## 2. Inserimento

- `WatermarkInjector(num_transforms, seed, random_rules=True)`: pool di regole 1-45 **senza 5 e 10**
  (`remove-unnecessary-cast`, `avoid-builtin-shadow`), cioè 43 regole; `random.seed(seed)` e scelta di
  `num_transforms` regole; ordinate e poi mescolate (`random_rules=True`, come la CLI).
  - 1-35: regole Sourcery (`refact_list.py`), applicate **tutte insieme** con
    `sourcery review --config <temporaneo> --fix <cartella>`;
  - 36-39: riordino con libcst (`a + b`, `a * b`, `<`/`>`, `<=`/`>=`: gli operandi si ordinano per hash);
  - 40-44: autopep8 (spazi, E27, E123, W292, righe vuote);
  - 45: indentazione con tabulazioni.
- Paper (§III-A, Algoritmo 1): si applicano le prime n trasformazioni **applicabili** in un ordine segreto;
  esperimenti con n = |T| (tutte). 46 regole nel paper, 45 identificativi nel codice (43 usati): codice
  coerente con sé stesso ma diverso dal paper → si segue il codice e si documenta.

### 2.1 Regole del paper e del codice (46 contro 43)

Confronto fra la tabella delle regole del README (`assets/rules.png`, 35 di refactoring, 5 di riordino,
6 di formattazione = 46) e il codice (`refact_list.py`, `one_rule_format.py`, `base_ast.py`):

| Gruppo | Paper | Codice | Strumento nel codice |
|---|---|---|---|
| Refactoring | 35 | 35 identificativi (1-35), 33 usati | Sourcery |
| Riordino | 5 | 4 (36-39) | libcst (`base_ast.py`) |
| Formattazione | 6 | 6 (40-45) | autopep8 (40-44), codice proprio (45) |

- **Nel paper ma non nel codice:**
  - refactoring "hash di una classe con più parametri: costruttore non eseguito se il valore è dispari"
    (trasformazione propria basata su hash, R7 nel paper);
  - refactoring "sostituire le ricerche per indice nei cicli con il riferimento diretto" (corrisponde alla
    regola Sourcery `for-index-replacement`, assente da `refact_list.py`);
  - riordino "scambio dei blocchi di un'assegnazione if-else ordinati per hash".
- **Nel codice ma non nella tabella del paper:** `hoist-statement-from-loop` (2) e `assign-if-exp` (35).
- **Nel codice ma esclusi dal pool:** 5 `remove-unnecessary-cast` e 10 `avoid-builtin-shadow` (presenti nel
  paper; nessuna motivazione nel codice).
- Il riconoscimento delle corrispondenze viene dalle descrizioni della tabella del README (non sempre i
  nomi delle regole Sourcery): va letto come la migliore corrispondenza possibile, non come un elenco
  ufficiale.
- **Watermark senza chiave** con tutte le regole: la "chiave" è solo il seme della scelta del sottoinsieme
  (§4).
- **Rischi semantici (da misurare con il tasso di preservazione, SPEC §13.3):**
  - regola 36 scambia gli operandi di **ogni** `+` senza operandi complessi, anche concatenazioni di stringhe
    e liste (`s + "x"` → `"x" + s`), che non sono commutative;
  - regola 45 converte gli spazi iniziali in tabulazioni anche **dentro le stringhe multiriga** e scarta gli
    spazi in eccesso (non multipli di 4), cambiando il contenuto delle stringhe.
  Codice coerente con sé stesso ma diverso dal "semantic-preserving" del paper → si segue il codice; l'effetto
  si vede su Pass@1.
- **Errori silenziosi:** il comando Sourcery gira con `shell=True` e output scartato, il codice di uscita è
  ignorato: senza login o senza rete le regole 1-35 non si applicano e non si nota. Lo shim fa un **controllo
  di partenza** (un file canarino che Sourcery deve modificare) e fallisce in modo esplicito.
- **Input** (SPEC §9.2): i 6 campioni della baseline per prompt (codice estratto D4); output = codice
  trasformato, `parent_id` = campione della baseline. `FAILED` se il codice resta invariato o il tool fallisce.

## 3. Rilevazione e punteggio (decisione aperta)

- Codice (`run_single_folder`): si riapplica lo **stesso insieme** di regole a una copia e si confrontano gli
  hash: invariato = marcato. Decisione **binaria per file**.
- Paper (§III-B): si applica **ciascuna** delle n trasformazioni e si controlla se il codice cambia; "marcato"
  se nessuna lo cambia; per i progetti, test binomiale sulle funzioni.
- SPEC §9.2: se il rilevatore è binario, usare il conteggio sottostante come punteggio continuo.
- **Decisione dell'utente:** punteggio = frazione delle regole selezionate la cui riapplicazione **singola**
  lascia il codice invariato (identificazione per trasformazione del paper, §III-B); decisione nativa =
  riapplicazione congiunta del codice, solo diagnostica (D2).
- In `extra` di ogni rilevazione: l'esito di ogni regola (`rule_unchanged`), il numero di regole selezionate,
  quante regole modificano il codice esaminato (`n_rules_changed`, cioè quante vi trovano un punto di
  applicazione) e l'esito congiunto: bastano per punteggi alternativi senza rieseguire Sourcery. In
  inserimento si registrano le regole che modificano il codice della baseline applicate da sole
  (`applicable_rules`, `n_applicable` = siti idonei di ACW, colonna `n_sites`).
- **Punteggio discreto** (valori k/43): la calibrazione segue SPEC §13.2; se la soglia all'1% di FPR non è
  raggiungibile (troppi negativi umani con punteggio pieno), in M7 si riporta esplicitamente invece di
  forzare una soglia.
- Codice vuoto: `FAILED`. Il codice con errori di sintassi si valuta come fa il metodo (Sourcery e libcst non
  lo modificano; autopep8 e la regola 45 sì).

## 4. Iperparametri (decisione aperta)

| Protocollo | Codice | Paper |
|---|---|---|
| numero di trasformazioni | `num_transforms` (classe: 10; CLI: 46 → tutte le 43) | n = \|T\| |
| chiave | seme della scelta del sottoinsieme | nessuna chiave; ordine segreto |
| soglia | nessuna (decisione binaria) | nessuna |

- La soglia nativa non è un iperparametro (D2).
- **Decisione dell'utente:** default fino all'HPO = **tutte le 43 regole** (paper n = |T|, CLI del codice);
  griglia della M8: `num_transforms` ∈ {10, 20, 30, 43}, con il sottoinsieme scelto dalla chiave.
- **Chiave e ordine con 43 regole:** il seme non sceglie più nulla ma mescola l'ordine
  (`random_rules=True`). Le regole Sourcery si applicano comunque tutte in una chiamata, quindi l'ordine conta
  solo fra le regole proprie 36-45. L'oracle confronta il codice marcato con la chiave k1 e con la k2
  (stesse regole, ordine diverso). Primo oracle: **0 codici diversi su 10**; con tutte le 43 regole la
  chiave non cambia il risultato su questi codici.
- **Verifica "cieca":** con n = |T| non c'è nessun segreto: chiunque conosca le regole (pubbliche nel
  repository) può verificare il watermark, e anche toglierlo o falsificarlo applicandole. Va dichiarato
  nella tesi.
- **T3.1 (seconda chiave):** se l'HPO sceglie 43 regole per la configurazione finale, T3.1 per ACW è
  `NOT_APPLICABLE` ("senza sottoinsieme scelto dalla chiave la seconda chiave non produce un watermark
  diverso"); promemoria per la M11.

## 5. Linguaggi

- Paper: valutato solo su Python; codice: Sourcery per Python, libcst, autopep8. **Solo Python** (D6, già
  decisa).

## 6. Esecuzione, rete e costo

- **Licenza:** al login Sourcery risponde "Your trial ends on 2026-10-10, update your payment method to continue
  using Sourcery Code Quality - Team". Dopo la scadenza la CLI potrebbe non applicare più le regole: il
  controllo di partenza dello shim lo rileva e ferma il worker. Decisione dell'utente da prendere (§6).
- Sourcery è una CLI che richiede login (`sourcery login --token $SOURCERY_TOKEN`) e rete: i nodi `defq` hanno
  accesso in uscita (cluster_info §6). ACW gira su CPU (`defq`).
- Il token entra solo come segreto del worker (`secrets`), mai in un file del repository; lo shim fa il login
  all'avvio.
- Costo: ogni chiamata a Sourcery richiede secondi; il punteggio per regola richiede 35 chiamate Sourcery per
  lotto. Lo shim lavora **a lotti** di 50 file (una cartella per chiamata; supporto ai lotti aggiunto al runner
  degli shim, `ShimBase.batch_size`) invece che file per file.
- **Limiti di Sourcery:** la documentazione attuale non descrive più la CLI (l'ultima versione su PyPI è la
  1.46.0, settembre 2026; l'ambiente ha la 1.33.0, gennaio 2025) e non indica limiti di frequenza o di volume.
  L'oracle fa una prova di 30 chiamate consecutive cronometrate, registrando codici di uscita e messaggi.
  Primo oracle (9 ottobre 2026, `tnode01`): 30 chiamate, tutte con codice 0, 8,4 secondi l'una dalla prima
  all'ultima, nessun messaggio su limiti o quote; 104 chiamate nel percorso diretto, mediana 8,5 s. Nessun
  limite osservato. Se emergono limiti, prima di lanciare L1 si propone una strategia (lotti più
  grandi, pause, cache dei risultati per codice già visto).
- **Stima delle chiamate** (lotti di 50; 36 chiamate per lotto in inserimento e altrettante in rilevazione,
  cioè 0,72 per campione e per operazione):
  - L1 dev, inserimento: 1.152 campioni della baseline (2 modelli × 576) → 24 lotti → circa 860 chiamate;
  - L1 dev, rilevazione (M7): marcati, baseline e negativi umani di sviluppo, circa 3.000-3.500 codici → circa
    2.200-2.500 chiamate;
  - HPO (M8), 4 valori di `num_transforms`: inserimento e rilevazione per ciascuno, al più circa 13.000
    chiamate (meno per i sottoinsiemi piccoli, che hanno meno regole Sourcery).
- **Tempo:** con 8,5 s a chiamata, l'inserimento di L1 dev richiede circa 2 ore, la rilevazione della M7
  circa 6, l'HPO fino a circa 31: se il tempo di una chiamata dipende poco dal numero di file (prova a 10
  file), conviene un lotto più grande; il secondo oracle misura una chiamata su 30 file.
- **Regole che modificano codice** nel primo oracle (20 codici di baseline e umani, applicate da sole): 11,
  15, 16, 23, 35 (Sourcery), 36-38 (riordino), 40, 44 (autopep8), 45 (tabulazioni, 19 codici su 20). La
  regola 45 domina: quasi ogni codice umano indentato con spazi cambia, il che separa codice marcato e umano
  ma rende il watermark fragile a una semplice riformattazione.
- Il numero di chiamate a Sourcery per campione è registrato in `extra` (`sourcery_calls_batch`,
  `sourcery_calls_per_sample`) come misura di efficienza.
- **Controllo di partenza:** login con il token e tre file canarino (casi tipici delle regole Sourcery 11,
  4 e 7, in una sola chiamata); se Sourcery non ne modifica nessuno il worker si ferma (errore di setup)
  invece di produrre campioni senza le regole 1-35. Il primo oracle usava la regola 1 e si è fermato: quella
  regola non ha mai trovato nulla da fare, neanche nel percorso diretto ("No issues detected"), quindi il
  canarino era sbagliato, non Sourcery. Il `PATH` del worker include la cartella dell'interprete dell'ambiente `acw`, dove stanno `sourcery` e
  `autopep8` (il codice li chiama dalla shell).
- `random.seed` globale, cartelle temporanee e file `.sourcery_temp_*.yaml` nella directory corrente: lo shim
  lavora in una cartella dedicata.

## 7. Equivalenza per riga

Non applicabile (nessun processor di logit).
