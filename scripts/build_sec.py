"""
CF — dépôts officiels des sociétés américaines auprès de la SEC (EDGAR), gratuits et sans clé.

Pour chaque action américaine du catalogue : derniers rapports annuels (10-K) et trimestriels (10-Q),
annonces importantes (8-K, avec leur motif), déclarations des dirigeants (formulaire 4 : achats
et ventes d'actions par les initiés), franchissements de seuil (13D/13G), introductions (S-1).

La SEC demande un User-Agent qui identifie l'utilisateur (nom et e-mail) et au plus 10 requêtes
par seconde : le contact est lu dans le secret GitHub SEC_CONTACT, jamais écrit dans le code.
Par roulement : 150 sociétés par passage, les plus anciennes d'abord (catalogue complet en ~2 h).

Écrit sec.json : { items: { "AAPL": { ts, cik, name, f4_90d, filings: [[date, forme, libellé, url], ...] } } }
"""
from __future__ import annotations

import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_data import FUND_TICKERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UNIV = ROOT / "universe.json"
OUT = ROOT / "sec.json"
NOW = datetime.now(timezone.utc)
# Les en-têtes HTTP n'acceptent pas toujours les accents : « Clément Guérin » devient « Clement Guerin »
CONTACT = unicodedata.normalize("NFKD", os.getenv("SEC_CONTACT", "").strip()).encode("ascii", "ignore").decode()
UA = {"User-Agent": f"Terminal-IA projet personnel {CONTACT}".strip(), "Accept-Encoding": "gzip, deflate"}
BATCH = 150
BUDGET = 150          # secondes
KEEP = 12             # dépôts conservés par société

FORMS = {"10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Current report",
         "20-F": "Annual report (foreign issuer)", "6-K": "Current report (foreign issuer)", "S-1": "IPO registration",
         "DEF 14A": "Proxy statement", "SC 13D": "Beneficial ownership (active)", "SC 13G": "Beneficial ownership (passive)",
         "SCHEDULE 13D": "Beneficial ownership (active)", "SCHEDULE 13G": "Beneficial ownership (passive)", "4": "Insider transaction"}
ITEMS_8K = {"1.01": "material agreement", "1.02": "termination of an agreement", "2.01": "acquisition or disposal", "2.02": "results",
            "2.03": "new debt", "2.05": "restructuring", "2.06": "impairment", "3.01": "delisting",
            "4.01": "change of auditor", "4.02": "non-reliance on financials", "5.02": "change of officers",
            "5.03": "bylaws", "5.07": "shareholder vote", "7.01": "Reg FD disclosure", "8.01": "other event"}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def get(url):
    time.sleep(0.15)                     # 10 requêtes / seconde maximum
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return r.json()


def main() -> int:
    if not CONTACT:
        log("Secret SEC_CONTACT absent : la SEC refuse les requêtes anonymes. Étape ignorée.")
        return 0
    t0 = time.monotonic()
    try:
        stocks = [x["s"] for x in json.loads(UNIV.read_text(encoding="utf-8"))["items"] if x.get("c") == "Action"]
    except Exception:
        stocks = []
    pool = [s for s in dict.fromkeys([*FUND_TICKERS, *stocks]) if "." not in s and "=" not in s and "^" not in s
            and s not in ("ACWI", "AGG", "BIL", "GLD", "SPY", "QQQ", "IWM", "TLT")]   # fonds indiciels : pas de dépôts de société
    try:
        cache = json.loads(OUT.read_text(encoding="utf-8")).get("items", {})
    except Exception:
        cache = {}
    try:
        ciks = {str(v["ticker"]).upper(): (int(v["cik_str"]), v["title"]) for v in get("https://www.sec.gov/files/company_tickers.json").values()}
    except Exception as e:
        log(f"Liste des sociétés SEC indisponible : {str(e)[:100]}")
        return 0
    todo = sorted(pool, key=lambda s: cache.get(s, {}).get("ts", ""))[:BATCH]
    ok = 0
    for s in todo:
        if time.monotonic() - t0 > BUDGET:
            log("Budget de temps atteint : suite au prochain passage")
            break
        hit = ciks.get(s.upper()) or ciks.get(s.upper().replace("-", "."))
        if not hit:
            cache[s] = {"ts": NOW.isoformat().replace("+00:00", "Z"), "none": True}
            continue
        cik, title = hit
        try:
            d = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            rc = d.get("filings", {}).get("recent", {})
            forms, dates, accs = rc.get("form", []), rc.get("filingDate", []), rc.get("accessionNumber", [])
            docs, items = rc.get("primaryDocument", []), rc.get("items", [])
            cut90 = (NOW - timedelta(days=90)).date().isoformat()
            f4 = sum(1 for f, dt in zip(forms, dates) if f == "4" and dt >= cut90)
            out, f4_kept = [], 0
            for i, f in enumerate(forms):
                if f not in FORMS:
                    continue
                if f == "4":
                    if f4_kept >= 3:
                        continue
                    f4_kept += 1
                lab = FORMS[f]
                if f == "8-K" and i < len(items) and items[i]:
                    motifs = [ITEMS_8K[x] for x in str(items[i]).split(",") if x.strip() in ITEMS_8K]
                    if motifs:
                        lab += ": " + ", ".join(dict.fromkeys(motifs))
                acc = accs[i].replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{docs[i]}" if i < len(docs) and docs[i] \
                    else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                out.append([dates[i], f, lab, url])
                if len(out) >= KEEP:
                    break
            cache[s] = {"ts": NOW.isoformat().replace("+00:00", "Z"), "cik": cik, "name": d.get("name") or title,
                        "f4_90d": f4, "filings": out,
                        "edgar": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40"}
            ok += 1
        except Exception as e:
            log(f"  ! {s} : {str(e)[:80]}")
            if "403" in str(e):
                log("La SEC refuse l'accès (403) : vérifiez le secret SEC_CONTACT (nom et e-mail).")
                break
    cache = {k: v for k, v in cache.items() if k in pool}
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "SEC EDGAR (official filings of US-listed companies)", "items": cache},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    have = sum(1 for v in cache.values() if v.get("filings"))
    log(f"SEC : {ok} sociétés lues ce passage, {have}/{len(pool)} couvertes ({OUT.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
