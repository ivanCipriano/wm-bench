# Patch di MCGMark

Submodule: `third_party/MCGMT` @ `eefa27b68747f5027c3121abcc506f3488eae990` (cluster_info §9).

## 0000-modifiche-preesistenti.patch

Modifiche preesistenti dell'utente (non scritte dall'agente). Escluso: `__pycache__`.

| File | Modifica | Impatto |
|---|---|---|
| `Watermark/homoglyphs.py`, `Watermark/normalizers.py`, `Watermark/homoglyph_data/` (nuovi) | copiati da `lm-watermarking` @ `82922516930c02f8aa322765defdb5863d07a00e` (submodule di riferimento `third_party/lm-watermarking`) | moduli importati da MCGMark ma assenti nel repository pubblicato |
| `Watermark/watermark_processor.py` | `from has_watermark import _runs_bool` invece di `from luxifer_test.has_watermark ...` (pacchetto assente); `device` passato a `judge_watermark_by_mod_phase` | rende importabile il processor |
| `Watermark/watermark_global.py` | aggiunti simboli assenti nella versione pubblicata (`base_result_dir`, `TOKEN_LENGTH`, `USER_ID`, `LLM_ID`, costruzione dei bit del messaggio, `set/get_old_water_info`), configurabili con variabili `MCG_*` | **rilevante**: ricostruisce parte della logica del messaggio. Nell'audit (M6) va confrontata con il paper e con la decisione D1 (messaggio a 24 bit) |
| `Watermark/watermark.py`, `Detection_Only.py`, `Watermark-MBPP.py` | `CUDA_VISIBLE_DEVICES` con `setdefault`; percorsi degli autori sostituiti con percorsi relativi; rimosso `mirror='tuna'` da `from_pretrained` | solo configurazione ed esecuzione |
| `Watermark/logs_counter.py` | cartella dei log relativa | solo script di conteggio |

## Verifica

- `scripts/apply_patches.sh mcgmark` applica la patch a `build/patched/mcgmark/` sul commit fissato: verificato in M0.
- La fedeltà del comportamento è verificata dal test oracle di MCGMark (SPEC §21.4, M6).
