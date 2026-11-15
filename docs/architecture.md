# Phase 1 : stock SQL et alertes Discord

## Périmètre actif

Le stock, l'historique de prix et les alertes de variation vivent dans la base SQL (`database/storage.py`) : PostgreSQL si `DATABASE_URL` est défini (Neon), sinon SQLite locale `data/mtg_manager.sqlite3`. C'est l'unique source de vérité. Les anciens fichiers `data/*.json` ne sont lus qu'une seule fois, à l'initialisation, pour migration ; ils sont ignorés par Git et peuvent servir de copie locale.

La migration refuse un fichier historique illisible ou une importation dans une table déjà remplie ; un fichier absent ne remplace jamais le stock existant. L'initialisation PostgreSQL utilise un verrou partagé entre processus. Une synchronisation Bulk vide ou dont la taille est inférieure à la moitié du catalogue existant est annulée : vérifier la source avant toute intervention manuelle.
Les évolutions SQL sont numérotées dans `metadata.schema_version` et exécutées sous verrou ; une version plus récente que l'application est refusée. La version 1 couvre l'évolution des colonnes Bulk existantes. La version 2 enregistre transactionnellement les changements de stock avec une copie avant/après, en commençant par un état initial à la migration.
La version 3 ajoute des triggers de validation des quantités et prix des nouvelles écritures d'inventaire. Elle ne modifie ni ne vérifie rétroactivement les lignes existantes ; une ancienne ligne invalide devra être corrigée avant sa prochaine modification ou restauration.
Un historique JSON réimporté ne remplace pas un relevé SQL déjà présent. En hébergement, `MTG_REQUIRE_POSTGRES=true` interdit le repli silencieux sur SQLite si l'URL de base est absente.

Organisation du code :
- `database/` : `storage.py` (connexion, schéma, stock, prix, alertes), `market_data.py` (impressions Scryfall, EDHREC, réimpressions), `snapshot.py` (export/restauration), `backup.py` (sauvegarde chiffrée), `healthcheck.py`.
- `services/inventory.py` : logique métier du stock, sans Streamlit.
- `data_manager.py` : façade Streamlit (lectures en cache 60 s, écritures qui invalident le cache).
- `services/phase_one_monitor.py` : analyse des prix et alertes Discord.
- `modules/` et le reste de `services/` : fonctions « Nexus » (watchlist, signaux, backtesting, assistant), affichées dans l'onglet « Nexus » de l'interface.
- `modules/sales_view.py` : affichage du bilan des ventes, alimenté par les services métier et la façade de lecture.
- `agents/tools.py` lit le stock par le service métier, sans importer la façade ou le cache Streamlit.

Seules les entrées `FOR_SALE` avec un identifiant Scryfall sont analysées. Les cartes vendues et les cartes qui ne figurent pas dans l'inventaire ne sont jamais demandées à Scryfall par le moniteur de phase 1.

Chaque analyse enregistre un relevé par jour et par variante (le dernier relevé du jour remplace le précédent) pour le prix Scryfall EUR normal ou foil. La variation est calculée entre deux relevés consécutifs de la même impression et finition. Une hausse ou baisse d'au moins 10 % crée une alerte (table `price_alerts`) et l'envoie au webhook Discord configuré par `DISCORD_WEBHOOK_URL`. Les alertes non envoyées restent en attente pour une tentative ultérieure.

L'analyse regroupe les lectures et écritures SQL par lots : les relevés, rangs et alertes sont enregistrés dans une même transaction. Une erreur pendant cette transaction annule ces écritures. Les journaux du workflow indiquent les durées de veille, préparation/Scryfall, calcul SQL et livraison Discord, sans afficher de secrets.
Les historiques du rapport hebdomadaire et de l'analyse du portefeuille sont aussi chargés par lots. Les invalidations de cache visent les données applicatives concernées plutôt que toutes les données Streamlit.
Les historiques de la watchlist et des prédictions de marché sont eux aussi lus par lots plutôt que par connexion SQL séparée pour chaque variante.
La synchronisation du catalogue Bulk lit et écrit les impressions par lots de 500, tout en préservant l'ordre de détection des réimpressions et l'annulation transactionnelle d'un flux incomplet.
La synchronisation Cardmarket indexe les fiches par article et variante au lieu de reparcourir tout le stock pour chaque ligne.
La préparation mémorise aussi les réponses Scryfall par produit pendant une heure dans le processus Streamlit (10 000 produits au maximum) ; les erreurs ne sont pas mémorisées. Au-delà ou après redémarrage, les produits sont à nouveau demandés.

Une variation strictement comprise entre -10 % et +10 % déclenche une alerte de stagnation : au maximum une par jour UTC et par impression/finition, pour limiter les répétitions. La comparaison est impossible au premier relevé, si l'ancien prix est nul, si Scryfall ne fournit pas de prix ou si la fiche ne possède pas d'identifiant Scryfall ; ces cas sont signalés dans le résultat de l'analyse. La stagnation décrit uniquement l'écart entre deux relevés, pas une tendance prolongée. Avec de nombreuses cartes, les alertes peuvent être nombreuses et certaines rester en attente si Discord refuse temporairement l'envoi.

Les alertes de stock en attente sont réunies en un seul message Discord par analyse, regroupées par hausse, baisse, stagnation, objectif et veille. Si le texte dépasse la limite d'un embed, le détail complet est joint en fichier texte au même message. Les alertes ne sont marquées envoyées qu'après acceptation par Discord ; les alertes de panne de source restent séparées.

Une seule analyse peut être active par base à la fois, y compris entre Streamlit et GitHub Actions : le verrou couvre la veille, les prix et la livraison, via un verrou transactionnel PostgreSQL ou un verrou SQLite local. Une analyse concurrente est refusée explicitement. Une interruption après acceptation Discord mais avant l'enregistrement de l'envoi peut encore entraîner un doublon : le webhook ne garantit pas une livraison exactement une fois.

Les prix Scryfall sont des références indicatives, pas des transactions garanties. Scryfall ne donne pas ici un prix individualisé par langue ou état; la courbe ne doit donc pas être interprétée comme un prix exact pour toutes les variantes physiques.
Les historiques agrégés du tableau de bord utilisent les relevés de référence FR/NM par impression et finition, sans écrasement par les autres variantes. Les coûts d'achat inconnus sont exclus des bénéfices et ROI : les rapports indiquent leur couverture. L'estimation d'une decklist est signalée comme partielle lorsque des prix manquent.
La courbe du portefeuille reconstitue les quantités et coûts en fin de journée UTC à partir de l'état initial et des mouvements enregistrés. Avant cet état initial, elle reste approximative. Les cartes sans prix disponible et les coûts inconnus sont exclus des totaux correspondants ; les copies des fiches dans l'historique augmentent la taille de la base et des sauvegardes. Une ancienne sauvegarde sans mouvements redémarre cet historique au moment de sa restauration.
Le calcul de marge et les totaux monétaires des tableaux de stock, de ventes, de l'historique et du rapport hebdomadaire utilisent une arithmétique décimale et un arrondi « moitié supérieure » au centime ; l'analyse du portefeuille agrège également les montants en décimal. Une suggestion de prix est arrondie au centime supérieur afin de ne pas passer sous le seuil demandé. Le stockage historique et d'autres calculs restent en nombres flottants ; les calculs décimaux ne restaurent pas une précision déjà perdue au stockage.

Les appels Scryfall ont un délai maximal et les erreurs HTTP interrompent l'opération au lieu d'être assimilées à des cartes introuvables. L'import CSV générique refuse les prix négatifs ou non finis et les quantités non entières ou inférieures à un.
Les écritures de stock, ventes et relevés contrôlent les quantités et montants à la frontière applicative : les quantités doivent être entières et strictement positives ; les prix doivent être finis et non négatifs. Les triggers SQL protègent aussi les colonnes de quantité et de prix de l'inventaire contre les écritures directes ; ils ne couvrent pas les champs du payload JSON ni les autres tables.

## Lancer l'application et le suivi

Installer les dépendances du projet avec `python -m pip install -r requirements.txt`, définir `DATABASE_URL` (PostgreSQL), vérifier avec `python -m database.healthcheck`, puis lancer l'interface avec `streamlit run app.py`.

Par défaut, l'interface conserve ses onglets classiques. Pour réduire le travail à chaque interaction, définir `MTG_LAZY_NAVIGATION=true` dans l'environnement ou les secrets Streamlit : un sélecteur remplace les onglets et seul l'écran sélectionné est exécuté. Les champs non enregistrés d'un écran peuvent être réinitialisés lors du changement de navigation ; terminer ou enregistrer une saisie avant de changer d'écran.

Configurer `DISCORD_WEBHOOK_URL` dans l'environnement local sans l'inscrire dans Git. L'interface fournit un bouton d'alerte test Discord, qui n'analyse ni ne modifie le stock.

Lancer une analyse ponctuelle avec `python -m services.phase_one_monitor --once`. Pour un suivi régulier, lancer le processus persistant avec `python -m services.phase_one_monitor`. L'intervalle par défaut est d'une heure et peut être réglé avec `NEXUS_POLL_INTERVAL_SECONDS` (minimum cinq minutes).

Envoyer une alerte de test sans analyser l'inventaire avec `python -m services.phase_one_monitor --test-discord`. Le webhook doit être configuré dans l'environnement du processus.

GitHub Actions : `stock_analysis.yml` lance l'analyse deux fois par jour (07:00 et 19:00 UTC, soit 8h/20h en heure d'hiver ; et à la demande) sur la base PostgreSQL, avec les secrets `DATABASE_URL` et `DISCORD_WEBHOOK_URL`. L'analyse ne concerne que le stock en vente : relevé de prix et de rang EDHREC, veille spoilers/réimpressions et Reddit (bans, rumeurs), puis récapitulatif Discord des alertes. Le contexte et le verdict sont conservés dans le récapitulatif et l'application ; ils viennent de `services/stock_advisor.py` (règles locales, sans API externe). `daily_update.yml` reste un test manuel de livraison Discord. Sans `DATABASE_URL`, le moniteur refuse de tourner sur un runner pour ne pas écrire dans une base SQLite éphémère.
`backup.yml` crée chaque dimanche à 03:30 UTC (et à la demande) une sauvegarde chiffrée, publiée comme artefact GitHub
conservé 90 jours (secrets `DATABASE_URL` et `BACKUP_ENCRYPTION_KEY`).
L'export utilise une vue transactionnelle cohérente et lit les tables par lots (curseur serveur sur PostgreSQL). Les nouvelles archives sont compressées puis chiffrées en flux avec AES-GCM authentifié, à partir de la clé de sauvegarde existante. Pour ces archives, la restauration vérifie d'abord l'authenticité, puis lit et insère les données par lots dans une transaction ; aucune copie déchiffrée temporaire n'est créée. Les anciennes archives Fernet restent restaurables mais leur restauration charge encore l'archive en mémoire.
`weekly_report.yml` envoie chaque lundi à 07:00 UTC un rapport Discord (valeur du stock, plus-value latente, plus fortes hausses/baisses sur 7 jours, ventes de la semaine), construit par `services/weekly_report.py`.

Objectif de prix : le champ « Objectif de vente » de l'édition rapide (`target_price`) déclenche une alerte Discord, une seule fois par valeur d'objectif, quand le prix du marché l'atteint.

Surveillance : les trois workflows envoient un message Discord « [PANNE] » s'ils échouent, et l'analyse prévient (au plus une fois par jour) quand Scryfall ou Reddit ne répondent pas. L'onglet « Bilan des ventes » (`services/sales_report.py`) affiche chiffre d'affaires, frais Cardmarket, bénéfice net et ROI par mois.

## Tests

Les tests utilisent une base SQLite temporaire et neutralisent `DATABASE_URL` : ils ne touchent jamais la base de production. Lancer `python -m unittest discover -s tests`.

## Prochaines étapes

Les commandes slash Discord et l'assistant IA (`agents/`, `ai_engine.py`) ne sont pas branchés sur l'interface. Le découpage de `database/storage.py` est fait en grande partie ; il reste les alertes et la migration JSON à isoler si le fichier grossit.
## Outils de l'application

- services/github_actions.py : boutons de lancement des workflows (secret Streamlit GITHUB_DISPATCH_TOKEN).
- services/pricing.py : suggestion de prix (marge minimale + frais Cardmarket 5 %).
- services/portfolio_history.py : historique des prix et valeur du stock au fil du temps.
- services/csv_import.py : import CSV, ajout sans remplacement du stock.

## Analyse financière du stock

`services/portfolio_metrics.py` calcule couverture, exposition et scénarios; `services/alert_triage.py` classe les alertes; `services/inventory.py` et `database/storage.py` conservent le journal dans la fiche de stock; `services/sales_report.py` compare les ventes à la référence historique.

L'onglet « Stock & Ventes » affiche le nombre d'exemplaires et la valeur aux prix de vente enregistrés, calculée comme la somme des prix unitaires multipliés par les quantités. Il affiche séparément la valeur aux derniers prix Scryfall et leur couverture : les cartes sans relevé ne sont pas incluses dans cette valeur, qui n'est pas un prix de vente garanti. Les expositions par carte et édition et les baisses de 10/20/30 % sont des vues descriptives basées sur les relevés de marché, pas des prévisions. Les données d'achat restent conservées en base mais ne sont pas utilisées dans cette vue.

La liste du stock se filtre par recherche, code d'extension, langue, état, finition et limites de prix de vente unitaires. Elle se trie par prix, nom ou quantité ; ces filtres s'appliquent à la galerie et au tableau. Une vente depuis la liste vise la fiche sélectionnée et est refusée si elle a changé depuis son affichage.

Les alertes de l'onglet « Prix & Alertes » sont triées par impact estimé sur la quantité détenue, part de portefeuille et fraîcheur. Les alertes communautaires restent qualifiées de rumeurs/événements à confirmer; les prix Scryfall sont des références indicatives.

Chaque fiche de stock peut conserver un journal daté (décision, justification, horizon) dans son payload PostgreSQL/SQLite existant, sans migration de schéma. Le bilan des ventes compare le prix réalisé au dernier relevé connu au plus tard à la date de vente; l'écart est indicatif, ne représente pas nécessairement un prix exécutable.

## Synchronisation Cardmarket

`services/cardmarket_import.py` contrôle l'export complet (identifiants ArticleID et idProduct, quantité, prix, langue et finition), puis résout chaque produit avec l'API Scryfall. Une carte non trouvée reste explicitement non identifiée, sans suivi de prix ni vente via l'application ; les erreurs de connexion et réponses invalides bloquent la synchronisation. L'interface exige une confirmation. `database/storage.py` compare l'aperçu à l'état courant et applique les changements en une transaction : conservation des fiches correspondantes, archivage `WITHDRAWN` des lignes absentes et maintien des ventes. Les prix d'achat inconnus des nouvelles fiches sont signalés, pas déduits du prix de vente. L'historique de portefeuille reste approximatif : les anciennes quantités et dates de retrait ne sont pas fournies par les exports Cardmarket.
