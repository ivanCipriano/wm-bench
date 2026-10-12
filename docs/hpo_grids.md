# Griglie dell'HPO (Milestone 8)

Le griglie hanno dimensioni diverse perché i metodi hanno un numero diverso di iperparametri regolabili:
- SWEET ne ha tre;
- STONE e PromptMark ne hanno due o tre;
- ACW ne ha uno;
- MCGMark nessuno: il bias è lo scarto dei logit, γ è fisso (D18) e la lunghezza del messaggio pure (D1).

I valori vengono dai paper e dai repository (audit §3 di ogni metodo) e dalle decisioni dell'utente
(`cluster_info` §11, TODO 6, 7, 16). L'HPO della M8 usa le soglie provvisorie di L1 e si rifà in M9 su L1+L2
(TODO 23, 24). La M9 riusa i risultati di L1 della M8 (stesse configurazioni) e aggiunge solo L2.

| Metodo | Iperparametri e valori | Configurazioni | Costo stimato M8 + M9 |
|---|---|---|---|
| SWEET | γ ∈ {0,1; 0,25; 0,5} × δ ∈ {0,5; 1; 2; 3; 4} × soglia di entropia ∈ {0,3; 0,6; 0,9; 1,2}, più il default del codice (γ = δ = soglia = 0,5) | 61 | ~116 GPU-h (generazione 84, rilevazione ~31) |
| STONE | γ ∈ {0,25; 0,5} × δ ∈ {0,5; 1,0; 1,5; 2,0; 3,0; 4,0; 5,0} (contiene δ = 0,5 e 1,0 del repository; sopra 1,0 è un'estensione, audit §3). **Provvisoria**: il limite superiore di δ si decide dopo la prova dose-risposta | 14 | ~13 GPU-h |
| PromptMark | iterazioni massime ∈ {1, 3, 5} × soglia η (come z) ∈ {1,64; 2,12; 2,58} × dimensione della green list ∈ {8, 10, 12}, più il default (5; 2,12; dimensione dalla chiave) | 28 | ~50 GPU-h: ricostruita da una traiettoria per dimensione della green list (ADR-009; ~240 GPU-h eseguendo ogni configurazione) |
| ACW | numero di trasformazioni ∈ {10, 20, 30, 43} | 4 | ~33 CPU-h (Sourcery) |
| MCGMark | nessuno | 1 | nessuno sweep |

STONE: l'HPO e le metriche usano `z_nonsyntax` come punteggio principale (D16, aggiornata il 12 ottobre 2026).

Origine dei costi:
- `scripts/estimate_hpo_cost.py`, sui tempi misurati in M7 (`docs/milestone_logs/M8_cost.txt`);
- la rilevazione di SWEET usa la velocità dei job di ripresa della M7 (0,06-0,14 s per codice), non quella
  del primo job (1,6-3,5 s, nodo lento);
- L2 è stimato con le numerosità di SPEC §10.2 e i costi per campione di L1, quindi è un limite inferiore:
  il codice di CodeNet è più lungo.
