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
import time
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
    ("CPIAUCSL", "CPI inflation (US, y/y)", "pc1", "%"),
    ("CPILFESL", "Core CPI inflation (US, y/y)", "pc1", "%"),
    ("PCEPILFE", "Core PCE (US, y/y) — Fed target", "pc1", "%"),
    ("PAYEMS", "Nonfarm payrolls (US, K)", "chg", "k"),
    ("UNRATE", "Unemployment rate (US)", "lin", "%"),
    ("ICSA", "Weekly jobless claims (US)", "lin", "n"),
    ("A191RL1Q225SBEA", "Real GDP (US, q/q annualised)", "lin", "%"),
    ("RSAFS", "Retail sales (US, m/m)", "pch", "%"),
    ("UMCSENT", "U. Michigan consumer sentiment", "lin", "n"),
]

# Zone euro : Eurostat publie le jour même (inflation flash, chômage) ; les séries FRED reprises de l'OCDE
# avaient un mois de retard (inflation) ou étaient arrêtées (chômage, figé à janvier 2023).
ESTAT_PRINTS = [
    ("HICP inflation (euro area, y/y)", "%", [("estat", "prc_hicp_minr", "geo=EA20&coicop18=TOTAL&unit=RCH_A"), ("estat", "prc_hicp_manr", "geo=EA20&coicop=CP00"),
                                              ("estat", "teicp000", "geo=EA20&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.U2.N.000000.4.ANR")]),
    ("Unemployment rate (euro area)", "%", [("estat", "une_rt_m", "geo=EA20&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), ("ecb", "LFSI/M.I9.S.UNEHRT.TOTAL0.15_74.T")]),
]

RELEASES = {
    "Employment Situation": "US jobs report (NFP, unemployment)",
    "Consumer Price Index": "CPI inflation",
    "Producer Price Index": "PPI producer prices",
    "Personal Income and Outlays": "Personal income and outlays (PCE)",
    "Gross Domestic Product": "GDP",
    "Advance Monthly Sales for Retail and Food Services": "Retail sales",
    "Unemployment Insurance Weekly Claims Report": "Weekly jobless claims",
    "Job Openings and Labor Turnover Survey": "JOLTS job openings",
    "Industrial Production and Capacity Utilization": "Industrial production",
    "New Residential Construction": "Housing starts",
    "Surveys of Consumers": "U. Michigan sentiment",
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
    # Toutes les sources sont lues et la plus récente l'emporte (prc_hicp_manr répond encore mais s'arrête à décembre 2025)
    for label, fmt, cands in ESTAT_PRINTS:
        best = None
        for kind, ds, *flt in cands:
            try:
                if kind == "ecb":
                    r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{ds}",
                                     params={"lastNObservations": 2, "format": "csvdata"}, headers=UA, timeout=30)
                    r.raise_for_status()
                    df = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"])
                    ser = [(str(a), float(b)) for a, b in zip(df["TIME_PERIOD"], df["OBS_VALUE"])]
                    src, url = f"BCE {ds.split('/')[0]}", f"https://data.ecb.europa.eu/data/datasets/{ds.split('/')[0]}"
                else:
                    q = dict(x.split("=", 1) for x in flt[0].split("&"))
                    r = requests.get(f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ds}",
                                     params={"format": "JSON", "lang": "EN", "lastTimePeriod": 2, **q}, headers=UA, timeout=30)
                    r.raise_for_status()
                    ser = list(_jsonstat_series(r.json()))
                    src, url = f"Eurostat {ds}", f"https://ec.europa.eu/eurostat/databrowser/view/{ds}/default/table"
                ser = sorted((per.replace("M", "-")[:7], v) for per, v in ser)
                if not ser:
                    raise RuntimeError("série vide")
                if best is None or ser[-1][0] > best[0][-1][0]:
                    best = (ser, src, url)
            except Exception as e:
                log(f"  · {label} via {kind} {ds} : {str(e)[:80]}")
        if best:
            ser, src, url = best
            y, m = ser[-1][0].split("-")[:2]
            out.append({"k": label, "v": round(float(ser[-1][1]), 2), "prev": round(float(ser[-2][1]), 2) if len(ser) > 1 else None,
                        "fmt": fmt, "ts": f"{y}-{int(m):02d}-01", "freq": "M", "src": src, "url": url})
    log(f"ECO : {len(out)}/{len(PRINTS) + len(ESTAT_PRINTS)} indicateurs")
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
            cal.append({"d": d, "ev": "Fed decision (FOMC)", "ctry": "US", "src": "Federal Reserve", "imp": 3})
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
FF_COUNTRIES = {"USD": "US", "EUR": "Euro area", "GBP": "UK", "JPY": "Japan", "CHF": "Switzerland", "CNY": "China"}
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
TITLE_FR = {}   # event names stay in English (Forex Factory title)


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


def _jsonstat_series(d):
    """JSON-stat Eurostat → [(période, valeur)] ; pour toute dimension autre que le temps, 1re modalité retenue."""
    ids, size = d["id"], d["size"]
    tpos = ids.index("time")
    tix = d["dimension"]["time"]["category"]["index"]
    stride = [1] * len(ids)
    for k in range(len(ids) - 2, -1, -1):
        stride[k] = stride[k + 1] * size[k + 1]
    vals = d.get("value") or {}
    out = []
    for per, i in tix.items():
        v = vals.get(str(i * stride[tpos]))
        if v is not None:
            out.append((per, float(v)))
    return sorted(out)


def _estat_last(dataset, filters):
    """Dernière valeur Eurostat (JSON-stat) : (période AAAA-MM, valeur)."""
    q = dict(x.split("=", 1) for x in filters.split("&"))
    r = requests.get(f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}",
                     params={"format": "JSON", "lang": "EN", "lastTimePeriod": 2, **q}, headers=UA, timeout=30)
    r.raise_for_status()
    return _jsonstat_series(r.json())[-1]


def _first(*fns):
    """Première source qui répond : Eurostat publie le jour même, la BCE quelques jours après."""
    last = None
    for fn in fns:
        try:
            return fn()
        except Exception as e:
            last = e
    raise last


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
    ("EUR", "Flash CPI y/y"): (lambda: _first(lambda: _estat_last("prc_hicp_manr", "geo=EA20&coicop=CP00"), lambda: _estat_last("prc_hicp_minr", "geo=EA20&coicop18=TOTAL&unit=RCH_A"), lambda: _ecb_last("ICP/M.U2.N.000000.4.ANR")), "%", 1, "m"),
    ("EUR", "CPI Flash Estimate y/y"): (lambda: _first(lambda: _estat_last("prc_hicp_manr", "geo=EA20&coicop=CP00"), lambda: _estat_last("prc_hicp_minr", "geo=EA20&coicop18=TOTAL&unit=RCH_A"), lambda: _ecb_last("ICP/M.U2.N.000000.4.ANR")), "%", 1, "m"),
    ("EUR", "Core CPI Flash Estimate y/y"): (lambda: _first(lambda: _estat_last("prc_hicp_manr", "geo=EA20&coicop=TOT_X_NRG_FOOD"), lambda: _estat_last("prc_hicp_minr", "geo=EA20&coicop18=TOT_X_NRG_FOOD&unit=RCH_A"), lambda: _ecb_last("ICP/M.U2.N.XEF000.4.ANR")), "%", 1, "m"),
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


# Chiffres publiés par le Bureau of Labor Statistics : disponibles dès 8 h 30 (New York),
# souvent plusieurs heures avant leur reprise par FRED. API publique v1, sans clé (25 requêtes par jour).
BLS = {
    "Non-Farm Employment Change": ("CES0000000001", "diff", "K", 0),
    "Unemployment Rate": ("LNS14000000", "level", "%", 1),
    "Average Hourly Earnings m/m": ("CES0500000003", "pct1", "%", 1),
    "CPI m/m": ("CUSR0000SA0", "pct1", "%", 1),
    "Core CPI m/m": ("CUSR0000SA0L1E", "pct1", "%", 1),
    "CPI y/y": ("CUUR0000SA0", "pct12", "%", 1),
    "PPI m/m": ("WPSFD4", "pct1", "%", 1),
}
_BLS_CACHE = {}


def _bls_series(ids):
    need = [i for i in ids if i not in _BLS_CACHE]
    if need:
        r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", json={"seriesid": need},
                          headers={**UA, "Content-type": "application/json"}, timeout=30)
        r.raise_for_status()
        for sr in (r.json().get("Results") or {}).get("series", []):
            obs = [(f"{x['year']}-{x['period'][1:]}", float(x["value"])) for x in sr.get("data", [])
                   if str(x.get("period", "")).startswith("M") and x["period"] != "M13" and x.get("value") not in ("-", "")]
            _BLS_CACHE[sr["seriesID"]] = sorted(obs)
    return _BLS_CACHE


def _actual_bls(title, when):
    sid, how, suf, dec = BLS[title]
    obs = _bls_series([sid]).get(sid) or []
    if len(obs) < 13:
        return None
    ref = (when.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")   # publication de novembre = chiffre d'octobre
    if obs[-1][0] < ref:
        return None
    v = obs[-1][1]
    if how == "diff":
        v = v - obs[-2][1]
    elif how == "pct1":
        v = (v / obs[-2][1] - 1) * 100
    elif how == "pct12":
        prev = dict(obs).get(f"{int(obs[-1][0][:4]) - 1}{obs[-1][0][4:]}")
        if not prev:
            return None
        v = (v / prev - 1) * 100
    return f"{v:.0f}{suf}" if suf == "K" else f"{v:.{dec}f}{suf}"


def _ff_events(url):
    """Calendrier Forex Factory : JSON, puis XML en secours (même contenu, heures de New York)."""
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code == 200 and r.text.strip().startswith("["):
        return r.json(), "json"
    import xml.etree.ElementTree as ET
    from zoneinfo import ZoneInfo
    rx = requests.get(url.replace(".json", ".xml"), headers=UA, timeout=25)
    rx.raise_for_status()
    out = []
    for ev in ET.fromstring(rx.content).iter("event"):
        g = lambda k: (ev.findtext(k) or "").strip()
        try:
            day = datetime.strptime(g("date"), "%m-%d-%Y")
            tm = g("time")
            t = datetime.strptime(tm, "%I:%M%p").time() if tm and tm[0].isdigit() else datetime.min.time()
            dt = datetime.combine(day.date(), t, tzinfo=ZoneInfo("America/New_York"))
        except Exception:
            continue
        out.append({"title": g("title"), "country": g("country"), "date": dt.isoformat(), "impact": g("impact"),
                    "forecast": g("forecast"), "previous": g("previous")})
    return out, "xml"


def eco_consensus():
    rows, seen, feeds = [], set(), {}
    for url in FF_URLS:
        week = "semaine suivante" if "next" in url else "semaine en cours"
        try:
            events, fmt = _ff_events(url)
            feeds[week] = f"ok ({fmt}, {len(events)} événements)"
            for e in events:
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
                covered = (cur == "USD" and (title in FF_TO_FRED and bool(KEY) or title in BLS)) or (cur, title) in OTHER_ACTUAL
                if cur == "USD" and title in BLS and when <= NOW:
                    actual = safe(lambda: _actual_bls(title, when), f"BLS {title}")
                if actual is None and cur == "USD" and title in FF_TO_FRED and KEY:
                    actual = safe(lambda: _actual(title, when), f"publié {title}")
                elif actual is None and (cur, title) in OTHER_ACTUAL:
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
            feeds[week] = f"indisponible ({str(ex)[:60]})"
            log(f"  ! Forex Factory {week} : {ex}")
    rows.sort(key=lambda x: x["ts"])
    log(f"ECO : {len(rows)} publications avec consensus ({sum(1 for x in rows if x['act'])} avec chiffre publié) · {feeds}")
    return {"rows": rows, "feeds": feeds,
            "src": "Consensus and prior: Forex Factory (unofficial compilation, personal use). "
                   "Actual: Bureau of Labor Statistics (on release), FRED (BEA, Fed), ECB, Bank of England and SNB. "
                   "Other releases (ISM, S&P Global PMI…) have no free source: marked \"not covered\"."}


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
            "src": "CBOT 30-day fed funds futures (Yahoo) + EFFR (FRED) — simplified FedWatch method"}


# ─────────────────────────────────────────────────────────────────────────────
# GC — courbes des taux
# ─────────────────────────────────────────────────────────────────────────────
US_TENORS = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1A", "DGS1"), ("2A", "DGS2"),
             ("3A", "DGS3"), ("5A", "DGS5"), ("7A", "DGS7"), ("10A", "DGS10"), ("20A", "DGS20"), ("30A", "DGS30")]
EU_TENORS = [("3M", "SR_3M"), ("6M", "SR_6M"), ("1A", "SR_1Y"), ("2A", "SR_2Y"), ("3A", "SR_3Y"), ("4A", "SR_4Y"),
             ("5A", "SR_5Y"), ("6A", "SR_6Y"), ("7A", "SR_7Y"), ("8A", "SR_8Y"), ("9A", "SR_9Y"), ("10A", "SR_10Y"),
             ("15A", "SR_15Y"), ("20A", "SR_20Y"), ("25A", "SR_25Y"), ("30A", "SR_30Y")]


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
    src = "US Treasury via FRED (constant maturity, previous close)"
    # Cotations de marché CNBC (différé ~15 min) : FRED publie la veille au soir, l'écart atteignait 30 pb
    # les jours de forte volatilité. La variation sur 1 mois reste calculée sur FRED.
    try:
        syms = {f"US{c}": t for t, c in [("1M", "1M"), ("3M", "3M"), ("6M", "6M"), ("1A", "1Y"), ("2A", "2Y"), ("3A", "3Y"),
                                          ("5A", "5Y"), ("7A", "7Y"), ("10A", "10Y"), ("20A", "20Y"), ("30A", "30Y")]}
        r = requests.get("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol",
                         params={"symbols": "|".join(syms), "requestMethod": "itv", "noform": 1, "partnerId": 2,
                                 "fund": 1, "exthrs": 1, "output": "json"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
        qs = r.json()["FormattedQuoteResult"]["FormattedQuote"]
        live = {}
        for q in (qs if isinstance(qs, list) else [qs]):
            try:
                y = float(str(q.get("last", "")).replace("%", ""))
            except ValueError:
                continue
            if q.get("symbol") in syms and -5 < y < 30:
                live[syms[q["symbol"]]] = (y, str(q.get("last_time") or TODAY.isoformat())[:10])
        for p in pts:
            if p["t"] in live:
                p["y"], p["ts"] = live[p["t"]]
                p["live"] = True
        if live:
            src = "CNBC (US Treasury quotes, ~15 min delay) · 1-month change: FRED"
        log(f"GC : courbe US — {len(live)} maturités en cotation de marché (CNBC)")
    except Exception as e:
        log(f"  ! CNBC courbe US : {str(e)[:90]} — clôture FRED conservée")
    log(f"GC : courbe US {sum(1 for p in pts if p['y'] is not None)}/{len(pts)}")
    return {"pts": pts, "src": src}


def curve_eu():
    """Une seule requête pour toutes les maturités (les appels successifs étaient parfois refusés par la BCE,
    d'où des trous à 6 mois, 3, 7, 10 et 30 ans), avec nouvel essai en cas d'échec."""
    codes = [c for _, c in EU_TENORS]
    df = None
    for attempt in range(3):
        try:
            r = requests.get("https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM." + "+".join(codes),
                             params={"lastNObservations": 25, "format": "csvdata"}, headers=UA, timeout=40)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"])
            break
        except Exception as e:
            log(f"  ! BCE courbe (essai {attempt + 1}) : {str(e)[:90]}")
            time.sleep(3 * (attempt + 1))
    col = next((c for c in (df.columns if df is not None else []) if c in ("DATA_TYPE_FM", "KEY")), None)
    pts = []
    for t, code in EU_TENORS:
        try:
            sub = df[df[col].astype(str).str.endswith(code)] if col == "KEY" else df[df[col] == code]
            sub = sub.sort_values("TIME_PERIOD")
            pts.append({"t": t, "y": round(float(sub["OBS_VALUE"].iloc[-1]), 3),
                        "y1m": round(float(sub["OBS_VALUE"].iloc[0]), 3), "ts": sub["TIME_PERIOD"].iloc[-1]})
        except Exception:
            pts.append({"t": t, "y": None})
    log(f"GC : courbe euro {sum(1 for p in pts if p['y'] is not None)}/{len(pts)}")
    return {"pts": pts, "src": "ECB — euro area AAA government bond curve (zero-coupon yields)"}


# ─────────────────────────────────────────────────────────────────────────────
# BTMM — tableau de bord monétaire US
# ─────────────────────────────────────────────────────────────────────────────
BTMM_SERIES = [
    ("DFEDTARU", "Fed funds — upper bound", "%"), ("DFEDTARL", "Fed funds — lower bound", "%"),
    ("EFFR", "Effective fed funds rate (EFFR)", "%"), ("SOFR", "SOFR", "%"),
    ("IORB", "Interest on reserves (IORB)", "%"), ("DTB3", "3-month T-bill", "%"),
    ("DTB6", "6-month T-bill", "%"), ("DGS2", "2-year Treasury", "%"), ("DGS10", "10-year Treasury", "%"),
    ("T10Y2Y", "2s10s slope", "pb"), ("T10Y3M", "3m10y slope", "pb"),
    ("RRPONTSYD", "Fed reverse repo ($bn)", "Md"), ("WALCL", "Fed balance sheet ($bn)", "Mdm"),
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
