import os
import requests
import data_manager

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def send_discord_alert(title: str, description: str, color: int = 3447003):
    """Envoie un embed enrichi à Discord via Webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("Aucune URL Webhook Discord configurée.")
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color
            }
        ]
    }
    requests.post(DISCORD_WEBHOOK_URL, json=payload)


def notify_daily_summary():
    """Filtre les nouvelles prédictions et envoie le rapport anti-bruit."""
    predictions = data_manager.get_ai_predictions()
    if not predictions:
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
    notify_daily_summary()
    # Test forcé
    
    
notify_daily_summary("🧪 **Test réussi !** Le bot MTG-IA communique parfaitement avec Discord.")