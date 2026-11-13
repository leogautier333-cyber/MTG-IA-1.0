# Magic Trading & Profit Manager

Application Streamlit pour gérer un stock de cartes Magic : prix, marges, alertes Discord et assistant IA à règles. Les données sont dans PostgreSQL (Neon).

## Lancer en local

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:DATABASE_URL = "postgresql://..."
.\.venv\Scripts\streamlit run app.py
```

## Secrets nécessaires

| Nom | Où | Rôle |
|---|---|---|
| `DATABASE_URL` | Streamlit + GitHub | Base Neon |
| `DISCORD_WEBHOOK_URL` | GitHub | Alertes et rapports Discord |
| `BACKUP_ENCRYPTION_KEY` | GitHub | Chiffrement des sauvegardes |
| `GITHUB_DISPATCH_TOKEN` | Streamlit | Boutons « Actions GitHub » (jeton limité au dépôt, permission Actions : Read and write) |

Les secrets de l'application Streamlit doivent être définis à la racine du fichier
de secrets (avant toute section TOML), afin d'être disponibles comme variables
d'environnement. Pour le bouton de test Discord dans l'application, configurer
également `DISCORD_WEBHOOK_URL` dans Streamlit. Les secrets Streamlit et GitHub
Actions sont indépendants ; ne jamais les enregistrer dans Git.

Le test Discord actualise uniquement son bouton. Les relevés SQL sont regroupés
par lots ; les états des workflows GitHub et les données de cartes consultées
pour l'affichage sont mis en cache jusqu'à 60 secondes.

## Workflows (`.github/workflows`)

- `stock_analysis.yml` : analyse des prix, spoilers, Reddit, alertes (07h et 19h UTC).
- `weekly_report.yml` : rapport hebdomadaire (lundi).
- `backup.yml` : sauvegarde chiffrée (dimanche).

Tous peuvent être lancés à la main depuis l'onglet « Prix & Alertes » de l'application.

## Synchroniser le stock Cardmarket

Dans « Recherche & Ajout » → « Synchroniser mon stock Cardmarket », dépose l'export CSV complet de Cardmarket, lance la vérification, contrôle l'aperçu puis confirme le remplacement du stock en vente. L'identification précise des impressions repose sur les identifiants produit Cardmarket et nécessite une connexion à Scryfall. Une erreur de connexion ou réponse invalide empêche le remplacement. Une carte introuvable dans Scryfall est signalée et conservée sans association approximative : ni suivi de prix ni vente dans l'application ne sont disponibles pour cette ligne tant qu'elle n'est pas identifiée.

Les noms français de l'export n'ont pas besoin d'être traduits : la recherche utilise l'identifiant produit. L'import CSV générique n'est pas destiné aux exports Cardmarket et indique la section à utiliser lorsqu'il en reconnaît un.

Les fiches correspondantes conservent notamment leur prix d'achat et leur journal. Les cartes absentes sont retirées du stock actif, sans être considérées comme vendues ; les ventes et les relevés restent conservés. Cardmarket n'indique pas le prix d'achat : il reste inconnu pour les nouvelles fiches. Le tableau de stock affiche la valeur aux prix de vente enregistrés, indépendamment des prix d'achat. L'export CSV de l'application est téléchargeable dans « Stock & Ventes » → « Export / Import » ; il ne remplace pas l'export Cardmarket attendu pour la synchronisation.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Restaurer une sauvegarde

```powershell
python -m database.backup restore <fichier> --yes
```

Voir [docs/architecture.md](docs/architecture.md) pour le détail.
