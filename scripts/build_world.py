"""
Sources publiques mondiales du Terminal (gratuites, sans clé) — écrit world.json :

  bis        BRI : taux directeurs d'une quarantaine de banques centrales (carte + tableau CB)
  imf        FMI, Perspectives de l'économie mondiale : prévisions par pays (croissance, inflation, dette, chômage)
  cot        CFTC, rapport COT : positions nettes des spéculateurs sur les principaux contrats à terme
  poly       Polymarket : probabilités cotées sur les grands événements macro et géopolitiques
  natural    USGS (séismes), GDACS (alertes catastrophes), NASA EONET (événements naturels en cours)

Les données lentes (BRI, FMI, COT) ne sont relues qu'une fois par jour ; Polymarket et les catastrophes
à chaque passage. Une source en échec garde sa dernière valeur (marquée « stale ») : rien n'est inventé.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isocodes import ISO2, ISO3  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "world.json"
NOW = datetime.now(timezone.utc)
FORCE = os.getenv("WORLD_FORCE", "").lower() == "true"
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)", "Accept": "application/json, text/csv, */*"}
DAILY = timedelta(hours=20)
RELAY_URL = os.getenv("RELAY_URL", "https://terminal-relais.299z7d8df6.workers.dev").rstrip("/")
RELAY_KEY = os.getenv("RELAY_KEY", "").strip()


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def iso_now():
    return NOW.isoformat().replace("+00:00", "Z")


# ── BRI : taux directeurs (jeu WS_CBPOL, mensuel) ─────────────────────────────
BIS_NAMES = {"XM": "BCE (zone euro)"}


def bis():
    url = "https://stats.bis.org/api/v1/data/WS_CBPOL/M./all"
    r = requests.get(url, params={"startPeriod": f"{NOW.year - 3}-01", "format": "csv"}, headers=UA, timeout=60)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows:
        raise RuntimeError("réponse vide")
    col = lambda name: next((k for k in rows[0] if k.upper().startswith(name)), None)
    c_area, c_t, c_v = col("REF_AREA"), col("TIME_PERIOD"), col("OBS_VALUE")
    by = {}
    for x in rows:
        try:
            v = float(x[c_v])
        except (TypeError, ValueError):
            continue
        area = (x[c_area] or "").split(":")[0].strip()
        by.setdefault(area, []).append((x[c_t][:7], v))
    out = {}
    for area, obs in by.items():
        obs.sort()
        last_d, last_v = obs[-1]
        i = len(obs) - 1
        while i > 0 and abs(obs[i][1] - obs[i - 1][1]) < 1e-9:
            i -= 1
        chg = [obs[i][0], round((obs[i][1] - obs[i - 1][1]) * 100)] if i > 0 else None
        num = ISO2.get(area)
        out[area] = {"v": round(last_v, 3), "d": last_d, "chg": chg, "iso": num, "n": BIS_NAMES.get(area)}
    log(f"BRI : {len(out)} banques centrales")
    return out


# ── FMI : Perspectives de l'économie mondiale (API DataMapper) ─────────────────
IMF_IND = {"gdpg": "NGDP_RPCH", "infl": "PCPIPCH", "debt": "GGXWDG_NGDP", "unemp": "LUR", "ca": "BCA_NGDPD"}


def imf():
    """FMI en direct ; s'il refuse le robot (pare-feu du site), copie DBnomics des mêmes Perspectives (même contenu)."""
    try:
        return imf_direct()
    except Exception as e:
        log(f"  ! FMI direct : {str(e)[:90]} — essai via le relais Cloudflare")
    if RELAY_KEY:
        try:
            return imf_direct(via_relay=True)
        except Exception as e:
            log(f"  ! FMI via relais : {str(e)[:90]} — repli sur DBnomics")
    else:
        log("  Secret RELAY_KEY absent : relais non utilisé, repli sur DBnomics")
    return imf_dbnomics()


def imf_dbnomics():
    years = [str(NOW.year), str(NOW.year + 1)]
    out = {}
    for k, code in IMF_IND.items():
        off = 0
        while True:
            r = requests.get("https://api.db.nomics.world/v22/series/IMF/WEO:latest", headers=UA, timeout=60, params={
                "dimensions": json.dumps({"weo-subject": [code]}), "observations": 1, "limit": 1000, "offset": off})
            r.raise_for_status()
            docs = (r.json().get("series") or {}).get("docs") or []
            for d in docs:
                dims = d.get("dimensions") or {}
                a3 = dims.get("weo-country") or str(d.get("series_code", "")).split(".")[0]
                num = ISO3.get(a3)
                if not num or num < 0:
                    continue
                per = dict(zip(d.get("period") or [], d.get("value") or []))
                vals = {y: round(float(per[y]), 2) for y in years if isinstance(per.get(y), (int, float))}
                if vals:
                    out.setdefault(str(num), {})[k] = vals
            if len(docs) < 1000:
                break
            off += 1000
        time.sleep(0.5)
    if not out:
        raise RuntimeError("DBnomics : aucune prévision")
    log(f"FMI via DBnomics : {len(out)} pays")
    return {"years": years, "items": out, "via": "DBnomics"}


def imf_direct(via_relay=False):
    years = [str(NOW.year), str(NOW.year + 1)]
    out = {}
    for k, code in IMF_IND.items():
        if via_relay:
            r = requests.get(f"{RELAY_URL}/imf", params={"code": code, "periods": ",".join(years)},
                             headers={**UA, "X-Relay-Key": RELAY_KEY}, timeout=40)
        else:
            r = requests.get(f"https://www.imf.org/external/datamapper/api/v1/{code}", params={"periods": ",".join(years)},
                             headers=UA, timeout=40)
        r.raise_for_status()
        vals = (r.json().get("values") or {}).get(code) or {}
        for a3, per in vals.items():
            num = ISO3.get(a3)
            if not num or num < 0:
                continue
            it = out.setdefault(str(num), {})
            it[k] = {y: round(float(per[y]), 2) for y in years if per.get(y) is not None}
        time.sleep(0.5)
    if not out:
        raise RuntimeError("réponse vide")
    log(f"FMI{' via le relais' if via_relay else ''} : {len(out)} pays, années {years}")
    return {"years": years, "items": out, "via": "IMF (Cloudflare relay)" if via_relay else "IMF"}


# ── CFTC : rapport COT (positions des non-commerciaux, contrats à terme seuls) ─
COT = [("067651", "WTI crude", "Energy"), ("023651", "Natural gas", "Energy"), ("088691", "Gold", "Metals"),
       ("084691", "Silver", "Metals"), ("085692", "Copper", "Metals"), ("099741", "Euro", "Currencies"),
       ("097741", "Yen", "Currencies"), ("096742", "Pound sterling", "Currencies"), ("092741", "Swiss franc", "Currencies"),
       ("043602", "10-year T-Note", "Rates"), ("020601", "30-year T-Bond", "Rates"), ("13874A", "S&P 500 E-mini", "Equities"),
       ("209742", "Nasdaq 100 E-mini", "Equities"), ("133741", "Bitcoin", "Crypto")]


def cot():
    codes = ",".join(f"'{c}'" for c, _, _ in COT)
    r = requests.get("https://publicreporting.cftc.gov/resource/6dca-aqww.json", headers=UA, timeout=60, params={
        "$select": "cftc_contract_market_code,report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                   "noncomm_positions_short_all,open_interest_all",
        "$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd > '{(NOW - timedelta(days=3 * 366)).date()}'",
        "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 5000})
    r.raise_for_status()
    by = {}
    for x in r.json():
        try:
            net = float(x["noncomm_positions_long_all"]) - float(x["noncomm_positions_short_all"])
            by.setdefault(x["cftc_contract_market_code"], []).append((x["report_date_as_yyyy_mm_dd"][:10], net, float(x["open_interest_all"])))
        except (KeyError, ValueError, TypeError):
            continue
    out = []
    for code, name, grp in COT:
        h = sorted(by.get(code, []))
        if len(h) < 10:
            continue
        nets = [v for _, v, _ in h]
        last, prev = h[-1], h[-2]
        lo, hi = min(nets), max(nets)
        out.append({"code": code, "n": name, "g": grp, "d": last[0], "net": round(last[1]), "chg": round(last[1] - prev[1]),
                    "oi": round(last[2]), "pct_oi": round(last[1] / last[2] * 100, 1) if last[2] else None,
                    "rank": round((last[1] - lo) / (hi - lo) * 100) if hi > lo else 50,
                    "h": [[d, round(v)] for d, v, _ in h[-52:]]})
    log(f"COT : {len(out)}/{len(COT)} contrats")
    return out


# ── Polymarket : marchés prédictifs (macro et géopolitique) ────────────────────
POLY_KEYS = re.compile(r"\b(fed|fomc|interest rate|rate cut|rate hike|ecb|bank of england|boj|inflation|cpi|recession|gdp|"
                       r"unemployment|tariff|treasury|yield|oil|brent|gold|bitcoin|s&p|nasdaq|election|president|prime minister|"
                       r"iran|israel|ukraine|russia|china|taiwan|ceasefire|war|nato|default|shutdown)\b", re.I)


def poly():
    events = []
    for off in (0, 100, 200):
        r = requests.get("https://gamma-api.polymarket.com/events", headers=UA, timeout=30, params={
            "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false", "limit": 100, "offset": off})
        r.raise_for_status()
        events += r.json() or []
        time.sleep(0.3)
    out = []
    for e in events:
        title = e.get("title") or ""
        if not POLY_KEYS.search(title) or re.search(r"\b(nba|nfl|nhl|mlb|ufc|soccer|premier league|tennis|f1|esports|weather)\b", title, re.I):
            continue
        mk = []
        for m in e.get("markets") or []:
            if m.get("closed"):
                continue
            try:
                outc, px = json.loads(m.get("outcomes") or "[]"), [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            except Exception:
                continue
            if not px:
                continue
            yes = px[outc.index("Yes")] if "Yes" in outc else px[0]
            mk.append({"q": m.get("groupItemTitle") or m.get("question") or "", "p": round(yes * 100, 1),
                       "v": round(float(m.get("volume") or 0))})
        if not mk:
            continue
        mk.sort(key=lambda x: -x["p"])
        out.append({"t": title, "u": f"https://polymarket.com/event/{e.get('slug')}", "end": (e.get("endDate") or "")[:10],
                    "v24": round(float(e.get("volume24hr") or 0)), "vol": round(float(e.get("volume") or 0)), "mk": mk[:6]})
    out.sort(key=lambda x: -x["v24"])
    log(f"Polymarket : {len(out)} événements macro / géopolitiques retenus sur {len(events)}")
    return out[:30]


# ── Catastrophes naturelles : USGS, GDACS, NASA EONET ─────────────────────────
def natural():
    out = {"quakes": [], "gdacs": [], "eonet": []}
    try:
        r = requests.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson", headers=UA, timeout=30)
        r.raise_for_status()
        for f in r.json().get("features", []):
            p, g = f.get("properties", {}), f.get("geometry", {}).get("coordinates", [None, None, None])
            if p.get("mag") is None or g[0] is None:
                continue
            out["quakes"].append({"m": round(p["mag"], 1), "t": p.get("place"), "ts": datetime.fromtimestamp(p["time"] / 1000, timezone.utc).isoformat()[:16] + "Z",
                                  "ll": [round(g[0], 2), round(g[1], 2)], "depth": round(g[2] or 0), "u": p.get("url"),
                                  "alert": p.get("alert"), "tsunami": p.get("tsunami")})
        out["quakes"].sort(key=lambda x: -x["m"])
        out["quakes"] = out["quakes"][:120]
    except Exception as e:
        out["err_usgs"] = str(e)[:100]
        log(f"  ! USGS : {e}")
    try:
        # API de recherche (la route MAP répondait 400) ; repli sur le flux RSS officiel
        cut = (NOW - timedelta(days=14)).date().isoformat()
        try:
            r = requests.get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH", headers=UA, timeout=40, params={
                "eventlist": "TC;FL;VO;DR;WF;EQ", "fromDate": cut, "toDate": NOW.date().isoformat(), "alertlevel": "Green;Orange;Red"})
            r.raise_for_status()
            feats = r.json().get("features", [])
        except Exception as e1:
            log(f"  ! GDACS recherche : {str(e1)[:80]} — flux RSS")
            import xml.etree.ElementTree as ET
            rx = requests.get("https://www.gdacs.org/xml/rss.xml", headers=UA, timeout=40)
            rx.raise_for_status()
            ns = {"gdacs": "http://www.gdacs.org", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}
            feats = []
            for it in ET.fromstring(rx.content).iter("item"):
                lat, lon = it.findtext("geo:Point/geo:lat", None, ns), it.findtext("geo:Point/geo:long", None, ns)
                if lat is None or lon is None:
                    continue
                feats.append({"geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                              "properties": {"eventtype": it.findtext("gdacs:eventtype", "", ns), "alertlevel": it.findtext("gdacs:alertlevel", "", ns),
                                             "name": it.findtext("title", ""), "country": it.findtext("gdacs:country", "", ns),
                                             "fromdate": it.findtext("gdacs:fromdate", "", ns), "todate": it.findtext("gdacs:todate", "", ns),
                                             "url": {"report": it.findtext("link", "")}}})
        for f in feats:
            p, g = f.get("properties", {}), f.get("geometry", {})
            if g.get("type") != "Point":
                continue
            todate = str(p.get("todate") or p.get("fromdate") or "")[:10]
            if todate and todate[:4].isdigit() and todate < cut:
                continue
            out["gdacs"].append({"type": p.get("eventtype"), "lvl": p.get("alertlevel"), "t": p.get("name") or p.get("eventname"),
                                 "c": p.get("country"), "ll": [round(g["coordinates"][0], 2), round(g["coordinates"][1], 2)],
                                 "from": str(p.get("fromdate") or "")[:10], "to": todate,
                                 "u": (p.get("url") or {}).get("report") if isinstance(p.get("url"), dict) else None})
    except Exception as e:
        out["err_gdacs"] = str(e)[:100]
        log(f"  ! GDACS : {e}")
    try:
        r = requests.get("https://eonet.gsfc.nasa.gov/api/v3/events", params={"status": "open", "days": 30, "limit": 300}, headers=UA, timeout=40)
        r.raise_for_status()
        for e in r.json().get("events", []):
            geo = (e.get("geometry") or [{}])[-1]
            c = geo.get("coordinates")
            if geo.get("type") != "Point" or not c:
                continue
            out["eonet"].append({"cat": ((e.get("categories") or [{}])[0]).get("title"), "t": e.get("title"),
                                 "ll": [round(c[0], 2), round(c[1], 2)], "d": str(geo.get("date") or "")[:10],
                                 "u": ((e.get("sources") or [{}])[0]).get("url")})
    except Exception as e:
        out["err_eonet"] = str(e)[:100]
        log(f"  ! NASA EONET : {e}")
    log(f"Catastrophes : {len(out['quakes'])} séismes, {len(out['gdacs'])} alertes GDACS, {len(out['eonet'])} événements EONET")
    return out


def main() -> int:
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    res = {"generated_at": iso_now()}

    def run(key, fn, daily):
        old, old_ts = prev.get(key), (prev.get("ts") or {}).get(key)
        if daily and not FORCE and old and old_ts and NOW - datetime.fromisoformat(old_ts.replace("Z", "+00:00")) < DAILY:
            res[key] = old
            res.setdefault("ts", {})[key] = old_ts
            return
        try:
            v = fn()
            if not v:
                raise RuntimeError("aucune donnée")
            res[key] = v
            res.setdefault("ts", {})[key] = iso_now()
        except Exception as e:
            log(f"  ! {key} : {str(e)[:120]}")
            res.setdefault("err", {})[key] = str(e)[:120]
            if old is not None:
                res[key] = old
                res.setdefault("ts", {})[key] = old_ts
                res.setdefault("stale", []).append(key)

    run("bis", bis, True)
    run("imf", imf, True)
    run("cot", cot, True)
    run("poly", poly, False)
    run("natural", natural, False)
    OUT.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"world.json : {', '.join(k for k in ('bis', 'imf', 'cot', 'poly', 'natural') if k in res)} ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
