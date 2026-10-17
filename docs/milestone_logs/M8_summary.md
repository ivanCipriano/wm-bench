# Riepilogo della Milestone 8 — HPO (SPEC §0, §22)

Data: 17 ottobre 2026. Stato: **chiusa** per la parte della M8 (HPO **provvisorio** su L1 dev, D24 e
TODO 23).
- La scelta definitiva su L1+L2 è della M9. È già fatta per STONE, PromptMark e MCGMark; ACW e SWEET sono in corso.
- La M9 e la M10 sono partite prima, su richiesta dell'utente.

Log:
- `M8_hpo_{sweet,stone,acw,promptmark,mcgmark}.txt`;
- `M8_cost.txt`;
- `M8_promptmark_replay.txt`;
- `M8_stone_{diag,dose,processor,vocab}.txt`.

## Criteri di accettazione (SPEC §22)

| Criterio | Esito |
|---|---|
| Griglie approvate dall'utente | fatte: `configs/hpo/<metodo>.yaml`, `docs/hpo_grids.md`. SWEET 61 configurazioni, STONE 18, PromptMark 28 (3 traiettorie), ACW 4, MCGMark 1 |
| Sweep come array SLURM | fatto con una cella per configurazione (`Cell.config_hash`, `hpo.grid`, ADR-010) invece di `--multirun`; quote `K/M` fra le due persone |
| `ConstrainedTprCriterion`, fase `tune` | fatti (`bench/hpo/criterion.py`, `stages/tune.py`) |
| File di configurazioni selezionate per (metodo, modello, linguaggio) | fatti: `hpo/selected/<m>/<modello>/<lang>.json` con la tabella dei candidati in `hpo/candidates/`, tutti `provisional` |
| Casi senza configurazione ammissibile segnalati | fatto: MCGMark con entrambi i modelli (nel report e nel log della fase) |

## Cosa è stato fatto

- **Sweep:**
  - celle espanse sulla griglia (`hpo.grid`, `hpo.part`);
  - `calibrate` per configurazione;
  - `tune` con soglia, TPR e Pass@1 del candidato. Il riferimento di ΔPass@1 è la gemella per MCGMark (D25) e la baseline LLM per gli altri metodi; per ACW è la baseline da cui deriva il codice.
- **PromptMark (ADR-009):** i 27 candidati si ricostruiscono da 3 traiettorie, una per dimensione della green list, invece di 27 generazioni. La ricostruzione è verificata carattere per carattere (`M8_promptmark_replay.txt`, 20/20 per configurazione); la stessa verifica è poi passata su L2 (`M9_promptmark_replay_l2.txt`).
- **Cluster:**
  - quote `K/M` e quote divise `K/M:k/m` per spartire il resto di una quota;
  - controllo dei conflitti fra job per nome (`wmb-<fase>.<metodi>[-s..]`);
  - posti liberi calcolati dagli script (MaxSubmit 10 per utente);
  - esclusione dei nodi guasti con `WMB_EXCLUDE_NODES` (gnode13 e gnode14, 14 ottobre 2026: `No CUDA GPUs are available`).
- **Script:**
  - `submit_hpo_l1.sh`, `check_hpo.py` e `report_hpo.py`;
  - `estimate_hpo_cost.py` (TODO 16);
  - le diagnosi di STONE (`diag_stone_*.py`).
- **STONE prima della griglia** (12 ottobre 2026; D16, D26):
  - lo z del codice ufficiale è invertito sul nostro codice: il punteggio principale diventa `z_nonsyntax`;
  - il default δ = 1,0 non lascia un watermark visibile;
  - la prova dose-risposta ha portato la griglia di δ fino a 10.

## Risultati su L1 dev (scelta provvisoria, soglie di solo L1)

Legenda: TPR alla soglia dell'1% di FPR. ΔPass@1 = baseline − marcati, in punti percentuali (positivo = perdita, vincolo ≤ 3 pp). Amm. = configurazioni ammissibili sul totale.

| Metodo | Modello | Ling. | Configurazione scelta | TPR % | ΔPass@1 pp | Amm. |
|---|---|---|---|---|---|---|
| SWEET | Qwen | python | δ 4, H 0,9, γ 0,25 | 26,6 | 2,43 | 42/61 |
| SWEET | Qwen | java | δ 4, H 0,6, γ 0,1 | 17,4 | 2,78 | 56/61 |
| SWEET | Qwen | cpp | δ 3, H 0,9, γ 0,1 | 8,3 | 0,00 | 32/61 |
| SWEET | Qwen | javascript | δ 2, H 0,6, γ 0,25 | 8,3 | 0,00 | 51/61 |
| SWEET | DeepSeek | python | δ 4, H 0,3, γ 0,25 | 62,0 | −0,35 | 52/61 |
| SWEET | DeepSeek | java | δ 4, H 0,3, γ 0,1 | 37,5 | 1,39 | 61/61 |
| SWEET | DeepSeek | cpp | δ 4, H 0,3, γ 0,5 | 45,1 | −0,69 | 60/61 |
| SWEET | DeepSeek | javascript | δ 3, H 0,9, γ 0,1 | 17,4 | 2,78 | 43/61 |
| STONE | Qwen | python | γ 0,5, δ 3 | 6,1 | 2,08 | 10/18 |
| STONE | Qwen | java | γ 0,25, δ 1 | 11,8 | 0,00 | 7/18 |
| STONE | Qwen | cpp | γ 0,5, δ 1,5 | 4,9 | 1,39 | 7/18 |
| STONE | DeepSeek | python | γ 0,25, δ 3 | 2,6 | 2,43 | 10/18 |
| STONE | DeepSeek | java | γ 0,25, δ 4 | 6,9 | −9,03 | 14/18 |
| STONE | DeepSeek | cpp | γ 0,5, δ 4 | 0,7 | −6,25 | 7/18 |
| PromptMark | Qwen | python | green 10, k 5, η 2,58 | 5,4 | −3,82 | 28/28 |
| PromptMark | DeepSeek | python | green 8, k 5, η 2,12 | 4,3 | 1,22 | 19/28 |
| ACW | Qwen | python | 43 regole | 100,0 | 0,35 | 4/4 |
| ACW | DeepSeek | python | 43 regole | 100,0 | 2,78 | 4/4 |
| MCGMark | Qwen | python | unica (default) | 7,3 | 48,78 | **0/1** |
| MCGMark | DeepSeek | python | unica (default) | 1,2 | 34,03 | **0/1** |

## Risultati da ricordare

- **SWEET:** l'HPO cambia il quadro rispetto al default della M7 (TPR 0-3%).
  - Con δ fino a 4 il TPR sale all'8-62%, quasi sempre senza costo su Pass@1.
  - DeepSeek si marca molto più di Qwen in tutti i linguaggi.
  - **La scelta cade sul bordo della griglia (δ = 4, il massimo) in 5 celle su 8:** il TPR migliore potrebbe stare oltre la griglia approvata (vedi le decisioni in fondo).
- **STONE:** TPR bassi (0,7-11,8%) anche dopo la griglia estesa; i δ alti che marcano davvero costano 20-60 pp di Pass@1 (`M8_hpo_stone.txt`).
- **Minaccia alla validità, vincolo applicato a stime rumorose** (promessa nel riepilogo della M7):
  - in Java, C++ e JavaScript lo sviluppo di L1 ha solo 24 problemi (144 campioni), quindi Pass@1 ha una risoluzione di 0,69 pp e ΔPass@1 un IC largo vari punti;
  - fra molti candidati il criterio tende a premiare quelli che rispettano il vincolo per caso (winner's curse). I ΔPass@1 negativi scelti lo mostrano: STONE con DeepSeek −9,0 pp in Java e −6,3 pp in C++, PromptMark con Qwen −3,8 pp;
  - lo conferma l'instabilità: con le soglie definitive su L1+L2 (M9) la scelta di STONE è cambiata in 5 celle su 6;
  - il costo vero si misura sul test, con la configurazione congelata (M10);
  - da dichiarare nella tesi.
- **ACW (TODO 20-22):**
  - con 10, 20 o 30 regole la soglia all'1% non è raggiungibile (τ = ∞, TPR 0): troppi negativi umani arrivano al punteggio pieno di un sottoinsieme piccolo. Il caso era previsto dall'audit (§3);
  - la scelta è quindi 43 regole: TPR 100%, ΔPass@1 0,35 e 2,78 pp;
  - con 43 regole T3.1 è `NOT_APPLICABLE` (TODO 21) e la verifica è "cieca" (TODO 22): da dichiarare nella tesi.
- **MCGMark:** una sola configurazione, **nessuna ammissibile** con entrambi i modelli (34-49 pp di perdita contro la gemella). Come prevede SPEC §14.4 si sceglie l'unica e si segnala. TPR bassi su L1, dove pochi campioni completano un ciclo di 24 posizioni (TODO 13, 25).
- **PromptMark:** TPR 4-5%. Il watermark resta debole con i modelli da 7B (TODO 19); la scelta pesa poco sul Pass@1.

## Cosa resta aperto

1. **Scelta definitiva su L1+L2 (M9):** fatta e congelata per STONE, PromptMark e MCGMark (`M9_hpo_final_*.txt`, `M10_freeze_*.txt`); in corso per ACW (detect di L2) e SWEET (execute e detect di L2, poi calibrate e tune definitivi).
2. Voci aperte delle milestone precedenti:
   - audit di MCGMark, PromptMark e ACW da confermare come approvati;
   - esclusione delle 6 classi ClassEval (D21).

## `TODO(user)` e decisioni

- **SWEET, bordo della griglia.** La griglia è stata approvata ed è la stessa in uso per L2 dev. Ci sono due strade:
  - **(a) Tenerla com'è e dichiarare nella tesi che δ = 4 è un limite della ricerca.** È la mia raccomandazione: la griglia è già approvata e il congelamento richiede una griglia invariata.
  - **(b) Estenderla (es. δ ∈ {5, 6}) prima della scelta definitiva.** Costa nuovi watermark, esecuzioni e rilevazioni su L1 e L2 dev per le configurazioni in più.
- `max_pass1_drop_pp` = 3,0 resta il valore provvisorio di SPEC §14: confermarlo come definitivo.

## Verifica locale finale

ruff e mypy puliti; pytest (unit) verde: 402 passati, 2 saltati al commit della fase 0 della M10.
