# Piano di chiusura: M13, M14 e SWEET in un unico blocco

**Eccezione alla regola "una milestone alla volta"** (CLAUDE.md, SPEC §0): autorizzata dall'utente il
30 ottobre 2026 per M13, M14 e la parte di SWEET. Il codice rimasto si scrive in un unico blocco,
poi si fanno prove brevissime (smoke test) e infine si lanciano tutti i calcoli lunghi. Il
riepilogo §0 si consegna per ciascuna milestone alla fine.

Fasi: 0) verifiche e domande; 1) implementazione; 2) smoke test sul cluster; 3) stima complessiva,
lancio con dipendenze SLURM, comando di stato.

## Fase 0: verifiche delle fasi esistenti su L3 (30 ottobre 2026)

| Punto | Esito |
|---|---|
| Configurazione congelata | ok: il `CellPlanner` dà a ogni cella con metodo e `split=test` l'hash di `freeze/<metodo>.json` per (modello, linguaggio), senza guardare il livello |
| Soggetti di `detect` | ok: positivi, `neg_human` (nativi di L3, filtrati su `split=test`), `neg_llm`, `neg_llm_twin` per MCGMark; niente `neg_integration` (solo L2 dev) |
| Contesto vuoto D3 | ok: `_input` usa il prompt solo se il `problem_key` è fra i problemi del livello; i negativi di CodeSearchNet e The Stack che non sono prompt hanno contesto vuoto, i 500 prompt per linguaggio (e le 94 classi) il loro prompt |
| Soglie congelate | ok: le metriche leggono le soglie per (metodo, modello, linguaggio, livelli `[L1, L2]`), con il controllo dello sha256 congelato (ADR-013); nessuna ricalibrazione |
| Da adattare | metriche di L3 (Pass@1 solo ClassEval, validità per CodeSearchNet e The Stack, per dataset); ciclo di correttezza di PromptMark su L3 (la cella Python mescola ClassEval e CodeSearchNet, e l'adattatore rifiuta celle miste) |
| Scoperto | la fase 0 sul test è stata fatta solo su **L1**: nessun metodo ha campioni marcati di **L2 test**, che servono alla curva T2.4 in k su CodeNet e alle tabelle di L2 della M14 |

Decisioni dell'utente già date (30 ottobre 2026): validità del codice (tree-sitter principale,
`py_compile` e `node --check` controlli aggiuntivi, `javac` e `g++` solo informazione; ADR-016);
`main` e `__init__` contano come le altre funzioni nella curva in k (ADR-014 §2.3).

## Lista delle attività

Stato: `[ ]` da fare, `[~]` in corso, `[x]` fatto (con il commit).

### Fase 0
- [x] Verifiche delle fasi su L3
- [ ] Risposte dell'utente alle domande della fase 0

### Fase 1: M13
- [ ] Metriche di L3 (Pass@1 e ΔPass@1 su ClassEval, 91 classi; validità per CodeSearchNet e The Stack; TPR e AUROC per dataset con il flag I6; preservazione condizionata di ACW solo su ClassEval)
- [ ] Ciclo di correttezza di PromptMark su L3 (secondo la risposta)
- [ ] Lancio dei metodi su L3 (watermark, gemella di MCGMark, detect, execute con l'immagine L3, metrics), stato e report, stima dei costi
- [ ] Fase 0 su L2 test (secondo la risposta)
- [ ] T2.4 in k (attacco, griglia, lancio, curva)
- [ ] Tests4Py: preparazione dei 5 progetti con la misura dei criteri di fattibilità

### Fase 1: SWEET
- [ ] Pilota dei tempi
- [ ] Catena: estensione D31 → scelta su L1+L2 → congelamento → fase 0 sul test → T1–T4 (con D34) → M12 → L3

### Fase 1: M14
- [ ] Comando unico del report: tabelle CSV e LaTeX, figure (ROC, TPR contro intensità, curva in k), efficienza (patch 0003 di MCGMark e speed-up), esclusioni I6, annotazioni delle deviazioni

### Fase 2
- [ ] Smoke test in una cartella separata, comando di lancio e di lettura dell'esito

### Fase 3
- [ ] Stima complessiva (ore GPU e CPU per metodo e livello, giorni reali)
- [ ] Script principale con dipendenze SLURM, riprendibile, divisione fra i due utenti
- [ ] Comando di stato per catena
