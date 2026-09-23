import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

import scryfall
import data_manager

# -------------------------------------------------------------------
# Configuration de la page Streamlit (optimisée PC & mobile)
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Magic Trading & Profit Manager V1.1",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.title("Magic Trading & Profit Manager V1.1")

# Onglets principaux
tab_stock, tab_search, tab_watchlist, tab_simu = st.tabs([
    "Stock & Ventes",
    "Recherche & Ajout",
    "Watchlist & Pépites",
    "Simulateur Négo"
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
        # Metrics financiers globaux
        for_sale_cards = [c for c in collection if c.get("status") == "FOR_SALE"]
        sold_cards = [c for c in collection if c.get("status") == "SOLD"]

        total_value = sum(c["selling_price"] * c.get("quantity", 1) for c in for_sale_cards)
        total_invested = sum(c["purchase_price"] * c.get("quantity", 1) for c in for_sale_cards)
        
        turnover = sum(c.get("actual_sale_price", 0) * c.get("quantity", 1) for c in sold_cards)
        total_sold_cost = sum(c["purchase_price"] * c.get("quantity", 1) for c in sold_cards)
        total_net_profit = sum(
            data_manager.calculate_net_margin(
                c.get("actual_sale_price", 0), c["purchase_price"]
            )["net_profit"] * c.get("quantity", 1)
            for c in sold_cards
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Valeur du Stock", f"{total_value:.2f} €")
        col2.metric("Capital Investi (En Vente)", f"{total_invested:.2f} €")
        col3.metric("Chiffre d'Affaires", f"{turnover:.2f} €")
        col4.metric("Bénéfice Net Cumulé", f"{total_net_profit:.2f} €", delta=f"{total_net_profit:.2f} €")

        st.divider()

        # Filtre par statut
        status_filter = st.radio(
            "Afficher les cartes :",
            ["En Vente", "Vendues"],
            horizontal=True
        )

        target_status = "FOR_SALE" if status_filter == "En Vente" else "SOLD"
        filtered_cards = [c for c in collection if c.get("status") == target_status]

        if filtered_cards:
            df = pd.DataFrame(filtered_cards)
            
            # Formattage pour l'affichage
            display_cols = ["name", "set_code", "condition", "language", "purchase_price", "selling_price", "liquidity_rating"]
            if target_status == "SOLD":
                display_cols.extend(["actual_sale_price", "date_sold"])

            st.dataframe(df[display_cols], use_container_width=True)

            st.subheader("Détail & Actions sur une carte")
            card_names = [f"{c['name']} ({c['set_code'].upper()}) - {c['purchase_price']}€" for c in filtered_cards]
            selected_idx = st.selectbox("Sélectionner une carte :", range(len(card_names)), format_func=lambda x: card_names[x])

            if selected_idx is not None:
                card = filtered_cards[selected_idx]
                col_img, col_info = st.columns([1, 2])

                with col_info:
                    st.write(f"Nom : {card['name']}")
                    st.write(f"Édition : {card['set_code'].upper()} | N° : {card['collector_number']}")
                    st.write(f"État : {card['condition']} | Langue : {card['language']} | **Foil :** {'Oui' if card['foil'] else 'Non'}")
                    st.write(f"Prix d'Achat : {card['purchase_price']:.2f} €")
                    st.write(f"Prix de Vente Fixé : {card['selling_price']:.2f} €")
                    
                    margin_info = data_manager.calculate_net_margin(card['selling_price'], card['purchase_price'])
                    st.write(f"**Bénéfice Net Estimé :** {margin_info['net_profit']:.2f} € (ROI : {margin_info['roi_percent']} %)")

                    if target_status == "FOR_SALE":
                        st.divider()
                        st.subheader("Marquer comme vendue")
                        sale_price = st.number_input(
                            "Prix réel de vente (€) :",
                            min_value=0.0,
                            value=float(card['selling_price']),
                            step=0.5
                        )
                        if st.button("Confirmer la vente"):
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
                    st.plotly_chart(fig, use_container_width=True)


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

                    # Visualisation
                    col_pic, col_form = st.columns([1, 2])

                    with col_pic:
                        img_url = selected_card.get("image_uris", {}).get("normal")
                        if not img_url and "card_faces" in selected_card:
                            img_url = selected_card["card_faces"][0].get("image_uris", {}).get("normal")
                        
                        if img_url:
                            st.image(img_url, use_container_width=True)

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
                                    liquidity_rating=liquidity
                                )
                                if success:
                                    st.success(f"{selected_card['name']} ajoutée au stock !")
                                else:
                                    st.error("Erreur d'enregistrement.")


# ===================================================================
# TAB 3 : WATCHLIST & OPPORUNITÉS D'ACHAT
# ===================================================================
with tab_watchlist:
    st.header("Watchlist & Opportunités d'Achat (Pépites)")

    watchlist = data_manager.get_watchlist()

    if not watchlist:
        st.info("Aucune opportunité repérée pour le moment. Le script automatique d'analyse IA remplira cet onglet.")
    else:
        for item in watchlist:
            with st.expander(f" {item.get('name')} — Prix Achat Max : {item.get('max_buy_price')} €"):
                st.write(f"**Cible de prix estimée :** {item.get('estimated_target_price')} €")
                st.write(f"**Raison :** {item.get('reasoning')}")
                st.write(f"**Fenêtre d'opportunité :** {item.get('window_hours')}h")
                if item.get("potential_seller_group_id"):
                    st.caption(f"Groupe vendeur optimisé : {item.get('potential_seller_group_id')}")


# ===================================================================
# TAB 4 : SIMULATEUR DE NÉGOCIATION
# ===================================================================
with tab_simu:
    st.header(" Simulateur de Négociation & Contre-Offre")
    st.write("Calcule immédiatement ton bénéfice réel avant d'accepter une offre sur Cardmarket.")

    col_s1, col_s2 = st.columns(2)
    p_price = col_s1.number_input("Ton prix d'achat (€) :", min_value=0.0, value=10.0, step=0.5)
    offered_price = col_s2.number_input("Prix proposé par l'acheteur (€) :", min_value=0.0, value=15.0, step=0.5)

    col_s3, col_s4 = st.columns(2)
    shipping = col_s3.number_input("Frais de port (€) :", min_value=0.0, value=1.50, step=0.10)
    cm_fee_pct = col_s4.number_input("Commission plateforme (%) :", min_value=0.0, value=5.0, step=0.5) / 100.0

    margin = data_manager.calculate_net_margin(offered_price, p_price, shipping, cm_fee_pct)

    st.divider()
    res_col1, res_col2, res_col3 = st.columns(3)
    res_col1.metric("Bénéfice Net Réel", f"{margin['net_profit']:.2f} €")
    res_col2.metric("Retour sur Investissement (ROI)", f"{margin['roi_percent']} %")
    res_col3.metric("Frais Cardmarket", f"{margin['cm_fee']:.2f} €")

    if margin['net_profit'] > 0:
        st.success("Offre rentable ! Tu dégages de la marge.")
    elif margin['net_profit'] == 0:
        st.warning("Vente à prix coûtant (aucun bénéfice net).")
    else:
        st.error("Vente à perte ! Propose une contre-offre supérieure.")