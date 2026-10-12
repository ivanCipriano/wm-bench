# ADR-009 — Griglia di PromptMark ricostruita da una traiettoria per dimensione della green list

- **Stato:** accettata (Milestone 8, 12 ottobre 2026; decisione dell'utente con due condizioni, entrambe
  verificate).
- **Contesto:** SPEC §14; `cluster_info` §11, TODO 16; audit di PromptMark §3;
  `docs/milestone_logs/M8_cost.txt`, `M8_promptmark_replay.txt`.

## Contesto

La griglia di PromptMark (TODO 16) è:
- iterazioni massime k ∈ {1, 3, 5};
- soglia η (come z) ∈ {1,64; 2,12; 2,58};
- dimensione della green list ∈ {8, 10, 12};
- più il default (5; 2,12; dimensione dalla chiave).

In tutto sono 28 configurazioni. Ogni campione di L1 costa circa 29,5 s di GPU, perché il ciclo fa in
media 4,75 iterazioni su 5. Eseguita configurazione per configurazione, la griglia costa circa 240 GPU-h
fra M8 e M9, due terzi dell'intero HPO.

## Decisione

Per ogni dimensione della green list si esegue **una sola traiettoria**: 5 iterazioni e soglia
irraggiungibile (z = 10⁶, quindi `norm.sf(z) = 0` e `p_exact < 0` non è mai vero). Il risultato di ogni
combinazione (k, η) si ricava a posteriori, fermando la traiettoria dove `run_phase1`
(`exp_iterative_wm.py:207-284`) si sarebbe fermato:
- **stop** alla prima iterazione t < k con codice corretto e `p_exact < norm.sf(η)`: si sceglie quella;
- **altrimenti** si sceglie la candidata `best`: la prima iterazione corretta, se c'è, se no la prima.

Lo shim registra già, per ogni iterazione, risposta, codice, correttezza e `p_exact` (`extra.iterations`).
La funzione di ricostruzione è `replay` in `scripts/verify_promptmark_replay.py`, coperta da test unitari.
Nella fase `tune` diventerà una funzione del framework, con gli stessi test.

**Costo:**
- M8: 3 traiettorie (green list 8, 10, 12); il default è già calcolato in M7;
- M9: le 3 traiettorie su L2 più il default su L2;
- in tutto circa 50 GPU-h invece di circa 240.

## Condizioni (decisione dell'utente) e verifica

1. **Il feedback e i semi non dipendono né da η né da k, salvo la decisione di fermarsi.** Verificato nel
   codice:
   - il feedback dell'iterazione t dipende solo dall'esito dell'iterazione t−1: messaggio d'errore degli
     esempi se il codice è sbagliato, oppure p-value e conteggi se è corretto ma non marcato. η decide solo
     lo stop e non compare nel testo;
   - i semi sono `derive_seed(seme del campione, "promptmark-iter", t)`;
   - il seme del campione dipende da modello, problema e indice.
2. **Su almeno 10 campioni, due configurazioni eseguite davvero coincidono carattere per carattere con
   quelle ricostruite.** `scripts/verify_promptmark_replay.py`, 12 ottobre 2026, job 793294 su gpuq.
   - **Campione:** Qwen, 10 problemi L1 dev (4 HumanEval+, 6 MBPP+) × 2 campioni. Sono i problemi con il
     p-value più basso fra le iterazioni corrette della M7, scelti per mettere alla prova anche lo stop.
   - **Risultati:**
     - configurazione A (k = 3, η = 2,12): 20 campioni su 20 identici; 10 si fermano prima del massimo;
     - configurazione B (k = 5, η = 1,64): 20 su 20 identici; 16 si fermano prima;
     - confronto in più, il default ricostruito contro gli artefatti della M7 (altro job e altro nodo):
       20 su 20 identici.
   - **Cosa si è confrontato:** risposta scelta, iterazione scelta, numero di iterazioni e tutte le risposte
     intermedie.

## Conseguenze

- La griglia completa da 28 resta quella decisa (TODO 16), senza riduzioni.
- **Le configurazioni ricostruite sono configurazioni a tutti gli effetti.** Ciascuna ha il proprio
  `config_hash` e la propria tabella di campioni marcati, poi esecuzione dei test, rilevazione e
  calibrazione come le altre. Cambia solo il modo in cui si ottengono le risposte scelte. Il manifest
  registra l'origine (`replayed_from`: hash della traiettoria).
- Il costo di esecuzione dei test e di rilevazione resta per configurazione (CPU, piccolo).
- Se in futuro cambiano il codice di PromptMark, lo shim o il decoding, la verifica va ripetuta.
