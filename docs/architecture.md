# Phase 1 : stock JSON et alertes Discord

## Périmètre actif

La phase 1 utilise `data/collection.json` comme inventaire et `data/price_history.json` comme historique de prix. Le stockage SQL présent dans le dépôt est une étape ultérieure et n'est pas utilisé par le parcours principal de l'application. La watchlist, les spoilers et les signaux communautaires sont hors périmètre.

Seules les entrées `FOR_SALE` avec un identifiant Scryfall sont analysées. Les cartes vendues et les cartes qui ne figurent pas dans l'inventaire ne sont jamais demandées à Scryfall par le moniteur de phase 1.

Chaque analyse ajoute un snapshot horodaté pour le prix Scryfall EUR normal ou foil. L'historique existant est conservé. La variation est calculée entre deux relevés consécutifs de la même impression et finition. Une hausse ou baisse d'au moins 10 % crée une alerte dans `data/price_alerts.json` et l'envoie au webhook Discord configuré par `DISCORD_WEBHOOK_URL`. Les alertes non envoyées restent en attente pour une tentative ultérieure.

Les prix Scryfall sont des références indicatives, pas des transactions garanties. Scryfall ne donne pas ici un prix individualisé par langue ou état; la courbe ne doit donc pas être interprétée comme un prix exact pour toutes les variantes physiques.

## Lancer l'application et le suivi

Installer les dépendances du projet avec `python -m pip install -r requirements.txt`, puis lancer l'interface avec `streamlit run app.py`.

Configurer `DISCORD_WEBHOOK_URL` dans l'environnement local sans l'inscrire dans Git. L'interface fournit un bouton d'alerte test Discord, qui n'analyse ni ne modifie le stock.

Lancer une analyse ponctuelle avec `python -m services.phase_one_monitor --once`. Pour un suivi régulier et des snapshots intrajournaliers, lancer le processus persistant avec `python -m services.phase_one_monitor`. L'intervalle par défaut est d'une heure et peut être réglé avec `NEXUS_POLL_INTERVAL_SECONDS` (minimum cinq minutes). Le processus doit rester actif sur une machine qui conserve les fichiers JSON.

Envoyer une alerte de test sans analyser l'inventaire avec `python -m services.phase_one_monitor --test-discord`. Le webhook doit être configuré dans l'environnement du processus.

Le workflow GitHub Actions ne lance plus d'analyses planifiées : un runner éphémère ne conserve pas `price_history.json` entre les exécutions, et publier le stock JSON dans Git n'est pas approprié pour des données financières sensibles. Le workflow manuel sert uniquement à vérifier la livraison Discord.

## Prochaines étapes

Les commandes slash Discord, la watchlist, le backtesting, les signaux avancés et la migration vers SQL ne font pas partie du parcours actif de phase 1. La migration SQL devra être planifiée séparément, avec sauvegarde et réconciliation explicite des données JSON avant tout changement de source de vérité.