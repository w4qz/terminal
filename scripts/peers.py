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
    "Majors pétrolières intégrées": ["TTE.PA", "SHEL.L", "BP.L", "XOM", "CVX", "ENI.MI", "EQNR.OL", "REP.MC", "GALP.LS"],
    "Services pétroliers": ["SLB", "HAL", "BKR"],
    "Constructeurs aéronautiques": ["AIR.PA", "BA", "EMBR3.SA"],
    "Motoristes et équipementiers aéronautiques": ["SAF.PA", "GE", "RR.L", "MTX.DE", "HWM", "TDG"],
    "Défense": ["LMT", "NOC", "GD", "RTX", "LHX", "HII", "BA.L", "HO.PA", "RHM.DE", "LDO.MI", "SAAB-B.ST", "AM.PA"],
    "Luxe": ["MC.PA", "RMS.PA", "KER.PA", "CFR.SW", "MONC.MI", "BC.MI", "BRBY.L", "UHR.SW", "RACE.MI", "TPR", "CPRI"],
    "Beauté et cosmétiques": ["OR.PA", "EL", "BEI.DE", "COTY", "4911.T", "PUIG.MC"],
    "Spiritueux": ["RI.PA", "DGE.L", "CPR.MI", "RCO.PA", "BF-B", "STZ"],
    "Boissons sans alcool": ["KO", "PEP", "KDP", "MNST", "CCEP"],
    "Agroalimentaire": ["BN.PA", "NESN.SW", "ULVR.L", "MDLZ", "GIS", "KHC"],
    "Distribution alimentaire": ["CA.PA", "TSCO.L", "AD.AS", "SBRY.L", "WMT", "COST", "KR"],
    "Tabac": ["BATS.L", "IMB.L", "PM", "MO"],
    "Banques européennes": ["BNP.PA", "GLE.PA", "ACA.PA", "DBK.DE", "CBK.DE", "SAN.MC", "BBVA.MC", "ISP.MI",
                            "UCG.MI", "INGA.AS", "HSBA.L", "BARC.L", "LLOY.L", "UBSG.SW", "KBC.BR"],
    "Banques américaines": ["JPM", "BAC", "C", "WFC", "GS", "MS", "USB", "PNC"],
    "Assurance": ["CS.PA", "ALV.DE", "MUV2.DE", "ZURN.SW", "G.MI", "AV.L", "LGEN.L", "SREN.SW", "HNR1.DE", "AIG", "MET", "CB"],
    "Paiements": ["V", "MA", "AXP", "PYPL", "ADYEN.AS", "FI"],
    "Pharmacie": ["SAN.PA", "NOVN.SW", "ROG.SW", "AZN.L", "GSK.L", "NOVO-B.CO", "BAYN.DE",
                  "PFE", "MRK", "LLY", "JNJ", "ABBV", "BMY"],
    "Automobile": ["STLAP.PA", "RNO.PA", "VOW3.DE", "BMW.DE", "MBG.DE", "P911.DE", "7203.T", "7267.T", "TSLA", "GM", "F"],
    "Pneumatiques": ["ML.PA", "CON.DE", "PIRC.MI", "5108.T"],
    "Semi-conducteurs": ["NVDA", "AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "NXPI", "STMPA.PA", "IFX.DE", "TSM", "ARM"],
    "Équipements pour semi-conducteurs": ["ASML.AS", "AMAT", "LRCX", "KLAC", "ASM.AS", "8035.T"],
    "Logiciels": ["MSFT", "ORCL", "SAP.DE", "CRM", "ADBE", "NOW", "INTU", "DSY.PA"],
    "Plateformes internet": ["GOOGL", "META", "AMZN", "NFLX"],
    "Services informatiques": ["CAP.PA", "ACN", "IBM", "SOP.PA", "CTSH", "INFY"],
    "Publicité": ["PUB.PA", "WPP.L", "OMC", "IPG"],
    "Télécoms européens": ["ORA.PA", "DTE.DE", "TEF.MC", "VOD.L", "BT-A.L", "KPN.AS", "SCMN.SW", "TIT.MI"],
    "Énergéticiens européens": ["ENGI.PA", "IBE.MC", "ENEL.MI", "EOAN.DE", "RWE.DE", "NG.L", "SSE.L", "EDP.LS"],
    "Eau et environnement": ["VIE.PA", "SVT.L", "UU.L"],
    "Gaz industriels": ["AI.PA", "LIN", "APD"],
    "Équipements électriques et automatismes": ["SU.PA", "ABBN.SW", "SIE.DE", "LR.PA", "ETN", "EMR"],
    "Matériaux de construction": ["SGO.PA", "HOLN.SW", "HEI.DE", "CRH"],
    "Construction et concessions": ["DG.PA", "EN.PA", "FGR.PA", "FER.AS", "ACS.MC", "HOT.DE", "SKA-B.ST"],
    "Mines": ["RIO.L", "AAL.L", "GLEN.L", "BHP", "VALE", "FCX"],
    "Hôtellerie": ["AC.PA", "IHG.L", "MAR", "HLT"],
    "Compagnies aériennes": ["AF.PA", "LHA.DE", "IAG.L", "RYA.IR", "DAL", "UAL"],
}


def all_peer_tickers() -> list[str]:
    seen, out = set(), []
    for lst in PEER_GROUPS.values():
        for s in lst:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out
