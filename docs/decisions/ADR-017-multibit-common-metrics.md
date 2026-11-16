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
