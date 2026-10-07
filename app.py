import json
import pandas as pd
import streamlit as st
import data_manager

# Configuration de la page
st.set_page_config(
    page_title="Magic Trading & Profit Manager V1.3",
    page_icon="🃏",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.title("Magic Trading & Profit Manager V1.3")

# Chargement de la collection
collection = data_manager.get_collection()

# Onglets principaux
tab_search, tab_stock = st.tabs(["🔍 Rechercher & Ajouter", "📦 Stock & Ventes"])

# ---------------------------------------------------------------
# ONGLET 1 : RECHERCHE ET AJOUT DE CARTES
# ---------------------------------------------------------------
with tab_search:
    st.subheader("Ajouter une carte au stock")
    
    query = st.text_input("Nom de la carte (ex: Sheoldred) :")
    
    if query:
        suggestions = data_manager.search_cards(query)
        if suggestions:
            card_names = [c["name"] for c in suggestions]
            selected_name = st.selectbox("Suggestions trouvées :", card_names)
            
            selected_card = next((c for c in suggestions if c["name"] == selected_name), None)
            
            if selected_card:
                prints = data_manager.get_card_prints(selected_card["id"])
                print_options = {f"{p['set_name']} ({p['set'].upper()}) #{p['collector_number']}": p for p in prints}
                
                selected_print_label = st.selectbox("Choisir l'édition :", list(print_options.keys()))
                chosen_print = print_options[selected_print_label]
                
                col_img, col_form = st.columns([1, 2])
                
                with col_img:
                    if chosen_print.get("image_url"):
                        st.image(chosen_print["image_url"], use_container_width=True)
                
                with col_form:
                    st.markdown("### Prix actuels Cardmarket")
                    st.write(f"**Prix Normal :** {chosen_print.get('price_normal', 'N/A')} € | **Prix Foil :** {chosen_print.get('price_foil', 'N/A')} €")
                    
                    c1, c2 = st.columns(2)
                    quantity = c1.number_input("Quantité :", min_value=1, value=1)
                    condition = c2.selectbox("État :", ["NM", "EX", "GD", "LP", "PL", "PO"])
                    
                    c3, c4 = st.columns(2)
                    language = c3.selectbox("Langue :", ["FR", "EN", "JP", "IT", "DE", "ES"])
                    foil = c4.checkbox("Foil / Brillante")
                    
                    c5, c6 = st.columns(2)
                    purchase_price = c5.number_input("Prix d'achat (€) :", min_value=0.0, value=0.0)
                    selling_price = c6.number_input("Prix de vente souhaité (€) :", min_value=0.0, value=0.0)
                    
                    liquidity_rating = st.selectbox("Indice de liquidité estimé :", ["FAST", "MEDIUM", "SLOW"])
                    
                    if st.button("Ajouter à mon stock", type="primary"):
                        success = data_manager.add_card_to_collection(
                            scryfall_id=chosen_print.get("id"),
                            name=chosen_print.get("name"),
                            set_code=chosen_print.get("set"),
                            collector_number=chosen_print.get("collector_number"),
                            language=language,
                            condition=condition,
                            foil=foil,
                            quantity=quantity,
                            purchase_price=purchase_price,
                            selling_price=selling_price,
                            liquidity_rating=liquidity_rating,
                            image_url=chosen_print.get("image_url", ""),
                            mana_cost=chosen_print.get("mana_cost", ""),
                            type_line=chosen_print.get("type_line", ""),
                            oracle_text=chosen_print.get("oracle_text", ""),
                            legalities=chosen_print.get("legalities", {})
                        )
                        if success:
                            st.success("Carte ajoutée avec succès !")
                            st.rerun()

# ---------------------------------------------------------------
# ONGLET 2 : GESTION DU STOCK ET VENTES + IMPORT/EXPORT (V1.3)
# ---------------------------------------------------------------
with tab_stock:
    st.subheader("Mon Stock Actuel")
    
    if collection:
        df = pd.DataFrame(collection)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Aucune carte dans le stock pour le moment.")

    # Module Export & Import
    st.divider()
    with st.expander("💾 Sauvegarde & Exportation du stock (Local / PC)"):
        col_exp1, col_exp2 = st.columns(2)
        
        # Export JSON (Pour sauvegarde/restauration)
        json_data = json.dumps(collection, indent=4, ensure_ascii=False)
        col_exp1.download_button(
            label="📥 Télécharger collection.json",
            data=json_data,
            file_name="collection.json",
            mime="application/json"
        )

        # Export CSV (Pour ouverture dans Excel/Tableur)
        if collection:
            df_export = pd.DataFrame(collection)
            csv_data = df