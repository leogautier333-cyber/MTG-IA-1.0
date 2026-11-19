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

# -------------------------------------------------------------------
# Configuration de la page Streamlit
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Magic Trading & Profit Manager V1.3",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Playfair+Display:wght@500;600;700&display=swap');

:root {
    --canvas: #111411;
    --surface: #191e1a;
    --surface-raised: #202720;
    --line: rgba(201, 169, 110, 0.18);
    --line-soft: rgba(255,255,255,0.08);
    --gold: #c9a96e;
    --gold-bright: #f0d79e;
    --jade: #4ad38b;
    --jade-soft: #b7f1ce;
    --rose: #ef6666;
    --rose-soft: #f4b3b3;
    --text: #f2efe7;
    --muted: #adb9af;
    --shadow: rgba(0,0,0,0.24);
}

.stApp {
    color: var(--text);
    background: linear-gradient(135deg, #111411 0%, #171c18 54%, #121612 100%);
    font-family: 'DM Sans', sans-serif;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1500px; padding: 2.2rem 3rem 4rem; }
h1, h2, h3 { font-family: 'Playfair Display', Georgia, serif !important; color: var(--text); }
h1 { font-size: 2.7rem !important; padding-bottom: .6rem; border-bottom: 1px solid var(--line); }
h2, h3 { letter-spacing: 0; }
p, label, .stCaption { color: var(--text); }
[data-testid="stMetric"] {
    background: linear-gradient(145deg, rgba(32,39,32,.95), rgba(25,30,26,.8));
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 1rem 1.1rem;
    box-shadow: 0 12px 24px rgba(0,0,0,0.10);
}
[data-testid="stMetricLabel"] { color: var(--muted); }
[data-testid="stMetricValue"] { color: var(--gold-bright); font-family: 'DM Sans', sans-serif; }
[data-testid="stTabs"] [role="tablist"] {
    gap: .55rem; border-bottom: 1px solid var(--line); margin-bottom: .25rem;
}
[data-testid="stTabs"] button[role="tab"] {
    color: var(--muted); border-radius: 10px 10px 0 0; padding: .75rem 1rem; border: 1px solid transparent;
    background: rgba(255,255,255,.01);
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: var(--gold-bright) !important;
    border: 1px solid var(--line) !important;
    border-bottom: 1px solid transparent !important;
    background: rgba(201,169,110,.08);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
}
input[type="radio"] { accent-color: var(--gold) !important; }
[data-testid="stExpander"] {
    border: 1px solid var(--line); border-radius: 10px; background: rgba(25,30,26,.72);
}
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--line); border-radius: 10px; background: rgba(25,30,26,.58);
    box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}
.stButton > button, [data-testid="stDownloadButton"] button, [data-testid="stFormSubmitButton"] button {
    border: 1px solid var(--gold); border-radius: 8px; color: var(--gold-bright);
    background: rgba(201,169,110,.08); transition: background .18s ease, transform .18s ease;
}
.stButton > button:hover, [data-testid="stDownloadButton"] button:hover {
    border-color: var(--gold-bright); background: rgba(201,169,110,.18); transform: translateY(-1px);
}
[data-baseweb="input"] > div, [data-baseweb="select"] > div, [data-baseweb="textarea"] {
    background: rgba(17,20,17,.8); border-color: var(--line-soft); border-radius: 8px;
}
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
[data-testid="stAlert"] { border-radius: 8px; }

.margin-badge {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: .45rem;
    padding: .58rem .75rem;
    border-radius: 999px;
    border: 1px solid transparent;
    font-size: .78rem;
    font-weight: 700;
    letter-spacing: .02em;
    margin-top: .65rem;
    width: 100%;
    box-sizing: border-box;
}
.margin-badge.profit-badge {
    background: linear-gradient(135deg, rgba(74,211,139,.22), rgba(41,161,97,.12));
    border-color: rgba(74,211,139,.4);
    color: #dbffe9;
}
.margin-badge.loss-badge {
    background: linear-gradient(135deg, rgba(239,102,102,.18), rgba(170,51,51,.12));
    border-color: rgba(239,102,102,.35);
    color: #ffe2e2;
}
.badge-label { opacity: .82; text-transform: uppercase; letter-spacing: .1em; font-size: .60rem; }
.badge-value { font-size: .82rem; }
.badge-sub { opacity: .88; }

.filter-toolbar {
    background: rgba(17,20,17,.45);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: .8rem .9rem;
    margin-bottom: 1rem;
    box-shadow: 0 12px 20px rgba(0,0,0,.08);
}
.card-meta {
    font-size: .74rem;
    color: var(--muted);
    line-height: 1.6;
}
.card-title-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: .75rem;
    margin-bottom: .25rem;
}
.card-title-row .stMarkdown {
    margin-bottom: 0;
}

@media (max-width: 760px) {
    .block-container { padding: 1rem 1rem 3rem; }
    h1 { font-size: 2rem !important; }
}
</style>
""", unsafe_allow_html=True)

st.title("Magic Trading & Profit Manager")

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