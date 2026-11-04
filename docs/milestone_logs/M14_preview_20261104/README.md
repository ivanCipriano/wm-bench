# Report (M14)

Rigenerato dai soli artefatti con `python scripts/report.py`.

## File

- `tables/metrics_long.csv`
- `tables/main_L1`
- `tables/main_L2`
- `tables/main_L3`
- `tables/attacks_L1`
- `figures/tpr_vs_intensity_T1.1`
- `figures/tpr_vs_intensity_T1.2`
- `figures/tpr_vs_intensity_T1.3`
- `figures/tpr_vs_intensity_T1.4`
- `figures/tpr_vs_intensity_T1.5`
- `figures/tpr_vs_intensity_T2.1`
- `figures/tpr_vs_intensity_T2.2`
- `figures/tpr_vs_intensity_T2.4`
- `figures/k_curve_L2`
- `figures/k_curve_L3`
- `figures/tpr_vs_intensity_T3.1`
- `figures/tpr_vs_intensity_T4.1`
- `figures/tpr_vs_intensity_T4.2`
- `figures/roc_sweet_qwen25_coder_7b_L1_python`
- `figures/roc_sweet_qwen25_coder_7b_L1_java`
- `figures/roc_sweet_qwen25_coder_7b_L1_cpp`
- `figures/roc_sweet_qwen25_coder_7b_L1_javascript`
- `figures/roc_sweet_deepseek_coder_6p7b_L1_python`
- `figures/roc_sweet_deepseek_coder_6p7b_L1_java`
- `figures/roc_sweet_deepseek_coder_6p7b_L1_cpp`
- `figures/roc_sweet_deepseek_coder_6p7b_L1_javascript`
- `figures/roc_acw_qwen25_coder_7b_L1_python`
- `figures/roc_acw_deepseek_coder_6p7b_L1_python`
- `figures/roc_stone_qwen25_coder_7b_L1_python`
- `figures/roc_stone_qwen25_coder_7b_L2_python`
- `figures/roc_stone_qwen25_coder_7b_L3_python`
- `figures/roc_stone_qwen25_coder_7b_L1_java`
- `figures/roc_stone_qwen25_coder_7b_L2_java`
- `figures/roc_stone_qwen25_coder_7b_L3_java`
- `figures/roc_stone_qwen25_coder_7b_L1_cpp`
- `figures/roc_stone_qwen25_coder_7b_L2_cpp`
- `figures/roc_stone_qwen25_coder_7b_L3_cpp`
- `figures/roc_stone_deepseek_coder_6p7b_L1_python`
- `figures/roc_stone_deepseek_coder_6p7b_L2_python`
- `figures/roc_stone_deepseek_coder_6p7b_L3_python`
- `figures/roc_stone_deepseek_coder_6p7b_L1_java`
- `figures/roc_stone_deepseek_coder_6p7b_L2_java`
- `figures/roc_stone_deepseek_coder_6p7b_L3_java`
- `figures/roc_stone_deepseek_coder_6p7b_L1_cpp`
- `figures/roc_stone_deepseek_coder_6p7b_L2_cpp`
- `figures/roc_stone_deepseek_coder_6p7b_L3_cpp`
- `figures/roc_promptmark_qwen25_coder_7b_L1_python`
- `figures/roc_promptmark_qwen25_coder_7b_L2_python`
- `figures/roc_promptmark_qwen25_coder_7b_L3_python`
- `figures/roc_promptmark_deepseek_coder_6p7b_L1_python`
- `figures/roc_promptmark_deepseek_coder_6p7b_L2_python`
- `figures/roc_promptmark_deepseek_coder_6p7b_L3_python`
- `figures/roc_mcgmark_qwen25_coder_7b_L1_python`
- `figures/roc_mcgmark_qwen25_coder_7b_L2_python`
- `figures/roc_mcgmark_deepseek_coder_6p7b_L1_python`
- `figures/roc_mcgmark_deepseek_coder_6p7b_L2_python`
- `tables/efficiency`

## Esclusioni I6 (contaminazione)

- L3, CodeSearchNet e The Stack C++: niente conclusioni sulla correttezza funzionale (nessun Pass@1); si riportano rilevabilità e validità del codice (tree-sitter, py_compile/node --check; javac e g++ solo informazione).

- righe di metriche con `contamination_risk`: 24

## Deviazioni annotate

- **D9**: accoppiamento baseline/marcato approssimato (ΔPass@1)
- **D16**: punteggio principale di STONE z_nonsyntax
- **D20**: riferimento di fpr_llm e ΔPass@1: baseline gemella
- **D23**: ciclo di correttezza sui soli esempi del prompt
- **D27**: ciclo di correttezza spento
- **D31**: estensione locale della griglia oltre δ = 4
- **D33**: prompt di attacco v2 approvati
- **D34**: controllo negativo sulla baseline riscritta
- **D38**: misure al netto della formattazione (impercettibilità)
- **D39**: negativi di CodeSearchNet senza le funzioni già usate in L1
- **D40**: ClassEval con 2.048 token nuovi
- **D41**: ClassEval 17, 48, 58 fuori da Pass@1 (91 classi)

## Efficienza

- MCGMark: tempi con la patch 0003 (solo prestazioni); speed-up della 0003 sull'oracle 3,11x (568,8 s -> 182,7 s su 9 generazioni, A100 80 GB; audit di MCGMark, Tempi).
