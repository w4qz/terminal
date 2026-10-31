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
    ("Financial Times — Marchés", "https://www.ft.com/markets?format=rss"),
    ("Financial Times — Économie mondiale", "https://www.ft.com/global-economy?format=rss"),
    ("Financial Times — Entreprises", "https://www.ft.com/companies?format=rss"),
    ("Financial Times — Monde", "https://www.ft.com/world?format=rss"),
    ("CNBC — Marchés", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("BBC — Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Le Monde — Économie", "https://www.lemonde.fr/economie/rss_full.xml"),
]
# Flux personnel myFT (sujets suivis sur ft.com), gardé dans le secret GitHub FT_MYFT_RSS.
# Plusieurs adresses possibles, séparées par des virgules. Les articles payants y figurent
# avec leur titre et leur chapô ; le lien ouvre ft.com, où l'abonné est connecté.
for _i, _u in enumerate([u.strip() for u in os.getenv("FT_MYFT_RSS", "").split(",") if u.strip()]):
    RSS_FEEDS.insert(_i, ("myFT — mes sujets", _u))
MAX_NEWS = 40

# Mots-clés → tickers, pour relier une news à une fiche du terminal.
TAGS = {
    "NVDA": r"\bnvidia\b", "ASTS": r"ast spacemobile",
    "SPX": r"s&p ?500|wall street", "NDX": r"nasdaq|big tech|tech stocks",
    "BRENT": r"\bbrent\b|\boil\b|pétrole|opec", "CL": r"\bwti\b|crude",
    "XAU": r"\bgold\b|\bor\b ", "BTC": r"bitcoin|crypto|binance",
    "EURUSD": r"\becb\b|\bbce\b|lagarde|euro\b", "USDJPY": r"\byen\b|bank of japan|\bboj\b",
    "N225": r"nikkei|japan", "HSI": r"hang seng|hong kong", "UKX": r"ftse|bank of england",
    "CAC": r"cac 40|\bfrance\b|french", "DAX": r"\bdax\b|germany|german",
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
    daily = yf.download(yt, period="10d", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
    hourly = yf.download(yt, period="5d", interval="60m", group_by="ticker",
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
            out[sym] = q
        except Exception as e:
            old = (previous.get("quotes") or {}).get(sym)
            if old:
                old = {**old, "st": "STALE", "src": old.get("src", "") + " — échec de la dernière mise à jour"}
                out[sym] = old
            log(f"  ! {sym} ({t}) : {e}")
    log(f"Prix : {len(out)} cotations écrites")
    return out


def fetch_fundamentals() -> dict:
    out = {}
    for sym in ("NVDA", "ASTS"):
        try:
            fi = yf.Ticker(sym).fast_info
            out[sym] = {
                "mcap": float(fi["market_cap"]),
                "w52lo": float(fi["year_low"]),
                "w52hi": float(fi["year_high"]),
                "shares": float(fi["shares"]),
            }
        except Exception as e:
            log(f"  ! fondamentaux {sym} : {e}")
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
                ts = datetime(*t[:6], tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if t else NOW.isoformat()
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
    mine = [x for x in items if x.get("mine")][:20]          # myFT : jusqu'à 20, toujours gardés
    rest = [x for x in items if not x.get("mine")][:MAX_NEWS - len(mine)]
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
        "Tu es analyste pour un conseiller en investissement à Monaco. En français, en 150 mots "
        "maximum, rédige la lecture de marché du moment à partir UNIQUEMENT des données ci-dessous. "
        "Trois paragraphes courts : (1) ce qui fait bouger les marchés, (2) taux, change et matières "
        "premières, (3) points à surveiller. N'invente aucun chiffre absent des données. "
        "Pas de recommandation d'achat ou de vente.\n\n"
        f"COTATIONS :\n{mk}\n\nTITRES DE PRESSE :\n{hd}"
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
        return {"text": text, "model": model, "ts": NOW.isoformat().replace("+00:00", "Z")}
    except Exception as e:
        log(f"  ! Gemini : {e}")
        return previous.get("summary")


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
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
