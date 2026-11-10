"""
Pipeline de données du Terminal.

Lancé toutes les 30 minutes par GitHub Actions (.github/workflows/update.yml).
Récupère prix, taux et news, puis écrit data.json à la racine du dépôt.
Le terminal (index.html) lit ce fichier à l'ouverture et toutes les 10 minutes.

Règle d'or, héritée du terminal : aucune valeur sans source, horodatage et statut.
Si une source échoue, on garde la dernière valeur connue et on la marque STALE
plutôt que d'inventer un chiffre.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import warnings
warnings.filterwarnings("ignore")  # masque les avertissements pandas de yfinance

import feedparser
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data.json"
NOW = datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────
# 1. UNIVERS — symbole du terminal → ticker Yahoo Finance
# ─────────────────────────────────────────────────────────────────────────────
TICKERS = {
    "NVDA": "NVDA", "ASTS": "ASTS",
    "SPX": "^GSPC", "NDX": "^NDX", "DJI": "^DJI", "RUT": "^RUT", "VIX": "^VIX",
    "N225": "^N225", "HSI": "^HSI", "SHCOMP": "000001.SS", "STI": "^STI",
    "UKX": "^FTSE", "DAX": "^GDAXI", "CAC": "^FCHI", "SX5E": "^STOXX50E",
    "EURUSD": "EURUSD=X", "USDJPY": "JPY=X", "GBPUSD": "GBPUSD=X", "DXY": "DX-Y.NYB",
    "XAU": "GC=F", "CL": "CL=F", "BRENT": "BZ=F", "BTC": "BTC-USD",
}
# TOPIX n'a pas de ticker fiable sur Yahoo : il reste sur la valeur figée du terminal.

FRED_SERIES = {"2A": "DGS2", "5A": "DGS5", "10A": "DGS10", "30A": "DGS30"}

RSS_FEEDS = [
    ("Financial Times — Markets", "https://www.ft.com/markets?format=rss"),
    ("Financial Times — Global economy", "https://www.ft.com/global-economy?format=rss"),
    ("Financial Times — Companies", "https://www.ft.com/companies?format=rss"),
    ("Financial Times — World", "https://www.ft.com/world?format=rss"),
    ("CNBC — Markets", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("BBC — Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Le Monde — Économie", "https://www.lemonde.fr/economie/rss_full.xml"),
    ("Wall Street Journal — Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Wall Street Journal — World", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ("The Economist — Finance", "https://www.economist.com/finance-and-economics/rss.xml"),
    ("Investing.com France", "https://fr.investing.com/rss/news.rss"),
    ("Federal Reserve — press releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB — press releases", "https://www.ecb.europa.eu/rss/press.html"),
]
# Flux personnel myFT (sujets suivis sur ft.com), gardé dans le secret GitHub FT_MYFT_RSS.
# Plusieurs adresses possibles, séparées par des virgules. Les articles payants y figurent
# avec leur titre et leur chapô ; le lien ouvre ft.com, où l'abonné est connecté.
for _i, _u in enumerate([u.strip() for u in os.getenv("FT_MYFT_RSS", "").split(",") if u.strip()]):
    _slug = _u.split("ft.com/")[-1].split("?")[0].replace("stream/", "")
    _slug = "followed topic" if re.fullmatch(r"[0-9a-f-]{36}", _slug) else _slug.replace("-", " ")
    RSS_FEEDS.insert(_i, (f"myFT — {_slug}", _u))
MAX_NEWS = 100

# Mots-clés → tickers, pour relier une news à une fiche du terminal.
TAGS = {
    "NVDA": r"\bnvidia\b", "ASTS": r"ast spacemobile",
    "SPX": r"s&p ?500|wall street|us stocks|actions américaines", "NDX": r"nasdaq",
    "BRENT": r"\bbrent\b|\boil\b|pétrole|opec", "CL": r"\bwti\b|crude",
    "XAU": r"\bgold price|\bgold\b(?! medal)|prix de l'or|cours de l'or|\bl'or\b", "BTC": r"bitcoin|\bcrypto|binance",
    "EURUSD": r"\becb\b|\bbce\b|lagarde|euro\b", "USDJPY": r"\byen\b|bank of japan|\bboj\b",
    "N225": r"nikkei|topix|tokyo stocks|japanese stocks", "HSI": r"hang seng|hong kong stocks", "UKX": r"ftse|bank of england|\bboe\b",
    "CAC": r"cac 40|cac40|bourse de paris|euronext paris|paris stocks", "DAX": r"\bdax\b|frankfurt stock|german stocks",
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def load_previous() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. PRIX — Yahoo Finance via yfinance (gratuit, sans clé, non officiel)
# ─────────────────────────────────────────────────────────────────────────────
def _col(df, ticker, field):
    """Accès robuste à une colonne, que yfinance renvoie un MultiIndex ou non."""
    try:
        return df[ticker][field].dropna()
    except Exception:
        try:
            return df[field][ticker].dropna()
        except Exception:
            return None


def fetch_quotes(previous: dict) -> dict:
    yt = list(TICKERS.values())
    log(f"Prix : {len(yt)} tickers")
    daily = yf.download(yt, period="1mo", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
    hourly = yf.download(yt, period="5d", interval="15m", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    out = {}
    for sym, t in TICKERS.items():
        try:
            dclose = _col(daily, t, "Close")
            hclose = _col(hourly, t, "Close")
            if dclose is None or len(dclose) < 2:
                raise ValueError("historique quotidien insuffisant")

            # Dernier prix : la barre horaire la plus récente si elle existe.
            if hclose is not None and len(hclose):
                price = float(hclose.iloc[-1])
                last_ts = hclose.index[-1]
            else:
                price = float(dclose.iloc[-1])
                last_ts = dclose.index[-1]
            last_ts = last_ts.tz_localize("UTC") if last_ts.tzinfo is None else last_ts.tz_convert("UTC")
            # Yahoo horodate une bougie à son OUVERTURE : le cours affiché est celui de la fin de la bougie
            # (ou du moment du passage si elle est en cours). Sans ce décalage, un cours relevé il y a
            # 5 minutes apparaissait « vieux de 63 min ».
            if hclose is not None and len(hclose):
                last_ts = min(last_ts + pd.Timedelta(minutes=15), pd.Timestamp(NOW))

            # Clôture précédente = dernière clôture quotidienne d'une séance antérieure.
            last_day = last_ts.date()
            prev_series = dclose[[d.date() < last_day for d in dclose.index]]
            prev = float(prev_series.iloc[-1]) if len(prev_series) else float(dclose.iloc[-2])

            try:
                day = daily[t].dropna(how="all")
                row = day.iloc[-1] if len(day) else None
            except Exception:
                row = None

            age = NOW - last_ts.to_pydatetime()
            # Marché fermé : la clôture officielle (barre quotidienne, fixing de clôture compris) remplace
            # la dernière barre de 15 min. Sans cela le DAX affichait 25 795 (-2,01 %) au lieu de 25 820 (-1,92 %).
            # Devises, contrats à terme et cryptos cotent en continu : leur barre quotidienne n'est pas une clôture.
            continuous = t.endswith("=X") or t.endswith("=F") or t.endswith("-USD") or t.startswith("DX-")
            if age >= timedelta(minutes=90) and not continuous and dclose.index[-1].date() == last_day:
                price = float(dclose.iloc[-1])
            if age < timedelta(minutes=90):
                status = "DELAYED_15M"      # marché ouvert, cotation différée Yahoo
            elif age < timedelta(days=4):
                status = "EOD"              # dernière clôture (week-end inclus)
            else:
                status = "STALE"

            q = {
                "p": round(price, 4),
                "prev": round(prev, 4),
                "chg": round(price - prev, 4),
                "pct": round((price / prev - 1) * 100, 2) if prev else None,
                "st": status,
                "src": f"Yahoo Finance via yfinance ({t})",
                "ts": last_ts.isoformat().replace("+00:00", "Z"),
            }
            if row is not None:
                for k, col in (("open", "Open"), ("lo", "Low"), ("hi", "High"), ("vol", "Volume")):
                    v = row.get(col)
                    if v is not None and v == v and float(v) > 0:
                        q[k] = round(float(v), 4) if k != "vol" else int(v)
            q["h"] = [round(float(v), 6 if v < 10 else 2) for v in dclose.tail(22)]   # mini-graphique 1 mois
            out[sym] = q
        except Exception as e:
            old = (previous.get("quotes") or {}).get(sym)
            if old:
                old = {**old, "st": "STALE", "src": old.get("src", "") + " — last update failed"}
                out[sym] = old
            log(f"  ! {sym} ({t}) : {e}")
    log(f"Prix : {len(out)} cotations écrites")
    return out


# Titres dont le robot actualise les fondamentaux (fiche Titre). Ajoute ou retire des tickers Yahoo ici.
# Les 30 premières capitalisations mondiales au 30/10/2026 (StockTitan, Motley Fool), plus ASTS (portefeuille).
FUND_TICKERS = [
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "TSM", "SPCX", "2222.SR", "META", "TSLA",
    "AVGO", "005930.KS", "LLY", "BRK-B", "000660.KS", "MU", "WMT", "JPM", "AMD", "V",
    "XOM", "JNJ", "ASML.AS", "MA", "ABBV", "CVX", "COST", "PLTR", "CSCO", "KO",
    "ASTS",
    "ACWI", "AGG", "GLD", "BIL",   # ETF des profils types (dividendes du rapport client)
]
FUND_FIELDS = {   # clé du terminal ← clé Yahoo
    "name": "longName", "web": "website", "sector": "sector", "industry": "industry", "ccy": "currency",
    "mcap": "marketCap", "pe": "trailingPE", "fpe": "forwardPE", "eps": "trailingEps",
    "feps": "forwardEps", "revTTM": "totalRevenue", "netIncome": "netIncomeToCommon",
    "shares": "sharesOutstanding", "beta": "beta", "div": "dividendRate", "divYield": "dividendYield",
    "target": "targetMeanPrice", "targetHi": "targetHighPrice", "targetLo": "targetLowPrice",
    "rating": "recommendationKey", "analysts": "numberOfAnalystOpinions",
    "revGrowth": "revenueGrowth", "epsGrowth": "earningsGrowth", "grossMargin": "grossMargins",
    "opMargin": "operatingMargins", "netMargin": "profitMargins", "roe": "returnOnEquity",
    "debtEq": "debtToEquity", "cash": "totalCash", "debt": "totalDebt", "fcf": "freeCashflow",
    "w52lo": "fiftyTwoWeekLow", "w52hi": "fiftyTwoWeekHigh", "evEbitda": "enterpriseToEbitda",
    # Calendrier (horodatages Unix) : prochaine publication de résultats et prochain dividende
    "earnTs": "earningsTimestamp", "earnStart": "earningsTimestampStart", "earnEnd": "earningsTimestampEnd",
    "earnEst": "isEarningsDateEstimate", "exDiv": "exDividendDate", "divDate": "dividendDate",
    "lastDiv": "lastDividendValue", "trailDiv": "trailingAnnualDividendRate",
}
PCT_FIELDS = {"divYield", "revGrowth", "epsGrowth", "grossMargin", "opMargin", "netMargin", "roe"}
RATING_FR = {"strong_buy": "Strong buy", "buy": "Buy", "hold": "Hold",
             "underperform": "Underperform", "sell": "Sell", "none": None}


def fix_div_yield(f: dict, info: dict) -> None:
    """Rendement du dividende recalculé (dividende annuel ÷ cours × 100), sans dépendre du format Yahoo,
    qui donne selon les titres une fraction (0,0019) ou un pourcentage (0,19). Les cours cotés en pence
    (Londres) sont ramenés en livres quand le dividende est exprimé en livres."""
    div = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    px = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    try:
        div, px = float(div or 0), float(px or 0)
    except Exception:
        div, px = 0.0, 0.0
    if div > 0 and px > 0:
        y = div / px * 100
        if y < 0.05 and info.get("currency") in ("GBp", "GBX", "ZAc", "ILA"):
            y *= 100                       # dividende en livres, cours en pence
        elif y > 40 and info.get("currency") in ("GBp", "GBX", "ZAc", "ILA"):
            y /= 100
        if 0 < y < 40:
            f["divYield"] = round(y, 3)
            f["div"] = round(div, 4)
            return
    if not div:
        f.pop("divYield", None)            # pas de dividende : pas de rendement affiché
    elif f.get("divYield", 0) > 40:
        f.pop("divYield", None)            # valeur incohérente : écartée plutôt qu'affichée


SECTOR_FIX = {"ML.PA": "Consumer Cyclical", "FISV": "Technology", "FI": "Technology"}   # trous connus de Yahoo


def sanitize_fund(f: dict, sym: str, info: dict) -> None:
    """Valeurs aberrantes corrigées avant publication : capitalisation nulle, PER non significatif,
    secteur manquant (fonds et investment trusts regroupés, trous connus de Yahoo complétés)."""
    if not (f.get("mcap") or 0) > 0:
        f.pop("mcap", None)
    for k in ("pe", "fpe"):
        if isinstance(f.get(k), (int, float)) and (f[k] > 300 or f[k] <= 0):
            f[k + "_ns"] = f.pop(k)
    if not f.get("sector"):
        qt = (info.get("quoteType") or "").upper()
        if sym in SECTOR_FIX:
            f["sector"] = SECTOR_FIX[sym]
        elif qt in ("ETF", "MUTUALFUND") or re.search(r"\b(trust|fund|investment)\b", str(info.get("longName", "")), re.I):
            f["sector"] = "Funds and trusts"
        elif len(f) > 3:
            f["sector"] = "Unclassified"


def fetch_fundamentals() -> dict:
    out = {}
    for sym in FUND_TICKERS:
        try:
            info = yf.Ticker(sym).info or {}
            f = {}
            for k, yk in FUND_FIELDS.items():
                v = info.get(yk)
                if v is None or v == "" or (isinstance(v, float) and v != v):
                    continue
                if k in PCT_FIELDS and isinstance(v, (int, float)):
                    # Yahoo donne des fractions (0,66 = 66 %) ; le rendement du dividende est parfois déjà en %.
                    v = v * 100 if (k != "divYield" or v < 0.2) else v
                if k == "rating":
                    v = RATING_FR.get(v, v)
                if v is not None:
                    f[k] = round(v, 4) if isinstance(v, float) else v
            fix_div_yield(f, info)
            sanitize_fund(f, sym, info)
            if len(f) > 3:
                out[sym] = f
        except Exception as e:
            log(f"  ! fondamentaux {sym} : {e}")
    log(f"Fondamentaux : {len(out)}/{len(FUND_TICKERS)} titres")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. TAUX — FRED (Réserve fédérale de St. Louis), gratuit avec clé
# ─────────────────────────────────────────────────────────────────────────────
def fetch_curve(previous: dict) -> list:
    key = os.getenv("FRED_API_KEY")
    if not key:
        log("Taux : pas de FRED_API_KEY, courbe non actualisée")
        return previous.get("curve", [])
    curve = []
    for tenor, sid in FRED_SERIES.items():
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": sid, "api_key": key, "file_type": "json",
                        "sort_order": "desc", "limit": 10},
                timeout=20,
            )
            r.raise_for_status()
            obs = next(o for o in r.json()["observations"] if o["value"] != ".")
            curve.append({"t": tenor, "y": float(obs["value"]), "st": "EOD",
                          "src": f"FRED {sid}", "ts": obs["date"]})
        except Exception as e:
            log(f"  ! FRED {sid} : {e}")
            curve.append({"t": tenor, "y": None})
    log(f"Taux : {sum(1 for c in curve if c.get('y') is not None)}/4 points")
    return curve


# ─────────────────────────────────────────────────────────────────────────────
# 4. NEWS — flux RSS publics (titres, chapô, lien — jamais l'article complet)
# ─────────────────────────────────────────────────────────────────────────────
def _clean(html: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", txt).strip()[:400]


def fetch_news(previous: dict) -> list:
    items, seen = [], set()
    # Date déjà connue d'un article (passage précédent) : un article sans date garde sa première apparition
    known = {x.get("h", "").lower(): x.get("ts") for x in previous.get("news", []) if x.get("h")}
    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (terminal-ia)")
            n = 0
            for e in feed.entries[:15]:
                title = _clean(e.get("title", ""))
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                t = e.get("published_parsed") or e.get("updated_parsed")
                if t:
                    ts = datetime(*t[:6], tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                else:
                    # Flux sans date (ex. Nikkei Asia) : sans ce garde-fou, chaque article recevait l'heure du
                    # passage et l'ensemble du flux passait en tête du fil à chaque fois.
                    ts = known.get(title.lower()) or (NOW - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
                text = (title + " " + _clean(e.get("summary", ""))).lower()
                syms = [s for s, pat in TAGS.items() if re.search(pat, text)]
                items.append({"h": title, "w": _clean(e.get("summary", "")), "syms": syms[:4],
                              "src": name, "ts": ts, "url": e.get("link"),
                              **({"mine": 1} if name.startswith("myFT") else {})})
                n += 1
            log(f"News : {name} → {n}")
        except Exception as e:
            log(f"  ! RSS {name} : {e}")
    if not items:
        return previous.get("news", [])
    items.sort(key=lambda x: x["ts"], reverse=True)
    mine = [x for x in items if x.get("mine")][:40]          # myFT : jusqu'à 40, toujours gardés
    # Au plus 12 articles par source, pour qu'aucun flux ne monopolise le fil
    per, rest = {}, []
    for x in items:
        if x.get("mine"):
            continue
        k = x["src"].split(" — ")[0]
        if per.get(k, 0) < 12:
            per[k] = per.get(k, 0) + 1
            rest.append(x)
    rest = rest[:MAX_NEWS - len(mine)]
    return sorted(mine + rest, key=lambda x: x["ts"], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# 5. SYNTHÈSE IA — Google Gemini, niveau gratuit (optionnel)
# ─────────────────────────────────────────────────────────────────────────────
def ai_summary(quotes: dict, news: list, previous: dict) -> dict | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key or not news:
        return previous.get("summary")
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    mk = "\n".join(f"{s}: {q['p']} ({q.get('pct')} %) [{q['st']}]" for s, q in quotes.items())
    hd = "\n".join(f"- {n['h']} ({n['src']})" for n in news[:20])
    prompt = (
        "You are an analyst for an investment adviser in Monaco. In English, in 150 words "
        "maximum, write the current market wrap using ONLY the data below (some headlines may be in "
        "French: translate them). Three short paragraphs, no markdown: (1) what is moving markets, "
        "(2) rates, FX and commodities, (3) what to watch. Do not invent any figure that is not in the data. "
        "No buy or sell recommendation.\n\n"
        f"PRICES:\n{mk}\n\nHEADLINES:\n{hd}"
    )
    base = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    def call(m):
        return requests.post(f"{base}/models/{m}:generateContent", headers=headers, json=body, timeout=90)

    try:
        r = call(model)
        if r.status_code == 404:
            # Modèle retiré ou renommé : on demande à Google la liste et on prend un « flash » disponible.
            lst = requests.get(f"{base}/models", headers=headers, timeout=30).json().get("models", [])
            names = [m["name"].split("/")[-1] for m in lst
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            log(f"  Gemini : '{model}' introuvable. Modèles disponibles : {', '.join(names[:15])}")
            flash = ([n for n in names if n == "gemini-flash-latest"]
                     + sorted([n for n in names if "flash" in n and "lite" not in n and "tts" not in n and "image" not in n], reverse=True)) or names
            if not flash:
                raise RuntimeError("aucun modèle disponible pour cette clé")
            model = flash[0]
            r = call(model)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code} : {r.text[:300]}")
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        log(f"Synthèse IA : {len(text)} caractères ({model})")
        return {"text": text, "model": model, "lang": "en", "ts": NOW.isoformat().replace("+00:00", "Z")}
    except Exception as e:
        log(f"  ! Gemini : {e}")
        prev = previous.get("summary")
        return prev if prev and prev.get("lang") == "en" else None   # an old French summary is dropped


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    # ft.json est écrit par le Google Apps Script des newsletters FT. Tant qu'il n'est pas installé,
    # un fichier vide évite une erreur 404 à chaque visite (le script le remplacera ensuite).
    ft = ROOT / "ft.json"
    if not ft.exists():
        ft.write_text(json.dumps({"generated_at": None, "items": [], "note": "FT newsletter Apps Script not installed"}), encoding="utf-8")
    previous = load_previous()
    quotes = fetch_quotes(previous)
    news = fetch_news(previous)
    data = {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "quotes": quotes,
        "fundamentals": fetch_fundamentals(),
        "curve": fetch_curve(previous),
        "news": news,
        "summary": ai_summary(quotes, news, previous),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"data.json écrit ({OUT.stat().st_size // 1024} Ko)")
    return 0 if quotes else 1


if __name__ == "__main__":
    sys.exit(main())
