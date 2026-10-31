# Terminal — pipeline de données de marché automatisé

Terminal de marché personnel (indices, change, matières premières, taux, news, portefeuille, DCF, Monte Carlo) alimenté toutes les 30 minutes par un pipeline gratuit.

## Architecture

```
GitHub Actions (toutes les 30 minutes : h00 et h30 UTC)
   └─ scripts/update_data.py
        ├─ Prix ............ Yahoo Finance (yfinance), différés
        ├─ Courbe des taux .. FRED (Réserve fédérale de St. Louis)
        ├─ News ............. flux RSS : myFT personnel, FT, CNBC, BBC, Le Monde
        └─ Synthèse IA ...... Google Gemini (niveau gratuit), optionnelle
   └─ scripts/build_universe.py
        ├─ Compositions ..... S&P 500, Nasdaq 100, FTSE 100, CAC 40, DAX,
        │                     Euro Stoxx 50, SMI, Nikkei 225 (Wikipédia, chaque semaine)
        └─ Catalogue ........ ~900 actions + indices, devises, matières premières,
                              taux US, crypto (Yahoo Finance)
   └─ scripts/build_fundamentals.py
        └─ Fondamentaux ..... tout le catalogue par roulement (120 titres par passage, ~4 h pour tout couvrir)
   └─ scripts/build_macro.py — onglet Macro, équivalents de fonctions Bloomberg
        ├─ ECO .............. derniers chiffres US/zone euro + calendrier (FRED, Fed)
        ├─ WIRP ............. probabilités des décisions de la Fed (futures fed funds)
        ├─ GC ............... courbes des taux US (FRED) et zone euro AAA (BCE)
        └─ BTMM ............. tableau de bord monétaire US (FRED)
   └─ commit de data.json, universe.json, fundamentals.json et macro.json
GitHub Pages
   └─ index.html lit data.json et universe.json à l'ouverture, puis toutes les 10 minutes
Google Apps Script (apps_script_ft.gs), toutes les 30 minutes
   └─ lit les newsletters FT du libellé Gmail « FT News » et écrit ft.json
      (titres, accroches et sujets du jour, lien vers le mail — jamais le texte des articles)
Cloudflare Worker (worker.js)
   └─ relais gratuit vers Yahoo Finance : recherche de n'importe quel ticker
      au monde, avec historique et graphique
```

## Principes

- **Aucune donnée sans source, horodatage et statut** (Clôture, Différé, Indicatif, Périmé).
- **Pas d'invention** : si une source échoue, la dernière valeur est conservée et marquée *Périmé*.
- **L'IA résume, elle ne décide pas** : la synthèse est étiquetée, ne cite que les données reçues et ne formule aucune recommandation.
- **Secrets côté serveur** : les clés API vivent dans les *Secrets* GitHub, jamais dans le code de la page.

## Limites connues

- `yfinance` est une bibliothèque non officielle ; en production on utiliserait un flux licencié (Bloomberg, Refinitiv).
- Les crons GitHub peuvent être décalés de quelques minutes.
- Les news RSS ne contiennent que titres, chapôs et liens, conformément aux droits des éditeurs.
