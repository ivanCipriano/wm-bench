# Prompt dell'attacco T2.2 (riscrittura con LLM, SPEC §15)

**Stato: BOZZA, non approvata dall'utente.** Nessun job T2.2 va lanciato finché il testo non è approvato
(requisiti dell'utente del 21 ottobre 2026).

- `system.txt`: system prompt comune alle due varianti;
- `conservative.txt`: variante (a), refactoring conservativo;
- `full_rewrite.txt`: variante (b), riscrittura completa.

Segnaposto: `{{ language }}` (Python, Java, C++, JavaScript), `{{ tag }}` (tag della fence: python, java,
cpp, javascript), `{{ code }}` (codice estratto del campione da attaccare). Testo identico per tutti i metodi
e i linguaggi: cambia solo il nome del linguaggio. Nessun riferimento al watermark.

Decoding proposto: `meta-llama/Llama-3.1-8B-Instruct` (`llama31_8b_attacker`), greedy (temperatura 0,
`do_sample=False`), `max_new_tokens` = 2048, nessun'altra trasformazione dei logit. Estrazione con la regola
D4 (`FencedCodeExtractor`); se fallisce, il campione si registra con lo stato di errore dell'attacco, mai
scartato. Lo SHA-256 dei tre file va nel manifest dell'attacco.

Anteprima prima/dopo su campioni reali: `bash scripts/preview_t2_2.sh` (gpuq), che scrive solo in
`$WMB/tmp/t2_2_preview/`.
