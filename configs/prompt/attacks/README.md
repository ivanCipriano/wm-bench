# Prompt dell'attacco T2.2 (riscrittura con LLM, SPEC §15)

**T2.2: testo v2 approvato dall'utente il 24 ottobre 2026** (anteprima
`docs/milestone_logs/M11_t2_2_preview_draft_v2.txt`). SHA-256 registrati in
`configs/attack/t2_2_llm_rewrite.yaml` e nel manifest di ogni cella: un testo diverso ferma l'attacco.
Prima del lancio completo: pilota C++ di T2.2(b) (`scripts/pilot_t2_2_cpp.sh`, regola del 10%).

**T2.3: BOZZA non approvata** (`system_translate.txt`, `translate.txt`); l'attacco è spento
(`enabled: false` in `configs/attack/t2_3_roundtrip.yaml`).

- `system.txt`: system prompt comune alle due varianti;
- `conservative.txt`: variante (a), refactoring conservativo;
- `full_rewrite.txt`: variante (b), riscrittura completa;
- `system_translate.txt`, `translate.txt`: T2.3, traduzione verso il linguaggio intermedio e ritorno
  (segnaposto in più: `{{ target_language }}`).

Segnaposto: `{{ language }}` (Python, Java, C++, JavaScript), `{{ tag }}` (tag della fence: python, java,
cpp, javascript), `{{ code }}` (codice estratto del campione da attaccare). Testo identico per tutti i metodi
e i linguaggi: cambia solo il nome del linguaggio. Nessun riferimento al watermark.

Decoding proposto: `meta-llama/Llama-3.1-8B-Instruct` (`llama31_8b_attacker`), greedy (temperatura 0,
`do_sample=False`), `max_new_tokens` = 2048, nessun'altra trasformazione dei logit. Decodifica del testo con
`clean_up_tokenization_spaces=False` (Llama 3.1 lo ha a `True` e toglierebbe lo spazio prima di `!`, `.`, `,`,
`?` anche nel codice e nelle stringhe: `a != b` diventava `a!= b` nella prima anteprima). Estrazione con la regola
D4 (`FencedCodeExtractor`); se fallisce, il campione si registra con lo stato di errore dell'attacco, mai
scartato. Lo SHA-256 dei tre file va nel manifest dell'attacco.

Anteprima prima/dopo su campioni reali: `bash scripts/preview_t2_2.sh` (gpuq), che scrive solo in
`$WMB/tmp/t2_2_preview/`.
