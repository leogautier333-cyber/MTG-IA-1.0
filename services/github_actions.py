"""Déclenchement et suivi des workflows GitHub Actions depuis l'application."""
import os

import requests

API_URL = "https://api.github.com"
DEFAULT_REPOSITORY = "leogautier333-cyber/MTG-IA-1.0"
DEFAULT_REF = "main"
TOKEN_VARIABLE = "GITHUB_DISPATCH_TOKEN"

WORKFLOWS = {
    "stock_analysis.yml": "Analyse du stock",
    "weekly_report.yml": "Rapport hebdomadaire",
    "backup.yml": "Sauvegarde chiffrée",
}


class GitHubActionsError(RuntimeError):
    """Erreur explicite, sans jamais inclure le jeton."""


def is_configured():
    return bool(os.getenv(TOKEN_VARIABLE))


def _headers():
    token = os.getenv(TOKEN_VARIABLE)
    if not token:
        raise GitHubActionsError(f"{TOKEN_VARIABLE} n'est pas configuré dans les secrets de l'application.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repository():
    return os.getenv("MTG_GITHUB_REPOSITORY") or DEFAULT_REPOSITORY


def _explain(response):
    status = response.status_code
    if status in (401, 403):
        return "Jeton refusé : vérifie qu'il est valide et qu'il a la permission « Actions : lecture et écriture »."
    if status == 404:
        return "Workflow ou dépôt introuvable : le jeton doit avoir accès à ce dépôt."
    if status == 422:
        return "GitHub refuse le lancement : le workflow n'accepte pas le lancement manuel sur cette branche."
    return f"Réponse inattendue de GitHub (code {status})."


def trigger(workflow_file, session=requests):
    if workflow_file not in WORKFLOWS:
        raise GitHubActionsError("Workflow inconnu.")
    try:
        response = session.post(
            f"{API_URL}/repos/{_repository()}/actions/workflows/{workflow_file}/dispatches",
            headers=_headers(), json={"ref": DEFAULT_REF}, timeout=15,
        )
    except requests.RequestException as error:
        raise GitHubActionsError(f"GitHub injoignable ({type(error).__name__}).") from None
    if response.status_code != 204:
        raise GitHubActionsError(_explain(response))
    return True


def latest_run(workflow_file, session=requests):
    """Dernière exécution : {status, conclusion, created_at, url} ou None."""
    try:
        response = session.get(
            f"{API_URL}/repos/{_repository()}/actions/workflows/{workflow_file}/runs",
            headers=_headers(), params={"per_page": 1}, timeout=15,
        )
    except requests.RequestException as error:
        raise GitHubActionsError(f"GitHub injoignable ({type(error).__name__}).") from None
    if response.status_code != 200:
        raise GitHubActionsError(_explain(response))
    runs = response.json().get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {
        "status": run.get("status"), "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"), "url": run.get("html_url"),
    }


def run_label(run):
    if not run:
        return "Jamais lancé"
    if run["status"] != "completed":
        return "⏳ En cours"
    return {"success": "✅ Réussi", "failure": "❌ Échec", "cancelled": "⚪ Annulé"}.get(
        run["conclusion"], f"ℹ️ {run['conclusion']}"
    )
