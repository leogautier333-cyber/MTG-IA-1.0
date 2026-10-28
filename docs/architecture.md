# Phase 1 : stock SQL et alertes Discord

## Périmètre actif

Le stock, l'historique de prix et les alertes de variation vivent dans la base SQL (`database/storage.py`) : PostgreSQL si `DATABASE_URL` est défini (Neon), sinon SQLite locale `data/mtg_manager.sqlite3`. C'est l'unique source de vérité. Les anciens fichiers `data/*.json` ne sont lus qu'une seule fois, à l'initialisation, pour migration ; ils sont ignorés par Git et peuvent servir de copie locale.

Organisation du code :
- `database/` : `storage.py` (connexion, schéma, stock, prix, alertes), `market_data.py` (impressions Scryfall, EDHREC, réimpressions), `snapshot.py` (export/restauration), `backup.py` (sauvegarde chiffrée), `healthcheck.py`.
- `services/inventory.py` : logique métier du stock, sans Streamlit.
- `data_manager.py` : façade Streamlit (lectures en cache 60 s, écritures qui invalident le cache).
- `services/phase_one_monitor.py` : analyse des prix et alertes Discord.
- `modules/` et le reste de `services/` : fonctions « Nexus » (watchlist, signaux, backtesting, assistant), affichées dans l'onglet « Nexus » de l'interface.

Seules les entrées `FOR_SALE` avec un identifiant Scryfall sont analysées. Les cartes vendues et les cartes qui ne figurent pas dans l'inventaire ne sont jamais demandées à Scryfall par le moniteur de phase 1.

Chaque analyse enregistre un relevé par jour et par variante (le dernier relevé du jour remplace le précédent) pour le prix Scryfall EUR normal ou foil. La variation est calculée entre deux relevés consécutifs de la même impression et finition. Une hausse ou baisse d'au moins 10 % crée une alerte (table `price_alerts`) et l'envoie au webhook Discord configuré par `DISCORD_WEBHOOK_URL`. Les alertes non envoyées restent en attente pour une tentative ultérieure.

Les prix Scryfall sont des références indicatives, pas des transactions garanties. Scryfall ne donne pas ici un prix individualisé par langue ou état; la courbe ne doit donc pas être interprétée comme un prix exact pour toutes les variantes physiques.

## Lancer l'application et le suivi

Installer les dépendances du projet avec `python -m pip install -r requirements.txt`, définir `DATABASE_URL` (PostgreSQL), vérifier avec `python -m database.healthcheck`, puis lancer l'interface avec `streamlit run app.py`.

Configurer `DISCORD_WEBHOOK_URL` dans l'environnement local sans l'inscrire dans Git. L'interface fournit un bouton d'alerte test Discord, qui n'analyse ni ne modifie le stock.

Lancer une analyse ponctuelle avec `python -m services.phase_one_monitor --once`. Pour un suivi régulier, lancer le processus persistant avec `python -m services.phase_one_monitor`. L'intervalle par défaut est d'une heure et peut être réglé avec `NEXUS_POLL_INTERVAL_SECONDS` (minimum cinq minutes).

Envoyer une alerte de test sans analyser l'inventaire avec `python -m services.phase_one_monitor --test-discord`. Le webhook doit être configuré dans l'environnement du processus.

GitHub Actions : `stock_analysis.yml` lance l'analyse chaque jour à 07:00 UTC (et à la demande) sur la base PostgreSQL, avec les secrets `DATABASE_URL` et `DISCORD_WEBHOOK_URL`. `daily_update.yml` reste un test manuel de livraison Discord. Sans `DATABASE_URL`, le moniteur refuse de tourner sur un runner pour ne pas écrire dans une base SQLite éphémère.
`backup.yml` crée chaque dimanche à 03:30 UTC (et à la demande) une sauvegarde chiffrée, publiée comme artefact GitHub
conservé 90 jours (secrets `DATABASE_URL` et `BACKUP_ENCRYPTION_KEY`).

## Tests

Les tests utilisent une base SQLite temporaire et neutralisent `DATABASE_URL` : ils ne touchent jamais la base de production. Lancer `python -m unittest discover -s tests`.

## Prochaines étapes

Les commandes slash Discord et l'assistant IA (`agents/`, `ai_engine.py`) ne sont pas branchés sur l'interface. Le découpage de `database/storage.py` est fait en grande partie ; il reste les alertes et la migration JSON à isoler si le fichier grossit.