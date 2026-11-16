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
    celle reali già calcolate (`metrics/*.parquet`).

  Sono ammesse solo righe nuove.
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
