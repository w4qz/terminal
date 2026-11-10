"""
Groupes de pairs cohérents pour la fonction RV du Terminal (comparables par métier, pas seulement par secteur).

La classification Yahoo/GICS range Airbus, Safran et Lockheed dans la même industrie
(« Aerospace & Defense ») alors que leurs métiers et leurs cycles n'ont rien à voir.
Ces groupes reprennent la logique d'un analyste : constructeurs avec constructeurs,
majors pétrolières avec majors, banques européennes avec banques européennes…

build_fundamentals.py ajoute ces titres au roulement des fondamentaux et publie peers.json,
que la page lit. Un titre absent de ces groupes retombe sur son industrie Yahoo.
"""

PEER_GROUPS = {
    "Integrated oil majors": ["TTE.PA", "SHEL.L", "BP.L", "XOM", "CVX", "ENI.MI", "EQNR.OL", "REP.MC", "GALP.LS"],
    "Oil services": ["SLB", "HAL", "BKR"],
    "Aircraft manufacturers": ["AIR.PA", "BA", "ERJ"],
    "Aero engines and equipment": ["SAF.PA", "GE", "RR.L", "MTX.DE", "HWM", "TDG"],
    "Defence": ["LMT", "NOC", "GD", "RTX", "LHX", "HII", "BA.L", "HO.PA", "RHM.DE", "LDO.MI", "SAAB-B.ST", "AM.PA"],
    "Luxury": ["MC.PA", "RMS.PA", "KER.PA", "CFR.SW", "MONC.MI", "BC.MI", "BRBY.L", "UHR.SW", "RACE.MI", "TPR", "CPRI"],
    "Beauty and cosmetics": ["OR.PA", "EL", "BEI.DE", "COTY", "4911.T", "PUIG.MC"],
    "Spirits": ["RI.PA", "DGE.L", "CPR.MI", "RCO.PA", "BF-B", "STZ"],
    "Soft drinks": ["KO", "PEP", "KDP", "MNST", "CCEP"],
    "Food producers": ["BN.PA", "NESN.SW", "ULVR.L", "MDLZ", "GIS", "KHC"],
    "Food retail": ["CA.PA", "TSCO.L", "AD.AS", "SBRY.L", "WMT", "COST", "KR"],
    "Tobacco": ["BATS.L", "IMB.L", "PM", "MO"],
    "European banks": ["BNP.PA", "GLE.PA", "ACA.PA", "DBK.DE", "CBK.DE", "SAN.MC", "BBVA.MC", "ISP.MI",
                            "UCG.MI", "INGA.AS", "HSBA.L", "BARC.L", "LLOY.L", "UBSG.SW", "KBC.BR"],
    "US banks": ["JPM", "BAC", "C", "WFC", "GS", "MS", "USB", "PNC"],
    "Insurance": ["CS.PA", "ALV.DE", "MUV2.DE", "ZURN.SW", "G.MI", "AV.L", "LGEN.L", "SREN.SW", "HNR1.DE", "AIG", "MET", "CB"],
    "Payments": ["V", "MA", "AXP", "PYPL", "ADYEN.AS", "FISV"],
    "Pharmaceuticals": ["SAN.PA", "NOVN.SW", "ROG.SW", "AZN.L", "GSK.L", "NOVO-B.CO", "BAYN.DE",
                  "PFE", "MRK", "LLY", "JNJ", "ABBV", "BMY"],
    "Autos": ["STLAP.PA", "RNO.PA", "VOW3.DE", "BMW.DE", "MBG.DE", "P911.DE", "7203.T", "7267.T", "TSLA", "GM", "F"],
    "Tyres": ["ML.PA", "CON.DE", "PIRC.MI", "5108.T"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "NXPI", "STMPA.PA", "IFX.DE", "TSM", "ARM"],
    "Semiconductor equipment": ["ASML.AS", "AMAT", "LRCX", "KLAC", "ASM.AS", "8035.T"],
    "Software": ["MSFT", "ORCL", "SAP.DE", "CRM", "ADBE", "NOW", "INTU", "DSY.PA"],
    "Internet platforms": ["GOOGL", "META", "AMZN", "NFLX"],
    "IT services": ["CAP.PA", "ACN", "IBM", "SOP.PA", "CTSH", "INFY"],
    "Advertising": ["PUB.PA", "WPP.L", "OMC"],
    "European telecoms": ["ORA.PA", "DTE.DE", "TEF.MC", "VOD.L", "BT-A.L", "KPN.AS", "SCMN.SW", "TIT.MI"],
    "European utilities": ["ENGI.PA", "IBE.MC", "ENEL.MI", "EOAN.DE", "RWE.DE", "NG.L", "SSE.L", "EDP.LS"],
    "Water and environment": ["VIE.PA", "SVT.L", "UU.L"],
    "Industrial gases": ["AI.PA", "LIN", "APD"],
    "Electrical equipment and automation": ["SU.PA", "ABBN.SW", "SIE.DE", "LR.PA", "ETN", "EMR"],
    "Building materials": ["SGO.PA", "HOLN.SW", "HEI.DE", "CRH"],
    "Construction and concessions": ["DG.PA", "EN.PA", "FGR.PA", "FER.AS", "ACS.MC", "HOT.DE", "SKA-B.ST"],
    "Mining": ["RIO.L", "AAL.L", "GLEN.L", "BHP", "VALE", "FCX"],
    "Hotels": ["AC.PA", "IHG.L", "MAR", "HLT"],
    "Airlines": ["AF.PA", "LHA.DE", "IAG.L", "RYA.IR", "DAL", "UAL"],
}


def all_peer_tickers() -> list[str]:
    seen, out = set(), []
    for lst in PEER_GROUPS.values():
        for s in lst:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out
