"""Valore effettivo di ``clean_up_tokenization_spaces`` di un tokenizer, nell'ambiente corrente.

Uso (con l'interprete dell'ambiente da provare; vedi scripts/check_tokenizer_cleanup.sh):
    python scripts/check_tokenizer_cleanup.py ENV MODEL_ID TOKENIZER_PATH

Richiesta dell'utente del 24 ottobre 2026: il codice marcato è stato generato negli ambienti dei
metodi, con versioni di transformers diverse da quella di bench-core, e il default della pulizia
degli spazi potrebbe essere diverso anche se ``tokenizer_config.json`` dice ``false``. Stampa una
riga per (ambiente, modello): versione di transformers, classe del tokenizer, valore dell'attributo
dopo il caricamento, e il testo di prova ricostruito con ``decode`` come lo chiamano la baseline
e gli shim (solo ``skip_special_tokens=True``).
Compatibile con Python 3.9, senza dipendenze di bench.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROBE = 'print("Yes !") ; a != b , c .. x ? y : z ; it \'s'


def main() -> int:
    env, model_id, path = sys.argv[1], sys.argv[2], sys.argv[3]
    row: dict[str, Any] = {"env": env, "model": model_id, "python": sys.version.split()[0]}
    try:
        import transformers
        from transformers import AutoTokenizer
    except ImportError:
        row["transformers"] = None
        print(json.dumps(row), flush=True)
        return 0
    row["transformers"] = transformers.__version__
    tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
    ids = tok(PROBE, add_special_tokens=False)["input_ids"]
    decoded = tok.decode(ids, skip_special_tokens=True)
    batch = tok.batch_decode([ids], skip_special_tokens=True)[0]
    row.update(
        {
            "tokenizer": type(tok).__name__,
            "clean_up_tokenization_spaces": getattr(tok, "clean_up_tokenization_spaces", "n/a"),
            "init_kwarg": tok.init_kwargs.get("clean_up_tokenization_spaces", "absent"),
            "decode_equal": decoded == PROBE,
            "batch_decode_equal": batch == PROBE,
            "decoded": decoded,
        }
    )
    print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
