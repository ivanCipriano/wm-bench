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

## Verifica

- `scripts/apply_patches.sh mcgmark` applica la patch a `build/patched/mcgmark/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di MCGMark (SPEC §21.4, M6): `scripts/oracle_method.sh mcgmark`.
