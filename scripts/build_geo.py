"""
Onglet Carte du Terminal : points chauds géopolitiques (GDELT) et fil de recherche publique (RSS).

GDELT (gdeltproject.org) recense la presse mondiale toutes les 15 minutes, gratuitement.
Pour chaque zone suivie : nombre d'articles sur 24 h, comparé aux 24 h précédentes (tendance),
et les derniers titres. GDELT demande au plus une requête toutes les 5 secondes : ce script
n'actualise que 4 zones par passage, les plus anciennes d'abord, et s'arrête avant 4 minutes.

Le fil « Recherche » ne reprend que les titres et liens de publications publiques
(banques centrales, institutions, gérants) : jamais le contenu, jamais de note réservée aux clients.

Écrit geo.json : { hotspots: [...], research: [...], research_sources: {...} }
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

import feedparser
import requests
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "geo.json"
NOW = datetime.now(timezone.utc)
FORCE = os.getenv("GEO_FORCE", "").lower() == "true"
T0 = time.monotonic()
BUDGET = 240          # secondes : l'étape du workflow est coupée à 6 min, on s'arrête bien avant
PER_RUN = 4           # zones GDELT actualisées par passage (les plus anciennes d'abord) : cycle complet ~1 h
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Zones suivies : (clé, nom affiché, requête GDELT, [longitude, latitude])
HOTSPOTS = [
    ("iran", "Iran / Gulf", "(iran OR hormuz OR tehran)", [53, 32]),
    ("ukraine", "Ukraine / Russia", "(ukraine OR kyiv OR donbas)", [31, 49]),
    ("israel", "Israel / Gaza / Lebanon", "(gaza OR israel OR hezbollah)", [35, 31.5]),
    ("redsea", "Red Sea / Yemen", '("red sea" OR houthi OR houthis OR yemen)', [42, 15.5]),
    ("taiwan", "Taiwan / South China Sea", '(taiwan OR "south china sea")', [121, 23.7]),
    ("venezuela", "Venezuela", "(venezuela OR maduro OR caracas)", [-66, 7]),
    ("korea", "North Korea", '("north korea" OR pyongyang)', [127, 40]),
    ("kashmir", "India / Pakistan", '(kashmir OR "india pakistan")', [74, 33]),
    ("sahel", "Sahel", '(sahel OR "burkina faso" OR mali OR niger) (attack OR coup OR jihadist)', [-2, 15]),
]

# Recherche publique : flux RSS officiels. Un flux qui ne répond pas est simplement ignoré
# (il apparaît en échec dans research_sources, lu par l'onglet Diagnostic).
RESEARCH_FEEDS = [
    ("Fed — speeches", "Banques centrales", "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("Fed — press releases", "Banques centrales", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("New York Fed — Liberty Street", "Taux", "https://libertystreeteconomics.newyorkfed.org/feed/"),
    ("ECB — press releases and speeches", "Banques centrales", "https://www.ecb.europa.eu/rss/press.html"),
    ("ECB — blog", "Banques centrales", "https://www.ecb.europa.eu/rss/blog.html"),
    ("Bank of England — speeches", "Banques centrales", "https://www.bankofengland.co.uk/rss/speeches"),
    ("Bank of England — publications", "Banques centrales", "https://www.bankofengland.co.uk/rss/publications"),
    ("BIS — central bankers' speeches", "Banques centrales", "https://www.bis.org/doclist/cbspeeches.rss"),
    ("BIS — research", "Taux", "https://www.bis.org/doclist/reshub_papers.rss"),
    ("CEPR — VoxEU", "Macro", "https://cepr.org/rss/vox-content"),
    ("EIA — Today in Energy", "Pétrole", "https://www.eia.gov/rss/todayinenergy.xml"),
]
TAGS = {
    "Taux": r"\b(yield|bond|treasur|rate|curve|taux|oblig)",
    "Actions": r"\b(equit|stock|earnings|action|valuation)",
    "Pétrole": r"\b(oil|crude|opec|brent|energy|pétrole|gas\b|lng)",
    "Banques centrales": r"\b(monetary|central bank|fed\b|fomc|ecb|bce|policy rate|inflation)",
    "Émergents": r"\b(emerging|china|india|brazil|émergent|africa)",
}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def gdelt(params, tries=2):
    for i in range(tries):
        if time.monotonic() - T0 > BUDGET:
            raise RuntimeError("budget de temps atteint")
        time.sleep(5.5)                       # limite GDELT : 1 requête / 5 s
        r = requests.get(GDELT, params={**params, "format": "json"}, headers=UA, timeout=20)
        if r.status_code == 429:
            time.sleep(15); continue
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:                     # GDELT renvoie du texte quand la requête est refusée
            raise RuntimeError(r.text[:120])
    raise RuntimeError("GDELT : limite de débit")


def hotspot(key, name, q, ll):
    q = f"{q} sourcelang:english"
    # Fenêtre explicite de 48 h (le paramètre « timespan=2d » ne renvoyait que les dernières 24 h)
    tl = gdelt({"query": q, "mode": "TimelineVolRaw",
                "startdatetime": (NOW - timedelta(hours=48)).strftime("%Y%m%d%H%M%S"),
                "enddatetime": NOW.strftime("%Y%m%d%H%M%S")})
    data = ((tl.get("timeline") or [{}])[0].get("data")) or []
    if not data:
        raise RuntimeError("chronologie GDELT vide (requête refusée ou limitée)")
    # Fenêtres bornées des deux côtés : GDELT renvoie parfois plus de 48 h de chronologie, ce qui gonflait
    # la veille et donnait des tendances de -60 à -80 % sur toutes les zones.
    cut = (NOW - timedelta(hours=24)).strftime("%Y%m%dT%H%M%S")
    cut48 = (NOW - timedelta(hours=48)).strftime("%Y%m%dT%H%M%S")
    norm = lambda d: re.sub(r"[^0-9T]", "", str(d.get("date", "")))[:15]
    now24 = sum(d.get("value", 0) for d in data if norm(d) >= cut)
    prev24 = sum(d.get("value", 0) for d in data if cut48 <= norm(d) < cut)
    al = gdelt({"query": q, "mode": "ArtList", "timespan": "24h", "maxrecords": 10, "sort": "DateDesc"})
    arts = [{"t": a.get("title", "")[:200], "u": a.get("url"), "d": a.get("domain"), "ts": a.get("seendate")}
            for a in (al.get("articles") or []) if a.get("url", "").startswith("http")]
    # Tendance seulement si la veille compte assez d'articles (sinon +12 000 % sur une base de 1 article)
    trend = None if prev24 < 20 else round((now24 / prev24 - 1) * 100)
    return {"k": key, "n": name, "ll": ll, "n24": now24, "prev24": prev24, "trend": trend, "arts": arts, "m": 2}


def _feed(item):
    """Un flux RSS, avec un délai maximal (feedparser seul peut rester bloqué sur un serveur lent)."""
    name, cat, url = item
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (terminal-ia)"}, timeout=12)
        r.raise_for_status()
        f = feedparser.parse(r.content)
        ents = f.entries[:8]
        if not ents:
            raise RuntimeError("flux vide")
        out = []
        for e in ents:
            tm = e.get("published_parsed") or e.get("updated_parsed")
            ts = datetime(*tm[:6], tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if tm else None
            title = re.sub(r"\s+", " ", e.get("title", "")).strip()
            tags = [t for t, rx in TAGS.items() if re.search(rx, title, re.I)] or [cat]
            if cat not in tags:
                tags.append(cat)
            if title and e.get("link", "").startswith("http"):
                out.append({"src": name, "t": title[:220], "u": e["link"], "ts": ts, "tags": tags})
        return name, out, "ok"
    except Exception as ex:
        return name, [], str(ex)[:80]


def research():
    rows, status = [], {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for name, out, st in ex.map(_feed, RESEARCH_FEEDS):
            rows += out
            status[name] = st
            if st != "ok":
                log(f"  ! Recherche {name} : {st}")
    rows.sort(key=lambda x: x["ts"] or "", reverse=True)
    cutoff = (NOW - timedelta(days=21)).isoformat()
    rows = [r for r in rows if (r["ts"] or "9") >= cutoff][:80]
    log(f"Recherche : {len(rows)} publications, {sum(1 for v in status.values() if v == 'ok')}/{len(RESEARCH_FEEDS)} sources")
    return rows, status


def main() -> int:
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    res, res_status = research()
    hs = {h["k"]: h for h in prev.get("hotspots", []) if h.get("k")}
    # Zones à relire : les plus anciennes d'abord (toutes si lancement manuel et budget suffisant)
    order = sorted(HOTSPOTS, key=lambda z: (hs.get(z[0]) or {}).get("at", ""))
    todo = order if FORCE else order[:PER_RUN]
    done = 0
    for key, name, q, ll in todo:
        if time.monotonic() - T0 > BUDGET:
            log("Budget de temps atteint : les autres zones seront lues au prochain passage")
            break
        try:
            h = hotspot(key, name, q, ll)
            h["at"] = NOW.isoformat().replace("+00:00", "Z")
            h.pop("err", None)
            hs[key] = h
            done += 1
        except Exception as ex:
            log(f"  ! GDELT {name} : {str(ex)[:100]}")
            if key in hs:
                hs[key]["stale"] = True
                hs[key]["err"] = str(ex)[:120]       # raison visible dans geo.json (diagnostic)
            else:
                hs[key] = {"k": key, "n": name, "ll": ll, "n24": 0, "prev24": 0, "trend": None, "arts": [], "stale": True, "err": str(ex)[:120]}
    hs_list = [hs[k] for k, *_ in HOTSPOTS if k in hs]
    hs_ts = max((h.get("at", "") for h in hs_list), default=None) or prev.get("hotspots_at")
    log(f"Points chauds : {done} zones actualisées, {len(hs_list)} au total")
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "hotspots": hs_list, "hotspots_at": hs_ts,
                               "hotspots_src": "GDELT Project — English-language press articles, rolling 24h",
                               "research": res or prev.get("research", []), "research_sources": res_status},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
