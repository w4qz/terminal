"""
Fondamentaux de tout le catalogue, par roulement.

Lancé par GitHub Actions toutes les 30 minutes, après build_universe.py.
À chaque passage, actualise les BATCH titres dont les fondamentaux sont les plus anciens
(ou absents), puis écrit fundamentals.json. Avec ~900 actions et 120 titres par passage,
tout le catalogue est rafraîchi en 4 heures environ, sans surcharger Yahoo.

Les titres de FUND_TICKERS (update_data.py) restent actualisés à chaque passage, en priorité.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yfinance as yf
from update_data import FUND_FIELDS, FUND_TICKERS, PCT_FIELDS, RATING_FR
from peers import PEER_GROUPS, all_peer_tickers

ROOT = Path(__file__).resolve().parent.parent
UNIV = ROOT / "universe.json"
OUT = ROOT / "fundamentals.json"
PEERS_OUT = ROOT / "peers.json"
NOW = datetime.now(timezone.utc)
BATCH = 120          # titres actualisés par passage
PAUSE = 0.4          # secondes entre deux requêtes Yahoo
MAX_ERRORS = 8       # erreurs d'affilée avant d'arrêter (Yahoo limite le débit)


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def extract(info: dict) -> dict:
    f = {}
    for k, yk in FUND_FIELDS.items():
        v = info.get(yk)
        if v is None or v == "" or (isinstance(v, float) and v != v):
            continue
        if k in PCT_FIELDS and isinstance(v, (int, float)):
            v = v * 100 if (k != "divYield" or v < 0.2) else v
        if k == "rating":
            v = RATING_FR.get(v, v)
        if v is not None:
            f[k] = round(v, 4) if isinstance(v, float) else v
    return f


def main() -> int:
    try:
        stocks = [x["s"] for x in json.loads(UNIV.read_text(encoding="utf-8"))["items"] if x.get("c") == "Action"]
    except Exception as e:
        log(f"universe.json illisible ({e}) : rien à faire")
        return 0
    # Les groupes de pairs de la fonction RV rejoignent le roulement, même hors des indices du catalogue.
    stocks += [s for s in all_peer_tickers() if s not in stocks]
    PEERS_OUT.write_text(json.dumps({"groups": PEER_GROUPS}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        cache = json.loads(OUT.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        cache = {}

    # Les plus anciens d'abord ; les titres jamais lus passent en tête.
    todo = sorted((s for s in stocks if s not in FUND_TICKERS),
                  key=lambda s: cache.get(s, {}).get("ts", ""))[:BATCH]
    ok, errors = 0, 0
    for s in todo:
        try:
            f = extract(yf.Ticker(s).info or {})
            if len(f) > 3:
                f["ts"] = NOW.isoformat().replace("+00:00", "Z")
                cache[s] = f
                ok += 1
            errors = 0
        except Exception as e:
            errors += 1
            log(f"  ! {s} : {str(e)[:80]}")
            if errors >= MAX_ERRORS:
                log("Trop d'erreurs d'affilée : Yahoo limite probablement le débit, reprise au prochain passage")
                break
        time.sleep(PAUSE)

    cache = {s: f for s, f in cache.items() if s in stocks}      # retire les titres sortis des indices
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "Yahoo Finance via yfinance, par roulement",
                               "items": cache}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Fondamentaux : {ok}/{len(todo)} actualisés ce passage, {len(cache)}/{len(stocks)} titres couverts "
        f"({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
