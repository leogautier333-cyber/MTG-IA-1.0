import json
import hashlib
import re

import pandas as pd
import plotly.express as px
import streamlit as st

import scryfall
import data_manager
import performance_view
from modules.nexus_view import render_nexus_tab

# -------------------------------------------------------------------
# Fonction d'affichage des symboles de mana SVG (Scryfall)
# -------------------------------------------------------------------
def render_mana_cost_html(mana_str):
    if not mana_str:
        return ""
    
    def replacer(match):
        symbol = match.group(1)  # ex: "2", "G", "W/P", "X"
        code = symbol.replace("/", "")
        url = f"https://svgs.scryfall.io/card-symbols/{code}.svg"
        return f'<img src="{url}" width="20" style="vertical-align: middle; margin: 0 1px;" alt="{symbol}" />'
    
    return re.sub(r'\{([^}]+)\}', replacer, mana_str)


def render_margin_badge(net_profit, roi_percent):
    is_positive = float(net_profit) >= 0
    badge_class = "profit-badge" if is_positive else "loss-badge"
    sign = "+" if is_positive else ""
    return f"""
    <div class="margin-badge {badge_class}">
        <span class="badge-label">Marge</span>
        <span class="badge-value">{sign}{float(net_profit):.2f} €</span>
        <span class="badge-sub">{float(roi_percent):.1f}%</span>
    </div>
    """


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

# Onglets principaux
tab_stock, tab_search, tab_performance, tab_watchlist, tab_nexus = st.tabs([
    "Stock & Ventes",
    "Recherche & Ajout",
    "Performance & Analyse",
    "Watchlist & Pépites",
    "Magic Intelligence Nexus"
])


# ===================================================================
# TAB 1 : STOCK & TABLEAU DE BORD FINANCIER
# ===================================================================
with tab_stock:
    st.header("Tableau de bord & Stock")

    collection = data_manager.get_collection()

    if not collection:
        st.info("Aucune carte enregistrée dans ton stock pour le moment.")
    else:
        with st.container(border=True):
            st.subheader("🔍 Filtres et Options d'affichage")
            filter_status, filter_view = st.columns([1.25, 1])
            with filter_status:
                status_filter = st.radio("Statut", ["En Vente", "Vendues"], horizontal=True)
            with filter_view:
                view_mode = st.radio("Affichage", ["Galerie", "Tableau"], horizontal=True)

            target_status = "FOR_SALE" if status_filter == "En Vente" else "SOLD"

            filter_search, filter_language, filter_condition = st.columns([2.2, 1, 1])
            with filter_search:
                search_stock = st.text_input("Rechercher une carte", placeholder="Nom, édition…", label_visibility="collapsed")
            with filter_language:
                languages = sorted({str(c.get("language", "FR")) for c in collection})
                language_filter = st.selectbox("Langue", ["Toutes"] + languages, label_visibility="collapsed")
            with filter_condition:
                conditions = sorted({str(c.get("condition", "NM")) for c in collection})
                condition_filter = st.selectbox("État", ["Tous"] + conditions, label_visibility="collapsed")

        indexed_cards = [
            (index, card) for index, card in enumerate(collection)
            if card.get("status") == target_status
            and (not search_stock or search_stock.casefold() in str(card.get("name", "")).casefold()
                 or search_stock.casefold() in str(card.get("set_code", "")).casefold())
            and (language_filter == "Toutes" or str(card.get("language", "FR")) == language_filter)
            and (condition_filter == "Tous" or str(card.get("condition", "NM")) == condition_filter)
        ]
        filtered_cards = [card for _, card in indexed_cards]

        st.divider()

        if filtered_cards:
            with st.container(border=True):
                st.caption("Édition rapide")
                edit_position = st.selectbox(
                    "Carte à modifier",
                    range(len(indexed_cards)),
                    format_func=lambda position: (
                        f"{indexed_cards[position][1].get('name', 'Carte sans nom')} · "
                        f"{indexed_cards[position][1].get('set_code', '').upper()} · "
                        f"#{indexed_cards[position][1].get('collector_number', 'N/A')}"
                    ),
                    key="quick_edit_position"
                )
                record_index, edit_card = indexed_cards[edit_position]
                with st.form(f"quick_edit_form_{record_index}"):
                    edit_quantity, edit_purchase, edit_selling = st.columns(3)
                    quantity_value = edit_quantity.number_input(
                        "Quantité", min_value=1, value=max(1, int(edit_card.get("quantity", 1))), step=1
                    )
                    purchase_value = edit_purchase.number_input(
                        "Prix d’achat unitaire (€)", min_value=0.0,
                        value=float(edit_card.get("purchase_price", 0)), step=0.5
                    )
                    selling_value = edit_selling.number_input(
                        "Prix de vente unitaire (€)", min_value=0.0,
                        value=float(edit_card.get("selling_price", 0)), step=0.5
                    )
                    save_quick_edit = st.form_submit_button("Enregistrer", type="primary")

                if save_quick_edit:
                    collection[record_index]["quantity"] = int(quantity_value)
                    collection[record_index]["purchase_price"] = float(purchase_value)
                    collection[record_index]["selling_price"] = float(selling_value)
                    data_manager.save_collection(collection)
                    st.cache_data.clear()
                    st.toast("Modifications enregistrées", icon="✅")
                    st.rerun()

        if not filtered_cards:
            st.info("Aucune carte ne correspond à ces filtres.")
        
        # ---------------------------------------------------------------
        # MODE 1 : GALERIE VISUELLE (TUILES DYNAMIQUES)
        # ---------------------------------------------------------------
        elif view_mode == "Galerie":
            cols_per_row = 3
            cols = st.columns(cols_per_row)

            for idx, card in enumerate(filtered_cards):
                scryfall_data = None
                if card.get("name") and (not card.get("image_url") or not card.get("mana_cost")):
                    scryfall_data = scryfall.get_card_by_name(card["name"])
                img_url = card.get("image_url") or (scryfall_data.get("image_uris", {}).get("normal") if scryfall_data else None)
                if not img_url and scryfall_data and "card_faces" in scryfall_data:
                    img_url = scryfall_data["card_faces"][0].get("image_uris", {}).get("normal")

                with cols[idx % cols_per_row]:
                    with st.container(border=True):
                        if img_url:
                            st.image(img_url, width="stretch")
                        else:
                            st.write("🖼️ *Image non disponible*")

                        mana_cost = card.get("mana_cost") or (scryfall_data.get("mana_cost") if scryfall_data else "")
                        mana_html = render_mana_cost_html(mana_cost)
                        st.markdown(f"**{card['name']}** {mana_html}", unsafe_allow_html=True)

                        quantity = int(card.get("quantity", 1))
                        st.markdown(
                            f"<div class='card-meta'>`{card['set_code'].upper()}` · N° {card.get('collector_number', 'N/A')} · {card.get('condition', 'NM')} · {card.get('language', 'FR')} · Qté {quantity}{' · Foil' if card.get('foil') else ''}</div>",
                            unsafe_allow_html=True
                        )

                        p_price = card.get("purchase_price", 0.0)
                        s_price = card.get("selling_price", 0.0)
                        margin_info = data_manager.calculate_net_margin(s_price, p_price)

                        c_p1, c_p2 = st.columns(2)
                        c_p1.markdown(f"Achat: **{p_price:.2f} €**")
                        c_p2.markdown(f"Vente: **{s_price:.2f} €**")

                        margin_value = margin_info["net_profit"] * quantity
                        st.markdown(render_margin_badge(margin_value, margin_info["roi_percent"]), unsafe_allow_html=True)

                        if target_status == "FOR_SALE":
                            with st.popover("Vendre", use_container_width=True):
                                actual_p = st.number_input("Prix de vente réel (€) :", min_value=0.0, value=float(s_price), step=0.5, key=f"p_{card['scryfall_id']}_{idx}")
                                if st.button("Confirmer", key=f"btn_{card['scryfall_id']}_{idx}", type="primary"):
                                    if data_manager.mark_card_as_sold(card['scryfall_id'], actual_p):
                                        st.success("Vendu !")
                                        st.rerun()

        # ---------------------------------------------------------------
        # MODE 2 : TABLEAU ÉPURÉ ET FICHE DÉTAILLÉE
        # ---------------------------------------------------------------
        else:
            df = pd.DataFrame(filtered_cards)
            display_cols = ["name", "set_code", "quantity", "condition", "language", "foil", "purchase_price", "selling_price", "liquidity_rating"]
            if target_status == "SOLD":
                display_cols.extend(["actual_sale_price", "date_sold"])

            column_config = {
                "name": st.column_config.TextColumn("Nom de la carte"),
                "set_code": st.column_config.TextColumn("Édition", width="small"),
                "quantity": st.column_config.NumberColumn("Qté", format="%d", width="small"),
                "condition": st.column_config.TextColumn("État", width="small"),
                "language": st.column_config.TextColumn("Langue", width="small"),
                "foil": st.column_config.CheckboxColumn("Foil", width="small"),
                "purchase_price": st.column_config.NumberColumn("Prix d'Achat", format="%.2f €"),
                "selling_price": st.column_config.NumberColumn("Prix Vente Fixé", format="%.2f €"),
                "liquidity_rating": st.column_config.TextColumn("Liquidité", width="small"),
                "actual_sale_price": st.column_config.NumberColumn("Prix Vente Réel", format="%.2f €"),
                "date_sold": st.column_config.TextColumn("Date de Vente")
            }

            st.dataframe(
                df.reindex(columns=display_cols),
                column_config=column_config,
                width="stretch",
                hide_index=True
            )

            st.subheader("Détail & Actions sur une carte")
            card_names = [f"{c['name']} ({c['set_code'].upper()}) - {c['purchase_price']}€" for c in filtered_cards]
            selected_idx = st.selectbox("Sélectionner une carte :", range(len(card_names)), format_func=lambda x: card_names[x])

            if selected_idx is not None:
                card = filtered_cards[selected_idx]
                col_img, col_info = st.columns([1, 2])

                scryfall_data = None
                if card.get("name") and (
                    not card.get("image_url") or not card.get("mana_cost")
                    or not card.get("type_line") or not card.get("oracle_text")
                ):
                    scryfall_data = scryfall.get_card_by_name(card["name"])

                with col_img:
                    img_url = card.get("image_url") or card.get("image_uris", {}).get("normal")
                    if not img_url and scryfall_data:
                        img_url = scryfall_data.get("image_uris", {}).get("normal")
                        if not img_url and "card_faces" in scryfall_data:
                            img_url = scryfall_data["card_faces"][0].get("image_uris", {}).get("normal")

                    if img_url:
                        st.image(img_url, width="stretch")
                    else:
                        st.warning("🖼️ Image non disponible")

                with col_info:
                    mana_cost = card.get("mana_cost") or (scryfall_data.get("mana_cost") if scryfall_data else "")
                    type_line = card.get("type_line") or (scryfall_data.get("type_line") if scryfall_data else "Type inconnu")
                    oracle_text = card.get("oracle_text") or (scryfall_data.get("oracle_text") if scryfall_data else "Aucun texte d'effet disponible.")
                    legalities = card.get("legalities") or (scryfall_data.get("legalities") if scryfall_data else {})

                    mana_html = render_mana_cost_html(mana_cost)
                    st.markdown(f"### ** {mana_html}", unsafe_allow_html=True)
                    st.markdown(f"*{type_line}*")
                    st.divider()

                    st.markdown(f"> **Texte Oracle :**\n> {oracle_text}")

                    st.write(f"**Édition :** `{card['set_code'].upper()}` | **N° :** {card.get('collector_number', 'N/A')}")
                    st.write(f"**État :** {card.get('condition', 'N/A')} | **Langue :** {card.get('language', 'N/A')} | **Foil :** {'Oui' if card.get('foil') else 'Non'}")
                    st.write(f"Prix d'Achat : {card['purchase_price']:.2f} €")
                    st.write(f"Prix de Vente Fixé : {card['selling_price']:.2f} €")

                    margin_info = data_manager.calculate_net_margin(card['selling_price'], card['purchase_price'])
                    st.write(f"**Bénéfice Net Estimé :** {margin_info['net_profit']:.2f} € (ROI : {margin_info['roi_percent']} %)")

                    if legalities:
                        st.markdown("#### **Légalités en Format**")
                        leg_cols = st.columns(2)
                        formats = ["standard", "pioneer", "modern", "legacy", "vintage", "commander"]
                        for idx, fmt in enumerate(formats):
                            status = legalities.get(fmt, "not_legal")
                            badge = "🟢 **LEGAL**" if status == "legal" else "⚪ **NOT LEGAL**"
                            leg_cols[idx % 2].markdown(f"{badge} `{fmt.capitalize()}`")

                    if target_status == "FOR_SALE":
                        st.divider()
                        st.subheader("Marquer comme vendue")
                        sale_price = st.number_input(
                            "Prix réel de vente (€) :",
                            min_value=0.0,
                            value=float(card['selling_price']),
                            step=0.5
                        )
                        if st.button("Confirmer la vente", type="primary"):
                            if data_manager.mark_card_as_sold(card['scryfall_id'], sale_price):
                                st.success("Carte enregistrée comme vendue !")
                                st.rerun()
                            else:
                                st.error("Erreur lors de la mise à jour.")

                # Historique de prix
                price_hist = data_manager.get_price_history().get(card["scryfall_id"], {})
                if price_hist:
                    hist_data = []
                    for ts, val in price_hist.items():
                        hist_data.append({"Date": ts, "Prix (€)": val.get("eur")})
                    df_hist = pd.DataFrame(hist_data)
                    fig = px.line(df_hist, x="Date", y="Prix (€)", title="Évolution du prix Cardmarket")
                    st.plotly_chart(fig, width="stretch")

        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        with st.popover("Export / Import", use_container_width=False):
            export_json, export_csv = st.columns(2)
            collection_json = json.dumps(collection, ensure_ascii=False, indent=2)
            collection_csv = pd.DataFrame(collection).to_csv(index=False).encode("utf-8-sig")

            with export_json:
                st.download_button(
                    "JSON",
                    data=collection_json,
                    file_name="collection.json",
                    mime="application/json",
                    use_container_width=True
                )
            with export_csv:
                st.download_button(
                    "CSV",
                    data=collection_csv,
                    file_name="collection.csv",
                    mime="text/csv",
                    disabled=not collection,
                    use_container_width=True
                )

            st.divider()
            uploaded_collection = st.file_uploader(
                "Choisir une sauvegarde collection.json",
                type=["json"],
                help="La restauration remplacera intégralement le stock actuel.",
                label_visibility="collapsed"
            )

            if uploaded_collection is not None:
                uploaded_bytes = uploaded_collection.getvalue()
                try:
                    restored_collection = json.loads(uploaded_bytes.decode("utf-8-sig"))
                    if not data_manager.is_valid_collection(restored_collection):
                        raise ValueError(
                            "Le fichier doit contenir une liste de cartes avec les champs "
                            "scryfall_id, name, set_code, purchase_price et selling_price."
                        )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
                    st.error(f"Fichier de sauvegarde invalide : {e}")
                else:
                    st.warning(
                        f"Cette opération remplacera les {len(collection)} carte(s) actuelles "
                        f"par {len(restored_collection)} carte(s)."
                    )
                    upload_hash = hashlib.sha256(uploaded_bytes).hexdigest()[:12]
                    confirmed = st.checkbox(
                        "Je confirme le remplacement complet du stock.",
                        key=f"confirm_collection_restore_{upload_hash}"
                    )
                    if st.button(
                        "Restaurer cette sauvegarde",
                        type="primary",
                        disabled=not confirmed
                    ):
                        if data_manager.replace_collection(restored_collection):
                            st.success("Le stock a été restauré.")
                            st.rerun()
                        else:
                            st.error("La restauration a échoué ; le stock actuel est inchangé.")


# ===================================================================
# TAB 2 : RECHERCHE & AJOUT DE CARTES
# ===================================================================
with tab_search:
    st.header("Rechercher et Ajouter une carte")

    query = st.text_input("Nom de la carte (FR ou EN) :", placeholder="ex: Sheoldred, Raavan, Atraxa...")

    if query and len(query) >= 2:
        suggestions = scryfall.search_card_autocomplete(query)
        if suggestions:
            selected_name = st.selectbox("Suggestions trouvées :", suggestions)

            if selected_name:
                card_data = scryfall.get_card_by_name(selected_name)
                if card_data:
                    oracle_id = card_data.get("oracle_id")
                    prints = scryfall.get_card_prints(oracle_id) if oracle_id else [card_data]

                    print_options = [
                        f"{p.get('set_name')} ({p.get('set').upper()}) #{p.get('collector_number')}"
                        for p in prints
                    ]
                    selected_print_idx = st.selectbox("Choisir l'édition :", range(len(print_options)), format_func=lambda x: print_options[x])
                    
                    selected_card = prints[selected_print_idx]

                    col_pic, col_form = st.columns([1, 2])

                    with col_pic:
                        img_url = selected_card.get("image_uris", {}).get("normal")
                        if not img_url and "card_faces" in selected_card:
                            img_url = selected_card["card_faces"][0].get("image_uris", {}).get("normal")
                        
                        if img_url:
                            st.image(img_url, width="stretch")

                    with col_form:
                        st.subheader("Prix actuels Cardmarket")
                        prices = selected_card.get("prices", {})
                        cm_price = float(prices.get("eur") or 0.0)
                        cm_foil_price = float(prices.get("eur_foil") or 0.0)
                        
                        st.write(f"Prix Normal : **{cm_price:.2f} €** | Prix Foil : **{cm_foil_price:.2f} €**")

                        with st.form("add_card_form"):
                            col_f1, col_f2 = st.columns(2)
                            quantity = col_f1.number_input("Quantité :", min_value=1, value=1)
                            condition = col_f2.selectbox("État :", ["NM", "EX", "GD", "LP", "PL", "PO"])
                            
                            language = col_f1.selectbox("Langue :", ["FR", "EN", "DE", "IT", "ES", "JP"])
                            is_foil = col_f2.checkbox("Foil / Brillante")

                            purchase_price = col_f1.number_input("Prix d'achat (€) :", min_value=0.0, value=cm_price, step=0.5)
                            selling_price = col_f2.number_input("Prix de vente souhaité (€) :", min_value=0.0, value=cm_price, step=0.5)
                            
                            liquidity = st.selectbox("Indice de liquidité estimé :", ["FAST", "MEDIUM", "SLOW"])

                            submit = st.form_submit_button("Ajouter à mon stock")

                            if submit:
                                # Extraction des métadonnées enrichies
                                mana_cost = selected_card.get("mana_cost", "")
                                if not mana_cost and "card_faces" in selected_card:
                                    mana_cost = selected_card["card_faces"][0].get("mana_cost", "")

                                success = data_manager.add_card_to_collection(
                                    scryfall_id=selected_card["id"],
                                    name=selected_card["name"],
                                    set_code=selected_card["set"],
                                    collector_number=selected_card["collector_number"],
                                    language=language,
                                    condition=condition,
                                    foil=is_foil,
                                    quantity=quantity,
                                    purchase_price=purchase_price,
                                    selling_price=selling_price,
                                    liquidity_rating=liquidity,
                                    image_url=img_url or "",
                                    mana_cost=mana_cost,
                                    type_line=selected_card.get("type_line", ""),
                                    oracle_text=selected_card.get("oracle_text", ""),
                                    legalities=selected_card.get("legalities", {})
                                )
                                if success:
                                    st.cache_data.clear()
                                    st.success(f"**{selected_card['name']}** enregistrée dans le stock !")
                                    st.rerun()
                                else:
                                    st.error("Erreur lors de l'écriture dans la base de données.")


# ===================================================================
# TAB 3 : PERFORMANCE & ANALYSE FINANCIÈRE
# ===================================================================
with tab_performance:
    st.header("Performance & Analyse Financière")
    performance_view.render_performance_tab()


# ===================================================================
# TAB 4 : WATCHLIST & OPPORTUNITÉS D'ACHAT
# ===================================================================
with tab_watchlist:
    st.header("Watchlist & Opportunités d'Achat (Pépites)")

    watchlist = data_manager.get_watchlist()

    if not watchlist:
        st.info("Aucune opportunité repérée pour le moment. Le script automatique d'analyse IA remplira cet onglet.")
    else:
        st.metric("Opportunités suivies", len(watchlist))
        opportunity_columns = st.columns(2)
        for index, item in enumerate(watchlist):
            max_buy_price = float(item.get("max_buy_price") or 0)
            target_price = float(item.get("estimated_target_price") or 0)
            expected_gain = target_price - max_buy_price

            with opportunity_columns[index % 2]:
                with st.container(border=True):
                    st.markdown(f"#### {item.get('name', 'Carte sans nom')}")
                    st.caption(f"Fenêtre estimée · {item.get('window_hours', '—')} h")
                    buy_col, target_col = st.columns(2)
                    buy_col.metric("Prix d’achat max", f"{max_buy_price:.2f} €")
                    target_col.metric("Cible estimée", f"{target_price:.2f} €", delta=f"{expected_gain:+.2f} €")
                    st.write(item.get("reasoning", "Aucune analyse complémentaire."))
                    if item.get("potential_seller_group_id"):
                        st.caption(f"Groupe vendeur · {item.get('potential_seller_group_id')}")


# ===================================================================
# TAB 5 : MAGIC INTELLIGENCE NEXUS
# ===================================================================
with tab_nexus:
    render_nexus_tab()