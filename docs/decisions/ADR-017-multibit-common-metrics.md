# ADR-017 — Metriche multi-bit comuni per i metodi post-hoc (`mcss`, `multimark`)

- **Stato:** proposto (P2 di `third_party/multimark/PIPELINE_metodo.md` v1.2.8, §5.1–§5.4), da
  concordare con Simone prima dell'unione su `main`: le modifiche toccano `detect.py`,
  `metrics.py` e `methods/base.py`, che le sue catene usano.
- **Contesto:** fino a P1 il benchmark aveva un solo metodo multi-bit, MCGMark. Il calcolo dei bit
  corretti, la lunghezza del messaggio (12 bit), le metriche di capacità e la preservazione
  condizionata (solo ACW) erano scritti per i metodi esistenti. `mcss` e `multimark` hanno 24 bit,
  un punteggio che non conta bit (`-log10` di una coda binomiale), un registro di esche e sono
  `CodeEmbedder` come ACW.
- **Vincolo comune a tutte le sezioni:** per i metodi esistenti nessuna metrica cambia. Lo
  dimostrano due test:
  - `tests/unit/test_metrics_compat.py` confronta riga per riga, a tolleranza 1e-9, le metriche
    di MCGMark (senza attacco e sotto T1.5), STONE e ACW con quelle registrate dal codice di
    prima (`tests/fixtures/compat/metrics_before_p2.json`, commit `955ab01`);
  - `scripts/check_metrics_compat.py`, da lanciare sul cluster, fa lo stesso confronto sulle
    celle reali già calcolate (`metrics/*.parquet`). La cella si ricostruisce dal manifest del
    parquet, non dal percorso: il seme di ogni intervallo bootstrap è `bootstrap_seed(global_seed,
    metrica, cell.key())`, e le celle con la configurazione di default hanno `config_hash = None`
    nella chiave. Il primo tentativo (16 novembre) ricostruiva la cella dal percorso: valori
    identici e tutti gli estremi spostati. Nelle fixture gli intervalli sono degeneri, quindi
    `tests/unit/test_metrics_compat_intervals.py` li rende dipendenti dal seme e confronta anche
    `ci_low` e `ci_high` a 1e-9.

  Sono ammesse solo righe nuove. Le metriche nuove non consumano numeri da un generatore comune:
  ogni metrica ha il suo seme (nome della metrica e cella).
- Ogni sezione ha un commit `[P2] common: …` a sé.

## 1. `bits_correct` per metodo (§5.1)

**Decisione.** `Detector.bits_correct(result: WorkerResult) -> int | None` in
`src/bench/methods/base.py`. Il default è il codice di prima (`detect.py::_bits_correct`):
`None` se mancano `decoded_message` o `score`, altrimenti `int(score)`. Per MCGMark il punteggio è
il numero di bit uguali al messaggio (D1). La fase `detect` chiama `adapter.bits_correct(result)`.
`mcss` e `multimark` ridefiniscono il metodo leggendo `extra["bits_correct"]`: i bit del messaggio
decodificato uguali a quelli attesi, o i bit decisi dal voto se la decodifica è ambigua (decisione
L di §7).

**Alternativa scartata.** Un ramo su `method in ("mcss", "multimark")` dentro `detect.py`:
avrebbe legato la fase ai nomi dei metodi, come fa oggi `method == "acw"` nelle metriche, cosa
che il §4 corregge.

**Effetto sui metodi esistenti.** Nessuno: nessun adapter esistente ridefinisce il metodo.
`tests/unit/test_detect_bits_correct_compat.py` lo verifica su 1.000 `WorkerResult` casuali per
ciascuno dei cinque metodi, confrontando con la funzione di prima, copiata nel test.

## 2. Lunghezza del messaggio per metodo e `message_accuracy_detected` (§5.2)

**Decisione.**

- `Detector.message_bits: ClassVar[int | None] = None`. `McgmarkAdapter.message_bits = 12`, come
  la costante `MESSAGE_BITS` di prima; `mcss` e `multimark` hanno 24.
- `MetricsStage._capacity(add, boot, positives, message_bits, tau)` usa la lunghezza
  dell'adapter. La costante `MESSAGE_BITS = 12` di `metrics.py` è tolta. Un metodo con messaggio
  (che ridefinisce `message_for`) ma senza `message_bits` è un errore di configurazione.
- `_multibit` resta com'è: un metodo è multi-bit se ridefinisce `message_for`.
- Nuova metrica `message_accuracy_detected`: frazione dei positivi **rilevati** con la soglia
  della cella (`detected` di `metrics/calibration.py`, la stessa regola di `tpr_at_fpr`) che hanno
  tutti i `message_bits` bit giusti. IC di Wilson, `n` = numero di rilevati.
  - Vale anche per MCGMark (misura nuova e additiva, §0.3 della pipeline).
  - Senza nessun positivo rilevato la metrica non è definita e la riga non si scrive, perché I7
    vuole un intervallo per ogni metrica. È il caso delle celle finte dei test, con soglia
    `inf`.

**Effetto sui metodi esistenti.**

- `message_accuracy`, `bit_accuracy` ed `embedding_success_rate` di MCGMark restano identici.
  `tests/unit/test_metrics_capacity_compat.py` li confronta con la funzione di prima, copiata
  nel test come `_capacity_legacy`, su cinque tabelle sintetiche da 300 positivi; lo stesso
  confronto lo fa `test_metrics_compat.py` sulla catena finta.
- MCGMark riceve la riga nuova `message_accuracy_detected`.
- Gli altri metodi non hanno messaggio e non cambiano.

## 3. Capacità sotto attacco e attribuzioni sbagliate (§5.3)

**Decisione.**

- **Capacità sotto attacco.** In `MetricsStage._attacked_metrics`, dopo i punteggi secondari, i
  metodi multi-bit ricevono la capacità anche sugli attaccati: `_capacity(..., message_bits,
  tau)`, con la stessa definizione della cella senza attacco (rilevazione `FAILED` = 0 bit) e la
  soglia congelata della cella senza attacco.
- **Attribuzioni sbagliate.** `wrong_attribution_rate_<nome>` per ogni punteggio secondario
  `<nome>` di `adapter.secondary_scores` per cui `extra` ha `best_<nome>`, cioè il messaggio
  scelto dal registro di esche (`nreg16`, `nreg1024` di `mcss` e `multimark`, pipeline
  §3.10.4).
  - Valore: frazione dei positivi **rilevati** con quel punteggio (soglia secondaria della
    cella) il cui messaggio scelto è diverso dal messaggio atteso del campione. Il messaggio
    atteso viene da `expected_message` dei campioni marcati o attaccati, per `sample_id`. IC di
    Wilson.
  - Si calcola nelle celle senza attacco e in quelle con attacco.
  - Non si emette quando nessuna riga ha `best_<nome>`, cioè per tutti i metodi esistenti, né
    quando nessun positivo è rilevato (I7).
- La tabella dei campioni attaccati (`refs["attacked"]`) si legge una volta sola: serve sia ai
  messaggi attesi sia all'intensità dell'attacco, come prima.

**Effetto sui metodi esistenti.**

- Nelle celle con attacco di MCGMark si aggiungono `message_accuracy`, `bit_accuracy`,
  `embedding_success_rate` e, se c'è almeno un rilevato, `message_accuracy_detected`. Le metriche
  che c'erano già restano identiche: `test_metrics_compat.py` le confronta riga per riga sulle tre
  celle T1.5 della catena finta e verifica che le righe nuove siano solo quelle elencate.
- Nessun metodo esistente ha `best_<nome>` in `extra`, quindi `wrong_attribution_rate_*` non
  compare per nessuno di loro (`tests/unit/test_metrics_attacked_compat.py`). Lo stesso test
  verifica il calcolo su tabelle sintetiche con un registro finto.

## 4. `conditional_preservation` per ogni `CodeEmbedder` (§5.4)

**Decisione.** In `MetricsStage.run` la condizione `if method == "acw"` diventa
`if self._post_hoc(method)`, cioè `isinstance(adapter, CodeEmbedder)`: ogni metodo che marca il
codice della baseline riceve P(corretto dopo | corretto prima), con lo stesso calcolo di prima
(`_conditional_preservation`, Wilson).

**Effetto sui metodi esistenti.**

- Fra i cinque metodi esistenti l'unico `CodeEmbedder` è ACW, quindi la metrica compare nelle
  stesse celle di prima (`tests/unit/test_metrics_conditional_compat.py`).
- Il valore di ACW sulla catena finta resta identico (`test_metrics_compat.py`).
- `mcss` e `multimark` la ricevono (`tests/unit/test_posthoc_smoke.py`).

## 5. `metrics` nello sweep dell'HPO (pipeline di multimark v1.3.0, 17 novembre 2026)

**Decisione.** `MetricsStage.sweep_roles = ("candidate",)`: con `hpo.grid=<metodo>` la fase
calcola le metriche di ogni candidato della griglia, come già facevano `watermark`, `execute`,
`detect` e `calibrate`. Serve ai due punti di sviluppo di `multimark` anticipati dalla pipeline
v1.3.0 (`configs/hpo/multimark.yaml`), che senza questa riga avrebbero avuto soglia e rilevazione
ma non Pass@1, ΔPass@1 e le altre metriche.

Due regole tengono ferme le uscite che c'erano:

- `sweep_default_unhashed` (nuovo `ClassVar` di `Stage`, vero solo per `metrics`): il punto della
  griglia con l'hash del default diventa la cella di default (`config_hash = None`), cioè la
  stessa cella pianificata senza `hpo.grid`. Stessa chiave, quindi stessi semi del bootstrap
  (`bootstrap_seed(global_seed, metrica, cell.key())`); se le uscite esistono la pipeline la
  salta. Senza questa regola il punto di default, calcolato nello sweep con l'hash esplicito,
  avrebbe scritto nello stesso percorso intervalli con semi diversi.
- `MetricsStage.sweep_cell`: si espandono solo le celle di sviluppo senza attacco. Il test usa
  solo la configurazione congelata (ADR-013), le celle sotto attacco restano quelle di prima.

**Effetto sui metodi esistenti.** Nessuno, se non si chiede lo sweep: senza `hpo.grid` il
pianificatore esce prima di guardare `sweep_roles`, quindi le celle e le metriche dei lanci di
sempre (anche quelle della configurazione congelata) non cambiano. Con `hpo.grid` si aggiungono
solo le celle dei candidati richiesti. Lo verificano:

- `tests/unit/test_metrics_sweep_cells.py`: per `multimark`, STONE e MCGMark le celle senza hash
  dello sweep sono un sottoinsieme di quelle di prima, il test e le celle sotto attacco restano
  uguali, e le celle in più sono esattamente i candidati di sviluppo (il default escluso);
- `tests/unit/test_posthoc_smoke.py::test_multimark_sweep_adds_only_the_candidate_metrics`: sulla
  catena finta, dopo il giro di default lo sweep di `multimark` esegue il codice marcato dei due
  punti e ne calcola le metriche (Pass@1 compreso); il parquet e il manifest delle metriche di
  default restano identici byte per byte;
- i test di retrocompatibilità di questa ADR, invariati.
