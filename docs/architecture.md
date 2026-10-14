# Architecture de production

## Stockage et snapshots

SQLite est la source de vérité locale, par défaut `data/mtg_manager.sqlite3`. Au premier démarrage, `collection.json`, `watchlist.json` et `price_history.json` sont importés une seule fois ; les JSON originaux restent disponibles comme sauvegarde. `MTG_DATABASE_PATH` permet de déplacer la base. SQLite convient à une instance applicative avec disque persistant ; plusieurs instances GitHub Actions ne doivent pas partager ce fichier. Un hébergement multi-instance nécessite un adaptateur PostgreSQL/Supabase/Neon.

Les prix sont enregistrés une fois par jour et impression, foil, langue et état. Une nouvelle observation le même jour met à jour le snapshot plutôt que de créer un faux échantillon de volatilité.

## Scryfall et fraîcheur

Le worker télécharge le flux Bulk `default_cards` une fois par jour, le met en cache dans `data/cache/`, puis compare impressions et légalités. Il peut aussi être lancé manuellement avec `python -m services.scryfall_bulk`. Le flux est analysé en streaming.

Lancer la veille persistante avec `python -m services.monitor_worker`. Les états de santé Scryfall API, Scryfall Bulk et Reddit sont indépendants et visibles dans Nexus ; `NEXUS_SCRYFALL_MAX_AGE_HOURS` et `NEXUS_SCRYFALL_BULK_MAX_AGE_HOURS` (36 h par défaut) ainsi que `NEXUS_REDDIT_MAX_AGE_HOURS` (48 h) règlent la fraîcheur.

## Signaux et portefeuille

Les risques de réimpression n’affichent un rendement historique à +30/+90 jours que si les snapshots et les événements Bulk correspondants existent. Les changements de prix seuls ne confirment pas une manipulation ; le score proxy requiert des données d’annonces/vendeurs qui ne sont pas fournies par Scryfall.

Les métriques de portefeuille utilisent les snapshots disponibles et retombent sur le prix de vente configuré si l’impression n’a pas d’historique. Les frais Cardmarket (5 % par défaut), frais d’envoi et décotes par état sont des paramètres estimatifs à ajuster à la transaction réelle.

## Copilote et Discord

Le copilote peut appeler uniquement les outils listés dans `agents/tools.py`. Le fournisseur OpenAI-compatible est optionnel (`OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`) ; sans clé, l’assistant local reste actif. Les nombres du texte modèle sont vérifiés contre les résultats d’outils.

Les commandes slash `/prix`, `/portfolio` et `/watch` nécessitent `DISCORD_BOT_TOKEN`. Le rapport quotidien webhook continue d’utiliser `DISCORD_WEBHOOK_URL`. `NEXUS_MIN_ALERT_SEVERITY` accepte `LOW`, `MEDIUM` ou `HIGH`.

Les decklists texte, Moxfield et Archidekt sont acceptées. Les coûts des cartes manquantes sont des estimations Scryfall EUR, pas des offres Cardmarket fermes. Les cartes du stock absentes d’une decklist ne sont pas automatiquement considérées comme jamais jouées.