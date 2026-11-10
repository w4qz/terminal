"""
Données économiques façon TradingView « ECONOMICS: » (USINTR, USIRYY, EUUR…).

Sources officielles et gratuites :
  FRED (Réserve fédérale de St. Louis, clé FRED_API_KEY) · BCE (ECB Data Portal) · Bank of England (IADB)

Chaque code liste une ou plusieurs sources candidates : la première qui répond avec un
historique à jour est retenue. Une série périmée (dernière donnée trop ancienne) est écartée
plutôt qu'affichée comme si elle était récente.

Écrit economics.json. Lancé par GitHub Actions, mais ne s'actualise qu'une fois par 20 heures
(ces chiffres sont publiés au plus une fois par mois) — sauf lancement manuel (ECON_FORCE=true).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "economics.json"
NOW = datetime.now(timezone.utc)
KEY = os.getenv("FRED_API_KEY", "")
FORCE = os.getenv("ECON_FORCE", "").lower() == "true"
UA = {"User-Agent": "Mozilla/5.0 (terminal-ia; projet personnel)"}
YEARS = 25           # profondeur d'historique conservée
REFRESH_H = 20       # pas plus d'une actualisation toutes les 20 heures

# Sources : ("fred", id, unités FRED, fréquence agrégée ou None) · ("ecb", clé SDMX) · ("boe", code IADB)
#   unités FRED : lin = niveau · pc1 = variation sur 1 an (%) · pch = variation sur la période (%) · chg = variation absolue
F = lambda sid, units="lin", freq=None, scale=1: ("fred", sid, units, freq, scale)
A = 4 / 1000   # Eurostat : PIB trimestriel en millions → rythme annuel en milliards (comme USGDP)
SPEC = [
    # ── États-Unis
    ("USINTR", "Fed policy rate (upper bound)", "United States", "%", [F("DFEDTARU", freq="m"), F("FEDFUNDS")]),
    ("USIRYY", "CPI inflation (y/y)", "United States", "%", [F("CPIAUCSL", "pc1")]),
    ("USCIR", "Core CPI inflation (ex food and energy, y/y)", "United States", "%", [F("CPILFESL", "pc1")]),
    ("USIRMM", "CPI inflation (m/m)", "United States", "%", [F("CPIAUCSL", "pch")]),
    ("USPCE", "Core PCE inflation (y/y)", "United States", "%", [F("PCEPILFE", "pc1")]),
    ("USPPI", "PPI producer prices (y/y)", "United States", "%", [F("PPIFIS", "pc1")]),
    ("USGDPQQ", "GDP growth (q/q annualised)", "United States", "%", [F("A191RL1Q225SBEA")]),
    ("USGDPYY", "GDP growth (y/y)", "United States", "%", [F("GDPC1", "pc1")]),
    ("USGDP", "Nominal GDP", "United States", "Md$", [F("GDP")]),
    ("USUR", "Unemployment rate", "United States", "%", [F("UNRATE")]),
    ("USNFP", "Nonfarm payrolls (m/m)", "United States", "milliers", [F("PAYEMS", "chg")]),
    ("USJC", "Weekly jobless claims", "United States", "inscriptions", [F("ICSA")]),
    ("USJVAC", "JOLTS job openings", "United States", "milliers", [F("JTSJOL")]),
    ("USWG", "Average hourly earnings (y/y)", "United States", "%", [F("CES0500000003", "pc1")]),
    ("USRSMM", "Retail sales (m/m)", "United States", "%", [F("RSAFS", "pch")]),
    ("USIPYY", "Industrial production (y/y)", "United States", "%", [F("INDPRO", "pc1")]),
    ("USHST", "Housing starts (annual rate)", "United States", "milliers", [F("HOUST")]),
    ("USHPI", "Case-Shiller home prices (y/y)", "United States", "%", [F("CSUSHPISA", "pc1")]),
    ("USMR", "30-year mortgage rate", "United States", "%", [F("MORTGAGE30US", freq="m")]),
    ("USCCI", "Consumer sentiment (Michigan)", "United States", "indice", [F("UMCSENT")]),
    ("USM2", "M2 money supply (y/y)", "United States", "%", [F("M2SL", "pc1")]),
    ("USCBBS", "Fed balance sheet", "United States", "M$", [F("WALCL", freq="m")]),
    ("USGDG", "Public debt / GDP", "United States", "%", [F("GFDEGDQ188S")]),
    ("USBOT", "Trade balance", "United States", "M$", [F("BOPGSTB")]),
    ("US10Y", "10-year yield (Treasury)", "United States", "%", [F("DGS10", freq="m")]),
    ("US02Y", "2-year yield (Treasury)", "United States", "%", [F("DGS2", freq="m")]),
    ("USYC", "10-year − 2-year slope", "United States", "pts", [F("T10Y2Y", freq="m")]),
    # Prix : niveaux d'indice et inflation globale PCE
    ("USCPI", "CPI price index (level)", "United States", "indice", [F("CPIAUCSL")]),
    ("USCCPI", "Core CPI price index (level)", "United States", "indice", [F("CPILFESL")]),
    ("USPCEYY", "Headline PCE inflation (y/y)", "United States", "%", [F("PCEPI", "pc1")]),
    ("USPCEMM", "Core PCE inflation (m/m)", "United States", "%", [F("PCEPILFE", "pch")]),
    ("USINFE", "5y5y inflation expectations", "United States", "%", [F("T5YIFR", freq="m")]),
    ("USBE10", "10-year breakeven inflation", "United States", "%", [F("T10YIE", freq="m")]),
    # Emploi et activité (l'indice ISM est payant : enquêtes régionales de la Fed et CFNAI en substitut)
    ("USADP", "ADP private employment (m/m)", "United States", "milliers", [F("ADPMNUSNERSA", "chg", scale=0.001), F("ADPWNUSNERSA", "chg", freq="m", scale=0.001)]),
    ("USCJC", "Continuing jobless claims", "United States", "inscriptions", [F("CCSA")]),
    ("USPHIL", "Philadelphia Fed manufacturing survey (ISM proxy)", "United States", "indice", [F("GACDFSA066MSFRBPHI")]),
    ("USCFNAI", "Chicago Fed National Activity Index CFNAI (ISM proxy)", "United States", "indice", [F("CFNAI")]),
    ("USDGO", "Durable goods orders (m/m)", "United States", "%", [F("DGORDER", "pch")]),
    # Taux monétaires et de marché
    ("USEFFR", "Effective fed funds rate (EFFR)", "United States", "%", [F("EFFR", freq="m"), F("DFF", freq="m")]),
    ("USSOFR", "SOFR rate", "United States", "%", [F("SOFR", freq="m")]),
    ("USIORB", "Interest on reserve balances (IORB)", "United States", "%", [F("IORB", freq="m")]),
    ("US03MY", "3-month yield (T-bill)", "United States", "%", [F("DGS3MO", freq="m")]),
    ("US05Y", "5-year yield (Treasury)", "United States", "%", [F("DGS5", freq="m")]),
    ("US30Y", "30-year yield (Treasury)", "United States", "%", [F("DGS30", freq="m")]),
    ("US10YR", "10-year real yield (TIPS)", "United States", "%", [F("DFII10", freq="m")]),
    ("USIGS", "Investment grade credit spread (ICE BofA)", "United States", "%", [F("BAMLC0A0CM", freq="m")]),
    ("USHYS", "High yield credit spread (ICE BofA)", "United States", "%", [F("BAMLH0A0HYM2", freq="m")]),
    # ── Zone euro
    ("EUINTR", "ECB deposit rate", "Euro area", "%", [("ecb", "FM/D.U2.EUR.4F.KR.DFR.LEV"), F("ECBDFR", freq="m")]),
    ("EUMRO", "ECB main refinancing rate", "Euro area", "%", [("ecb", "FM/D.U2.EUR.4F.KR.MRR_FR.LEV"), F("ECBMRRFR", freq="m")]),
    ("EUESTR", "€STR rate", "Euro area", "%", [F("ECBESTRVOLWGTTRMDMNRT", freq="m")]),
    ("EURIBOR3M", "3-month Euribor", "Euro area", "%", [("ecb", "FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA"), F("IR3TIB01EZM156N")]),
    ("EU02Y", "Euro area AAA 2-year yield (ECB)", "Euro area", "%", [("ecb", "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y")]),
    ("EU10Y", "Euro area AAA 10-year yield (ECB)", "Euro area", "%", [("ecb", "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y")]),
    ("EUIRYY", "HICP inflation (y/y)", "Euro area", "%", [("estat", "prc_hicp_manr", "geo=EA20&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=EA20&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=EA20&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.U2.N.000000.4.ANR"), F("CP0000EZ19M086NEST", "pc1")]),
    ("EUCIR", "Core inflation (HICP ex energy and food, y/y)", "Euro area", "%", [("estat", "prc_hicp_manr", "geo=EA20&coicop=TOT_X_NRG_FOOD"), ("estat", "prc_hicp_minr", "geo=EA20&coicop18=TOT_X_NRG_FOOD&unit=RCH_A"), ("ecb", "ICP/M.U2.N.XEF000.4.ANR")]),
    ("EUUR", "Unemployment rate", "Euro area", "%", [("estat", "une_rt_m", "geo=EA20&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), ("ecb", "LFSI/M.I9.S.UNEHRT.TOTAL0.15_74.T"), F("LRHUTTTTEZM156S")]),
    ("EUGDPYY", "GDP growth (y/y)", "Euro area", "%", [("estat", "namq_10_gdp", "geo=EA20&unit=CLV_PCH_SM&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQEA19", "pc1"), F("CLVMNACSCAB1GQEA20", "pc1")]),
    ("EUGDP", "Nominal GDP (annual rate)", "Euro area", "Md€", [("estat", "namq_10_gdp", "geo=EA20&unit=CP_MEUR&s_adj=SCA&na_item=B1GQ", 0.004), F("CPMNACSCAB1GQEA20", scale=A), F("CPMNACSCAB1GQEA19", scale=A)]),
    # ── Allemagne, France, Italie
    ("DEIRYY", "Inflation (y/y)", "Germany", "%", [("estat", "prc_hicp_manr", "geo=DE&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=DE&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=DE&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.DE.N.000000.4.ANR"), F("CPALTT01DEM659N"), ("wb", "DE", "FP.CPI.TOTL.ZG")]),
    ("DEUR", "Unemployment rate", "Germany", "%", [("estat", "une_rt_m", "geo=DE&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), F("LRHUTTTTDEM156S")]),
    ("DEGDPYY", "GDP growth (y/y)", "Germany", "%", [("estat", "namq_10_gdp", "geo=DE&unit=CLV_PCH_SM&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQDE", "pc1")]),
    ("DEGDP", "Nominal GDP (annual rate)", "Germany", "Md€", [("estat", "namq_10_gdp", "geo=DE&unit=CP_MEUR&s_adj=SCA&na_item=B1GQ", 0.004), F("CPMNACSCAB1GQDE", scale=A)]),
    ("DEGDPQQ", "GDP growth (q/q)", "Germany", "%", [("estat", "namq_10_gdp", "geo=DE&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQDE", "pch")]),
    ("DE10Y", "10-year yield (Bund)", "Germany", "%", [F("IRLTLT01DEM156N")]),
    ("FRIRYY", "Inflation (y/y)", "France", "%", [("estat", "prc_hicp_manr", "geo=FR&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=FR&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=FR&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.FR.N.000000.4.ANR"), F("CPALTT01FRM659N"), ("wb", "FR", "FP.CPI.TOTL.ZG")]),
    ("FRUR", "Unemployment rate", "France", "%", [("estat", "une_rt_m", "geo=FR&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), F("LRHUTTTTFRM156S")]),
    ("FRGDPYY", "GDP growth (y/y)", "France", "%", [("estat", "namq_10_gdp", "geo=FR&unit=CLV_PCH_SM&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQFR", "pc1")]),
    ("FRGDP", "Nominal GDP (annual rate)", "France", "Md€", [("estat", "namq_10_gdp", "geo=FR&unit=CP_MEUR&s_adj=SCA&na_item=B1GQ", 0.004), F("CPMNACSCAB1GQFR", scale=A)]),
    ("FRGDPQQ", "GDP growth (q/q)", "France", "%", [("estat", "namq_10_gdp", "geo=FR&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQFR", "pch")]),
    ("FR10Y", "10-year yield (OAT)", "France", "%", [F("IRLTLT01FRM156N")]),
    ("ITIRYY", "Inflation (y/y)", "Italy", "%", [("estat", "prc_hicp_manr", "geo=IT&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=IT&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=IT&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.IT.N.000000.4.ANR"), ("wb", "IT", "FP.CPI.TOTL.ZG")]),
    ("ITGDPYY", "GDP growth (y/y)", "Italy", "%", [("estat", "namq_10_gdp", "geo=IT&unit=CLV_PCH_SM&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQIT", "pc1"), F("NGDPRSAXDCITQ", "pc1")]),
    ("ITGDP", "Nominal GDP (annual rate)", "Italy", "Md€", [("estat", "namq_10_gdp", "geo=IT&unit=CP_MEUR&s_adj=SCA&na_item=B1GQ", 0.004), F("CPMNACSCAB1GQIT", scale=A)]),
    ("ITGDPQQ", "GDP growth (q/q)", "Italy", "%", [("estat", "namq_10_gdp", "geo=IT&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQIT", "pch")]),
    ("ESGDPYY", "GDP growth (y/y)", "Spain", "%", [("estat", "namq_10_gdp", "geo=ES&unit=CLV_PCH_SM&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQES", "pc1"), F("NGDPRSAXDCESQ", "pc1")]),
    ("ESGDP", "Nominal GDP (annual rate)", "Spain", "Md€", [("estat", "namq_10_gdp", "geo=ES&unit=CP_MEUR&s_adj=SCA&na_item=B1GQ", 0.004), F("CPMNACSCAB1GQES", scale=A)]),
    ("ESGDPQQ", "GDP growth (q/q)", "Spain", "%", [("estat", "namq_10_gdp", "geo=ES&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQES", "pch")]),
    ("EUGDPQQ", "GDP growth (q/q)", "Euro area", "%", [("estat", "namq_10_gdp", "geo=EA20&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQEA20", "pch"), F("CLVMNACSCAB1GQEA19", "pch")]),
    ("IT10Y", "10-year yield (BTP)", "Italy", "%", [F("IRLTLT01ITM156N")]),
    # ── Royaume-Uni
    ("GBINTR", "BoE policy rate (Bank Rate)", "United Kingdom", "%", [("boe", "IUDBEDR"), F("IRSTCB01GBM156N")]),
    ("GBSONIA", "SONIA rate", "United Kingdom", "%", [("boe", "IUDSOIA")]),
    ("GBIRYY", "CPI inflation (y/y)", "United Kingdom", "%", [("ons", "D7G7", "MM23"), F("CPALTT01GBM659N"), F("GBRCPIALLMINMEI", "pc1")]),
    ("GBUR", "Unemployment rate", "United Kingdom", "%", [F("LRHUTTTTGBM156S")]),
    ("GBGDPYY", "GDP growth (y/y)", "United Kingdom", "%", [F("CLVMNACSCAB1GQUK", "pc1"), F("NGDPRSAXDCGBQ", "pc1")]),
    ("GBGDP", "Nominal GDP (annual rate)", "United Kingdom", "Md£", [F("UKNGDP", scale=A), F("CPMNACSCAB1GQUK", scale=A)]),
    ("GBGDPQQ", "GDP growth (q/q)", "United Kingdom", "%", [F("CLVMNACSCAB1GQUK", "pch")]),
    ("GB10Y", "10-year yield (Gilt)", "United Kingdom", "%", [F("IRLTLT01GBM156N")]),
    # ── Suisse (pertinent pour Monaco)
    ("CHINTR", "SNB policy rate", "Switzerland", "%", [("snb", "snbgwdzid", "LZ"), F("IRSTCB01CHM156N"), F("IRSTCI01CHM156N")]),
    ("CHIRYY", "Inflation (y/y)", "Switzerland", "%", [("wb", "CH", "FP.CPI.TOTL.ZG"), F("CPALTT01CHM659N"), F("CHECPIALLMINMEI", "pc1")]),
    ("CHGDPYY", "GDP growth (y/y)", "Switzerland", "%", [F("CLVMNACSCAB1GQCH", "pc1"), F("NGDPRSAXDCCHQ", "pc1")]),
    ("CHGDP", "Nominal GDP (annual rate)", "Switzerland", "Md CHF", [F("CPMNACSCAB1GQCH", scale=A)]),
    ("CHGDPQQ", "GDP growth (q/q)", "Switzerland", "%", [F("CLVMNACSCAB1GQCH", "pch")]),
    ("CH10Y", "10-year yield (Confederation)", "Switzerland", "%", [F("IRLTLT01CHM156N")]),
    # ── Japon
    ("JPINTR", "BoJ policy rate", "Japan", "%", [F("IRSTCB01JPM156N")]),
    ("JPIRYY", "Inflation (y/y)", "Japan", "%", [("wb", "JP", "FP.CPI.TOTL.ZG"), F("CPALTT01JPM659N"), F("JPNCPIALLMINMEI", "pc1")]),
    ("JPUR", "Unemployment rate", "Japan", "%", [F("LRUN64TTJPM156S"), F("LRHUTTTTJPM156S")]),
    ("JPGDPYY", "GDP growth (y/y)", "Japan", "%", [F("JPNRGDPEXP", "pc1")]),
    ("JPGDP", "Nominal GDP (annual rate)", "Japan", "Md¥", [F("JPNNGDP")]),
    ("JPGDPQQ", "GDP growth (q/q)", "Japan", "%", [F("JPNRGDPEXP", "pch")]),
    ("JP10Y", "10-year yield (JGB)", "Japan", "%", [F("IRLTLT01JPM156N")]),
    # ── Chine
    ("CNIRYY", "Inflation (y/y)", "China", "%", [("wb", "CN", "FP.CPI.TOTL.ZG"), F("CPALTT01CNM659N"), F("CHNCPIALLMINMEI", "pc1")]),
    ("CNGDPYY", "GDP growth (y/y)", "China", "%", [F("CHNGDPNQDSMEI", "pc1"), F("NGDPRXDCCNA", "pc1")]),
    ("CNGDP", "Nominal GDP (annual, World Bank)", "China", "Md$", [F("MKTGDPCNA646NWDB", scale=1e-9)]),
    # ── Inde
    ("INGDP", "Nominal GDP (annual, World Bank)", "India", "Md$", [F("MKTGDPINA646NWDB", scale=1e-9)]),
    ("INGDPYY", "GDP growth (y/y)", "India", "%", [F("NAEXKP01INQ659S"), F("MKTGDPINA646NWDB", "pc1")]),
]

SRC_LABEL = {"fred": "FRED (St. Louis Fed)", "ecb": "ECB Data Portal", "boe": "Bank of England — IADB",
             "snb": "Swiss National Bank — data.snb.ch", "ons": "Office for National Statistics (UK)",
             "wb": "World Bank (annual)",
             "estat": "Eurostat"}


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def _monthly(obs):
    """Agrège une série quotidienne/hebdomadaire en fin de mois (dernière valeur du mois)."""
    out = {}
    for d, v in obs:
        out[d[:7]] = (d, v)
    return [(d[:7] + "-01", v) for d, v in out.values()]


def get_fred(sid, units, freq, scale=1):
    if not KEY:
        raise RuntimeError("pas de FRED_API_KEY")
    p = {"series_id": sid, "api_key": KEY, "file_type": "json", "units": units,
         "observation_start": (NOW - timedelta(days=365 * YEARS)).strftime("%Y-%m-%d")}
    if freq:
        p.update(frequency=freq, aggregation_method="eop")
    for attempt in range(3):
        time.sleep(0.4)                   # reste sous la limite FRED (120 requêtes / minute)
        r = requests.get("https://api.stlouisfed.org/fred/series/observations", params=p, timeout=30)
        if r.status_code == 429:          # limite de débit FRED : 120 requêtes / minute
            time.sleep(5 * (attempt + 1)); continue
        r.raise_for_status()
        obs = [(o["date"], float(o["value"]) * scale) for o in r.json()["observations"] if o["value"] not in (".", "")]
        if freq and obs:
            # Série quotidienne agrégée en fin de mois : le dernier point doit rester la DERNIÈRE valeur
            # du jour (sinon US10Y affichait le taux du 31/10 alors que GC montre celui d'hier).
            q = {"series_id": sid, "api_key": KEY, "file_type": "json", "units": units, "sort_order": "desc", "limit": 1}
            last = requests.get("https://api.stlouisfed.org/fred/series/observations", params=q, timeout=30)
            if last.ok:
                o = next((o for o in last.json()["observations"] if o["value"] not in (".", "")), None)
                if o and o["date"] > obs[-1][0] and o["date"] <= NOW.date().isoformat():
                    if obs[-1][0][:7] == o["date"][:7]:
                        obs[-1] = (o["date"], float(o["value"]) * scale)     # mois en cours : remplace la valeur partielle
                    else:
                        obs.append((o["date"], float(o["value"]) * scale))
        return obs, f"https://fred.stlouisfed.org/series/{sid}"
    raise RuntimeError("FRED : limite de débit")


def get_ecb(key):
    # La BCE refuse parfois les appels trop rapprochés (429/503) : jusqu'à 3 essais espacés
    for attempt in range(3):
        time.sleep(1.0 + 2 * attempt)
        r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{key}",
                         params={"startPeriod": str(NOW.year - YEARS), "format": "csvdata"}, headers=UA, timeout=40)
        if r.status_code not in (429, 500, 502, 503, 504):
            break
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text)).dropna(subset=["OBS_VALUE"])
    obs = []
    for tp, v in zip(df["TIME_PERIOD"].astype(str), df["OBS_VALUE"]):
        d = tp if len(tp) == 10 else tp + "-01" if len(tp) == 7 else tp
        obs.append((d, float(v)))
    if obs and len(obs[0][0]) == 10:
        obs = _monthly(obs)              # taux quotidiens → fin de mois
    flow, series = key.split("/", 1)
    return obs, f"https://data.ecb.europa.eu/data/datasets/{flow}/{flow}.{series}"


def get_ons(series, dataset):
    """Office for National Statistics (Royaume-Uni) : série mensuelle, ex. D7G7 = inflation CPI sur un an."""
    r = requests.get(f"https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/{series.lower()}/{dataset.lower()}/data",
                     headers=UA, timeout=40)
    r.raise_for_status()
    mo = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"])}
    obs = []
    for x in r.json().get("months", []):
        try:
            obs.append((f"{int(x['year'])}-{mo[x['month']]:02d}-01", float(x["value"])))
        except Exception:
            continue
    cut = f"{NOW.year - YEARS}-01-01"
    return [o for o in obs if o[0] >= cut], f"https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/{series.lower()}/{dataset.lower()}"


def get_wb(country, indicator):
    """Banque mondiale : série annuelle (repli quand aucune série mensuelle gratuite n'est à jour)."""
    r = requests.get(f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
                     params={"format": "json", "per_page": 100, "date": f"{NOW.year - YEARS}:{NOW.year}"}, headers=UA, timeout=40)
    r.raise_for_status()
    d = r.json()
    rows = d[1] if isinstance(d, list) and len(d) > 1 and d[1] else []
    return [(f"{x['date']}-01-01", float(x["value"])) for x in rows if x.get("value") is not None], \
        f"https://data.worldbank.org/indicator/{indicator}?locations={country}"


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


def get_estat(dataset, filters, scale=1.0):
    """Eurostat (API JSON-stat, sans clé) : séries officielles publiées le jour même, avant leur reprise par la BCE ou FRED."""
    q = dict(x.split("=", 1) for x in filters.split("&"))
    r = requests.get(f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}",
                     params={"format": "JSON", "lang": "EN", "sinceTimePeriod": str(NOW.year - YEARS), **q}, headers=UA, timeout=40)
    r.raise_for_status()
    d = r.json()
    obs = []
    for per, v in _jsonstat_series(d):
        m = re.match(r"(\d{4})-?(Q(\d)|M?(\d{2}))?$", per)
        if not m:
            continue
        y = m.group(1)
        mo = (int(m.group(3)) - 1) * 3 + 1 if m.group(3) else int(m.group(4) or 1)
        obs.append((f"{y}-{mo:02d}-01", float(v) * scale))
    return sorted(obs), f"https://ec.europa.eu/eurostat/databrowser/view/{dataset}/default/table"


def get_boe(code):
    r = requests.get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
                     params={"csv.x": "yes", "Datefrom": f"01/Jan/{NOW.year - YEARS}", "Dateto": "now",
                             "SeriesCodes": code, "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"},
                     headers=UA, timeout=40)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text)).dropna(subset=[code])
    dates = pd.to_datetime(df[df.columns[0]], dayfirst=True).dt.strftime("%Y-%m-%d")
    obs = _monthly(sorted(zip(dates, df[code].astype(float))))
    return obs, "https://www.bankofengland.co.uk/boeapps/database/"


def get_snb(cube, dim):
    """Cube SNB (CSV « Date;D0;Value ») : taux directeur = cube snbgwdzid, dimension LZ (Leitzins)."""
    r = requests.get(f"https://data.snb.ch/api/cube/{cube}/data/csv/en",
                     params={"fromDate": f"{NOW.year - YEARS}-01-01"}, headers=UA, timeout=40)
    r.raise_for_status()
    obs = [(d if len(d) == 10 else d + "-01", float(v)) for d, k, v in
           __import__("re").findall(r'"?(\d{4}-\d{2}(?:-\d{2})?)"?;"?([A-Za-z0-9_]+)"?;"?(-?\d+\.?\d*)"?', r.text) if k == dim]
    if obs and len(obs[0][0]) == 10:
        obs = _monthly(sorted(obs))
    # Un taux directeur n'est publié qu'aux dates de décision : il reste en vigueur jusqu'à la suivante.
    filled, by = [], dict(obs)
    if obs:
        y, m, last = int(obs[0][0][:4]), int(obs[0][0][5:7]), None
        while (y, m) <= (NOW.year, NOW.month):
            k = f"{y}-{m:02d}-01"
            last = by.get(k, last)
            filled.append((k, last))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return filled, f"https://data.snb.ch/en/topics/snb/cube/{cube}"


def freq_of(obs):
    if len(obs) < 3:
        return "?"
    ds = [datetime.fromisoformat(d) for d, _ in obs[-13:]]
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]))
    g = gaps[len(gaps) // 2]
    return "quotidienne" if g <= 3 else "hebdomadaire" if g <= 10 else "mensuelle" if g <= 40 else "trimestrielle" if g <= 120 else "annuelle"


MAX_AGE = {"quotidienne": 120, "hebdomadaire": 120, "mensuelle": 200, "trimestrielle": 330, "annuelle": 800, "?": 400}


def main() -> int:
    if not FORCE and OUT.exists():
        try:
            prev = datetime.fromisoformat(json.loads(OUT.read_text(encoding="utf-8"))["generated_at"].replace("Z", "+00:00"))
            if NOW - prev < timedelta(hours=REFRESH_H):
                log(f"economics.json a {(NOW - prev).seconds // 3600} h : pas d'actualisation (une fois par {REFRESH_H} h)")
                return 0
        except Exception:
            pass

    items, skipped = [], []
    for code, name, ctry, unit, sources in SPEC:
        got = None
        for src in sources:
            kind = src[0]
            try:
                obs, url = (get_fred(*src[1:]) if kind == "fred" else get_ecb(src[1]) if kind == "ecb"
                            else get_snb(*src[1:]) if kind == "snb" else get_ons(*src[1:]) if kind == "ons"
                            else get_wb(*src[1:]) if kind == "wb" else get_estat(*src[1:]) if kind == "estat"
                            else get_boe(src[1]))
                obs = sorted(obs)
                if len(obs) < 3:
                    raise RuntimeError("historique vide")
                fq = freq_of(obs)
                age = (NOW.date() - datetime.fromisoformat(obs[-1][0]).date()).days
                if age > MAX_AGE[fq]:
                    raise RuntimeError(f"périmée (dernière donnée {obs[-1][0]})")
                got = {"code": code, "n": name, "ctry": ctry, "unit": unit, "freq": fq,
                       "src": SRC_LABEL[kind], "sid": src[1], "url": url,
                       "last": round(obs[-1][1], 4), "prev": round(obs[-2][1], 4), "date": obs[-1][0],
                       "h": [[d, round(v, 4)] for d, v in obs]}
                break
            except Exception as e:
                log(f"  · {code} via {kind} {src[1]} : {str(e)[:90]}")
            time.sleep(0.3)
        if got:
            items.append(got)
            log(f"  ✓ {code:8} {got['last']:>12} {unit:8} au {got['date']} ({got['src'].split(' ')[0]})")
        else:
            skipped.append(code)

    if not items:
        log("Aucune série récupérée : economics.json inchangé")
        return 0
    OUT.write_text(json.dumps({"generated_at": NOW.isoformat().replace("+00:00", "Z"), "items": items,
                               "skipped": skipped}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Économie : {len(items)}/{len(SPEC)} séries ({OUT.stat().st_size // 1024} Ko)"
        + (f" · écartées : {', '.join(skipped)}" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
