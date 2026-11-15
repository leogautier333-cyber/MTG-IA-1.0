"""Tableau de bord et gestion du stock."""
import hashlib
import json
import re

import pandas as pd
import plotly.express as px
import streamlit as st

import data_manager
from modules.prices_view import price_history_frame
from services import portfolio_metrics, stock_advisor, stock_filters


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


def render_stock_tab():
    st.header("Tableau de bord & Stock")

    collection = data_manager.get_collection()
    price_history = data_manager.get_price_history()
    metrics = portfolio_metrics.summarize(collection, price_history)
    if metrics["cards"]:
        st.subheader("Vue du stock · cartes en vente")
        unresolved = [
            card for card in collection
            if card.get("status", "FOR_SALE") == "FOR_SALE"
            and card.get("scryfall_resolution_pending")
        ]
        if unresolved:
            st.warning(
                f"{len(unresolved)} ligne(s) sans correspondance Scryfall exacte : "
                + ", ".join(card.get("name", "Carte") for card in unresolved)
                + ". Le suivi de prix et la vente dans l'application sont indisponibles pour ces cartes."
            )
        metric_cols = st.columns(4)
        metric_cols[0].metric("Cartes en stock", f"{metrics['copies']} exemplaires",
                              f"{metrics['cards']} lignes")
        metric_cols[1].metric(
            "Valeur aux prix de vente", f"{metrics['asking_value']:.2f} €",
            help="Somme des prix de vente unitaires enregistrés, multipliés par les quantités. "
                 "Ce montant ne garantit pas une vente.",
        )
        metric_cols[2].metric(
            "Valeur marché estimée",
            f"{metrics['gross_value']:.2f} €" if metrics["coverage_pct"] else "—",
            help="Somme des derniers relevés Scryfall disponibles, multipliés par les quantités. "
                 "Elle peut être incomplète et ne garantit pas une vente.",
        )
        metric_cols[3].metric(
            "Prix couverts", f"{metrics['coverage_pct']:.0f} %",
            f"{metrics['missing_prices']} sans prix · {metrics['stale_prices']} anciens (> 7 j)",
        )
        if metrics["missing_prices"] or metrics["stale_prices"]:
            st.caption(
                "Les cartes sans relevé ne sont pas incluses dans cette valeur; "
                "les prix sont des références Scryfall indicatives."
            )
        exposure_col, stress_col = st.columns([3, 2])
        with exposure_col:
            st.caption("Exposition par carte · les 10 plus grosses positions")
            top_positions = metrics["positions"][:10]
            if top_positions:
                exposure_frame = pd.DataFrame(top_positions)
                exposure_frame["Carte"] = exposure_frame.apply(
                    lambda row: f"{row['name']} · {row['set_code']}", axis=1
                )
                st.plotly_chart(
                    px.bar(exposure_frame, x="market_value", y="Carte", orientation="h",
                           labels={"market_value": "Valeur marché (€)"})
                    .update_layout(yaxis={"categoryorder": "total ascending"}),
                    use_container_width=True,
                )
            if metrics["set_exposure"]:
                st.caption("Exposition par édition")
                st.dataframe(
                    [{
                        "Édition": set_code, "Valeur (€)": round(value, 2),
                        "Part du portefeuille": f"{value / metrics['gross_value'] * 100:.1f}%"
                        if metrics["gross_value"] else "—",
                    } for set_code, value in metrics["set_exposure"]],
                    hide_index=True, use_container_width=True,
                )
        with stress_col:
            st.caption("Scénarios sur les prix de marché disponibles (simulation, pas une prévision)")
            if metrics["coverage_pct"]:
                st.dataframe(
                    [{
                        "Baisse": f"-{scenario['drop_pct']}%",
                        "Valeur nette": f"{scenario['value_after_fees']:.2f} €",
                        "Écart actuel": f"{scenario['change_vs_now']:+.2f} €",
                    } for scenario in metrics["scenarios"]],
                    hide_index=True, use_container_width=True,
                )
            else:
                st.info("Aucun relevé Scryfall disponible pour calculer ces scénarios.")
        st.divider()

    indexed_cards = []
    filtered_cards = []

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

            status_cards = [
                card for card in collection
                if card.get("status", "FOR_SALE") == target_status
            ]
            filter_search, filter_set, filter_sort = st.columns([2, 1.4, 1.6])
            with filter_search:
                search_stock = st.text_input("Rechercher", placeholder="Nom, code d'extension ou numéro")
            with filter_set:
                set_codes = sorted({str(c.get("set_code") or "").upper() for c in status_cards} - {""})
                set_filter = st.selectbox("Extension", ["Toutes"] + set_codes)
            with filter_sort:
                sort_filter = st.selectbox("Trier par", stock_filters.SORT_OPTIONS)
            filter_language, filter_condition, filter_foil, filter_min, filter_max = st.columns(5)
            with filter_language:
                languages = sorted({str(c.get("language") or "FR") for c in status_cards})
                language_filter = st.selectbox("Langue", ["Toutes"] + languages)
            with filter_condition:
                conditions = sorted({str(c.get("condition") or "NM") for c in status_cards})
                condition_filter = st.selectbox("État", ["Tous"] + conditions)
            with filter_foil:
                foil_filter = st.selectbox("Finition", ["Toutes", "Foil", "Non foil"])
            with filter_min:
                min_price = st.number_input("Prix min. (€)", min_value=0.0, value=0.0, step=0.5)
            with filter_max:
                max_price = st.number_input(
                    "Prix max. (€)", min_value=0.0, value=0.0, step=0.5,
                    help="0 signifie sans limite de prix maximum.",
                )

        try:
            indexed_cards = stock_filters.filter_stock(
                collection, status=target_status, query=search_stock,
                set_code="" if set_filter == "Toutes" else set_filter,
                language="" if language_filter == "Toutes" else language_filter,
                condition="" if condition_filter == "Tous" else condition_filter,
                foil="" if foil_filter == "Toutes" else foil_filter,
                min_price=min_price, max_price=max_price or None, sort_by=sort_filter,
            )
        except ValueError as error:
            st.warning(str(error))
            indexed_cards = []
        filtered_cards = [card for _, card in indexed_cards]
        st.caption(
            f"{len(filtered_cards)} ligne(s) · "
            f"{sum(max(1, int(card.get('quantity') or 1)) for card in filtered_cards)} exemplaire(s)"
        )
        popularity_by_card = data_manager.get_popularity_deltas(
            tuple(sorted({str(card["scryfall_id"]) for card in filtered_cards if card.get("scryfall_id")}))
        )
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
                    edit_quantity, edit_selling, edit_target = st.columns(3)
                    quantity_value = edit_quantity.number_input(
                        "Quantité", min_value=1, value=max(1, int(edit_card.get("quantity", 1))), step=1
                    )
                    selling_value = edit_selling.number_input(
                        "Prix de vente unitaire (€)", min_value=0.0,
                        value=float(edit_card.get("selling_price", 0)), step=0.5
                    )
                    target_value = edit_target.number_input(
                        "Objectif de vente (€, 0 = aucun)", min_value=0.0,
                        value=float(edit_card.get("target_price") or 0), step=0.5
                    )
                    save_quick_edit = st.form_submit_button("Enregistrer", type="primary")

                if save_quick_edit:
                    if data_manager.update_card(
                        edit_card, quantity_value, edit_card.get("purchase_price", 0),
                        selling_value, target_value
                    ):
                        st.toast("Modifications enregistrées", icon="✅")
                    else:
                        st.toast("Carte modifiée ailleurs : données rechargées, réessaie.", icon="⚠️")
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
                original_index = indexed_cards[idx][0]
                scryfall_data = None
                if card.get("name") and not card.get("scryfall_resolution_pending") and not card.get("image_url"):
                    scryfall_data = data_manager.get_scryfall_card(card["name"])
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

                        s_price = card.get("selling_price", 0.0)
                        st.markdown(f"Prix de vente : **{s_price:.2f} €**")
                        st.caption("Popularité EDHREC (30 j) : " + stock_advisor.popularity_badge(
                            popularity_by_card.get(str(card.get("scryfall_id")))
                        ))
                        if target_status == "FOR_SALE" and card.get("scryfall_id"):
                            with st.popover("Vendre", use_container_width=True):
                                actual_p = st.number_input("Prix de vente réel (€) :", min_value=0.0, value=float(s_price), step=0.5, key=f"p_{card['scryfall_id']}_{original_index}")
                                if st.button("Confirmer", key=f"btn_{card['scryfall_id']}_{original_index}", type="primary"):
                                    if data_manager.mark_selected_card_as_sold(card, actual_p):
                                        st.success("Vendu !")
                                        st.rerun()
                                    else:
                                        st.error("Cette fiche a changé : recharge les données avant de réessayer.")

        # ---------------------------------------------------------------
        # MODE 2 : TABLEAU ÉPURÉ ET FICHE DÉTAILLÉE
        # ---------------------------------------------------------------
        else:
            df = pd.DataFrame(filtered_cards)
            df["popularity"] = [
                stock_advisor.popularity_badge(popularity_by_card.get(str(card.get("scryfall_id"))))
                for card in filtered_cards
            ]
            display_cols = ["name", "set_code", "quantity", "condition", "language", "foil", "selling_price", "liquidity_rating", "popularity"]
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
                "popularity": st.column_config.TextColumn("Popularité 30 j", width="small"),
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
            card_names = [f"{c['name']} ({c['set_code'].upper()}) - {c['selling_price']}€" for c in filtered_cards]
            selected_idx = st.selectbox("Sélectionner une carte :", range(len(card_names)), format_func=lambda x: card_names[x])

            if selected_idx is not None:
                card = filtered_cards[selected_idx]
                col_img, col_info = st.columns([1, 2])

                scryfall_data = None
                if card.get("name") and not card.get("scryfall_resolution_pending") and (
                    not card.get("image_url") or not card.get("mana_cost")
                    or not card.get("type_line") or not card.get("oracle_text")
                ):
                    scryfall_data = data_manager.get_scryfall_card(card["name"])

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
                    st.write(f"Prix de Vente Fixé : {card['selling_price']:.2f} €")

                    if legalities:
                        st.markdown("#### **Légalités en Format**")
                        leg_cols = st.columns(2)
                        formats = ["standard", "pioneer", "modern", "legacy", "vintage", "commander"]
                        for idx, fmt in enumerate(formats):
                            status = legalities.get(fmt, "not_legal")
                            badge = "🟢 **LEGAL**" if status == "legal" else "⚪ **NOT LEGAL**"
                            leg_cols[idx % 2].markdown(f"{badge} `{fmt.capitalize()}`")

                    if target_status == "FOR_SALE" and card.get("scryfall_id"):
                        st.divider()
                        st.subheader("Marquer comme vendue")
                        sale_price = st.number_input(
                            "Prix réel de vente (€) :",
                            min_value=0.0,
                            value=float(card['selling_price']),
                            step=0.5
                        )
                        if st.button("Confirmer la vente", type="primary"):
                            if data_manager.mark_selected_card_as_sold(card, sale_price):
                                st.success("Carte enregistrée comme vendue !")
                                st.rerun()
                            else:
                                st.error("Erreur lors de la mise à jour.")

                    st.divider()
                    st.subheader("Journal de décision")
                    with st.form(f"decision_journal_{selected_idx}_{card.get('scryfall_id')}"):
                        decision_type = st.selectbox(
                            "Décision",
                            ["Conserver", "Achat", "Vendre", "Vente réalisée"],
                            index=0,
                        )
                        decision_horizon = st.text_input(
                            "Horizon ou objectif", placeholder="Ex. jusqu'à la prochaine extension"
                        )
                        decision_notes = st.text_area(
                            "Pourquoi cette décision ?", placeholder="Thèse d'achat, raison de conserver ou de vendre…"
                        )
                        save_decision = st.form_submit_button("Ajouter au journal")
                    if save_decision:
                        try:
                            if data_manager.record_decision(
                                card, decision_type, decision_notes, decision_horizon
                            ):
                                st.success("Décision ajoutée au journal.")
                                st.rerun()
                            else:
                                st.warning("Cette fiche a changé depuis son affichage. Recharge la page et réessaie.")
                        except ValueError as error:
                            st.error(str(error))
                    journal = card.get("decision_journal", [])
                    if journal:
                        for item in reversed(journal):
                            st.markdown(
                                f"**{item.get('created_at', '')[:10]} · {item.get('decision', '')}**"
                                + (f" · {item['horizon']}" if item.get("horizon") else "")
                                + f"\n\n{item.get('notes', '')}"
                            )

                # Historique de prix
                price_hist = data_manager.get_variant_price_history(
                    card["scryfall_id"], foil=card.get("foil", False)
                )
                if price_hist:
                    df_hist = price_history_frame(price_hist)
                    fig = px.line(
                        df_hist.sort_values("Date"), x="Date", y="price_eur",
                        title="Historique des relevés Scryfall",
                        labels={"price_eur": "Prix (€)"},
                    )
                    st.plotly_chart(fig, width="stretch")

        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        with st.popover("Export / Import", use_container_width=False):
            export_json, export_csv = st.columns(2)
            collection_json = json.dumps(collection, ensure_ascii=False, indent=2)
            active_collection = [
                card for card in collection if card.get("status", "FOR_SALE") == "FOR_SALE"
            ]
            collection_csv = pd.DataFrame(active_collection).to_csv(index=False).encode("utf-8-sig")

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
                    "CSV du stock en vente",
                    data=collection_csv,
                    file_name="stock.csv",
                    mime="text/csv",
                    disabled=not active_collection,
                    help="Export de l'application, distinct de l'export Cardmarket nécessaire à la synchronisation.",
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
