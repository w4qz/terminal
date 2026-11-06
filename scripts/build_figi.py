"""
Codes Bloomberg des titres du catalogue, via OpenFIGI (service gratuit de Bloomberg L.P.).

Pour chaque titre : identifiant FIGI et code Bloomberg complet (« MC FP Equity »). Sans clé, OpenFIGI
accepte 25 requêtes par minute de 10 titres chacune : le script en lit 250 par passage, les plus
anciens d'abord, puis ne relit un titre que tous les 30 jours. Clé facultative : secret OPENFIGI_KEY.

Écrit figi.json : { items: { "MC.PA": { figi, bbg: "MC FP Equity", name, type } } }
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_data import FUND_TICKERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UNIV = ROOT / "universe.json"
OUT = ROOT / "figi.json"
NOW = datetime.now(timezone.utc)
KEY = os.getenv("OPENFIGI_KEY", "").strip()
PER_REQ = 100 if KEY else 10
PAUSE = 0.3 if KEY else 2.6
MAX_REQ = 25
STALE = timedelta(days=30)

# Suffixe Yahoo → code de place Bloomberg
EXCH = {"": "US", "PA": "FP", "L": "LN", "DE": "GY", "F": "GF", "MI": "IM", "AS": "NA", "MC": "SM", "SW": "SE", "BR": "BB",
        "LS": "PL", "ST": "SS", "CO": "DC", "OL": "NO", "HE": "FH", "IR": "ID", "VI": "AV", "T": "JT", "HK": "HK",
        "SS": "CH", "SZ": "CH", "KS": "KS", "TW": "TT", "AX": "AU", "TO": "CN", "SA": "BZ", "NS": "IN", "BO": "IN", "SI": "SP"}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def job(sym):
    base, _, suf = sym.partition(".")
    ex = EXCH.get(suf)
    if ex is None or "=" in sym or sym.startswith("^"):
        return None
    tk = base.replace("-", "/")              # BRK-B → BRK/B
    if suf == "HK":
        tk = tk.lstrip("0") or tk
    return {"idType": "TICKER", "idValue": tk, "exchCode": ex, "marketSecDes": "Equity"}


def main() -> int:
    try:
        cat = [x["s"] for x in json.loads(UNIV.read_text(encoding="utf-8"))["items"] if x.get("c") in ("Action", "ETF")]
    except Exception:
        cat = []
    pool = list(dict.fromkeys([*FUND_TICKERS, *cat]))
    try:
        cache = json.loads(OUT.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        cache = {}
    limit = (NOW - STALE).isoformat()
    todo = [s for s in sorted(pool, key=lambda s: cache.get(s, {}).get("ts", "")) if cache.get(s, {}).get("ts", "") < limit and job(s)]
    todo = todo[:PER_REQ * MAX_REQ]
    hdr = {"Content-Type": "application/json", **({"X-OPENFIGI-APIKEY": KEY} if KEY else {})}
    ok = 0
    for i in range(0, len(todo), PER_REQ):
        chunk = todo[i:i + PER_REQ]
        try:
            time.sleep(PAUSE)
            r = requests.post("https://api.openfigi.com/v3/mapping", json=[job(s) for s in chunk], headers=hdr, timeout=30)
            if r.status_code == 429:
                log("OpenFIGI : limite de débit atteinte, suite au prochain passage")
                break
            r.raise_for_status()
            for s, res in zip(chunk, r.json()):
                ts = NOW.isoformat().replace("+00:00", "Z")
                rows = res.get("data") or []
                if not rows:
                    cache[s] = {"ts": ts, "none": True}
                    continue
                d = next((x for x in rows if x.get("securityType") in ("Common Stock", "ETP", "Depositary Receipt")), rows[0])
                cache[s] = {"ts": ts, "figi": d.get("figi"), "cfigi": d.get("compositeFIGI"),
                            "bbg": f"{d.get('ticker')} {d.get('exchCode')} {d.get('marketSector') or 'Equity'}",
                            "name": d.get("name"), "type": d.get("securityType")}
                ok += 1
        except Exception as e:
            log(f"  ! OpenFIGI : {str(e)[:100]}")
            break
    cache = {k: v for k, v in cache.items() if k in pool}
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "OpenFIGI (Bloomberg L.P.)", "items": cache},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"OpenFIGI : {ok} titres identifiés ce passage, {sum(1 for v in cache.values() if v.get('figi'))}/{len(pool)} au total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
