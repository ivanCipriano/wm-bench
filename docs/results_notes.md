# Registro dei risultati da discutere nella tesi

Fenomeni emersi dai risultati che vanno spiegati nella tesi, con i numeri e il log da cui vengono.
Non sono deviazioni dal protocollo (quelle stanno in `docs/deviations.md`); le voci della tesi già
fissate come TODO restano in `docs/cluster_info.md` §11 (es. TODO 29, intensità di T1.2 per
linguaggio). Registro aperto il 26 ottobre 2026 su richiesta dell'utente.

| # | Fenomeno | Numeri | Fonte | Da discutere |
|---|---|---|---|---|
| R1 | **MCGMark: l'attacco T2.2 "ripara" il codice marcato.** Dopo la riscrittura con Llama-3.1-8B-Instruct il Pass@1 **sale** rispetto al marcato non attaccato, in entrambe le varianti, perché il codice di MCGMark (con gli outlier, patch 0004) ha già perso molta correttezza e l'attaccante la recupera | Pass@1 del marcato 0,374 (Qwen) e 0,338 (DeepSeek), contro 0,70–0,76 della baseline (ΔPass@1 ≈ 36–39 pp, M10). ΔPass@1 rispetto al marcato (marcato − attaccato): (a) conservative −13,6 pp (Qwen) e −10,1 pp (DeepSeek), (b) full_rewrite −9,5 e −7,7 pp. In (a) MCGMark ha il 60–64% di campioni modificati, contro l'11–17% di STONE e PromptMark in Python (ACW 41–53%) | `docs/milestone_logs/M11_attacks_t2_2_mcgmark.txt`, `M10_attacks_mcgmark_outliers.txt` | per MCGMark un attaccante con LLM toglie il watermark (TPR da 0,08/0,03 a 0,06/0,01 in (a), già vicino all'FPR) **e** migliora il codice: l'attacco non ha un costo di correttezza, anzi conviene. Va letto insieme al costo di correttezza dell'inserimento di MCGMark |
