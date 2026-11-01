"""
Catalogue de recherche du Terminal.

Lancé par GitHub Actions juste après update_data.py, toutes les 30 minutes.
Écrit universe.json : ~900 actions + indices, devises, matières premières, taux US,
chacun avec son dernier cours, sa variation, sa source et son horodatage.

- Les compositions d'indices (S&P 500, Nasdaq 100, FTSE 100, CAC 40, DAX,
  Euro Stoxx 50, SMI, Nikkei 225) sont lues sur Wikipédia une fois par semaine
  et gardées dans constituents.json.
- Les cours viennent de Yahoo Finance (yfinance), par paquets, pour rester
  sous les limites de Yahoo.
- Les taux directeurs viennent des sources officielles : BCE (ECB Data Portal),
  Bank of England (IADB), Fed et BoJ (FRED).
"""
from __future__ import annotations

import json
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "universe.json"
CONST = ROOT / "constituents.json"
NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}

# ─────────────────────────────────────────────────────────────────────────────
# 1. ACTIFS FIXES — indices, devises, matières premières, taux, crypto
# ─────────────────────────────────────────────────────────────────────────────
FIXED = {
    "Indice": {
        "^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^IXIC": "Nasdaq Composite", "^DJI": "Dow Jones",
        "^RUT": "Russell 2000", "^VIX": "VIX (volatilité S&P 500)", "^GSPTSE": "S&P/TSX (Canada)",
        "^BVSP": "Bovespa (Brésil)", "^MXX": "IPC (Mexique)", "^FTSE": "FTSE 100", "^GDAXI": "DAX",
        "^FCHI": "CAC 40", "^STOXX50E": "Euro Stoxx 50", "^STOXX": "Stoxx Europe 600",
        "^AEX": "AEX (Pays-Bas)", "^IBEX": "IBEX 35 (Espagne)", "FTSEMIB.MI": "FTSE MIB (Italie)",
        "^SSMI": "SMI (Suisse)", "^BFX": "BEL 20 (Belgique)", "^N225": "Nikkei 225",
        "^HSI": "Hang Seng", "000001.SS": "Shanghai Composite", "399001.SZ": "Shenzhen Component",
        "^KS11": "KOSPI (Corée)", "^TWII": "Taiex (Taïwan)", "^AXJO": "ASX 200 (Australie)",
        "^NSEI": "Nifty 50 (Inde)", "^BSESN": "Sensex (Inde)", "^STI": "Straits Times (Singapour)",
        "^JKSE": "Jakarta Composite",
    },
    "Devise": {
        "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "JPY=X": "USD/JPY", "CHF=X": "USD/CHF",
        "EURCHF=X": "EUR/CHF", "EURGBP=X": "EUR/GBP", "EURJPY=X": "EUR/JPY", "GBPCHF=X": "GBP/CHF",
        "AUDUSD=X": "AUD/USD", "NZDUSD=X": "NZD/USD", "CAD=X": "USD/CAD", "CNY=X": "USD/CNY",
        "HKD=X": "USD/HKD", "SGD=X": "USD/SGD", "INR=X": "USD/INR", "BRL=X": "USD/BRL",
        "MXN=X": "USD/MXN", "ZAR=X": "USD/ZAR", "TRY=X": "USD/TRY", "SEK=X": "USD/SEK",
        "NOK=X": "USD/NOK", "EURSEK=X": "EUR/SEK", "EURNOK=X": "EUR/NOK", "DX-Y.NYB": "Indice dollar (DXY)",
    },
    "Matière première": {
        "GC=F": "Or (Comex)", "SI=F": "Argent", "PL=F": "Platine", "PA=F": "Palladium",
        "HG=F": "Cuivre", "ALI=F": "Aluminium", "CL=F": "Pétrole WTI", "BZ=F": "Pétrole Brent",
        "NG=F": "Gaz naturel (Henry Hub)", "RB=F": "Essence RBOB", "HO=F": "Fioul domestique",
        "ZW=F": "Blé", "ZC=F": "Maïs", "ZS=F": "Soja", "KC=F": "Café", "SB=F": "Sucre",
        "CC=F": "Cacao", "CT=F": "Coton", "LE=F": "Bovins vivants",
    },
    "Taux": {
        "^IRX": "Taux US 3 mois (T-Bill)", "^FVX": "Taux US 5 ans", "^TNX": "Taux US 10 ans",
        "^TYX": "Taux US 30 ans",
    },
    "Crypto": {
        "BTC-USD": "Bitcoin", "ETH-USD": "Ether", "SOL-USD": "Solana", "XRP-USD": "XRP",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. COMPOSITIONS D'INDICES — Wikipédia, rafraîchies chaque semaine
# ─────────────────────────────────────────────────────────────────────────────
WIKI = [
    # (liste, page, suffixe Yahoo, règle)
    ("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "", "us"),
    ("Nasdaq 100", "https://en.wikipedia.org/wiki/Nasdaq-100", "", "us"),
    ("FTSE 100", "https://en.wikipedia.org/wiki/FTSE_100_Index", ".L", "uk"),
    ("CAC 40", "https://en.wikipedia.org/wiki/CAC_40", ".PA", "eu"),
    ("DAX", "https://en.wikipedia.org/wiki/DAX", ".DE", "eu"),
    ("Euro Stoxx 50", "https://en.wikipedia.org/wiki/EURO_STOXX_50", "", "eu"),
    ("SMI", "https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW", "eu"),
    ("Nikkei 225", "https://en.wikipedia.org/wiki/Nikkei_225", ".T", "jp"),
]
SUFFIXES = {"PA", "DE", "L", "SW", "AS", "MI", "MC", "BR", "HE", "LS", "IR", "VI", "ST", "CO", "OL", "T", "F"}
TICK_COLS = ("symbol", "ticker", "ticker symbol", "epic", "code", "stock code")
NAME_COLS = ("security", "company", "name", "constituent", "company name")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def _norm(raw: str, suffix: str, rule: str) -> str | None:
    t = re.sub(r"\[.*?\]", "", str(raw)).strip()
    if not t or t.lower() == "nan":
        return None
    if ":" in t:                                   # « EPA: AC » → « AC »
        t = t.split(":")[-1].strip()
    t = t.split()[0].upper()
    if rule == "us":
        return t.replace(".", "-")
    if rule == "jp":
        return f"{t}.T" if t.isdigit() else None
    if "." in t and t.rsplit(".", 1)[1] in SUFFIXES:
        return t                                   # déjà au format Yahoo
    if rule == "uk":
        return t.replace(".", "-").rstrip("-") + ".L"
    return t + suffix if suffix else None          # Euro Stoxx sans suffixe : ignoré


def _pick(cols, wanted):
    low = {str(c).lower().strip(): c for c in cols}
    for w in wanted:
        if w in low:
            return low[w]
    for k, c in low.items():
        if any(w in k for w in wanted):
            return c
    return None


def fetch_constituents() -> dict:
    try:
        cached = json.loads(CONST.read_text(encoding="utf-8"))
        age = NOW - datetime.fromisoformat(cached["generated_at"].replace("Z", "+00:00"))
        if age < timedelta(days=7) and cached.get("items"):
            log(f"Compositions : cache de {age.days} j, {len(cached['items'])} titres")
            return cached
    except Exception:
        cached = {}

    items: dict[str, dict] = {}
    for lst, url, suffix, rule in WIKI:
        try:
            html = requests.get(url, headers=UA, timeout=30).text
            best = None
            for tb in pd.read_html(StringIO(html)):
                if isinstance(tb.columns, pd.MultiIndex):
                    tb.columns = [" ".join(map(str, c)).strip() for c in tb.columns]
                tc, nc = _pick(tb.columns, TICK_COLS), _pick(tb.columns, NAME_COLS)
                if tc is not None and len(tb) >= 20 and (best is None or len(tb) > len(best[0])):
                    best = (tb, tc, nc)
            if not best:
                raise ValueError("aucun tableau de composition trouvé")
            tb, tc, nc = best
            n = 0
            for _, row in tb.iterrows():
                s = _norm(row[tc], suffix, rule)
                if not s:
                    continue
                name = str(row[nc]).strip() if nc is not None else s
                it = items.setdefault(s, {"n": re.sub(r"\[.*?\]", "", name), "l": []})
                if lst not in it["l"]:
                    it["l"].append(lst)
                n += 1
            log(f"Compositions : {lst} → {n}")
        except Exception as e:
            log(f"  ! {lst} : {e}")

    if len(items) < 100 and cached.get("items"):
        log("Compositions : échec Wikipédia, ancien cache conservé")
        return cached
    out = {"generated_at": NOW.isoformat().replace("+00:00", "Z"), "items": items}
    CONST.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. COURS — Yahoo Finance par paquets de 150
# ─────────────────────────────────────────────────────────────────────────────
def fetch_prices(tickers: list[str]) -> dict:
    out = {}
    for i in range(0, len(tickers), 150):
        chunk = tickers[i:i + 150]
        try:
            df = yf.download(chunk, period="1mo", interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            log(f"  ! paquet {i // 150 + 1} : {e}")
            continue
        for t in chunk:
            try:
                c = (df[t]["Close"] if len(chunk) > 1 else df["Close"]).dropna()
                if len(c) < 1:
                    continue
                p = float(c.iloc[-1])
                prev = float(c.iloc[-2]) if len(c) > 1 else None
                d = c.index[-1]
                today = d.date() == NOW.date()
                out[t] = {
                    "p": round(p, 4),
                    "pct": round((p / prev - 1) * 100, 2) if prev else None,
                    "st": "DELAYED_15M" if today else ("EOD" if NOW.date() - d.date() < timedelta(days=4) else "STALE"),
                    "ts": d.strftime("%Y-%m-%d"),
                    # 22 dernières clôtures : mini-graphique sur 1 mois, à côté du nom
                    "h": [round(float(v), 6 if v < 10 else 2) for v in c.tail(22)],
                }
            except Exception:
                pass
        log(f"Cours : paquet {i // 150 + 1} → {sum(1 for t in chunk if t in out)}/{len(chunk)}")
        time.sleep(2)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. TAUX DIRECTEURS — sources officielles des banques centrales
#    BCE : ECB Data Portal (sans clé) · BoE : base IADB (sans clé)
#    Fed et BoJ : FRED (clé FRED_API_KEY, déjà utilisée par update_data.py)
# ─────────────────────────────────────────────────────────────────────────────
def _rate(s, n, cb, value, date, src, extra=None):
    it = {"s": s, "n": n, "c": "Taux", "l": ["Taux directeur"], "p": round(float(value), 3),
          "pct": None, "st": "EOD", "ts": str(date)[:10], "nw": 1, "cb": cb, "src": src}
    if extra:
        it.update(extra)
    return it


def _fred_last(sid: str, key: str):
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": sid, "api_key": key, "file_type": "json",
                             "sort_order": "desc", "limit": 10}, timeout=20)
    r.raise_for_status()
    o = next(o for o in r.json()["observations"] if o["value"] != ".")
    return float(o["value"]), o["date"]


def _boj_tona():
    """Taux au jour le jour non garanti (TONA), cible opérationnelle de la BoJ, via l'API statistique
    de la Banque du Japon. Le format de réponse est lu de façon tolérante : on cherche deux listes
    parallèles (dates, valeurs) et on garde la dernière valeur renseignée."""
    r = requests.get("https://www.stat-search.boj.or.jp/api/v1/getDataCode",
                     params={"format": "json", "lang": "en", "db": "FM01", "code": "STRDCLUCON"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    found = []

    def walk(o):
        if isinstance(o, dict):
            lists = {k: v for k, v in o.items() if isinstance(v, list) and v}
            dk = next((k for k in lists if "DATE" in k.upper()), None)
            vk = next((k for k in lists if k.upper() in ("VALUES", "VALUE", "DATA") and k != dk), None)
            if dk and vk and len(lists[dk]) == len(lists[vk]):
                found.append((lists[dk], lists[vk]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(r.json())
    for dates, vals in found:
        pairs = [(str(d), v) for d, v in zip(dates, vals) if v not in (None, "", "NaN")]
        if pairs:
            d, v = pairs[-1]
            d = re.sub(r"\D", "", d)
            d = f"{d[:4]}-{d[4:6] or '01'}-{d[6:8] or '01'}"
            return float(v), d
    raise RuntimeError("réponse BoJ sans série exploitable")


def fetch_policy_rates() -> list:
    import os
    out = []

    # BCE — taux de dépôt (taux directeur de référence), refinancement, €STR
    for code, s, n in (
        ("FM/D.U2.EUR.4F.KR.DFR.LEV", "ECB-DFR", "BCE — taux de dépôt (taux directeur de référence)"),
        ("FM/D.U2.EUR.4F.KR.MRR_FR.LEV", "ECB-MRO", "BCE — taux de refinancement principal"),
        ("EST/B.EU000A2X2A25.WT", "ESTR", "€STR (taux au jour le jour en euro)"),
    ):
        try:
            r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{code}",
                             params={"lastNObservations": 1, "format": "csvdata"},
                             headers=UA, timeout=30)
            r.raise_for_status()
            row = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"]).iloc[-1]
            out.append(_rate(s, n, "BCE" if s == "ECB-DFR" else None, row["OBS_VALUE"],
                             row["TIME_PERIOD"], "BCE — ECB Data Portal"))
        except Exception as e:
            log(f"  ! BCE {s} : {e}")

    # BoE — Bank Rate et SONIA
    try:
        r = requests.get(
            "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
            params={"csv.x": "yes", "Datefrom": (NOW - timedelta(days=60)).strftime("%d/%b/%Y"),
                    "Dateto": "now", "SeriesCodes": "IUDBEDR,IUDSOIA", "CSVF": "TN",
                    "UsingCodes": "Y", "VPD": "Y", "VFD": "N"},
            headers=UA, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        dcol = df.columns[0]
        for code, s, n, cb in (("IUDBEDR", "BOE-BANK", "BoE — Bank Rate", "BoE"),
                               ("IUDSOIA", "SONIA", "SONIA (taux au jour le jour en livre)", None)):
            sub = df.dropna(subset=[code])
            if len(sub):
                out.append(_rate(s, n, cb, sub[code].iloc[-1],
                                 pd.to_datetime(sub[dcol].iloc[-1], dayfirst=True).date(),
                                 "Bank of England — base IADB"))
    except Exception as e:
        log(f"  ! BoE : {e}")

    # Fed (fourchette) et BoJ via FRED
    key = os.getenv("FRED_API_KEY")
    if key:
        try:
            hi, d = _fred_last("DFEDTARU", key)
            lo, _ = _fred_last("DFEDTARL", key)
            out.append(_rate("FED-FUNDS", "Fed — fed funds (borne haute)", "Fed", hi, d,
                             "FRED DFEDTARU / DFEDTARL", {"lo": lo}))
        except Exception as e:
            log(f"  ! Fed : {e}")
        try:
            v, d = _fred_last("IRSTCB01JPM156N", key)
            boj_fred = (v, d)
        except Exception as e:
            boj_fred = None
            log(f"  ! BoJ (FRED) : {e}")
    else:
        boj_fred = None
        log("Taux directeurs : pas de FRED_API_KEY, Fed ignorée")

    # BoJ : API officielle d'abord (à jour) ; série OCDE de FRED seulement en dernier recours
    try:
        v, d = _boj_tona()
        out.append(_rate("BOJ-RATE", "BoJ — taux au jour le jour non garanti (TONA, cible de la BoJ)", "BoJ", v, d,
                         "Banque du Japon — API statistique (série FM01 STRDCLUCON)"))
        log(f"  BoJ : {v} % au {d} (API BoJ)")
    except Exception as e:
        log(f"  ! BoJ (API) : {str(e)[:120]}")
        if boj_fred:
            out.append(_rate("BOJ-RATE", "BoJ — taux directeur (série OCDE, possiblement arrêtée)", "BoJ", *boj_fred,
                             "FRED IRSTCB01JPM156N (OCDE, mensuel)"))

    log(f"Taux directeurs : {len(out)} relevés ({', '.join(x['s'] for x in out)})")
    return out


def main() -> int:
    cons = fetch_constituents()
    catalog = []
    for cls, d in FIXED.items():
        for s, n in d.items():
            catalog.append({"s": s, "n": n, "c": cls, "l": []})
    fixed_syms = {x["s"] for x in catalog}
    for s, it in cons.get("items", {}).items():
        if s not in fixed_syms:
            catalog.append({"s": s, "n": it["n"], "c": "Action", "l": it["l"]})

    # Titres suivis hors des grands indices (prioritaires, pairs RV, ETF des profils) : ajoutés au catalogue
    # pour qu'ils aient eux aussi cours, variation et mini-graphique partout dans le terminal.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from update_data import FUND_TICKERS
        from peers import all_peer_tickers
        have = {x["s"] for x in catalog}
        extra = [t for t in dict.fromkeys([*FUND_TICKERS, *all_peer_tickers()]) if t not in have]
        catalog.extend({"s": t, "n": t, "c": "ETF" if t in ("ACWI", "AGG", "GLD", "BIL") else "Action", "l": ["Hors indice"]} for t in extra)
        log(f"Catalogue : {len(extra)} titres suivis ajoutés hors indices")
    except Exception as e:
        log(f"  ! titres suivis hors indices : {e}")

    prices = fetch_prices([x["s"] for x in catalog])
    for x in catalog:
        x.update(prices.get(x["s"], {"p": None, "pct": None, "st": "STALE", "ts": None}))
    dead = [x["s"] for x in catalog if x["c"] == "Action" and x["p"] is None]
    if dead:
        catalog = [x for x in catalog if not (x["c"] == "Action" and x["p"] is None)]
        log(f"Catalogue : {len(dead)} actions sans cotation retirées (ticker obsolète ou rachat) : {', '.join(dead[:15])}")
    catalog.extend(fetch_policy_rates())

    out = {"generated_at": NOW.isoformat().replace("+00:00", "Z"),
           "source": "Yahoo Finance via yfinance ; compositions : Wikipédia",
           "items": catalog}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ok = sum(1 for x in catalog if x["p"] is not None)
    log(f"universe.json : {ok}/{len(catalog)} instruments cotés ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
