import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

DATA_DIR = Path(__file__).parent / "data"

COLLECTION_FILE = DATA_DIR / "collection.json"
PRICE_HISTORY_FILE = DATA_DIR / "price_history.json"
AI_PREDICTIONS_FILE = DATA_DIR / "ai_predictions.json"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"


# -------------------------------------------------------------------
# Fonctions génériques de lecture / écriture
# -------------------------------------------------------------------

def _load_json(file_path: Path, default_factory):
    """Charge un fichier JSON. Retourne le contenu ou la valeur par défaut."""
    if not file_path.exists():
        return default_factory()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_factory()


def _save_json(file_path: Path, data: Any) -> bool:
    """Sauvegarde les données au format JSON dans le fichier cible."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


# -------------------------------------------------------------------
# Collection (Stock & Ventes)
# -------------------------------------------------------------------

def get_collection() -> List[Dict[str, Any]]:
    """Récupère l'ensemble de la collection."""
    return _load_json(COLLECTION_FILE, list)


def save_collection(collection: List[Dict[str, Any]]) -> bool:
    """Sauvegarde la collection complète."""
    return _save_json(COLLECTION_FILE, collection)


def add_card_to_collection(
    scryfall_id: str,
    name: str,
    set_code: str,
    collector_number: str,
    language: str,
    condition: str,
    foil: bool,
    quantity: int,
    purchase_price: float,
    selling_price: float,
    liquidity_rating: str = "MEDIUM"
) -> bool:
    """Ajoute une nouvelle carte au stock en vente."""
    collection = get_collection()
    
    new_entry = {
        "scryfall_id": scryfall_id,
        "name": name,
        "set_code": set_code,
        "collector_number": collector_number,
        "language": language,
        "condition": condition,
        "foil": foil,
        "quantity": quantity,
        "purchase_price": round(purchase_price, 2),
        "selling_price": round(selling_price, 2),
        "date_added": datetime.now().strftime("%Y-%m-%d"),
        "status": "FOR_SALE",
        "date_sold": None,
        "actual_sale_price": None,
        "liquidity_rating": liquidity_rating,
        "stagnant": False
    }
    
    collection.append(new_entry)
    return save_collection(collection)


def mark_card_as_sold(scryfall_id: str, actual_sale_price: float) -> bool:
    """Passe une carte en statut VENDU et enregistre le prix réel de vente."""
    collection = get_collection()
    updated = False
    
    for item in collection:
        if item["scryfall_id"] == scryfall_id and item["status"] == "FOR_SALE":
            item["status"] = "SOLD"
            item["date_sold"] = datetime.now().strftime("%Y-%m-%d")
            item["actual_sale_price"] = round(actual_sale_price, 2)
            updated = True
            break
            
    if updated:
        return save_collection(collection)
    return False


# -------------------------------------------------------------------
# Historique de Prix
# -------------------------------------------------------------------

def get_price_history() -> Dict[str, Any]:
    """Récupère l'historique de tous les prix."""
    return _load_json(PRICE_HISTORY_FILE, dict)


def update_price_point(
    scryfall_id: str,
    timestamp_key: str,
    eur: Optional[float],
    eur_foil: Optional[float]
) -> bool:
    """
    Ajoute un point de prix pour une carte donnée.
    timestamp_key ex: '2026-09-23_08h'
    """
    history = get_price_history()
    
    if scryfall_id not in history:
        history[scryfall_id] = {}
        
    history[scryfall_id][timestamp_key] = {
        "eur": round(eur, 2) if eur is not None else None,
        "eur_foil": round(eur_foil, 2) if eur_foil is not None else None
    }
    
    return _save_json(PRICE_HISTORY_FILE, history)


# -------------------------------------------------------------------
# Watchlist & Opportunités
# -------------------------------------------------------------------

def get_watchlist() -> List[Dict[str, Any]]:
    """Récupère la liste de surveillance."""
    return _load_json(WATCHLIST_FILE, list)


def save_watchlist(watchlist: List[Dict[str, Any]]) -> bool:
    """Sauvegarde la watchlist."""
    return _save_json(WATCHLIST_FILE, watchlist)


# -------------------------------------------------------------------
# Prédictions IA
# -------------------------------------------------------------------

def get_ai_predictions() -> List[Dict[str, Any]]:
    """Récupère toutes les prédictions IA."""
    return _load_json(AI_PREDICTIONS_FILE, list)


def save_ai_predictions(predictions: List[Dict[str, Any]]) -> bool:
    """Sauvegarde le journal de prédictions IA."""
    return _save_json(AI_PREDICTIONS_FILE, predictions)


# -------------------------------------------------------------------
# Moteur Financier & Calculs
# -------------------------------------------------------------------

def calculate_net_margin(
    selling_price: float,
    purchase_price: float,
    shipping_cost: float = 1.50,
    cm_fee_percent: float = 0.05
) -> Dict[str, float]:
    """
    Calcule le bénéfice net exact après commission Cardmarket (5 %) et frais de port.
    """
    cm_fee = selling_price * cm_fee_percent
    gross_revenue = selling_price - cm_fee
    net_profit = gross_revenue - purchase_price - shipping_cost
    roi_percent = (net_profit / purchase_price * 100) if purchase_price > 0 else 0.0
    
    return {
        "selling_price": round(selling_price, 2),
        "cm_fee": round(cm_fee, 2),
        "shipping_cost": round(shipping_cost, 2),
        "net_profit": round(net_profit, 2),
        "roi_percent": round(roi_percent, 1)
    }