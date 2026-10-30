# Terminal — pipeline de données de marché automatisé

Terminal de marché personnel (indices, change, matières premières, taux, news, portefeuille, DCF, Monte Carlo) alimenté toutes les 30 minutes par un pipeline gratuit.

## Architecture

```
GitHub Actions (toutes les 30 minutes : h00 et h30 UTC)
   └─ scripts/update_data.py
        ├─ Prix ............ Yahoo Finance (yfinance), différés
        ├─ Courbe des taux .. FRED (Réserve fédérale de St. Louis)
        ├─ News ............. flux RSS publics (FT, CNBC, BBC, Les Échos)
        └─ Synthèse IA ...... Google Gemini (niveau gratuit), optionnelle
   └─ commit de data.json
GitHub Pages
   └─ index.html lit data.json à l'ouverture, puis toutes les 10 minutes
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
