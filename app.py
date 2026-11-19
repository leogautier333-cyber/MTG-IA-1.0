import os

from contextlib import nullcontext
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

# Root-level Streamlit secrets must be loaded before services read the environment.
try:
    len(st.secrets)
except StreamlitSecretNotFoundError as error:
    if getattr(error, "error_id", None) != "no-secrets-found":
        raise

from modules.sales_view import render_sales_tab
from modules.search_view import render_search_tab
from modules.prices_view import render_prices_tab
from modules.stock_view import render_stock_tab
from modules.theme import apply_theme

# -------------------------------------------------------------------
# Configuration de la page Streamlit
# -------------------------------------------------------------------
st.set_page_config(
    page_title="MTG // Trading Terminal",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="collapsed"
)

apply_theme()

st.title("Magic Trading // Profit Terminal")

tab_names = [
    "Stock & Ventes",
    "Recherche & Ajout",
    "Prix & Alertes",
    "Bilan des ventes",
    "Nexus"
]
lazy_navigation = os.getenv("MTG_LAZY_NAVIGATION", "").lower() == "true"
if lazy_navigation:
    active_tab = st.radio("Navigation", tab_names, horizontal=True, key="main_navigation")
    tab_stock = tab_search = tab_prices = tab_sales = tab_nexus = nullcontext()
else:
    active_tab = None
    tab_stock, tab_search, tab_prices, tab_sales, tab_nexus = st.tabs(tab_names)


# ===================================================================
# TAB 1 : STOCK & TABLEAU DE BORD FINANCIER
# ===================================================================
if active_tab is None or active_tab == "Stock & Ventes":
    with tab_stock:
        render_stock_tab()

# ===================================================================
# TAB 2 : RECHERCHE & AJOUT DE CARTES
# ===================================================================
if active_tab is None or active_tab == "Recherche & Ajout":
    with tab_search:
        render_search_tab()


# ===================================================================
# TAB 3 : PRICE HISTORY AND STOCK ALERTS
# ===================================================================
if active_tab is None or active_tab == "Prix & Alertes":
    with tab_prices:
        render_prices_tab()


# ===================================================================
# TAB 4 : BILAN DES VENTES
# ===================================================================
if active_tab is None or active_tab == "Bilan des ventes":
    with tab_sales:
        render_sales_tab()


# ===================================================================
# TAB 5 : NEXUS (watchlist, signaux, backtesting, decklist)
# ===================================================================
if active_tab is None or active_tab == "Nexus":
    with tab_nexus:
        try:
            from modules.nexus_view import render_nexus_tab
            render_nexus_tab()
        except Exception as error:
            st.error(f"L'onglet Nexus est indisponible : {error}")