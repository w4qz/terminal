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
    ("USINTR", "Taux directeur Fed (borne haute)", "États-Unis", "%", [F("DFEDTARU", freq="m"), F("FEDFUNDS")]),
    ("USIRYY", "Inflation CPI (IPC, a/a)", "États-Unis", "%", [F("CPIAUCSL", "pc1")]),
    ("USCIR", "Inflation CPI core (hors énergie et alimentation, a/a)", "États-Unis", "%", [F("CPILFESL", "pc1")]),
    ("USIRMM", "Inflation CPI (IPC, m/m)", "États-Unis", "%", [F("CPIAUCSL", "pch")]),
    ("USPCE", "Inflation PCE core (a/a)", "États-Unis", "%", [F("PCEPILFE", "pc1")]),
    ("USPPI", "Prix à la production PPI (a/a)", "États-Unis", "%", [F("PPIFIS", "pc1")]),
    ("USGDPQQ", "Croissance du PIB (t/t annualisé)", "États-Unis", "%", [F("A191RL1Q225SBEA")]),
    ("USGDPYY", "Croissance du PIB (a/a)", "États-Unis", "%", [F("GDPC1", "pc1")]),
    ("USGDP", "PIB nominal", "États-Unis", "Md$", [F("GDP")]),
    ("USUR", "Taux de chômage", "États-Unis", "%", [F("UNRATE")]),
    ("USNFP", "Créations d'emplois NFP (non agricoles, m/m)", "États-Unis", "milliers", [F("PAYEMS", "chg")]),
    ("USJC", "Inscriptions hebdomadaires au chômage", "États-Unis", "inscriptions", [F("ICSA")]),
    ("USJVAC", "Offres d'emploi JOLTS", "États-Unis", "milliers", [F("JTSJOL")]),
    ("USWG", "Salaire horaire moyen (a/a)", "États-Unis", "%", [F("CES0500000003", "pc1")]),
    ("USRSMM", "Ventes au détail (m/m)", "États-Unis", "%", [F("RSAFS", "pch")]),
    ("USIPYY", "Production industrielle (a/a)", "États-Unis", "%", [F("INDPRO", "pc1")]),
    ("USHST", "Mises en chantier (rythme annuel)", "États-Unis", "milliers", [F("HOUST")]),
    ("USHPI", "Prix immobiliers Case-Shiller (a/a)", "États-Unis", "%", [F("CSUSHPISA", "pc1")]),
    ("USMR", "Taux hypothécaire 30 ans", "États-Unis", "%", [F("MORTGAGE30US", freq="m")]),
    ("USCCI", "Confiance des consommateurs (Michigan)", "États-Unis", "indice", [F("UMCSENT")]),
    ("USM2", "Masse monétaire M2 (a/a)", "États-Unis", "%", [F("M2SL", "pc1")]),
    ("USCBBS", "Bilan de la Fed", "États-Unis", "M$", [F("WALCL", freq="m")]),
    ("USGDG", "Dette publique / PIB", "États-Unis", "%", [F("GFDEGDQ188S")]),
    ("USBOT", "Balance commerciale", "États-Unis", "M$", [F("BOPGSTB")]),
    ("US10Y", "Taux à 10 ans (Trésor)", "États-Unis", "%", [F("DGS10", freq="m")]),
    ("US02Y", "Taux à 2 ans (Trésor)", "États-Unis", "%", [F("DGS2", freq="m")]),
    ("USYC", "Pente 10 ans − 2 ans", "États-Unis", "pts", [F("T10Y2Y", freq="m")]),
    # Prix : niveaux d'indice et inflation globale PCE
    ("USCPI", "Indice des prix CPI (niveau)", "États-Unis", "indice", [F("CPIAUCSL")]),
    ("USCCPI", "Indice des prix CPI core (niveau)", "États-Unis", "indice", [F("CPILFESL")]),
    ("USPCEYY", "Inflation PCE globale (a/a)", "États-Unis", "%", [F("PCEPI", "pc1")]),
    ("USPCEMM", "Inflation PCE core (m/m)", "États-Unis", "%", [F("PCEPILFE", "pch")]),
    ("USINFE", "Anticipations d'inflation 5 ans dans 5 ans", "États-Unis", "%", [F("T5YIFR", freq="m")]),
    ("USBE10", "Point mort d'inflation 10 ans (breakeven)", "États-Unis", "%", [F("T10YIE", freq="m")]),
    # Emploi et activité (l'indice ISM est payant : enquêtes régionales de la Fed et CFNAI en substitut)
    ("USADP", "Emploi privé ADP (m/m)", "États-Unis", "milliers", [F("ADPMNUSNERSA", "chg", scale=0.001), F("ADPWNUSNERSA", "chg", freq="m", scale=0.001)]),
    ("USCJC", "Inscriptions continues au chômage", "États-Unis", "inscriptions", [F("CCSA")]),
    ("USPHIL", "Enquête manufacturière Fed de Philadelphie (substitut ISM)", "États-Unis", "indice", [F("GACDFSA066MSFRBPHI")]),
    ("USCFNAI", "Indice d'activité nationale de la Fed de Chicago CFNAI (substitut ISM)", "États-Unis", "indice", [F("CFNAI")]),
    ("USDGO", "Commandes de biens durables (m/m)", "États-Unis", "%", [F("DGORDER", "pch")]),
    # Taux monétaires et de marché
    ("USEFFR", "Taux effectif des fed funds (EFFR)", "États-Unis", "%", [F("EFFR", freq="m"), F("DFF", freq="m")]),
    ("USSOFR", "Taux SOFR", "États-Unis", "%", [F("SOFR", freq="m")]),
    ("USIORB", "Rémunération des réserves (IORB)", "États-Unis", "%", [F("IORB", freq="m")]),
    ("US03MY", "Taux à 3 mois (T-bill)", "États-Unis", "%", [F("DGS3MO", freq="m")]),
    ("US05Y", "Taux à 5 ans (Trésor)", "États-Unis", "%", [F("DGS5", freq="m")]),
    ("US30Y", "Taux à 30 ans (Trésor)", "États-Unis", "%", [F("DGS30", freq="m")]),
    ("US10YR", "Taux réel à 10 ans (TIPS)", "États-Unis", "%", [F("DFII10", freq="m")]),
    ("USIGS", "Spread crédit Investment Grade (ICE BofA)", "États-Unis", "%", [F("BAMLC0A0CM", freq="m")]),
    ("USHYS", "Spread crédit High Yield (ICE BofA)", "États-Unis", "%", [F("BAMLH0A0HYM2", freq="m")]),
    # ── Zone euro
    ("EUINTR", "Taux de dépôt BCE", "Zone euro", "%", [("ecb", "FM/D.U2.EUR.4F.KR.DFR.LEV"), F("ECBDFR", freq="m")]),
    ("EUMRO", "Taux de refinancement BCE", "Zone euro", "%", [("ecb", "FM/D.U2.EUR.4F.KR.MRR_FR.LEV"), F("ECBMRRFR", freq="m")]),
    ("EUESTR", "Taux €STR", "Zone euro", "%", [F("ECBESTRVOLWGTTRMDMNRT", freq="m")]),
    ("EURIBOR3M", "Euribor 3 mois", "Zone euro", "%", [("ecb", "FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA"), F("IR3TIB01EZM156N")]),
    ("EU02Y", "Taux à 2 ans zone euro AAA (BCE)", "Zone euro", "%", [("ecb", "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y")]),
    ("EU10Y", "Taux à 10 ans zone euro AAA (BCE)", "Zone euro", "%", [("ecb", "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y")]),
    ("EUIRYY", "Inflation HICP / CPI (IPCH, a/a)", "Zone euro", "%", [("estat", "prc_hicp_manr", "geo=EA20&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=EA20&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=EA20&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.U2.N.000000.4.ANR"), F("CP0000EZ19M086NEST", "pc1")]),
    ("EUCIR", "Inflation sous-jacente (IPCH hors énergie et alimentation, a/a)", "Zone euro", "%", [("estat", "prc_hicp_manr", "geo=EA20&coicop=TOT_X_NRG_FOOD"), ("estat", "prc_hicp_minr", "geo=EA20&coicop18=TOT_X_NRG_FOOD&unit=RCH_A"), ("ecb", "ICP/M.U2.N.XEF000.4.ANR")]),
    ("EUUR", "Taux de chômage", "Zone euro", "%", [("estat", "une_rt_m", "geo=EA20&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), ("ecb", "LFSI/M.I9.S.UNEHRT.TOTAL0.15_74.T"), F("LRHUTTTTEZM156S")]),
    ("EUGDPYY", "Croissance du PIB (a/a)", "Zone euro", "%", [F("CLVMNACSCAB1GQEA19", "pc1"), F("CLVMNACSCAB1GQEA20", "pc1")]),
    ("EUGDP", "PIB nominal (rythme annuel)", "Zone euro", "Md€", [F("CPMNACSCAB1GQEA20", scale=A), F("CPMNACSCAB1GQEA19", scale=A)]),
    # ── Allemagne, France, Italie
    ("DEIRYY", "Inflation (a/a)", "Allemagne", "%", [("estat", "prc_hicp_manr", "geo=DE&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=DE&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=DE&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.DE.N.000000.4.ANR"), F("CPALTT01DEM659N"), ("wb", "DE", "FP.CPI.TOTL.ZG")]),
    ("DEUR", "Taux de chômage", "Allemagne", "%", [("estat", "une_rt_m", "geo=DE&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), F("LRHUTTTTDEM156S")]),
    ("DEGDPYY", "Croissance du PIB (a/a)", "Allemagne", "%", [F("CLVMNACSCAB1GQDE", "pc1")]),
    ("DEGDP", "PIB nominal (rythme annuel)", "Allemagne", "Md€", [F("CPMNACSCAB1GQDE", scale=A)]),
    ("DEGDPQQ", "Croissance du PIB (t/t)", "Allemagne", "%", [("estat", "namq_10_gdp", "geo=DE&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQDE", "pch")]),
    ("DE10Y", "Taux à 10 ans (Bund)", "Allemagne", "%", [F("IRLTLT01DEM156N")]),
    ("FRIRYY", "Inflation (a/a)", "France", "%", [("estat", "prc_hicp_manr", "geo=FR&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=FR&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=FR&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.FR.N.000000.4.ANR"), F("CPALTT01FRM659N"), ("wb", "FR", "FP.CPI.TOTL.ZG")]),
    ("FRUR", "Taux de chômage", "France", "%", [("estat", "une_rt_m", "geo=FR&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT"), F("LRHUTTTTFRM156S")]),
    ("FRGDPYY", "Croissance du PIB (a/a)", "France", "%", [F("CLVMNACSCAB1GQFR", "pc1")]),
    ("FRGDP", "PIB nominal (rythme annuel)", "France", "Md€", [F("CPMNACSCAB1GQFR", scale=A)]),
    ("FRGDPQQ", "Croissance du PIB (t/t)", "France", "%", [("estat", "namq_10_gdp", "geo=FR&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQFR", "pch")]),
    ("FR10Y", "Taux à 10 ans (OAT)", "France", "%", [F("IRLTLT01FRM156N")]),
    ("ITIRYY", "Inflation (a/a)", "Italie", "%", [("estat", "prc_hicp_manr", "geo=IT&coicop=CP00"), ("estat", "prc_hicp_minr", "geo=IT&coicop18=TOTAL&unit=RCH_A"), ("estat", "teicp000", "geo=IT&coicop=CP00&unit=RCH_A"), ("ecb", "ICP/M.IT.N.000000.4.ANR"), ("wb", "IT", "FP.CPI.TOTL.ZG")]),
    ("ITGDPYY", "Croissance du PIB (a/a)", "Italie", "%", [F("CLVMNACSCAB1GQIT", "pc1"), F("NGDPRSAXDCITQ", "pc1")]),
    ("ITGDP", "PIB nominal (rythme annuel)", "Italie", "Md€", [F("CPMNACSCAB1GQIT", scale=A)]),
    ("ITGDPQQ", "Croissance du PIB (t/t)", "Italie", "%", [("estat", "namq_10_gdp", "geo=IT&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQIT", "pch")]),
    ("ESGDPYY", "Croissance du PIB (a/a)", "Espagne", "%", [F("CLVMNACSCAB1GQES", "pc1"), F("NGDPRSAXDCESQ", "pc1")]),
    ("ESGDP", "PIB nominal (rythme annuel)", "Espagne", "Md€", [F("CPMNACSCAB1GQES", scale=A)]),
    ("ESGDPQQ", "Croissance du PIB (t/t)", "Espagne", "%", [("estat", "namq_10_gdp", "geo=ES&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQES", "pch")]),
    ("EUGDPQQ", "Croissance du PIB (t/t)", "Zone euro", "%", [("estat", "namq_10_gdp", "geo=EA20&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"), F("CLVMNACSCAB1GQEA20", "pch"), F("CLVMNACSCAB1GQEA19", "pch")]),
    ("IT10Y", "Taux à 10 ans (BTP)", "Italie", "%", [F("IRLTLT01ITM156N")]),
    # ── Royaume-Uni
    ("GBINTR", "Taux directeur BoE (Bank Rate)", "Royaume-Uni", "%", [("boe", "IUDBEDR"), F("IRSTCB01GBM156N")]),
    ("GBSONIA", "Taux SONIA", "Royaume-Uni", "%", [("boe", "IUDSOIA")]),
    ("GBIRYY", "Inflation (IPC, a/a)", "Royaume-Uni", "%", [("ons", "D7G7", "MM23"), F("CPALTT01GBM659N"), F("GBRCPIALLMINMEI", "pc1")]),
    ("GBUR", "Taux de chômage", "Royaume-Uni", "%", [F("LRHUTTTTGBM156S")]),
    ("GBGDPYY", "Croissance du PIB (a/a)", "Royaume-Uni", "%", [F("CLVMNACSCAB1GQUK", "pc1"), F("NGDPRSAXDCGBQ", "pc1")]),
    ("GBGDP", "PIB nominal (rythme annuel)", "Royaume-Uni", "Md£", [F("UKNGDP", scale=A), F("CPMNACSCAB1GQUK", scale=A)]),
    ("GBGDPQQ", "Croissance du PIB (t/t)", "Royaume-Uni", "%", [F("CLVMNACSCAB1GQUK", "pch")]),
    ("GB10Y", "Taux à 10 ans (Gilt)", "Royaume-Uni", "%", [F("IRLTLT01GBM156N")]),
    # ── Suisse (pertinent pour Monaco)
    ("CHINTR", "Taux directeur BNS", "Suisse", "%", [("snb", "snbgwdzid", "LZ"), F("IRSTCB01CHM156N"), F("IRSTCI01CHM156N")]),
    ("CHIRYY", "Inflation (a/a)", "Suisse", "%", [("wb", "CH", "FP.CPI.TOTL.ZG"), F("CPALTT01CHM659N"), F("CHECPIALLMINMEI", "pc1")]),
    ("CHGDPYY", "Croissance du PIB (a/a)", "Suisse", "%", [F("CLVMNACSCAB1GQCH", "pc1"), F("NGDPRSAXDCCHQ", "pc1")]),
    ("CHGDP", "PIB nominal (rythme annuel)", "Suisse", "Md CHF", [F("CPMNACSCAB1GQCH", scale=A)]),
    ("CHGDPQQ", "Croissance du PIB (t/t)", "Suisse", "%", [F("CLVMNACSCAB1GQCH", "pch")]),
    ("CH10Y", "Taux à 10 ans (Confédération)", "Suisse", "%", [F("IRLTLT01CHM156N")]),
    # ── Japon
    ("JPINTR", "Taux directeur BoJ", "Japon", "%", [F("IRSTCB01JPM156N")]),
    ("JPIRYY", "Inflation (a/a)", "Japon", "%", [("wb", "JP", "FP.CPI.TOTL.ZG"), F("CPALTT01JPM659N"), F("JPNCPIALLMINMEI", "pc1")]),
    ("JPUR", "Taux de chômage", "Japon", "%", [F("LRUN64TTJPM156S"), F("LRHUTTTTJPM156S")]),
    ("JPGDPYY", "Croissance du PIB (a/a)", "Japon", "%", [F("JPNRGDPEXP", "pc1")]),
    ("JPGDP", "PIB nominal (rythme annuel)", "Japon", "Md¥", [F("JPNNGDP")]),
    ("JPGDPQQ", "Croissance du PIB (t/t)", "Japon", "%", [F("JPNRGDPEXP", "pch")]),
    ("JP10Y", "Taux à 10 ans (JGB)", "Japon", "%", [F("IRLTLT01JPM156N")]),
    # ── Chine
    ("CNIRYY", "Inflation (a/a)", "Chine", "%", [("wb", "CN", "FP.CPI.TOTL.ZG"), F("CPALTT01CNM659N"), F("CHNCPIALLMINMEI", "pc1")]),
    ("CNGDPYY", "Croissance du PIB (a/a)", "Chine", "%", [F("CHNGDPNQDSMEI", "pc1"), F("NGDPRXDCCNA", "pc1")]),
    ("CNGDP", "PIB nominal (annuel, Banque mondiale)", "Chine", "Md$", [F("MKTGDPCNA646NWDB", scale=1e-9)]),
    # ── Inde
    ("INGDP", "PIB nominal (annuel, Banque mondiale)", "Inde", "Md$", [F("MKTGDPINA646NWDB", scale=1e-9)]),
    ("INGDPYY", "Croissance du PIB (a/a)", "Inde", "%", [F("NAEXKP01INQ659S"), F("MKTGDPINA646NWDB", "pc1")]),
]

SRC_LABEL = {"fred": "FRED (Fed de St. Louis)", "ecb": "BCE — ECB Data Portal", "boe": "Bank of England — IADB",
             "snb": "Banque nationale suisse — data.snb.ch", "ons": "Office for National Statistics (Royaume-Uni)",
             "wb": "Banque mondiale (annuel)",
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
                if o and o["date"] > obs[-1][0]:
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


def get_estat(dataset, filters):
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
        obs.append((f"{y}-{mo:02d}-01", float(v)))
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
