"""
Onglet Carte du Terminal : points chauds géopolitiques (GDELT) et fil de recherche publique (RSS).

GDELT (gdeltproject.org) recense la presse mondiale toutes les 15 minutes, gratuitement.
Pour chaque zone suivie : nombre d'articles sur 24 h, comparé aux 24 h précédentes (tendance),
et les derniers titres. GDELT demande au plus une requête toutes les 5 secondes : ce script
ne tourne donc qu'une fois par heure (GEO_FORCE=true pour forcer).

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

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "geo.json"
NOW = datetime.now(timezone.utc)
FORCE = os.getenv("GEO_FORCE", "").lower() == "true"
REFRESH = timedelta(minutes=55)
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Zones suivies : (clé, nom affiché, requête GDELT, [longitude, latitude])
HOTSPOTS = [
    ("iran", "Iran / Golfe", "(iran OR hormuz OR tehran)", [53, 32]),
    ("ukraine", "Ukraine / Russie", "(ukraine OR kyiv OR donbas)", [31, 49]),
    ("israel", "Israël / Gaza / Liban", "(gaza OR israel OR hezbollah)", [35, 31.5]),
    ("redsea", "Mer Rouge / Yémen", '("red sea" OR houthi OR houthis OR yemen)', [42, 15.5]),
    ("taiwan", "Taïwan / mer de Chine", '(taiwan OR "south china sea")', [121, 23.7]),
    ("venezuela", "Venezuela", "(venezuela OR maduro OR caracas)", [-66, 7]),
    ("korea", "Corée du Nord", '("north korea" OR pyongyang)', [127, 40]),
    ("kashmir", "Inde / Pakistan", '(kashmir OR "india pakistan")', [74, 33]),
    ("sahel", "Sahel", '(sahel OR "burkina faso" OR mali OR niger) (attack OR coup OR jihadist)', [-2, 15]),
]

# Recherche publique : flux RSS officiels. Un flux qui ne répond pas est simplement ignoré
# (il apparaît en échec dans research_sources, lu par l'onglet Diagnostic).
RESEARCH_FEEDS = [
    ("Fed — discours", "Banques centrales", "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("Fed — communiqués", "Banques centrales", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Fed de New York — Liberty Street", "Taux", "https://libertystreeteconomics.newyorkfed.org/feed/"),
    ("BCE — communiqués et discours", "Banques centrales", "https://www.ecb.europa.eu/rss/press.html"),
    ("BCE — blog", "Banques centrales", "https://www.ecb.europa.eu/rss/blog.html"),
    ("Bank of England — discours", "Banques centrales", "https://www.bankofengland.co.uk/rss/speeches"),
    ("Bank of England — publications", "Banques centrales", "https://www.bankofengland.co.uk/rss/publications"),
    ("BRI — discours de banquiers centraux", "Banques centrales", "https://www.bis.org/doclist/cbspeeches.rss"),
    ("BRI — recherche", "Taux", "https://www.bis.org/doclist/reshub_papers.rss"),
    ("FMI — blog", "Macro", "https://www.imf.org/en/Blogs/rss"),
    ("Banque de France — bloc-notes Éco", "Macro", "https://www.banque-france.fr/fr/rss/bloc-notes-eco"),
    ("Bruegel", "Macro", "https://www.bruegel.org/rss.xml"),
    ("CEPR — VoxEU", "Macro", "https://cepr.org/rss/vox-content"),
    ("AIE — actualités", "Pétrole", "https://www.iea.org/rss/news.xml"),
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


def gdelt(params, tries=3):
    for i in range(tries):
        time.sleep(5.5)                       # limite GDELT : 1 requête / 5 s
        r = requests.get(GDELT, params={**params, "format": "json"}, headers=UA, timeout=40)
        if r.status_code == 429:
            time.sleep(10 * (i + 1)); continue
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:                     # GDELT renvoie du texte quand la requête est refusée
            raise RuntimeError(r.text[:120])
    raise RuntimeError("GDELT : limite de débit")


def hotspot(key, name, q, ll):
    q = f"{q} sourcelang:english"
    tl = gdelt({"query": q, "mode": "TimelineVolRaw", "timespan": "2d"})
    data = ((tl.get("timeline") or [{}])[0].get("data")) or []
    cut = (NOW - timedelta(hours=24)).strftime("%Y%m%dT%H%M%S")
    now24 = sum(d.get("value", 0) for d in data if str(d.get("date", "")) >= cut)
    prev24 = sum(d.get("value", 0) for d in data if str(d.get("date", "")) < cut)
    al = gdelt({"query": q, "mode": "ArtList", "timespan": "24h", "maxrecords": 10, "sort": "DateDesc"})
    arts = [{"t": a.get("title", "")[:200], "u": a.get("url"), "d": a.get("domain"), "ts": a.get("seendate")}
            for a in (al.get("articles") or []) if a.get("url", "").startswith("http")]
    trend = None if not prev24 else round((now24 / prev24 - 1) * 100)
    return {"k": key, "n": name, "ll": ll, "n24": now24, "prev24": prev24, "trend": trend, "arts": arts}


def research():
    rows, status = [], {}
    for name, cat, url in RESEARCH_FEEDS:
        try:
            f = feedparser.parse(url, agent="Mozilla/5.0 (terminal-ia)")
            ents = f.entries[:8]
            if not ents:
                raise RuntimeError(f"flux vide (HTTP {getattr(f, 'status', '?')})")
            for e in ents:
                tm = e.get("published_parsed") or e.get("updated_parsed")
                ts = datetime(*tm[:6], tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if tm else None
                title = re.sub(r"\s+", " ", e.get("title", "")).strip()
                tags = [t for t, rx in TAGS.items() if re.search(rx, title, re.I)] or [cat]
                if cat not in tags:
                    tags.append(cat)
                if title and e.get("link", "").startswith("http"):
                    rows.append({"src": name, "t": title[:220], "u": e["link"], "ts": ts, "tags": tags})
            status[name] = "ok"
        except Exception as ex:
            status[name] = str(ex)[:80]
            log(f"  ! Recherche {name} : {status[name]}")
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
    hs, hs_ts = prev.get("hotspots", []), prev.get("hotspots_at")
    fresh = hs_ts and NOW - datetime.fromisoformat(hs_ts.replace("Z", "+00:00")) < REFRESH
    if FORCE or not fresh:
        new, fails = [], 0
        for key, name, q, ll in HOTSPOTS:
            try:
                new.append(hotspot(key, name, q, ll))
                fails = 0
            except Exception as ex:
                fails += 1
                log(f"  ! GDELT {name} : {str(ex)[:100]}")
                old = next((h for h in hs if h.get("k") == key), None)
                if old:
                    new.append({**old, "stale": True})
                if fails >= 3:
                    log("GDELT : trop d'échecs d'affilée, reprise au prochain passage")
                    break
        if new:
            hs, hs_ts = new, NOW.isoformat().replace("+00:00", "Z")
            log(f"Points chauds : {len(new)} zones")
    else:
        log("Points chauds lus il y a moins d'une heure : pas d'actualisation GDELT")
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "hotspots": hs, "hotspots_at": hs_ts,
                               "hotspots_src": "GDELT Project — articles de presse en anglais, 24 h glissantes",
                               "research": res or prev.get("research", []), "research_sources": res_status},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
