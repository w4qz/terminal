"""
Courbes des taux souverains hors US et zone euro AAA (fonction GC et panneau « Taux souverains »).

Sources officielles et gratuites, une par pays (la meilleure disponible sans abonnement) :
  Allemagne  Bundesbank — rendements des titres fédéraux par maturité résiduelle (quotidien)
  Japon      Ministère des Finances — JGB par maturité constante (quotidien)
  Suisse     Banque nationale suisse — rendements des obligations de la Confédération (quotidien)
  Royaume-Uni Bank of England — courbe nominale des gilts 5, 10 et 20 ans (quotidien)
  France, Italie  CNBC — rendements cotés OAT / BTP, 3 mois à 30 ans (repli : 10 ans OCDE mensuel)

Chaque point garde sa valeur du jour et celle d'il y a environ un mois. Une source en échec
laisse le pays vide : rien n'est inventé. Écrit curves.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "curves.json"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
MONTH_AGO = (TODAY - timedelta(days=30)).isoformat()
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}
KEY = os.getenv("FRED_API_KEY", "")
WEBSTAT_KEY = os.getenv("WEBSTAT_API_KEY", "")


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def point(t, series):
    """series : liste [(date ISO, valeur)] triée. → point {t, y, y1m, ts}."""
    series = [(d, v) for d, v in series if v is not None]
    if not series:
        return None
    old = next((v for d, v in reversed(series) if d <= MONTH_AGO), None)
    return {"t": t, "y": round(series[-1][1], 3), "y1m": None if old is None else round(old, 3), "ts": series[-1][0]}


# ── Allemagne : Bundesbank (série BBSIS, rendements par maturité résiduelle) ──
def curve_de():
    pts = []
    for yrs, t in [(1, "1A"), (2, "2A"), (3, "3A"), (5, "5A"), (7, "7A"), (10, "10A"), (15, "15A"), (20, "20A"), (30, "30A")]:
        key = f"D.I.ZST.ZI.EUR.S1311.B.A604.R{yrs:02d}XX.R.A.A._Z._Z.A"
        try:
            r = requests.get(f"https://api.statistiken.bundesbank.de/rest/data/BBSIS/{key}",
                             params={"format": "csv", "lang": "en", "lastNObservations": 40}, headers=UA, timeout=30)
            r.raise_for_status()
            ser = sorted((d, float(v.replace(",", "."))) for d, v in re.findall(r"(\d{4}-\d{2}-\d{2})[\";,\s]+(-?\d+[.,]\d+)", r.text))
            p = point(t, ser)
            if p:
                pts.append(p)
        except Exception as e:
            log(f"  ! Bundesbank {t} : {str(e)[:90]}")
    return pts, "Bundesbank — rendements des titres fédéraux allemands (maturité résiduelle)", "quotidien"


# ── Japon : ministère des Finances (JGB, maturités constantes) ──
def curve_jp():
    """Le fichier historique (jgbcme_all) s'arrête à la fin du mois précédent ; le fichier du mois
    en cours (jgbcme) donne les derniers jours. On fusionne les deux."""
    frames = []
    for url in ("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv",
                "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            lines = r.content.decode("utf-8", "ignore").splitlines()
            h = next(i for i, l in enumerate(lines) if l.lower().startswith("date"))
            df = pd.read_csv(StringIO("\n".join(lines[h:])))
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            df = df.dropna(subset=["Date"])
            frames.append(df)
            log(f"  MOF Japon {url.rsplit('/', 1)[-1]} : {len(df)} lignes, dernière au {df['Date'].max().date()}")
        except Exception as e:
            log(f"  ! MOF Japon ({url.rsplit('/', 1)[-1]}) : {str(e)[:90]}")
    if not frames:
        return [], "", ""
    df = pd.concat(frames).drop_duplicates(subset=["Date"], keep="last").sort_values("Date").tail(60)
    pts = []
    for col in df.columns[1:]:
        m = re.fullmatch(r"(\d+)Y", str(col).strip())
        if not m:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        p = point(f"{m.group(1)}A", [(d.date().isoformat(), None if pd.isna(v) else float(v)) for d, v in zip(df["Date"], vals)])
        if p:
            pts.append(p)
    return pts, "Ministère des Finances du Japon — JGB à maturité constante", "quotidien"


# ── Suisse : Banque nationale suisse (rendements de la Confédération) ──
def curve_ch():
    try:
        r = requests.get("https://data.snb.ch/api/cube/rendoblid/data/csv/en",
                         params={"fromDate": (TODAY - timedelta(days=50)).isoformat()}, headers=UA, timeout=40)
        r.raise_for_status()
        by = {}
        for d, k, v in re.findall(r"\"?(\d{4}-\d{2}(?:-\d{2})?)\"?;\"?(\d+)J\"?;\"?(-?\d+\.?\d*)\"?", r.text):
            by.setdefault(int(k), []).append((d, float(v)))
        pts = [point(f"{k}A", sorted(v)) for k, v in sorted(by.items())]
        return [p for p in pts if p], "Banque nationale suisse — rendements des obligations de la Confédération", "quotidien"
    except Exception as e:
        log(f"  ! BNS : {str(e)[:90]}")
        return [], "", ""


# ── Royaume-Uni : Bank of England (courbe nominale des gilts) ──
def curve_uk():
    series = {"5A": ["IUDSNZC", "IUDSNPY"], "10A": ["IUDMNZC", "IUDMNPY"], "20A": ["IUDLNZC", "IUDLNPY"]}
    codes = [c for v in series.values() for c in v]
    try:
        r = requests.get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
                         params={"csv.x": "yes", "Datefrom": (TODAY - timedelta(days=50)).strftime("%d/%b/%Y"),
                                 "Dateto": "now", "SeriesCodes": ",".join(codes), "CSVF": "TN",
                                 "UsingCodes": "Y", "VPD": "Y", "VFD": "N"}, headers=UA, timeout=40)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        dates = pd.to_datetime(df[df.columns[0]], dayfirst=True).dt.strftime("%Y-%m-%d")
        pts = []
        for t, cands in series.items():
            for c in cands:
                if c in df.columns:
                    vals = pd.to_numeric(df[c], errors="coerce")
                    p = point(t, sorted((d, None if pd.isna(v) else float(v)) for d, v in zip(dates, vals)))
                    if p:
                        pts.append(p)
                        break
        return pts, "Bank of England — courbe nominale des gilts (5, 10 et 20 ans)", "quotidien"
    except Exception as e:
        log(f"  ! BoE : {str(e)[:90]}")
        return [], "", ""


# ── France : Banque de France, taux de l'échéance constante (TEC) des OAT (clé Webstat gratuite) ──
WEBSTAT = "https://webstat.banque-france.fr/api/explore/v2.1/catalog/datasets"


def _ws(path, params):
    r = requests.get(f"{WEBSTAT}/{path}", params=params, headers={**UA, "Authorization": f"Apikey {WEBSTAT_KEY}"}, timeout=40)
    r.raise_for_status()
    return r.json()


def _ws_series(key):
    """Dernières observations d'une série Webstat → [(date, valeur)]. Noms de champs lus de façon tolérante."""
    d = _ws("observations/records", {"where": f'series_key="{key}"', "order_by": "time_period_end desc", "limit": 45})
    out = []
    for rec in d.get("results", []):
        date = next((str(v)[:10] for k, v in rec.items() if "time_period" in k and v), None)
        val = next((v for k, v in rec.items() if k in ("obs_value", "value", "obs_value_num") and v not in (None, "")), None)
        try:
            out.append((date, float(str(val).replace(",", "."))))
        except Exception:
            pass
    return sorted(x for x in out if x[0])


def curve_fr():
    if not WEBSTAT_KEY:
        return curve_fr_it("FR", "IRLTLT01FRM156N")
    # 1) Recherche des séries TEC quotidiennes ; 2) à défaut, clés usuelles
    keys = {}
    try:
        d = _ws("series/records", {"where": 'series_key like "FM.D.FR.EUR.FR2.BB.FR*"', "limit": 100})
        for rec in d.get("results", []):
            k = rec.get("series_key", "")
            m = re.search(r"\.FR(\d{1,2})(M|Y|A)[A-Z_]*\.", k + ".")
            if m and ("TEC" in json.dumps(rec, ensure_ascii=False).upper() or "YT" in k):
                n = int(m.group(1)); t = f"{n}M" if m.group(2) == "M" else f"{n}A"
                keys.setdefault(t, k)
        log(f"  Webstat : {len(keys)} séries TEC trouvées ({', '.join(sorted(keys))})")
    except Exception as e:
        log(f"  ! Webstat recherche : {str(e)[:120]}")
    if not keys:
        for n in (1, 2, 3, 5, 7, 10, 15, 20, 30):
            for k in (f"FM.D.FR.EUR.FR2.BB.FR{n}YT_RR.YLD", f"FM.D.FR.EUR.FR2.BB.FR{n:02d}YT_RR.YLD"):
                keys.setdefault(f"{n}A", k)
    pts = []
    for t, k in sorted(keys.items(), key=lambda x: TEN_ORD_PY.index(x[0]) if x[0] in TEN_ORD_PY else 99):
        try:
            p = point(t, _ws_series(k))
            if p:
                pts.append(p)
        except Exception as e:
            log(f"  ! Webstat {k} : {str(e)[:100]}")
    if pts:
        return pts, "Banque de France (Webstat) — taux de l'échéance constante des OAT", "quotidien"
    log("  Webstat sans résultat : repli sur les cotations CNBC (courbe complète)")
    return curve_fr_it("FR", "IRLTLT01FRM156N")


TEN_ORD_PY = ["1M", "3M", "6M", "1A", "2A", "3A", "4A", "5A", "6A", "7A", "8A", "9A", "10A", "15A", "20A", "25A", "30A", "40A", "50A"]


# ── France, Italie : OCDE via FRED (10 ans, mensuel) ──
def ten_year(sid):
    if not KEY:
        return [], "", ""
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": sid, "api_key": KEY, "file_type": "json", "sort_order": "desc", "limit": 3},
                         timeout=25)
        r.raise_for_status()
        obs = sorted((o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] != ".")
        p = point("10A", obs)
        if p and len(obs) > 1:
            p["y1m"] = round(obs[-2][1], 3)
        return ([p] if p else []), "OCDE via FRED — taux à 10 ans (moyenne mensuelle)", "mensuel"
    except Exception as e:
        log(f"  ! FRED {sid} : {str(e)[:90]}")
        return [], "", ""


# ── France, Italie : cotations CNBC (rendements de marché, différés ~15 min, toutes maturités) ──
# L'OCDE ne publie qu'une MOYENNE MENSUELLE du 10 ans, avec un mois de retard : 4,48 % affiché
# alors que l'OAT cotait bien plus haut. CNBC sert gratuitement les rendements cotés des OAT et BTP.
CNBC_URL = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
CNBC_TENORS = [("1M", "1M"), ("3M", "3M"), ("6M", "6M"), ("1A", "1Y"), ("2A", "2Y"), ("3A", "3Y"), ("4A", "4Y"),
               ("5A", "5Y"), ("7A", "7Y"), ("8A", "8Y"), ("9A", "9Y"), ("10A", "10Y"), ("15A", "15Y"), ("20A", "20Y"),
               ("25A", "25Y"), ("30A", "30Y")]
PREV = {}   # curves.json précédent (historique des points CNBC pour la variation sur 1 mois)


def curve_cnbc(cc):
    syms = {f"{cc}{c}-{cc}": t for t, c in CNBC_TENORS}
    try:
        r = requests.get(CNBC_URL, params={"symbols": "|".join(syms), "requestMethod": "itv", "noform": 1,
                                           "partnerId": 2, "fund": 1, "exthrs": 1, "output": "json"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
        quotes = r.json()["FormattedQuoteResult"]["FormattedQuote"]
        quotes = quotes if isinstance(quotes, list) else [quotes]
    except Exception as e:
        log(f"  ! CNBC {cc} : {str(e)[:100]}")
        return []
    hist = (PREV.get(cc) or {}).get("hist") or {}
    pts = []
    for q in quotes:
        t = syms.get(q.get("symbol"))
        try:
            y = float(str(q.get("last", "")).replace("%", "").replace(",", ""))
        except ValueError:
            continue
        if t is None or not (-5 < y < 30):
            continue
        ts = str(q.get("last_time") or TODAY.isoformat())[:10]
        h = [x for x in hist.get(t, []) if x[0] != ts] + [[ts, y]]
        h = [x for x in h if x[0] >= (TODAY - timedelta(days=45)).isoformat()]
        hist[t] = sorted(h)
        p = point(t, [tuple(x) for x in hist[t]])
        if p:
            p["ts"] = ts
            p["_sym"] = f"{cc}{dict(CNBC_TENORS)[t]}-{cc}"
            pts.append(p)
    pts.sort(key=lambda p: TEN_ORD_PY.index(p["t"]) if p["t"] in TEN_ORD_PY else 99)
    if pts:
        pts[0]["_hist"] = hist
    return pts


def curve_fr_it(cc, oecd_sid):
    pts = curve_cnbc(cc)
    if len(pts) >= 3:
        return pts, "CNBC — rendements cotés des emprunts d'État (différé ~15 min)", "intrajournalier"
    log(f"  CNBC {cc} insuffisant : repli sur le 10 ans OCDE (moyenne mensuelle)")
    return ten_year(oecd_sid)



# ── Historique quotidien des taux à 10 ans (CNBC, 5 ans) pour les séries ECONOMICS:xx10Y ──
# Les séries OCDE sont des moyennes mensuelles : elles lissent les pics (l'OAT à 5 % fin octobre
# n'apparaissait pas). Ce relevé quotidien remplace la partie récente de leur historique.
HIST10 = {"US10Y": "US10Y", "DE10Y": "DE10Y-DE", "FR10Y": "FR10Y-FR", "IT10Y": "IT10Y-IT",
          "GB10Y": "GB10Y-GB", "JP10Y": "JP10Y-JP", "CH10Y": "CH10Y-CH"}


def _bars(obj):
    """Trouve la liste de barres (dicts avec « close ») dans la réponse CNBC, quelle que soit sa profondeur."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "close" in obj[0]:
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            b = _bars(v)
            if b:
                return b
    if isinstance(obj, list):
        for v in obj:
            b = _bars(v)
            if b:
                return b
    return None


def _hist(sym, rng):
    r = requests.get(f"https://ts-api.cnbc.com/harmony/app/charts/{rng}.json", params={"symbol": sym},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    out = {}
    for b in _bars(r.json()) or []:
        t = str(b.get("tradeTime") or "")
        if len(t) >= 8 and t[:8].isdigit():
            d = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
        elif b.get("tradeTimeinMills"):
            d = datetime.fromtimestamp(int(b["tradeTimeinMills"]) / 1000, timezone.utc).date().isoformat()
        else:
            continue
        try:
            v = float(str(b["close"]).replace("%", ""))
        except (TypeError, ValueError):
            continue
        if -5 < v < 30:
            out[d] = round(v, 3)
    return sorted(out.items())


def hist_10y(sym):
    """5 ans en hebdomadaire (CNBC 5Y) complétés par la dernière année en quotidien (CNBC 1Y) : pics visibles et dernier jour à jour."""
    week = dict(_hist(sym, "5Y"))
    try:
        day = dict(_hist(sym, "1Y"))
    except Exception as e:
        log(f"  ! Historique quotidien {sym} : {str(e)[:80]}")
        day = {}
    if day:
        d0 = min(day)
        week = {d: v for d, v in week.items() if d < d0}
    week.update(day)
    return sorted(week.items())


# ── Cohérence des courbes ──
Y1M = {}   # {symbole CNBC : [date du calcul, rendement il y a 1 mois]} : relu une fois par jour seulement


def cnbc_y1m(sym):
    """Rendement coté il y a 1 mois (même instrument que le rendement du jour : la variation est cohérente)."""
    c = Y1M.get(sym)
    if c and c[0] == TODAY.isoformat():
        return c[1]
    try:
        h = _hist(sym, "3M")
        old = next((v for d, v in reversed(h) if d <= MONTH_AGO), None)
    except Exception as e:
        log(f"  ! Historique 1 mois {sym} : {str(e)[:60]}")
        return c[1] if c else None
    Y1M[sym] = [TODAY.isoformat(), old]
    return old


def _yrs(t):
    return int(t[:-1]) / 12 if t.endswith("M") else int(t[:-1])


def clean_curve(name, pts):
    """Retire les points périmés (source arrêtée depuis des semaines) et les cotations aberrantes."""
    newest = max((p["ts"] for p in pts if p.get("ts")), default="")
    if newest:
        lim = (datetime.fromisoformat(newest[:10]) - timedelta(days=14)).date().isoformat()
        old = [p["t"] for p in pts if (p.get("ts") or "")[:10] < lim]
        if old and len(old) < len(pts):
            log(f"  {name} : points périmés retirés ({', '.join(old)}, antérieurs au {lim})")
            pts = [p for p in pts if (p.get("ts") or "")[:10] >= lim]
    out = [p for p in pts if p.get("y") is not None]
    while len(out) >= 3:
        worst, dev = None, 0.35          # écart à l'interpolation de ses deux voisins, en points de %
        for i in range(1, len(out) - 1):
            a, p, b = out[i - 1], out[i], out[i + 1]
            w = (_yrs(p["t"]) - _yrs(a["t"])) / (_yrs(b["t"]) - _yrs(a["t"]))
            d = abs(p["y"] - (a["y"] + w * (b["y"] - a["y"])))
            if d > dev:
                worst, dev = p, d
        if not worst:
            break
        log(f"  {name} : cotation aberrante retirée ({worst['t']} à {worst['y']} %, écart {dev:.2f} pt à ses voisins)")
        out.remove(worst)
    return out


FILL_CNBC = {"DE": "DE", "UK": "GB", "JP": "JP", "CH": "CH", "FR": "FR"}   # FR : seulement quand la courbe vient de Webstat

COUNTRIES = [
    ("DE", "Allemagne", curve_de),
    ("FR", "France", lambda: curve_fr() if WEBSTAT_KEY else curve_fr_it("FR", "IRLTLT01FRM156N")),
    ("IT", "Italie", lambda: curve_fr_it("IT", "IRLTLT01ITM156N")), ("UK", "Royaume-Uni", curve_uk),
    ("JP", "Japon", curve_jp), ("CH", "Suisse", curve_ch),
]


def main() -> int:
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        prev = {}
    PREV.update(prev)
    try:
        Y1M.update(json.loads(OUT.read_text(encoding="utf-8")).get("y1m", {}))
    except Exception:
        pass
    items = {}
    for k, name, fn in COUNTRIES:
        pts, src, freq = fn()
        if k in FILL_CNBC and not (k == "FR" and "Webstat" not in (src or "")):
            # Les sources officielles ne publient que quelques maturités (BoE : 5, 10, 20 ans ; Bundesbank :
            # pas de court terme) : les maturités absentes sont complétées par les rendements cotés CNBC.
            live = curve_cnbc(FILL_CNBC[k])
            # Cotations de marché (différé ~15 min) à la place de la clôture de la veille des sources officielles :
            # la courbe est à jour à chaque passage du robot (10 min). La variation sur 1 mois reste celle de la source officielle.
            by = {c["t"]: c for c in live}
            n_live = 0
            for p in pts:
                c = by.get(p["t"])
                if c and c.get("y") is not None:
                    p["y"], p["ts"], p["live"] = c["y"], c["ts"], True
                    n_live += 1
            if n_live:
                src = f"CNBC (cotations de marché, différé ~15 min) · courbe et variation 1 mois : {src}"
                freq = "intrajournalier"
            have = {p["t"] for p in pts}
            extra = [p for p in live if p["t"] not in have]
            for p in extra:
                p.pop("_hist", None)
                p["cnbc"] = True
            if extra:
                pts = sorted(pts + extra, key=lambda p: TEN_ORD_PY.index(p["t"]) if p["t"] in TEN_ORD_PY else 99)
                src = (src or "CNBC") + f" · {len(extra)} maturités complétées par CNBC"
                log(f"  {name} : {len(extra)} maturités ajoutées depuis CNBC")
        if pts:
            hist = pts[0].pop("_hist", None)
            # Variation sur 1 mois : rendement coté CNBC il y a 1 mois pour tout point coté CNBC
            # (avant : la source officielle pour certains points, rien pour la France et l'Italie)
            cc = FILL_CNBC.get(k, k)
            for p in pts:
                p.pop("_hist", None)
                sym = p.pop("_sym", None)
                if p.get("live") or p.get("cnbc") or sym:
                    sym = sym or f"{cc}{dict(CNBC_TENORS).get(p['t'], '')}-{cc}"
                    if dict(CNBC_TENORS).get(p["t"]):
                        v = cnbc_y1m(sym)
                        if v is not None:
                            p["y1m"] = v
            pts = clean_curve(name, pts)
            items[k] = {"n": name, "pts": pts, "src": src, "freq": freq}
            if hist:
                items[k]["hist"] = hist
            log(f"GC : {name} {len(pts)} points ({', '.join(p['t'] for p in pts)})")
        elif k in prev:
            items[k] = {**prev[k], "stale": True}
            log(f"GC : {name} en échec, dernière courbe conservée")
        else:
            log(f"GC : {name} indisponible")
    try:
        prev_h = json.loads(OUT.read_text(encoding="utf-8")).get("hist10", {})
    except Exception:
        prev_h = {}
    hist10 = {}
    for code, sym in HIST10.items():
        try:
            h = hist_10y(sym)
            if len(h) >= 50:
                hist10[code] = h
                log(f"Historique {code} : {len(h)} points ({h[0][0]} → {h[-1][0]})")
                continue
            log(f"  ! Historique {code} : {len(h)} points seulement")
        except Exception as e:
            log(f"  ! Historique {code} : {str(e)[:100]}")
        if code in prev_h:
            hist10[code] = prev_h[code]
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"), "items": items, "hist10": hist10, "y1m": Y1M},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
