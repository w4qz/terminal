"""
Onglet Macro du Terminal — équivalents gratuits de quatre fonctions Bloomberg :

  ECO   calendrier économique US (FRED) + derniers chiffres publiés
  WIRP  probabilités implicites des décisions de la Fed (contrats futures fed funds, Yahoo)
  GC    courbes des taux : Trésor US (FRED) et zone euro AAA (BCE), aujourd'hui / il y a 1 mois
  BTMM  tableau de bord monétaire US : fed funds, EFFR, SOFR, IORB, T-bills, pentes, bilan Fed

Écrit macro.json. Lancé par GitHub Actions toutes les 30 minutes, avec FRED_API_KEY.
Chaque valeur porte sa source et sa date ; une source en échec laisse une case vide, jamais un chiffre inventé.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "macro.json"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}
KEY = os.getenv("FRED_API_KEY", "")


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def fred(sid, n=1, units="lin", start=None):
    """Dernières observations d'une série FRED, de la plus récente à la plus ancienne."""
    # observation_end = aujourd'hui : FRED publie certains taux administrés (IORB, fed funds cible)
    # à l'avance, avec une date d'effet future ; on n'affiche jamais une observation postérieure au passage.
    p = {"series_id": sid, "api_key": KEY, "file_type": "json", "sort_order": "desc",
         "limit": n, "units": units, "observation_end": TODAY.isoformat()}
    if start:
        p["observation_start"] = start
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params=p, timeout=25)
    r.raise_for_status()
    return [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] != "."]


def safe(fn, label):
    try:
        return fn()
    except Exception as e:
        log(f"  ! {label} : {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ECO — derniers chiffres et calendrier des publications
# ─────────────────────────────────────────────────────────────────────────────
PRINTS = [
    # (série FRED, libellé, unités FRED, format)
    ("CPIAUCSL", "Inflation CPI (US, a/a)", "pc1", "%"),
    ("CPILFESL", "Inflation CPI core (US, a/a)", "pc1", "%"),
    ("PCEPILFE", "PCE core (US, a/a) — cible Fed", "pc1", "%"),
    ("PAYEMS", "Créations d'emplois NFP (US, k)", "chg", "k"),
    ("UNRATE", "Taux de chômage (US)", "lin", "%"),
    ("ICSA", "Inscriptions hebdo. au chômage (US)", "lin", "n"),
    ("A191RL1Q225SBEA", "PIB réel (US, t/t annualisé)", "lin", "%"),
    ("RSAFS", "Ventes au détail (US, m/m)", "pch", "%"),
    ("UMCSENT", "Confiance consommateurs U. Michigan", "lin", "n"),
    ("CP0000EZ19M086NEST", "Inflation HICP (zone euro, a/a)", "pc1", "%"),
    ("LRHUTTTTEZM156S", "Taux de chômage (zone euro)", "lin", "%"),
]

RELEASES = {
    "Employment Situation": "Emploi US (NFP, chômage)",
    "Consumer Price Index": "Inflation CPI",
    "Producer Price Index": "Prix à la production PPI",
    "Personal Income and Outlays": "Revenus et dépenses (PCE)",
    "Gross Domestic Product": "PIB",
    "Advance Monthly Sales for Retail and Food Services": "Ventes au détail",
    "Unemployment Insurance Weekly Claims Report": "Inscriptions hebdo. au chômage",
    "Job Openings and Labor Turnover Survey": "Offres d'emploi JOLTS",
    "Industrial Production and Capacity Utilization": "Production industrielle",
    "New Residential Construction": "Mises en chantier",
    "Surveys of Consumers": "Confiance U. Michigan",
}

# Calendrier FOMC 2026 publié par la Fed (deuxième jour = jour de la décision).
FOMC_FALLBACK = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
                 "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _fred_full(sid, units):
    """Deux dernières observations avec leur date de publication (realtime_start = date de la
    dernière publication ou révision de la valeur) et la fréquence de la série."""
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": sid, "api_key": KEY, "file_type": "json", "sort_order": "desc", "limit": 2, "units": units,
        "observation_end": TODAY.isoformat()}, timeout=25)
    r.raise_for_status()
    obs = [o for o in r.json()["observations"] if o["value"] != "."]
    m = requests.get("https://api.stlouisfed.org/fred/series", params={"series_id": sid, "api_key": KEY, "file_type": "json"}, timeout=25)
    freq = (m.json().get("seriess") or [{}])[0].get("frequency_short", "") if m.ok else ""
    return obs, freq


def eco_prints():
    out = []
    for sid, label, units, fmt in PRINTS:
        res = safe(lambda: _fred_full(sid, units), sid)
        if not res or not res[0]:
            continue
        obs, freq = res
        o, prev = obs[0], (float(obs[1]["value"]) if len(obs) > 1 else None)
        out.append({"k": label, "v": round(float(o["value"]), 2), "prev": round(prev, 2) if prev is not None else None,
                    "fmt": fmt, "ts": o["date"], "pub": o.get("realtime_start"), "freq": freq,
                    "src": f"FRED {sid}", "url": f"https://fred.stlouisfed.org/series/{sid}"})
    log(f"ECO : {len(out)}/{len(PRINTS)} indicateurs")
    return out


def fomc_dates():
    """Dates FOMC lues sur federalreserve.gov ; à défaut, calendrier 2026 publié."""
    try:
        html = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                            headers=UA, timeout=25).text
        out = []
        for year, block in re.findall(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)", html, re.S):
            months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?'
                                r'fomc-meeting__date[^>]*>([\d\-–*]+)', block, re.S)
            for mon, days in months:
                mon = mon.split("/")[-1][:3]
                day = re.findall(r"\d+", days)[-1]
                m = list(calendar.month_abbr).index(mon.title())
                out.append(date(int(year), m, int(day)).isoformat())
        if len(out) >= 8:
            return sorted(set(out))
    except Exception as e:
        log(f"  ! calendrier FOMC : {e}")
    return FOMC_FALLBACK


def eco_calendar(fomc):
    cal = []
    if KEY:
        try:
            r = requests.get("https://api.stlouisfed.org/fred/releases/dates", params={
                "api_key": KEY, "file_type": "json", "include_release_dates_with_no_data": "true",
                "realtime_start": (TODAY - timedelta(days=7)).isoformat(),
                "realtime_end": (TODAY + timedelta(days=28)).isoformat(),
                "sort_order": "asc", "limit": 1000}, timeout=25)
            r.raise_for_status()
            seen = set()
            for x in r.json().get("release_dates", []):
                name = x.get("release_name", "")
                fr = next((v for k, v in RELEASES.items() if name.startswith(k)), None)
                if fr and (fr, x["date"]) not in seen:
                    seen.add((fr, x["date"]))
                    cal.append({"d": x["date"], "ev": fr, "ctry": "US", "src": "FRED"})
        except Exception as e:
            log(f"  ! calendrier FRED : {e}")
    for d in fomc:
        if TODAY - timedelta(days=7) <= date.fromisoformat(d) <= TODAY + timedelta(days=60):
            cal.append({"d": d, "ev": "Décision de la Fed (FOMC)", "ctry": "US", "src": "Federal Reserve", "imp": 3})
    cal.sort(key=lambda x: x["d"])
    log(f"ECO : {len(cal)} publications au calendrier")
    return cal


# ─────────────────────────────────────────────────────────────────────────────
# ECO — consensus : flux public de Forex Factory (cette semaine et la suivante)
#   Le consensus (« forecast ») est une compilation de Forex Factory, proche des sondages
#   Bloomberg / Reuters sans être identique. Le chiffre publié vient de FRED (source officielle).
# ─────────────────────────────────────────────────────────────────────────────
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
FF_COUNTRIES = {"USD": "US", "EUR": "Zone euro", "GBP": "R.-U.", "JPY": "Japon", "CHF": "Suisse", "CNY": "Chine"}
# Titre Forex Factory (USD) → (série FRED, unités FRED, conversion, suffixe affiché comme chez FF)
FF_TO_FRED = {
    "Non-Farm Employment Change": ("PAYEMS", "chg", 1, "K"),
    "Unemployment Rate": ("UNRATE", "lin", 1, "%"),
    "CPI m/m": ("CPIAUCSL", "pch", 1, "%"),
    "CPI y/y": ("CPIAUCSL", "pc1", 1, "%"),
    "Core CPI m/m": ("CPILFESL", "pch", 1, "%"),
    "Core PCE Price Index m/m": ("PCEPILFE", "pch", 1, "%"),
    "PPI m/m": ("PPIFIS", "pch", 1, "%"),
    "Retail Sales m/m": ("RSAFS", "pch", 1, "%"),
    "Unemployment Claims": ("ICSA", "lin", 0.001, "K"),
    "Advance GDP q/q": ("A191RL1Q225SBEA", "lin", 1, "%"),
    "Prelim GDP q/q": ("A191RL1Q225SBEA", "lin", 1, "%"),
    "Final GDP q/q": ("A191RL1Q225SBEA", "lin", 1, "%"),
    "Prelim UoM Consumer Sentiment": ("UMCSENT", "lin", 1, ""),
    "Revised UoM Consumer Sentiment": ("UMCSENT", "lin", 1, ""),
    "JOLTS Job Openings": ("JTSJOL", "lin", 0.001, "M"),
    "Federal Funds Rate": ("DFEDTARU", "lin", 1, "%"),
}
TITLE_FR = {
    "Non-Farm Employment Change": "Créations d'emplois NFP", "Unemployment Rate": "Taux de chômage",
    "Unemployment Claims": "Inscriptions hebdo. au chômage", "Retail Sales m/m": "Ventes au détail m/m",
    "Federal Funds Rate": "Décision de la Fed", "Main Refinancing Rate": "Décision de la BCE (refi)",
    "Official Bank Rate": "Décision de la BoE", "BOJ Policy Rate": "Décision de la BoJ",
    "SNB Policy Rate": "Décision de la BNS", "FOMC Statement": "Communiqué de la Fed",
    "FOMC Meeting Minutes": "Compte rendu du FOMC", "Monetary Policy Statement": "Communiqué de politique monétaire",
    "JOLTS Job Openings": "Offres d'emploi JOLTS", "Prelim UoM Consumer Sentiment": "Confiance U. Michigan (prélim.)",
    "Revised UoM Consumer Sentiment": "Confiance U. Michigan (révisée)", "ISM Manufacturing PMI": "ISM manufacturier",
    "ISM Services PMI": "ISM services", "German Prelim CPI m/m": "Inflation Allemagne (prélim.) m/m",
    "Flash CPI y/y": "Inflation zone euro (flash) a/a", "Core CPI Flash Estimate y/y": "Inflation core zone euro (flash) a/a",
}


def _num(s):
    """« 150K » → 150 ; « -0.2% » → -0.2 ; « 7.25M » → 7.25. None si non numérique."""
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group()) if m else None


def _fred_asof(sid, units, day):
    """Dernière observation connue à une date donnée (vintage ALFRED) : (date de référence, valeur)."""
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": sid, "api_key": KEY, "file_type": "json", "units": units, "sort_order": "desc",
        "limit": 1, "realtime_start": day, "realtime_end": day}, timeout=25)
    r.raise_for_status()
    o = next((o for o in r.json()["observations"] if o["value"] != "."), None)
    return (o["date"], float(o["value"])) if o else None


def _actual(title, when):
    """Chiffre publié le jour de l'événement : la valeur que FRED ajoute ce jour-là, sinon None."""
    sid, units, mult, suf = FF_TO_FRED[title]
    day = when.date()
    if when > NOW:
        return None                       # pas encore publié
    now = _fred_asof(sid, units, day.isoformat())
    before = _fred_asof(sid, units, (day - timedelta(days=1)).isoformat())
    if not now or (before and before[0] == now[0] and title != "Federal Funds Rate"):
        return None                       # pas encore de nouvelle observation dans FRED
    v = now[1] * mult
    return f"{v:,.0f}{suf}".replace(",", " ") if suf in ("K",) else f"{v:.2f}{suf}" if suf == "M" else f"{v:.1f}{suf}"


# Chiffres publiés hors FRED : sources officielles gratuites (BCE, BoE, BNS).
#   (devise, titre FF) → (fonction de lecture, suffixe, nombre de décimales, type)
#   type « m » : statistique mensuelle (le chiffre du mois précédent doit être paru) ; « r » : décision de taux.
def _ecb_last(key):
    r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{key}",
                     params={"lastNObservations": 2, "format": "csvdata"}, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"])
    return str(df["TIME_PERIOD"].iloc[-1]), float(df["OBS_VALUE"].iloc[-1])


def _boe_last(code):
    r = requests.get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
                     params={"csv.x": "yes", "Datefrom": (TODAY - timedelta(days=20)).strftime("%d/%b/%Y"),
                             "Dateto": "now", "SeriesCodes": code, "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text)).dropna(subset=[code])
    d = pd.to_datetime(df[df.columns[0]], dayfirst=True).dt.strftime("%Y-%m-%d").iloc[-1]
    return d, float(df[code].iloc[-1])


def _snb_last(cube, dim):
    r = requests.get(f"https://data.snb.ch/api/cube/{cube}/data/csv/en",
                     params={"fromDate": (TODAY - timedelta(days=400)).isoformat()}, headers=UA, timeout=30)
    r.raise_for_status()
    obs = sorted((d, float(v)) for d, k, v in
                 re.findall(r'"?(\d{4}-\d{2}(?:-\d{2})?)"?;"?([A-Za-z0-9_]+)"?;"?(-?\d+\.?\d*)"?', r.text) if k == dim)
    return obs[-1]


OTHER_ACTUAL = {
    ("EUR", "Flash CPI y/y"): (lambda: _ecb_last("ICP/M.U2.N.000000.4.ANR"), "%", 1, "m"),
    ("EUR", "CPI Flash Estimate y/y"): (lambda: _ecb_last("ICP/M.U2.N.000000.4.ANR"), "%", 1, "m"),
    ("EUR", "Core CPI Flash Estimate y/y"): (lambda: _ecb_last("ICP/M.U2.N.XEF000.4.ANR"), "%", 1, "m"),
    ("GBP", "Official Bank Rate"): (lambda: _boe_last("IUDBEDR"), "%", 2, "r"),
    ("CHF", "SNB Policy Rate"): (lambda: _snb_last("snbgwdzid", "LZ"), "%", 2, "r"),
}


def _actual_other(cur, title, when):
    fn, suf, dec, kind = OTHER_ACTUAL[(cur, title)]
    if when > NOW:
        return None
    period, v = fn()
    if kind == "m":
        ref = (when.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")   # un flash publié en novembre couvre octobre
        if str(period)[:7] < ref:
            return None                   # la nouvelle valeur n'est pas encore dans la base officielle
    elif str(period)[:10] < when.date().isoformat():   # le taux doit être daté du jour de la décision
        return None
    return f"{v:.{dec}f}{suf}"


def eco_consensus():
    rows, seen = [], set()
    for url in FF_URLS:
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code != 200:
                continue
            for e in r.json():
                cur, imp = e.get("country"), e.get("impact")
                if cur not in FF_COUNTRIES or imp not in ("High", "Medium"):
                    continue
                key = (e.get("title"), e.get("date"), cur)
                if key in seen:
                    continue
                seen.add(key)
                when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
                title = e.get("title", "")
                actual = None
                covered = (cur == "USD" and title in FF_TO_FRED and bool(KEY)) or (cur, title) in OTHER_ACTUAL
                if cur == "USD" and title in FF_TO_FRED and KEY:
                    actual = safe(lambda: _actual(title, when), f"publié {title}")
                elif (cur, title) in OTHER_ACTUAL:
                    actual = safe(lambda: _actual_other(cur, title, when), f"publié {title}")
                f, a = _num(e.get("forecast")), _num(actual)
                rows.append({
                    "ts": when.isoformat().replace("+00:00", "Z"),
                    "ctry": FF_COUNTRIES[cur], "cur": cur,
                    "ev": TITLE_FR.get(title, title), "ev_en": title,
                    "imp": 3 if imp == "High" else 2,
                    "cons": e.get("forecast") or None, "prev": e.get("previous") or None,
                    "act": actual, "cov": covered, "surp": round(a - f, 3) if a is not None and f is not None else None,
                })
        except Exception as ex:
            log(f"  ! Forex Factory {url.rsplit('_', 1)[-1]} : {ex}")
    rows.sort(key=lambda x: x["ts"])
    log(f"ECO : {len(rows)} publications avec consensus ({sum(1 for x in rows if x['act'])} avec chiffre publié)")
    return {"rows": rows, "src": "Consensus et précédent : Forex Factory (compilation non officielle, usage personnel). "
                                "Chiffre publié : FRED (BLS, BEA, Fed), BCE, Bank of England et BNS. Les autres publications "
                                "(ISM, PMI S&P Global…) n'ont pas de source gratuite : marquées « non couvert »."}


# ─────────────────────────────────────────────────────────────────────────────
# WIRP — probabilités implicites des décisions de la Fed (méthode CME FedWatch simplifiée)
# ─────────────────────────────────────────────────────────────────────────────
MCODE = "FGHJKMNQUVXZ"


def wirp(fomc):
    effr = safe(lambda: fred("EFFR", 1), "EFFR")
    lo = safe(lambda: fred("DFEDTARL", 1), "DFEDTARL")
    hi = safe(lambda: fred("DFEDTARU", 1), "DFEDTARU")
    if not effr:
        return {"error": "taux effectif (EFFR) indisponible"}
    # Contrats futures fed funds 30 jours, 8 prochains mois : prix = 100 − taux moyen du mois.
    months, syms = [], []
    y, m = TODAY.year, TODAY.month
    for _ in range(14):
        months.append((y, m))
        syms.append(f"ZQ{MCODE[m - 1]}{str(y)[2:]}.CBT")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    px = {}
    try:
        df = yf.download(syms, period="5d", interval="1d", group_by="ticker", progress=False, threads=True)
        for s in syms:
            try:
                c = df[s]["Close"].dropna()
                if len(c):
                    px[s] = float(c.iloc[-1])
            except Exception:
                pass
    except Exception as e:
        log(f"  ! futures fed funds : {e}")
    implied = {mm: round(100 - px[s], 4) for mm, s in zip(months, syms) if s in px}
    log(f"WIRP : {len(implied)}/{len(syms)} contrats cotés")
    if len(implied) < 3:
        return {"error": "contrats futures fed funds indisponibles chez Yahoo", "effr": effr[0][1],
                "range": [lo[0][1] if lo else None, hi[0][1] if hi else None]}

    rate = effr[0][1]
    rows = []
    for d in fomc:
        md = date.fromisoformat(d)
        if md <= TODAY or (md.year, md.month) not in implied:
            continue
        n = calendar.monthrange(md.year, md.month)[1]
        frac = md.day / n
        avg = implied[(md.year, md.month)]
        nxt = (md.year + (md.month == 12), md.month % 12 + 1)
        # Réunion en fin de mois : le contrat du mois suivant donne directement le taux post-réunion.
        post = implied.get(nxt) if frac > 0.75 and nxt in implied else (avg - frac * rate) / (1 - frac)
        move = post - rate
        rows.append({"d": d, "pre": round(rate, 3), "post": round(post, 3),
                     "move_bp": round(move * 100, 1),
                     "p_cut": round(max(0, min(1, -move / 0.25)) * 100) if move < 0 else 0,
                     "p_hike": round(max(0, min(1, move / 0.25)) * 100) if move > 0 else 0})
        rate = post
    return {"effr": effr[0][1], "effr_ts": effr[0][0],
            "range": [lo[0][1] if lo else None, hi[0][1] if hi else None],
            "meetings": rows,
            "src": "Futures fed funds 30 jours CBOT (Yahoo) + EFFR (FRED) — méthode FedWatch simplifiée"}


# ─────────────────────────────────────────────────────────────────────────────
# GC — courbes des taux
# ─────────────────────────────────────────────────────────────────────────────
US_TENORS = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1A", "DGS1"), ("2A", "DGS2"),
             ("3A", "DGS3"), ("5A", "DGS5"), ("7A", "DGS7"), ("10A", "DGS10"), ("20A", "DGS20"), ("30A", "DGS30")]
EU_TENORS = [("3M", "SR_3M"), ("6M", "SR_6M"), ("1A", "SR_1Y"), ("2A", "SR_2Y"), ("3A", "SR_3Y"),
             ("5A", "SR_5Y"), ("7A", "SR_7Y"), ("10A", "SR_10Y"), ("20A", "SR_20Y"), ("30A", "SR_30Y")]


def curve_us():
    pts = []
    month_ago = (TODAY - timedelta(days=30)).isoformat()
    for t, sid in US_TENORS:
        obs = safe(lambda: fred(sid, 40), sid)
        if not obs:
            pts.append({"t": t, "y": None})
            continue
        old = next((v for d, v in obs if d <= month_ago), None)
        pts.append({"t": t, "y": obs[0][1], "y1m": old, "ts": obs[0][0]})
    log(f"GC : courbe US {sum(1 for p in pts if p['y'] is not None)}/{len(pts)}")
    return {"pts": pts, "src": "Trésor US via FRED (taux constant maturity)"}


def curve_eu():
    pts = []
    for t, code in EU_TENORS:
        try:
            r = requests.get(f"https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{code}",
                             params={"lastNObservations": 25, "format": "csvdata"}, headers=UA, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"]).sort_values("TIME_PERIOD")
            pts.append({"t": t, "y": round(float(df["OBS_VALUE"].iloc[-1]), 3),
                        "y1m": round(float(df["OBS_VALUE"].iloc[0]), 3), "ts": df["TIME_PERIOD"].iloc[-1]})
        except Exception as e:
            log(f"  ! BCE {code} : {e}")
            pts.append({"t": t, "y": None})
    log(f"GC : courbe euro {sum(1 for p in pts if p['y'] is not None)}/{len(pts)}")
    return {"pts": pts, "src": "BCE — courbe des emprunts d'État AAA de la zone euro (taux zéro-coupon)"}


# ─────────────────────────────────────────────────────────────────────────────
# BTMM — tableau de bord monétaire US
# ─────────────────────────────────────────────────────────────────────────────
BTMM_SERIES = [
    ("DFEDTARU", "Fed funds — borne haute", "%"), ("DFEDTARL", "Fed funds — borne basse", "%"),
    ("EFFR", "Taux effectif des fed funds (EFFR)", "%"), ("SOFR", "SOFR", "%"),
    ("IORB", "Taux sur les réserves (IORB)", "%"), ("DTB3", "T-bill 3 mois", "%"),
    ("DTB6", "T-bill 6 mois", "%"), ("DGS2", "Trésor 2 ans", "%"), ("DGS10", "Trésor 10 ans", "%"),
    ("T10Y2Y", "Pente 2s10s", "pb"), ("T10Y3M", "Pente 3m10a", "pb"),
    ("RRPONTSYD", "Reverse repo de la Fed (Md$)", "Md"), ("WALCL", "Bilan de la Fed (Md$)", "Mdm"),
]


def btmm():
    out = []
    for sid, label, fmt in BTMM_SERIES:
        obs = safe(lambda: fred(sid, 6), sid)
        if not obs:
            continue
        v, d = obs[0][1], obs[0][0]
        prev = obs[1][1] if len(obs) > 1 else None
        if fmt == "pb":                      # FRED publie les pentes en points de %
            v, prev = v * 100, (prev * 100 if prev is not None else None)
        if fmt == "Mdm":                     # WALCL est en millions
            v, prev = v / 1000, (prev / 1000 if prev is not None else None)
        out.append({"k": label, "v": round(v, 3), "prev": round(prev, 3) if prev is not None else None,
                    "fmt": "Md" if fmt == "Mdm" else fmt, "ts": d, "src": f"FRED {sid}"})
    log(f"BTMM : {len(out)}/{len(BTMM_SERIES)} séries")
    return out


def main():
    if not KEY:
        log("Pas de FRED_API_KEY : onglet Macro non actualisé")
        return 0
    fomc = fomc_dates()
    data = {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "eco": {"prints": eco_prints(), "calendar": eco_calendar(fomc),
                "consensus": safe(eco_consensus, "consensus") or {"rows": [], "src": ""}},
        "wirp": safe(lambda: wirp(fomc), "WIRP") or {"error": "calcul impossible"},
        "gc": {"us": curve_us(), "eu": curve_eu()},
        "btmm": btmm(),
        "fomc": fomc,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"macro.json écrit ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
