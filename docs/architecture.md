# Architecture de production

## Stockage et snapshots

SQLite est la source de vérité locale, par défaut `data/mtg_manager.sqlite3`. Au premier démarrage, `collection.json`, `watchlist.json` et `price_history.json` sont importés ; les JSON originaux restent disponibles comme sauvegarde, et les nouveaux relevés JSON sont fusionnés sans remplacer le stock. `MTG_DATABASE_PATH` permet de déplacer la base locale. Pour PostgreSQL, définir `DATABASE_URL` (ou `MTG_DATABASE_URL`) sur une URI `postgresql://...` ; la connexion doit autoriser TLS en production. GitHub Actions exige le secret `DATABASE_URL`, car son disque local est éphémère.

Dans GitHub, créer un secret de dépôt `DATABASE_URL` contenant la chaîne de connexion PostgreSQL de Neon/Supabase. Le workflow installe `requirements.txt`, initialise le schéma, exécute le Bulk, les signaux, le watcher et le rapport Discord. Il attache une sauvegarde gzip à chaque exécution ; les artifacts sont conservés 30 jours. Le webhook Discord utilise le secret `DISCORD_WEBHOOK_URL`.

Les sauvegardes sont compressées puis chiffrées/authentifiées avec Fernet. Définir `BACKUP_ENCRYPTION_KEY` dans l’environnement ; générer une clé avec `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` et la conserver séparément de la base. Ajouter la même clé aux secrets GitHub Actions sous `BACKUP_ENCRYPTION_KEY`. Sans cette clé, création/restauration échoue volontairement. Créer une archive avec `python -m database.backup create backup.json.gz.enc` et la restaurer avec `python -m database.backup restore backup.json.gz.enc` après confirmation. L’option `--allow-plaintext-legacy` n’est prévue que pour les anciennes archives gzip non chiffrées. L’interface Streamlit télécharge/restaure le format chiffré. Les archives Actions ne sont plus publiées comme JSON dans Git ; elles restent des artifacts privés 30 jours.

Les prix sont enregistrés une fois par jour et impression, foil, langue et état. Une nouvelle observation le même jour met à jour le snapshot plutôt que de créer un faux échantillon de volatilité.

## Scryfall et fraîcheur

Le worker télécharge le flux Bulk `default_cards` une fois par jour, le met en cache dans `data/cache/`, puis compare impressions et légalités. Il peut aussi être lancé manuellement avec `python -m services.scryfall_bulk`. Le flux est analysé en streaming.

Lancer la veille persistante avec `python -m services.monitor_worker`. Les états de santé Scryfall API, Scryfall Bulk et Reddit sont indépendants et visibles dans Nexus ; `NEXUS_SCRYFALL_MAX_AGE_HOURS` et `NEXUS_SCRYFALL_BULK_MAX_AGE_HOURS` (36 h par défaut) ainsi que `NEXUS_REDDIT_MAX_AGE_HOURS` (48 h) règlent la fraîcheur.

## Signaux et portefeuille

Le risque de réimpression combine le flag Reserved List, l’âge de la dernière sortie Scryfall, les intervalles observés entre éditions et, si disponibles, les rendements de prix à +30/+90 jours après les réimpressions.

L’indice anti-buyout n’utilise aucune API Cardmarket : il compare un spike sur sept jours aux snapshots SQLite, mesure son écart à la volatilité récente, puis neutralise le score si un changement de légalité ou une réimpression est détecté sur la période. Le rang `edhrec_rank` du Bulk est historisé quotidiennement ; son amélioration récente peut qualifier la hausse de soutenue par l’adoption Commander. Un rang élevé seul indique une popularité, pas une accélération de demande. Ce score est une alerte heuristique, jamais une preuve de manipulation ni une mesure de volume vendu.

Les métriques de portefeuille utilisent les snapshots disponibles et retombent sur le prix de vente configuré si l’impression n’a pas d’historique. Les frais Cardmarket (5 % par défaut), frais d’envoi et décotes par état sont des paramètres estimatifs à ajuster à la transaction réelle.

## Copilote et Discord

Le copilote peut appeler uniquement les outils listés dans `agents/tools.py`. Le fournisseur OpenAI-compatible est optionnel (`OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`) ; sans clé, l’assistant local reste actif. Les nombres du texte modèle sont vérifiés contre les résultats d’outils.

Les commandes slash `/prix`, `/portfolio` et `/watch` nécessitent `DISCORD_BOT_TOKEN`. Le rapport quotidien webhook continue d’utiliser `DISCORD_WEBHOOK_URL`. `NEXUS_MIN_ALERT_SEVERITY` accepte `LOW`, `MEDIUM` ou `HIGH`.

Les decklists texte, Moxfield et Archidekt sont acceptées. Les coûts des cartes manquantes sont des estimations Scryfall EUR, pas des offres Cardmarket fermes. Les cartes du stock absentes d’une decklist ne sont pas automatiquement considérées comme jamais jouées.