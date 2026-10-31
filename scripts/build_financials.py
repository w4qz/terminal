"""
FA — états financiers annuels (4 derniers exercices) et historique des dividendes, par roulement.

Lancé par GitHub Actions toutes les 30 minutes. À chaque passage, lit BATCH titres :
d'abord les titres prioritaires (FUND_TICKERS) dont les comptes ont plus de 24 h,
puis le reste du catalogue et des groupes de pairs, les plus anciens d'abord.
Les comptes changent au plus une fois par trimestre : un cycle complet d'environ
12 heures suffit largement.

Écrit financials.json : { items: { "AAPL": { ts, ccy, years: [...], is: {...}, bs: {...}, cf: {...} } } }
"""
from __future__ import annotations

import json
import math
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yfinance as yf
from peers import all_peer_tickers
from update_data import FUND_TICKERS

ROOT = Path(__file__).resolve().parent.parent
UNIV = ROOT / "universe.json"
OUT = ROOT / "financials.json"
NOW = datetime.now(timezone.utc)
BATCH = 40
PAUSE = 0.5
MAX_ERRORS = 6
PRIORITY_AGE = timedelta(hours=24)

# Lignes retenues : clé du terminal ← libellés Yahoo possibles (le premier trouvé gagne)
IS_ROWS = {
    "rev": ["Total Revenue", "Operating Revenue"],
    "gross": ["Gross Profit"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["Operating Income", "EBIT"],
    "net": ["Net Income Common Stockholders", "Net Income"],
    "eps": ["Diluted EPS", "Basic EPS"],
}
BS_ROWS = {
    "assets": ["Total Assets"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "debt": ["Total Debt"],
    "netdebt": ["Net Debt"],
}
CF_ROWS = {
    "cfo": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "fcf": ["Free Cash Flow"],
    "div": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "buyback": ["Repurchase Of Capital Stock", "Common Stock Payments"],
}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def _table(df, rows):
    """DataFrame Yahoo (lignes = postes, colonnes = exercices) → {clé: [valeurs du plus ancien au plus récent]}."""
    if df is None or getattr(df, "empty", True):
        return None, {}
    cols = sorted(df.columns)[-4:]
    years = [str(c)[:10] for c in cols]
    out = {}
    for k, labels in rows.items():
        for lab in labels:
            if lab in df.index:
                vals = []
                for c in cols:
                    v = df.at[lab, c]
                    try:
                        v = float(v)
                        vals.append(None if math.isnan(v) else round(v, 4))
                    except Exception:
                        vals.append(None)
                if any(v is not None for v in vals):
                    out[k] = vals
                    break
    return years, out


def read(sym: str) -> dict | None:
    t = yf.Ticker(sym)
    y1, is_ = _table(t.income_stmt, IS_ROWS)
    y2, bs = _table(t.balance_sheet, BS_ROWS)
    y3, cf = _table(t.cashflow, CF_ROWS)
    years = y1 or y2 or y3
    if not years or not is_:
        return None
    # Aligne bilan et flux sur les exercices du compte de résultat
    def align(ys, d):
        if not ys or ys == years:
            return d
        return {k: [v[ys.index(y)] if y in ys else None for y in years] for k, v in d.items()}
    rec = {"ts": NOW.isoformat().replace("+00:00", "Z"), "years": years,
           "is": is_, "bs": align(y2, bs), "cf": align(y3, cf)}
    try:
        rec["ccy"] = (t.fast_info.get("currency") if hasattr(t.fast_info, "get") else None) or None
    except Exception:
        pass
    return rec


def main() -> int:
    try:
        stocks = [x["s"] for x in json.loads(UNIV.read_text(encoding="utf-8"))["items"] if x.get("c") == "Action"]
    except Exception:
        stocks = []
    pool = list(dict.fromkeys([*FUND_TICKERS, *stocks, *all_peer_tickers()]))
    try:
        cache = json.loads(OUT.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        cache = {}

    ts = lambda s: cache.get(s, {}).get("ts", "")
    stale = (NOW - PRIORITY_AGE).isoformat()
    prio = [s for s in FUND_TICKERS if ts(s) < stale]
    rest = sorted((s for s in pool if s not in prio), key=ts)
    todo = (prio + rest)[:BATCH]

    ok, errors = 0, 0
    for s in todo:
        try:
            rec = read(s)
            if rec:
                cache[s] = rec
                ok += 1
            else:
                cache.setdefault(s, {})["ts"] = NOW.isoformat().replace("+00:00", "Z")   # rien à lire : on passe au suivant
            errors = 0
        except Exception as e:
            errors += 1
            log(f"  ! {s} : {str(e)[:80]}")
            if errors >= MAX_ERRORS:
                log("Trop d'erreurs d'affilée : reprise au prochain passage")
                break
        time.sleep(PAUSE)

    cache = {s: v for s, v in cache.items() if s in pool}
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "Yahoo Finance via yfinance (états annuels), par roulement",
                               "items": cache}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    have = sum(1 for v in cache.values() if v.get("years"))
    log(f"États financiers : {ok}/{len(todo)} lus ce passage, {have}/{len(pool)} titres couverts ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
