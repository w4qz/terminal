"""
Banque mondiale : indicateurs annuels pour environ 200 pays (onglet Carte : tous les pays colorés).

API publique et gratuite, sans clé : api.worldbank.org. Une requête par indicateur, dernière valeur
disponible de chaque pays (souvent l'année précédente : ce sont des données annuelles).
Ne s'actualise qu'une fois par jour (WB_FORCE=true pour forcer).

Écrit worldbank.json : { ind: {...}, items: { "250": { "name": "France", "gdp": [valeur, année], ... } } }
Les pays sont indexés par code ISO numérique, comme le fond de carte.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "worldbank.json"
NOW = datetime.now(timezone.utc)
FORCE = os.getenv("WB_FORCE", "").lower() == "true"
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}

IND = {
    "gdp": ("NY.GDP.MKTP.CD", "Nominal GDP", "Md$", 1e-9),
    "gdpg": ("NY.GDP.MKTP.KD.ZG", "Real GDP growth", "%", 1),
    "gdppc": ("NY.GDP.PCAP.CD", "GDP per capita", "$", 1),
    "infl": ("FP.CPI.TOTL.ZG", "Inflation (consumer prices)", "%", 1),
    "unemp": ("SL.UEM.TOTL.ZS", "Unemployment rate (ILO)", "%", 1),
    "debt": ("GC.DOD.TOTL.GD.ZS", "Central government debt", "% of GDP", 1),
    "ca": ("BN.CAB.XOKA.GD.ZS", "Current account balance", "% of GDP", 1),
}
# Code pays ISO alpha-3 (Banque mondiale) → ISO numérique (fond de carte)
ISO3 = {"ABW":533,"AFG":4,"AGO":24,"AIA":660,"ALA":248,"ALB":8,"AND":20,"ARE":784,"ARG":32,"ARM":51,"ASM":16,"ATA":10,"ATF":260,"ATG":28,"AUS":36,"AUT":40,"AZE":31,"BDI":108,"BEL":56,"BEN":204,"BES":535,"BFA":854,"BGD":50,"BGR":100,"BHR":48,"BHS":44,"BIH":70,"BLM":652,"BLR":112,"BLZ":84,"BMU":60,"BOL":68,"BRA":76,"BRB":52,"BRN":96,"BTN":64,"BVT":74,"BWA":72,"CAF":140,"CAN":124,"CCK":166,"CHE":756,"CHL":152,"CHN":156,"CIV":384,"CMR":120,"COD":180,"COG":178,"COK":184,"COL":170,"COM":174,"CPV":132,"CRI":188,"CUB":192,"CUW":531,"CXR":162,"CYM":136,"CYP":196,"CZE":203,"DEU":276,"DJI":262,"DMA":212,"DNK":208,"DOM":214,"DZA":12,"ECU":218,"EGY":818,"ERI":232,"ESH":732,"ESP":724,"EST":233,"ETH":231,"FIN":246,"FJI":242,"FLK":238,"FRA":250,"FRO":234,"FSM":583,"GAB":266,"GBR":826,"GEO":268,"GGY":831,"GHA":288,"GIB":292,"GIN":324,"GLP":312,"GMB":270,"GNB":624,"GNQ":226,"GRC":300,"GRD":308,"GRL":304,"GTM":320,"GUF":254,"GUM":316,"GUY":328,"HKG":344,"HMD":334,"HND":340,"HRV":191,"HTI":332,"HUN":348,"IDN":360,"IMN":833,"IND":356,"IOT":86,"IRL":372,"IRN":364,"IRQ":368,"ISL":352,"ISR":376,"ITA":380,"JAM":388,"JEY":832,"JOR":400,"JPN":392,"KAZ":398,"KEN":404,"KGZ":417,"KHM":116,"KIR":296,"KNA":659,"KOR":410,"KWT":414,"LAO":418,"LBN":422,"LBR":430,"LBY":434,"LCA":662,"LIE":438,"LKA":144,"LSO":426,"LTU":440,"LUX":442,"LVA":428,"MAC":446,"MAF":663,"MAR":504,"MCO":492,"MDA":498,"MDG":450,"MDV":462,"MEX":484,"MHL":584,"MKD":807,"MLI":466,"MLT":470,"MMR":104,"MNE":499,"MNG":496,"MNP":580,"MOZ":508,"MRT":478,"MSR":500,"MTQ":474,"MUS":480,"MWI":454,"MYS":458,"MYT":175,"NAM":516,"NCL":540,"NER":562,"NFK":574,"NGA":566,"NIC":558,"NIU":570,"NLD":528,"NOR":578,"NPL":524,"NRU":520,"NZL":554,"OMN":512,"PAK":586,"PAN":591,"PCN":612,"PER":604,"PHL":608,"PLW":585,"PNG":598,"POL":616,"PRI":630,"PRK":408,"PRT":620,"PRY":600,"PSE":275,"PYF":258,"QAT":634,"REU":638,"ROU":642,"RUS":643,"RWA":646,"SAU":682,"SDN":729,"SEN":686,"SGP":702,"SGS":239,"SHN":654,"SJM":744,"SLB":90,"SLE":694,"SLV":222,"SMR":674,"SOM":706,"SPM":666,"SRB":688,"SSD":728,"STP":678,"SUR":740,"SVK":703,"SVN":705,"SWE":752,"SWZ":748,"SXM":534,"SYC":690,"SYR":760,"TCA":796,"TCD":148,"TGO":768,"THA":764,"TJK":762,"TKL":772,"TKM":795,"TLS":626,"TON":776,"TTO":780,"TUN":788,"TUR":792,"TUV":798,"TWN":158,"TZA":834,"UGA":800,"UKR":804,"UMI":581,"URY":858,"USA":840,"UZB":860,"VAT":336,"VCT":670,"VEN":862,"VGB":92,"VIR":850,"VNM":704,"VUT":548,"WLF":876,"WSM":882,"YEM":887,"ZAF":710,"ZMB":894,"ZWE":716,"XKX":-1}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    if not FORCE and OUT.exists():
        try:
            prev = datetime.fromisoformat(json.loads(OUT.read_text(encoding="utf-8"))["generated_at"].replace("Z", "+00:00"))
            if NOW - prev < timedelta(hours=20):
                log("worldbank.json a moins de 20 h : pas d'actualisation")
                return 0
        except Exception:
            pass
    items, ok = {}, 0
    for key, (code, label, unit, mult) in IND.items():
        try:
            r = requests.get(f"https://api.worldbank.org/v2/country/all/indicator/{code}",
                             params={"format": "json", "mrnev": 1, "per_page": 400}, headers=UA, timeout=40)
            r.raise_for_status()
            data = r.json()
            rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
            n = 0
            for row in rows:
                iso = ISO3.get(row.get("countryiso3code") or "")
                if not iso or iso < 0 or row.get("value") is None:
                    continue
                it = items.setdefault(str(iso), {"name": (row.get("country") or {}).get("value")})
                it[key] = [round(row["value"] * mult, 3), int(row["date"])]
                n += 1
            ok += 1
            log(f"  {code} : {n} pays")
        except Exception as e:
            log(f"  ! {code} : {str(e)[:100]}")
        time.sleep(0.5)
    if not items:
        log("Banque mondiale indisponible : worldbank.json inchangé")
        return 0
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"),
                               "source": "World Bank — World Development Indicators (annual data)",
                               "ind": {k: {"code": v[0], "l": v[1], "u": v[2]} for k, v in IND.items()},
                               "items": items}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Banque mondiale : {ok}/{len(IND)} indicateurs, {len(items)} pays")
    return 0


if __name__ == "__main__":
    sys.exit(main())
