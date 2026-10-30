# Riepilogo della Milestone 11 — Attacchi T2–T4 (SPEC §0, §22)

Data: 30 ottobre 2026. Stato: **chiusa per STONE, MCGMark, ACW e PromptMark** su L1 test. **SWEET è
sospeso** (non congelato): i suoi attacchi restano da fare quando riprende. La M12 e la M13 sono
partite prima, su richiesta dell'utente.

Decisioni e dettagli: ADR-012 (T1, M10), ADR-014 (T2–T4), D29–D36 in `docs/deviations.md`.

## Criteri di accettazione (SPEC §22)

| Criterio | Esito |
|---|---|
| Ogni attacco produce celle complete | fatto per i quattro metodi congelati: T2.1, T2.2, T2.4, T3.1, T4.1 Python, con `attack`, `execute`, `detect` e `metrics` `COMPLETE` in ogni cella (`M11_attacks_*`) |
| T3.1 riporta le 4 categorie (k1/k2/entrambi/nessuno) | fatto, con soglia di k2 calibrata come k1, FPR di test con k2 e, per ACW, le categorie per numero di regole condivise (ADR-014 §3) |
| T2.3 dietro flag | prompt in bozza, spento (D33) |

## Cosa è stato fatto

- **T2.1** refactoring a regole (ADR-014 §1): catalogo per linguaggio, regole Ruff equivalenti a
  quelle di ACW in Python, catalogo C++ versione 2 dopo il primo lancio (§1.6); verifiche sulle
  canoniche e sulla baseline di L1 dev prima del lancio.
- **T2.2** riscrittura con Llama-3.1-8B-Instruct, varianti conservativa e completa (prompt v2
  approvati, D33), pilota C++ (`M11_t2_2_cpp_pilot.txt`) e **controllo negativo** sulla baseline
  (D34, `M11_t2_2_control_*`).
- **T2.4** troncamento al 75% e al 50% delle istruzioni; la variante in k funzioni è rinviata alla
  M13 (TODO 32).
- **T3.1** ri-marcatura con k2 per i quattro metodi (ADR-014 §3.5–3.6), con la stima dei costi
  prima del lancio (`M11_t3_1_cost.txt`). Esiti principali:
  - STONE: la ri-marcatura rompe l'interfaccia (funzione d'ingresso rinominata o persa nel
    4,0–43,7% dei campioni riscritti); controllo senza watermark: circa metà delle rinomine si deve
    al watermark (McNemar p < 0,001);
  - MCGMark: k1 quasi del tutto rimosso, k2 poco rilevabile (rilevazione fallita sull'89–94%);
  - ACW: ri-marcatura inefficace (k1 sul 99%), per le regole condivise fra k1 e k2;
  - PromptMark: non informativo, perché già senza attacco il TPR è del 2,5–4,6% (TODO 19).
- **T4.1** minificazione in Python (python-minifier) per i quattro metodi; verifiche sulle canoniche
  (`M11_canonical_t4.txt`); T4.1 JavaScript e T4.2 pronti e verificati sulle canoniche
  (`M11_canonical_t4_js.txt`, `M11_t4_2_npm.txt`) ma solo per SWEET.

## Cosa resta aperto

- **SWEET** (promemoria TODO 31): estensione D31 della griglia, congelamento, T2.2 con il controllo
  D34, T4.1 JavaScript, T4.2, T3.1 (dopo la stima dei costi) e la M12. Va pianificato insieme a
  SWEET sul Livello 3 (stima in `M13_l3_cost_2.txt`).
- **T2.4 in k** (TODO 32): curva di troncamento su ClassEval e sui programmi CodeNet con almeno due
  funzioni, nella M13; prima del lancio la stima dei costi e la decisione su `main` e `__init__`.
- Frasi per la tesi già decise: interfaccia rotta da T3.1 con STONE (ADR-014 §3.5), intensità di
  T1.2 per linguaggio (TODO 29).

## `TODO(user)` che richiedono una decisione

Nessuno nuovo per la M11. Restano i punti di SWEET sopra, da pianificare alla ripresa.
