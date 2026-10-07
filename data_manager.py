import json
import os
from datetime import datetime
import streamlit as st

# Chemins absolus basés sur l'emplacement exact du fichier
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
COLLECTION_FILE = os.path.join(DATA_DIR, "collection.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
PRICE_HIST_FILE = os.path.join(DATA_DIR, "price_history.json")

def _ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)

@st.cache_data(ttl=60)
def get_collection():
    _ensure_data_dir()
    if not os.path.exists(COLLECTION_FILE):
        return []
    try:
        with open(COLLECTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur lecture collection: {e}")
        return []

def save_collection(collection):
    _ensure_data_dir()
    with open(COLLECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)

def add_card_to_collection(**kwargs):
    try:
        collection = get_collection()
        
        card_entry = {
            "scryfall_id": kwargs.get("scryfall_id", ""),
            "name": kwargs.get("name", ""),
            "set_code": kwargs.get("set_code", ""),
            "collector_number": str(kwargs.get("collector_number", "")),
            "language": kwargs.get("language", "FR"),
            "condition": kwargs.get("condition", "NM"),
            "foil": kwargs.get("foil", False),
            "quantity": int(kwargs.get("quantity", 1)),
            "purchase_price": float(kwargs.get("purchase_price", 0.0)),
            "selling_price": float(kwargs.get("selling_price", 0.0)),
            "liquidity_rating": kwargs.get("liquidity_rating", "MEDIUM"),
            "status": "FOR_SALE",
            "date_added": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "image_url": kwargs.get("image_url", ""),
            "mana_cost": kwargs.get("mana_cost", ""),
            "type_line": kwargs.get("type_line", ""),
            "oracle_text": kwargs.get("oracle_text", ""),
            "legalities": kwargs.get("legalities", {})
        }
        
        collection.append(card_entry)
        save_collection(collection)
        st.cache_data.clear()  # Vider le cache Streamlit
        return True
    except Exception as e:
        print(f"Erreur lors de l'ajout de la carte : {e}")
        return False

def mark_card_as_sold(scryfall_id, actual_sale_price):
    try:
        collection = get_collection()
        updated = False
        for card in collection:
            if card.get("scryfall_id") == scryfall_id and card.get("status") == "FOR_SALE":
                card["status"] = "SOLD"
                card["actual_sale_price"] = float(actual_sale_price)
                card["date_sold"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = True
                break
        if updated:
            save_collection(collection)
            st.cache_data.clear()
        return updated
    except Exception as e:
        print(f"Erreur lors du marquage de vente : {e}")
        return False

def calculate_net_margin(selling_price, purchase_price, shipping=0.0, cm_fee_pct=0.05):
    selling_price = float(selling_price or 0.0)
    purchase_price = float(purchase_price or 0.0)
    shipping = float(shipping or 0.0)
    
    cm_fee = selling_price * cm_fee_pct
    net_revenue = selling_price - cm_fee - shipping
    net_profit = net_revenue - purchase_price
    roi_percent = (net_profit / purchase_price * 100) if purchase_price > 0 else 0.0
    
    return {
        "net_profit": round(net_profit, 2),
        "roi_percent": round(roi_percent, 1),
        "cm_fee": round(cm_fee, 2)
    }

def get_watchlist():
    _ensure_data_dir()
    if not os.path.exists(WATCHLIST_FILE):
        return []
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def get_price_history():
    _ensure_data_dir()
    if not os.path.exists(PRICE_HIST_FILE):
        return {}
    try:
        with open(PRICE_HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}