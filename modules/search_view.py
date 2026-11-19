"""Recherche de cartes et import de stock."""
import hashlib

import requests
import streamlit as st

import data_manager
import scryfall
from services import cardmarket_import, csv_import


def scryfall_for_display(function, *args):
    try:
        return function(*args)
    except requests.RequestException as error:
        st.error(f"Scryfall indisponible ({type(error).__name__}) ; réessaie plus tard.")
        st.stop()




def render_search_tab():
    st.header("Rechercher et Ajouter une carte")

    with st.expander("Synchroniser mon stock Cardmarket (CSV complet)"):
        cardmarket_file = st.file_uploader(
            "Export complet Cardmarket", type=["csv"], key="cardmarket_sync_file"
        )
        if cardmarket_file is not None:
            file_bytes = cardmarket_file.getvalue()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            prepared = st.session_state.get("cardmarket_sync_prepared")
            if prepared and prepared["hash"] != file_hash:
                st.session_state.pop("cardmarket_sync_prepared", None)
                prepared = None
            if st.button("Vérifier et préparer le stock Cardmarket", key="prepare_cardmarket_sync"):
                st.session_state.pop("cardmarket_sync_prepared", None)
                prepared = None
                try:
                    rows = cardmarket_import.parse(file_bytes.decode("utf-8-sig"))
                    with st.spinner(f"Identification de {len(rows)} ligne(s) via Scryfall…"):
                        resolved, errors = cardmarket_import.resolve(
                            rows, fetch=data_manager.get_cardmarket_product
                        )
                    if errors:
                        for error in errors:
                            st.error(error)
                        st.error("Import partiel refusé : le stock n'a pas été modifié.")
                    else:
                        prepared = {
                            "hash": file_hash, "cards": resolved,
                            "collection": data_manager.get_collection(),
                        }
                        st.session_state["cardmarket_sync_prepared"] = prepared
                except (UnicodeDecodeError, ValueError, RuntimeError) as error:
                    st.error(f"Préparation impossible : {error}")
            if prepared:
                unresolved_cards = [
                    card for card in prepared["cards"] if card.get("scryfall_resolution_pending")
                ]
                if unresolved_cards:
                    st.warning(
                        "Sans correspondance Scryfall exacte (pas de suivi de prix ni vente dans l'application) : "
                        + ", ".join(card["name"] for card in unresolved_cards)
                        + ". Ces lignes seront conservées avec les données Cardmarket, sans associer une impression au hasard."
                    )
                st.dataframe(
                    [{
                        "Carte": card["name"], "Édition": card["set_code"].upper(),
                        "Qté": card["quantity"], "Langue": card["language"],
                        "État": card["condition"], "Foil": card["foil"],
                        "Prix de vente (€)": card["selling_price"],
                        "Scryfall": "Non identifié" if card.get("scryfall_resolution_pending") else "Identifié",
                    } for card in prepared["cards"]],
                    hide_index=True, use_container_width=True,
                )
                st.warning(
                    f"{len(prepared['cards'])} ligne(s) préparée(s). "
                    "Les cartes absentes du fichier seront retirées du stock en vente, sans créer de vente. "
                    "Les prix de vente et les quantités du fichier seront utilisés pour la valeur du stock."
                )
                confirmed = st.checkbox(
                    "Je confirme que ce fichier contient tout mon stock Cardmarket actuel.",
                    key=f"confirm_cardmarket_{file_hash}",
                )
                if st.button("Remplacer le stock en vente", disabled=not confirmed, type="primary"):
                    try:
                        result = data_manager.synchronize_cardmarket_stock(
                            prepared["cards"], prepared["collection"]
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.session_state.pop("cardmarket_sync_prepared", None)
                        st.session_state["cardmarket_sync_result"] = result
                        st.rerun()
        sync_result = st.session_state.pop("cardmarket_sync_result", None)
        if sync_result:
            st.success(
                f"Stock synchronisé : {sync_result['matched']} mise(s) à jour, "
                f"{sync_result['added']} nouvelle(s) ligne(s), "
                f"{sync_result['withdrawn']} retirée(s) du stock."
            )

    with st.expander("Importer un CSV générique (tableur…)"):
        csv_file = st.file_uploader("Fichier CSV", type=["csv"], key="csv_import_file")
        if csv_file is not None:
            try:
                csv_text = csv_file.getvalue().decode("utf-8-sig")
                if cardmarket_import.is_cardmarket_export(csv_text):
                    csv_rows = []
                    csv_errors = [
                        "Cet export Cardmarket doit être utilisé dans « Synchroniser mon stock Cardmarket "
                        "(CSV complet) » ci-dessus. L'identification se fait par identifiant produit, "
                        "sans traduction des noms."
                    ]
                else:
                    csv_rows, csv_errors = csv_import.parse_csv(csv_text)
            except UnicodeDecodeError:
                csv_rows, csv_errors = [], ["Fichier illisible : enregistre-le en UTF-8."]
            if csv_rows:
                try:
                    resolved_cards, resolve_errors = csv_import.resolve(csv_rows)
                except requests.RequestException as error:
                    resolved_cards = []
                    resolve_errors = [f"Scryfall indisponible ({type(error).__name__}) ; import annulé."]
                csv_errors += resolve_errors
                if resolved_cards:
                    st.write(f"{len(resolved_cards)} carte(s) prête(s) à importer :")
                    st.dataframe(
                        [{"Carte": c["name"], "Édition": c["set_code"].upper(), "Qté": c["quantity"],
                          "Achat": c["purchase_price"], "Vente": c["selling_price"]} for c in resolved_cards],
                        hide_index=True, use_container_width=True,
                    )
                    if st.button("Importer ces cartes", type="primary", key="csv_import_go"):
                        added = data_manager.add_cards_to_collection(resolved_cards)
                        st.success(f"{added} carte(s) ajoutée(s) au stock.")
                        st.rerun()
            for message in csv_errors:
                st.warning(message)

    query = st.text_input("Nom de la carte (FR ou EN) :", placeholder="ex: Sheoldred, Raavan, Atraxa...")

    if query and len(query) >= 2:
        suggestions = scryfall_for_display(scryfall.search_card_autocomplete, query)
        if suggestions:
            selected_name = st.selectbox("Suggestions trouvées :", suggestions)

            if selected_name:
                card_data = scryfall_for_display(scryfall.get_card_by_name, selected_name)
                if card_data:
                    oracle_id = card_data.get("oracle_id")
                    prints = scryfall_for_display(scryfall.get_card_prints, oracle_id) if oracle_id else [card_data]
                    if not prints:
                        st.warning("Aucune liste d'éditions disponible ; seule la carte trouvée est affichée.")
                        prints = [card_data]

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
                                    st.success(f"**{selected_card['name']}** enregistrée dans le stock !")
                                    st.rerun()
                                else:
                                    st.error("Erreur lors de l'écriture dans la base du stock.")

