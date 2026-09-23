from datetime import datetime
import scryfall
import data_manager


def analyze_market_and_predict():
    """
    Analyse l'historique des prix, détecte les hausses/baisses brusques
    et génère les recommandations et opportunités d'achat.
    """
    collection = data_manager.get_collection()
    price_history = data_manager.get_price_history()
    predictions = data_manager.get_ai_predictions()
    
    # 1. Mise à jour de l'historique des prix via Scryfall Batch (75 cartes max)
    scryfall_ids = list({c["scryfall_id"] for c in collection})
    if scryfall_ids:
        updated_cards = scryfall.get_cards_batch(scryfall_ids)
        timestamp_key = datetime.now().strftime("%Y-%m-%d_%Hh")
        
        for card_data in updated_cards:
            sc_id = card_data.get("id")
            prices = card_data.get("prices", {})
            eur = float(prices.get("eur")) if prices.get("eur") else None
            eur_foil = float(prices.get("eur_foil")) if prices.get("eur_foil") else None
            
            if sc_id:
                data_manager.update_price_point(sc_id, timestamp_key, eur, eur_foil)

    # 2. Détection des Spikes & Recommandations de Vente
    for item in collection:
        if item.get("status") != "FOR_SALE":
            continue
            
        sc_id = item["scryfall_id"]
        history = price_history.get(sc_id, {})
        dates = sorted(history.keys())
        
        if len(dates) >= 2:
            latest_price = history[dates[-1]].get("eur")
            prev_price = history[dates[-2]].get("eur")
            
            if latest_price and prev_price and prev_price > 0:
                diff = latest_price - prev_price
                pct_change = (diff / prev_price) * 100
                
                # Alerte Spike : Variation > 15 % ET au moins 1,00 € d'écart
                if pct_change >= 15.0 and diff >= 1.0:
                    prediction_entry = {
                        "prediction_id": f"pred_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        "timestamp": datetime.now().isoformat(),
                        "scryfall_id": sc_id,
                        "card_name": item["name"],
                        "category": "SELL_RECOMMENDATION",
                        "suggested_action": "RAISE_PRICE",
                        "confidence_score": 0.85,
                        "target_price": round(latest_price * 1.1, 2),
                        "ban_risk_level": "LOW",
                        "reprint_risk_level": "LOW",
                        "reasoning": f"Hausse rapide détectée (+{pct_change:.1f}% / +{diff:.2f}€). Réaligne ton prix vers le haut.",
                        "verified": False,
                        "actual_outcome": None
                    }
                    predictions.append(prediction_entry)

    data_manager.save_ai_predictions(predictions)


if __name__ == "__main__":
    analyze_market_and_predict()