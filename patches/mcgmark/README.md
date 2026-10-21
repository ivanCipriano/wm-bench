# Patch di MCGMark

Submodule: `third_party/MCGMT` @ `eefa27b68747f5027c3121abcc506f3488eae990` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente). Escluso: `__pycache__`.

| File | Modifica | Impatto |
|---|---|---|
| `Watermark/homoglyphs.py`, `Watermark/normalizers.py`, `Watermark/homoglyph_data/` (nuovi) | copiati da `lm-watermarking` @ `82922516930c02f8aa322765defdb5863d07a00e` (submodule di riferimento `third_party/lm-watermarking`) | moduli importati da MCGMark ma assenti nel repository pubblicato |
| `Watermark/watermark_processor.py` | `from has_watermark import _runs_bool` invece di `from luxifer_test.has_watermark ...` (pacchetto assente); `device` passato a `judge_watermark_by_mod_phase` | rende importabile il processor |
| `Watermark/watermark_global.py` | aggiunti simboli assenti nella versione pubblicata (`base_result_dir`, `TOKEN_LENGTH`, `USER_ID`, `LLM_ID`, costruzione dei bit del messaggio, `set/get_old_water_info`), configurabili con variabili `MCG_*` | **rilevante**: ricostruisce parte della logica del messaggio. Verificato nell'audit di MCGMark (M6, §3.2): vedi sotto |
| `Watermark/watermark.py`, `Detection_Only.py`, `Watermark-MBPP.py` | `CUDA_VISIBLE_DEVICES` con `setdefault`; percorsi degli autori sostituiti con percorsi relativi; rimosso `mirror='tuna'` da `from_pretrained` | solo configurazione ed esecuzione |
| `Watermark/logs_counter.py` | cartella dei log relativa | solo script di conteggio |

## Messaggio: rapporto fra la patch 0000 e il framework

- `_bits()` (0000) costruisce il messaggio di default nel formato 4 bit utente + `1111` + 4 bit LLM (variabili
  `MCG_USER_ID`, `MCG_LLM_ID`). È solo il default di `get_old_water_info()`.
- `set_old_water_info(info)` (0000) accetta **qualunque stringa**: lo shim vi passa il messaggio di 12 bit del
  campione (D1, SPEC §9.5). Il formato 4+1111+4 quindi non vincola il framework e **non serve una patch** per un
  messaggio arbitrario.
- Chiude il punto 2 di `cluster_info` §11 per MCGMark.

## 0001-gamma-fisso.patch

- `Watermark/watermark_processor.py`, `__call__`: `self.gamma = 0.5` al posto della scelta dinamica (0,25 se i logit
  sono "uniformi", altrimenti 0,5).
- Motivo: codice incoerente con sé stesso (il rilevatore usa γ = 0,5 fisso) e paper §5.1 (rapporto di partizione
  0,5). Deviazione D18, decisa dall'utente il 6 ottobre 2026.
- Verifica: l'oracle confronta originale (sola 0000, copiata in `build/oracle_src/mcgmark`) e patch sullo stesso testo
  forzato: logit identici sui passi con γ = 0,5, differenze solo sui passi con γ = 0,25 e sui passi di correzione che
  ne dipendono; misura la quota di passi con γ = 0,25 e i bit recuperati.

## 0002-rimuovi-decodifica-vocabolario.patch

- `Watermark/watermark_processor.py`, `__call__`: rimossa `vocab_test = tokenizer.batch_decode(self.vocab)`, la
  decodifica dell'intero vocabolario a ogni passo in una variabile mai usata (`Useless_code_5` nel codice).
- Nessun effetto sulla semantica; solo velocità.

## 0003-rimuovi-calcoli-inutili.patch

Patch **puramente di prestazioni** (decisione dell'utente del 7 ottobre 2026): nessun effetto sul
testo generato né sui bit inseriti. In `Watermark/watermark_processor.py`, `__call__`, rimuove a ogni
passo di generazione:

| Calcolo rimosso | Perché è inutile dopo la 0001 |
|---|---|
| `teet = tokenizer.batch_decode(input_ids)` (decodifica dell'intera sequenza) e `Identify_chars = teet[-10:]` | `Identify_chars` non è mai letto |
| `scores[0].tolist()` (lista Python di tutto il vocabolario) | serviva solo a massimo, minimo e indice del massimo, ora presi con torch sugli stessi valori (`max().item()`, `min().item()`, `argmax`: primo indice del massimo, come `list.index`) |
| `is_evenly_distributed(...)` (media e deviazione in Python sull'intero vocabolario) | il risultato sceglieva γ, fissato a 0,5 dalla 0001 |
| `detect_outlier(...)` (percentili e scansione del vocabolario) e `selected_indices`, con `np.random.seed(hash_key)` e `np.random.choice` | `selected_indices` non è mai letto: il suo uso in `_get_greenlist_ids` è commentato nel repository |
| `tensor_value`, `scores[0].tolist()` dopo il bias, `max_value_3` | mai letti |

**Generatore casuale.** Fra i calcoli rimossi solo `np.random.seed`/`np.random.choice` usavano un
generatore: quello globale di **numpy**. Nessun'altra parte del metodo, di transformers o dello shim
legge il generatore di numpy (il campionamento usa quello di torch, le green list un `torch.Generator`
proprio): la rimozione non cambia la generazione.

**Verifica** (oracle, `test_patch_0003_keeps_the_text_identical_and_is_faster`): con lo stesso seme il
testo generato con 0000-0003 è identico carattere per carattere a quello con 0000-0002 (stessi id, stesse
posizioni e bit inseriti), sui prompt lunghi (3 CodeNet + 6 ClassEval); su L1 il codice del framework è
identico all'originale dove γ = 0,25 non compare. Lo speed-up misurato è riportato nell'audit (§9).

## Verifica

- `scripts/apply_patches.sh mcgmark` applica la patch a `build/patched/mcgmark/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di MCGMark (SPEC §21.4, M6): `scripts/oracle_method.sh mcgmark`.

## 0004-outlier-del-paper.patch

Componente del paper assente nel codice in forma attivabile (terza regola delle discrepanze di
`CLAUDE.md`, decisione dell'utente del 21 ottobre 2026; D32, audit di MCGMark §3.3). Il paper
(§4.3, Eq. (1), Algoritmo 1) include gli outlier superiori della distribuzione nella parte
favorita del vocabolario; il repository li calcola ma li usa solo in codice commentato, che non
funzionerebbe (con un solo outlier itera su un intero) e per il bit 0 li metterebbe nella parte
penalizzata.

In `Watermark/watermark_processor.py`, **dietro l'opzione `outlier_scale`** (S di Eq. (1);
`None` = spenta, default):

| Punto | Modifica (solo con l'opzione attiva) |
|---|---|
| `WatermarkBase.__init__` | argomento `outlier_scale` (default `None`) e stato `outlier_pending`, `outlier_log` |
| `__call__`, inizio | `_finish_outlier_bit`: bit di correzione della posizione d'informazione precedente = 1 se il token scelto è un outlier incluso che il rilevatore legge nell'altra parte (§4.3: "When outliers impact the watermark information, MCGMark sets the error-correction bit to 1") |
| `__call__`, dopo `max_index` | `_select_outliers`: outlier superiori dei logit con l'IQR (25° e 75° percentile, soglia Q3 + S·IQR) come `detect_outlier` del repository; uno → incluso, più di uno → metà (parte intera inferiore) con `np.random.choice` e seme `hash_key`, come il codice commentato, ma su un `RandomState` locale invece del generatore globale |
| `_bias_greenlist_logits`, rami del bit 1 e del bit 0 | `_include_outliers`: alle sole posizioni d'informazione (§4.3: i bit di correzione si inseriscono senza outlier) gli outlier scelti entrano nella green list per il bit 1 e ne escono per il bit 0, cioè vanno nella parte che riceve il bias in entrambi i casi |
| `_get_greenlist_ids` | il caso particolare del codice (token più probabile nella green list col bit 1, `is_pure`) resta solo a opzione spenta: con l'opzione attiva lo sostituisce l'Algoritmo 1 |

- **Scelte dal codice dove il paper non è esplicito:** outlier sui logit (il paper dice "token
  probability distributions"); metà per difetto (l'Algoritmo 1 scrive ⌈|F|/2⌉); scelta con
  `hash_key`. Il paper non dà un valore di S: la griglia S ∈ {1,0; 1,5; 3,0} è fissata
  dall'utente prima della prova (audit §3.3).
- **Rilevazione invariata:** il rilevatore legge l'appartenenza alla green list senza outlier e
  decodifica `informazione XOR correzione`, come prima.
- **Verifica:** a opzione spenta il testo deve essere identico carattere per carattere a quello
  dei campioni marcati di L1 dev congelati (0000-0003, stesso seme); a opzione attiva
  inserimento, messaggi recuperati e bit corretti dal bit di correzione
  (`scripts/check_mcgmark_outliers.sh`, 20 prompt di L1 dev Python).
