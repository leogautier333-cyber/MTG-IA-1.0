import json
import os
from pathlib import Path

import requests
import data_manager
from services.discord_interactions import run_bot
from services.source_health import get_source_statuses

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PREDICTIONS_FILE = Path(__file__).resolve().parent / "data" / "ai_predictions.json"


def send_discord_alert(title: str, description: str, color: int = 3447003):
    """Envoie un embed enrichi à Discord via Webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("Aucune URL Webhook Discord configurée.")
        return False

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color
            }
        ]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"Échec de l'envoi Discord : {error}")
        return False
    return True


def _load_predictions():
    getter = getattr(data_manager, "get_ai_predictions", None)
    if callable(getter):
        predictions = getter()
    else:
        with PREDICTIONS_FILE.open("r", encoding="utf-8") as predictions_file:
            predictions = json.load(predictions_file)

    if not isinstance(predictions, list):
        raise ValueError("Le fichier des prédictions doit contenir une liste.")
    return predictions


def notify_daily_summary():
    """Envoie une alerte détaillée ou un rapport quotidien sans silence."""
    try:
        predictions = _load_predictions()
    except Exception as error:
        print(f"Impossible de lire les alertes quotidiennes : {error}")
        send_discord_alert(
            "⚠️ Rapport quotidien incomplet - Magic Trading & Profit Manager",
            "_Impossible de lire les alertes du jour. La veille n'a pas pu être vérifiée._",
            16744192,
        )
        return

    source_issues = [
        f"{source}: {status['status']}"
        for source, status in get_source_statuses().items()
        if status["status"] != "HEALTHY"
    ]
    if source_issues:
        send_discord_alert(
            "⚠️ Rapport quotidien partiel - sources à vérifier",
            "_Sources indisponibles ou trop anciennes : " + ", ".join(source_issues) + "._",
            16744192,
        )

    if not predictions and source_issues:
        return
    if not predictions:
        send_discord_alert(
            "📅 Rapport Quotidien - Magic Trading & Profit Manager",
            "_Aucune alerte majeure, spoiler ou variation critique de prix aujourd'hui. Tout est stable._",
            5793266,
        )
        return

    # On ne prend que la dernière prédiction générée
    latest = predictions[-1]
    
    title = f"🎯 Alerte Marché : {latest['card_name']}"
    description = (
        f"**Action recommandée :** {latest['suggested_action']}\n"
        f"**Raison :** {latest['reasoning']}\n"
        f"**Objectif de Prix :** {latest['target_price']} €\n"
        f"**Indice de confiance :** {int(latest['confidence_score'] * 100)} %"
    )
    
    # Vert si opportunité, Orange si hausse de prix conseillée
    color = 65280 if latest["category"] == "BUY_SIGNAL" else 16744192
    send_discord_alert(title, description, color)


if __name__ == "__main__":
    if os.getenv("DISCORD_BOT_TOKEN"):
        run_bot()
    else:
        notify_daily_summary()
    
