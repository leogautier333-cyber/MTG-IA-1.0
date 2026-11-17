"""Façade Streamlit du stock : lectures mises en cache, écritures qui invalident le cache."""
import functools

import streamlit as st

import scryfall
from database import market_data, storage
from services import cardmarket_import, inventory, stock_advisor
from services.inventory import (  # noqa: F401  (ré-exports)
    REQUIRED_COLLECTION_FIELDS,
    calculate_net_margin,
    is_valid_collection,
)

CACHE_TTL_SECONDS = 60


def clear_cache():
    get_collection.clear()
    get_watchlist.clear()
    get_price_history.clear()
    get_variant_price_history.clear()
    get_price_alerts.clear()
    get_inventory_events.clear()
    get_popularity_deltas.clear()
    get_price_points_batch.clear()
    get_edhrec_histories.clear()
    get_alert_metrics.clear()


def _invalidating(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        result = function(*args, **kwargs)
        clear_cache()
        return result
    return wrapper


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_collection():
    return inventory.get_collection()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_watchlist():
    return inventory.get_watchlist()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_price_history():
    return inventory.get_price_history()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_variant_price_history(scryfall_id, foil=False, language="FR", condition="NM"):
    return inventory.get_variant_price_history(scryfall_id, foil, language, condition)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_price_alerts():
    return inventory.get_price_alerts()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_inventory_events():
    return storage.get_inventory_events()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_popularity_deltas(scryfall_ids):
    return stock_advisor.popularity_deltas_pct(scryfall_ids)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_price_points_batch(variants):
    return storage.get_price_points_batch(variants)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_edhrec_histories(scryfall_ids):
    return market_data.get_edhrec_histories(scryfall_ids)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_alert_metrics():
    return storage.get_alert_metrics()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_scryfall_card(name):
    return scryfall.get_card_by_name(name)


@st.cache_data(ttl=3600, max_entries=10000)
def get_cardmarket_product(product_id):
    return cardmarket_import.fetch_product(product_id)


save_collection = _invalidating(inventory.save_collection)
replace_collection = _invalidating(inventory.replace_collection)
add_card_to_collection = _invalidating(inventory.add_card_to_collection)
add_cards_to_collection = _invalidating(inventory.add_cards_to_collection)
synchronize_cardmarket_stock = _invalidating(inventory.synchronize_cardmarket_stock)
mark_card_as_sold = _invalidating(inventory.mark_card_as_sold)
mark_selected_card_as_sold = _invalidating(inventory.mark_selected_card_as_sold)
update_card = _invalidating(inventory.update_card)
record_decision = _invalidating(inventory.record_decision)
save_watchlist = _invalidating(inventory.save_watchlist)
record_price_snapshot = _invalidating(inventory.record_price_snapshot)
