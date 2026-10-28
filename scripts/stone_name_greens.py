"""Token verdi di STONE nei nomi delle funzioni (ambiente ``stone``; lanciato da
``scripts/t3_1_renames.py --greens``, non a mano). Compatibile con Python 3.9.

Per ogni cella costruisce l'istanza ``STONE`` con lo shim del framework (``StoneShim``, stessa
richiesta di rilevazione, chiavi k1 e k2, dispositivo della rilevazione) e prende la green list.
Con ``prefix_length = 0`` il seme è ``hash_key * 1`` a ogni passo (``stone.py:65-72``): la lista non
dipende dal contesto. Ogni nome si tokenizza con lo spazio che lo precede nel codice
(``" " + nome``, ``add_special_tokens=False`` come la rilevazione). Per nome e chiave: numero di
token, token verdi, se il primo è verde.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    ns = parser.parse_args()

    import torch
    from bench_contracts import WorkerRequest
    from bench_shims.stone.__main__ import StoneShim

    with open(ns.input, encoding="utf-8") as handle:
        cells: list[dict[str, Any]] = json.load(handle)
    out = []
    for cell in cells:
        green: dict[str, Any] = {}
        tokenizer = None
        for key_id, req in cell["requests"].items():
            shim = StoneShim()
            shim.setup(WorkerRequest.from_dict(req))
            stone = shim.stone(cell["language"])
            if int(stone.config.prefix_length) != 0:
                raise SystemExit("prefix_length != 0: the green list depends on the context")
            dummy = torch.zeros(1, dtype=torch.long, device=stone.config.device)
            green[key_id] = {int(i) for i in stone.utils.get_greenlist_ids(dummy).tolist()}
            tokenizer = shim.tokenizer
        pairs = []
        for pair in cell["pairs"]:
            row = dict(pair)
            for which in ("original", "new"):
                ids = tokenizer(" " + pair[which], add_special_tokens=False)["input_ids"]
                row[which + "_tokens"] = len(ids)
                for key_id, ids_green in green.items():
                    flags = [int(i in ids_green) for i in ids]
                    row[f"{which}_{key_id}_green"] = sum(flags)
                    row[f"{which}_{key_id}_first"] = flags[0] if flags else None
            pairs.append(row)
        out.append({k: v for k, v in cell.items() if k != "requests"} | {"pairs": pairs})
    with open(ns.output, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
