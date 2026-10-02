# ADR-002 — Submodule fissati e copie patchate dei metodi

- **Stato:** accettata (Milestone 0, 1° ottobre 2026; aggiornata il 2 ottobre 2026: 5 metodi, 8 submodule)
- **Contesto:** SPEC §4, §8.1, §9.0; `docs/cluster_info.md` §1, §9, §9.1.
- ADR-001 è riservato alla scelta su sandbox e rete (Milestone 4).

## Contesto

SPEC prevede che i worker eseguano il codice originale da `third_party/<repo>` e che ogni modifica
sia una patch in `patches/<metodo>/`. Sul cluster esistono già modifiche preesistenti ai repository
dei metodi (patch `0000`), che vanno applicate senza mai alterare i submodule.

## Decisione

1. **Submodule** in `third_party/`, fissati ai commit seguenti e verificati da `scripts/setup_submodules.sh`:

   | Percorso | URL | Commit | Fonte del commit |
   |---|---|---|---|
   | `third_party/sweet-watermark` | https://github.com/hongcheki/sweet-watermark | `853b47eb064c180beebd383302d09491fc98a565` | cluster_info §9 |
   | `third_party/ACW` | https://github.com/Noelle1831-k/ACW | `2236dc304478a31fe7cc7cc393527375024428db` | cluster_info §9 |
   | `third_party/STONE-watermarking` | https://github.com/inistory/STONE-watermarking | `bb5d809c0c494a219411e861f2313cca2b9fd7b4` | cluster_info §9 |
   | `third_party/PromptMark` | https://github.com/ahmedfahad04/promptmark | `c04c8f61db1f0ec7ba2f213f4aa1a15787af484e` | cluster_info §9 |
   | `third_party/MCGMT` | https://github.com/KevinHeiwa/MCGMT | `eefa27b68747f5027c3121abcc506f3488eae990` | cluster_info §9 |
   | `third_party/bigcode-evaluation-harness` | https://github.com/bigcode-project/bigcode-evaluation-harness | `8fc5bae6479c4fbbb28c3f8b644f6a15b3f3b5bd` | HEAD del ramo predefinito al 1° ottobre 2026 (scelta dell'utente) |
   | `third_party/ClassEval` | https://github.com/FudanSELab/ClassEval | `eaeac44d0d5dcd8a95feec50726d66fedc73a98f` | HEAD del ramo predefinito al 1° ottobre 2026 (scelta dell'utente) |
   | `third_party/lm-watermarking` | https://github.com/jwkirchenbauer/lm-watermarking | `82922516930c02f8aa322765defdb5863d07a00e` | cluster_info §9.1 (solo tracciabilità dei file homoglyph di MCGMark) |

2. **Copie patchate.** `scripts/apply_patches.sh` esporta ogni submodule (`git checkout-index`) in
   `build/patched/<metodo>/` (non versionata) e vi applica in ordine `patches/<metodo>/NNNN-*.patch`
   con `git apply`. Il `PYTHONPATH` dei worker punterà a `build/patched/<metodo>/` (M5).
   Accanto a ogni copia: `<metodo>.source_commit` e `<metodo>.patches.sha256`, da riportare nei manifest.

3. **Nomi dei metodi** nelle cartelle `patches/` e `build/patched/`: `sweet`, `acw`, `stone`,
   `promptmark`, `mcgmark` (come SPEC), uguali ai nomi degli ambienti di cluster_info §1.

4. **Patch delle dipendenze** (`patches/_deps/`): solo documentazione, non applicate.

5. **Fine riga.** `.gitattributes` marca `patches/**` come `-text`: le patch sono conservate byte per
   byte (la patch di ACW ha fine riga misti). I submodule si clonano senza conversione dei fine riga.

## Conseguenze

- I submodule restano intatti; ogni modifica al codice dei metodi è una nuova patch numerata.
- Una patch che non si applica fa fallire `apply_patches.sh` e la copia incompleta viene rimossa.
- Aggiornare un commit fissato richiede di aggiornare insieme gitlink, tabella di
  `setup_submodules.sh` e questo ADR, e invalida i `config_hash` (che includono il commit del submodule).
- Su Windows `third_party/lm-watermarking` richiede `core.longpaths=true` (percorsi lunghi); sul cluster non serve.
