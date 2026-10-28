"""Façade Streamlit du stock : lectures mises en cache, écritures qui invalident le cache."""
import functools

import streamlit as st

from database import storage  # noqa: F401  (utilisé via data_manager.storage par les modules Nexus)
from services import inventory
from services.inventory import (  # noqa: F401  (ré-exports)
    REQUIRED_COLLECTION_FIELDS,
    calculate_net_margin,
    is_valid_collection,
)

CACHE_TTL_SECONDS = 60


def clear_cache():
    st.cache_data.clear()


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


save_collection = _invalidating(inventory.save_collection)
replace_collection = _invalidating(inventory.replace_collection)
add_card_to_collection = _invalidating(inventory.add_card_to_collection)
mark_card_as_sold = _invalidating(inventory.mark_card_as_sold)
save_watchlist = _invalidating(inventory.save_watchlist)
record_price_snapshot = _invalidating(inventory.record_price_snapshot)
