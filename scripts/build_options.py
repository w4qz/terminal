"""
OMON — chaînes d'options des titres américains prioritaires (Yahoo Finance via yfinance).

Yahoo ne sert les options qu'avec un jeton de session : le navigateur ne peut pas les lire
directement, d'où ce script. Pour ne pas surcharger Yahoo, il ne tourne qu'une fois par
heure et ne garde, pour chaque titre, que les 4 prochaines échéances et les 25 prix
d'exercice les plus proches du cours.

Écrit options.json : { items: { "AAPL": { ts, spot, exps: [ { d, calls: [...], puts: [...] } ] } } }
Chaque option : [prix d'exercice, dernier, bid, ask, volume, intérêt ouvert, volatilité implicite %]
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yfinance as yf
from update_data import FUND_TICKERS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "options.json"
NOW = datetime.now(timezone.utc)
FORCE = os.getenv("OPT_FORCE", "").lower() == "true"
REFRESH = timedelta(minutes=55)
N_EXP = 4
N_STRIKES = 25
# Titres américains cotés (pas de suffixe de place) + quelques ETF très optionnés
TICKERS = [s for s in FUND_TICKERS if "." not in s and s not in ("SPCX",)] + ["SPY", "QQQ", "IWM", "GLD", "TLT"]


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def num(v, d=4):
    try:
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else round(v, d)
    except Exception:
        return None


def chain(df, spot):
    if df is None or df.empty:
        return []
    df = df.assign(_dist=(df["strike"] - spot).abs()).nsmallest(N_STRIKES, "_dist").sort_values("strike")
    return [[num(r.strike, 2), num(r.lastPrice, 2), num(r.bid, 2), num(r.ask, 2),
             int(r.volume) if num(r.volume) else 0, int(r.openInterest) if num(r.openInterest) else 0,
             num(r.impliedVolatility * 100, 1) if num(r.impliedVolatility) else None] for r in df.itertuples()]


def main() -> int:
    if not FORCE and OUT.exists():
        try:
            prev = datetime.fromisoformat(json.loads(OUT.read_text(encoding="utf-8"))["generated_at"].replace("Z", "+00:00"))
            if NOW - prev < REFRESH:
                log("options.json a moins d'une heure : pas d'actualisation")
                return 0
        except Exception:
            pass
    items, fails = {}, 0
    for s in TICKERS:
        try:
            t = yf.Ticker(s)
            exps = list(t.options or [])[:N_EXP]
            if not exps:
                continue
            spot = num(t.fast_info["last_price"], 2)
            out = []
            for d in exps:
                oc = t.option_chain(d)
                out.append({"d": d, "calls": chain(oc.calls, spot), "puts": chain(oc.puts, spot)})
                time.sleep(0.25)
            items[s] = {"ts": NOW.isoformat().replace("+00:00", "Z"), "spot": spot, "exps": out}
            fails = 0
        except Exception as e:
            fails += 1
            log(f"  ! {s} : {str(e)[:80]}")
            if fails >= 5:
                log("Trop d'erreurs d'affilée : arrêt, reprise au prochain passage")
                break
    if not items:
        log("Aucune chaîne d'options lue : options.json inchangé")
        return 0
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "Yahoo Finance via yfinance, cours différés", "items": items},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Options : {len(items)}/{len(TICKERS)} titres ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
