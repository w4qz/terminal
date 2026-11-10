"""
Finnhub (formule gratuite, clé dans le secret FINNHUB_API_KEY) — écrit finnhub.json :

  earnings   calendrier des résultats : 7 jours passés et 30 jours à venir, avec le consensus du bénéfice
             par action (BPA) et du chiffre d'affaires, puis le chiffre publié et la surprise
  news       actualités par société (7 derniers jours), titres et liens
  surprises  historique des 4 derniers trimestres : BPA publié contre consensus

La formule gratuite couvre surtout les sociétés américaines et accepte 60 requêtes par minute :
le calendrier est relu à chaque passage, les actualités et surprises par roulement (100 sociétés par passage).
La clé n'est jamais publiée : elle reste dans les secrets GitHub, seul le résultat est écrit.
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
FUND = ROOT / "fundamentals.json"
OUT = ROOT / "finnhub.json"
NOW = datetime.now(timezone.utc)
KEY = os.getenv("FINNHUB_API_KEY", "").strip()
BATCH = 100
BUDGET = 150          # secondes
API = "https://finnhub.io/api/v1"


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def get(path, **params):
    for attempt in range(3):
        time.sleep(1.05)                     # 60 requêtes / minute maximum
        r = requests.get(f"{API}{path}", params={**params, "token": KEY}, timeout=25)
        if r.status_code == 429:
            time.sleep(10); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("limite de débit Finnhub")


def main() -> int:
    if not KEY:
        log("Secret FINNHUB_API_KEY absent : étape ignorée.")
        return 0
    t0 = time.monotonic()
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    try:
        univ = [x["s"] for x in json.loads(UNIV.read_text(encoding="utf-8"))["items"] if x.get("c") == "Action"]
    except Exception:
        univ = []
    try:
        fund = json.loads(FUND.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        fund = {}
    us = [s for s in dict.fromkeys([*FUND_TICKERS, *univ]) if "." not in s and "=" not in s and "^" not in s
          and s not in ("ACWI", "AGG", "BIL", "GLD", "SPY", "QQQ", "IWM", "TLT")]   # fonds : pas de résultats de société
    us_set = set(us)

    # 1. Calendrier des résultats (tout le marché américain, filtré sur le catalogue)
    earnings = prev.get("earnings", [])
    try:
        d = get("/calendar/earnings", **{"from": (NOW - timedelta(days=7)).date().isoformat(),
                                        "to": (NOW + timedelta(days=30)).date().isoformat()})
        earnings = []
        for e in d.get("earningsCalendar") or []:
            s = (e.get("symbol") or "").replace(".", "-")
            if s not in us_set:
                continue
            est, act = e.get("epsEstimate"), e.get("epsActual")
            earnings.append({"s": s, "d": e.get("date"), "h": e.get("hour") or "", "q": f"Q{e.get('quarter')} {e.get('year')}",
                             "epsE": est, "epsA": act, "revE": e.get("revenueEstimate"), "revA": e.get("revenueActual"),
                             "surp": round((act - est) / abs(est) * 100, 1) if act is not None and est not in (None, 0) else None})
        earnings.sort(key=lambda x: (x["d"] or "", x["s"]))
        log(f"Calendrier des résultats : {len(earnings)} publications du catalogue")
    except Exception as ex:
        log(f"  ! calendrier Finnhub : {str(ex)[:100]}")

    # 2. Actualités et surprises, par roulement (les plus grosses capitalisations et la watchlist d'abord)
    cache = prev.get("co", {})
    prio = list(dict.fromkeys([*FUND_TICKERS, *sorted(us, key=lambda s: -(fund.get(s, {}).get("mcap") or 0))]))
    todo = sorted(prio[:400], key=lambda s: cache.get(s, {}).get("ts", ""))[:BATCH]
    ok = 0
    for s in todo:
        if time.monotonic() - t0 > BUDGET:
            log("Budget de temps atteint : suite au prochain passage")
            break
        try:
            news = get("/company-news", symbol=s, **{"from": (NOW - timedelta(days=7)).date().isoformat(), "to": NOW.date().isoformat()})
            try:
                sur = get("/stock/earnings", symbol=s, limit=4)
            except Exception as ex2:
                sur = []
                if "403" not in str(ex2):
                    log(f"  ! surprises {s} : {str(ex2)[:60]}")
            cache[s] = {"ts": NOW.isoformat().replace("+00:00", "Z"),
                        "news": [{"h": n.get("headline", "")[:220], "src": n.get("source"), "url": n.get("url"),
                                  "ts": datetime.fromtimestamp(n["datetime"], timezone.utc).isoformat().replace("+00:00", "Z")}
                                 for n in (news or [])[:10] if n.get("headline") and n.get("url") and n.get("datetime")],
                        "sur": [{"p": x.get("period"), "q": f"Q{x.get('quarter')} {x.get('year')}", "a": x.get("actual"),
                                 "e": x.get("estimate"), "sp": x.get("surprisePercent")} for x in (sur or [])[:4]]}
            ok += 1
        except Exception as ex:
            msg = str(ex)
            if "401" in msg:
                log("Clé Finnhub refusée (401) : vérifiez le secret FINNHUB_API_KEY.")
                break
            # 403 = titre non couvert par la formule gratuite (fonds, titre étranger…) : on le note et on passe au suivant
            cache[s] = {"ts": NOW.isoformat().replace("+00:00", "Z"), "none": "403" in msg}
            log(f"  · {s} : {msg[:70]}")
    cache = {k: v for k, v in cache.items() if k in us_set}
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "Finnhub (formule gratuite)", "earnings": earnings, "co": cache},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Finnhub : {ok} sociétés lues ce passage, {len(cache)} en cache ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
