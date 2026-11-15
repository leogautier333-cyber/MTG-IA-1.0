"""Affichage des prix, alertes et actions GitHub."""
import pandas as pd
import plotly.express as px
import streamlit as st

import data_manager
from services import alert_triage, github_actions, portfolio_history, pricing
from services.phase_one_monitor import analyze_stock_prices, send_test_alert


@st.fragment
def discord_test_button():
    if st.button("Envoyer une alerte test Discord"):
        try:
            send_test_alert()
            st.session_state["phase_one_test_sent"] = True
            st.session_state.pop("phase_one_test_error", None)
        except Exception as error:
            st.session_state["phase_one_test_error"] = str(error)
    if st.session_state.get("phase_one_test_error"):
        st.error(f"Test Discord impossible : {st.session_state['phase_one_test_error']}")
    if st.session_state.pop("phase_one_test_sent", False):
        st.success("Alerte test envoyée à Discord.")


@st.cache_data(ttl=60)
def github_latest_run(workflow_file):
    return github_actions.latest_run(workflow_file)



def price_history_frame(history):
    """Relevés de prix avec une colonne Date, tolérante aux anciens formats (2026-09-23_22h)."""
    frame = pd.DataFrame(history)
    frame["Date"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame["Date"] = frame["Date"].fillna(
        pd.to_datetime(frame["snapshot_date"], utc=True, errors="coerce")
    )
    return frame


def render_prices_tab():
    st.header("Courbes de prix & alertes du stock")
    st.caption(
        "Entre deux relevés : alerte de baisse à −10 % ou moins, de hausse à +10 % ou plus, "
        "et de stagnation entre ces seuils. Une alerte de stagnation par carte et finition "
        "est envoyée au maximum par jour UTC."
    )

    action_col, test_col = st.columns(2)
    if action_col.button("Analyser le stock maintenant", type="primary"):
        try:
            st.session_state["phase_one_analysis"] = analyze_stock_prices()
            data_manager.clear_cache()
            st.session_state.pop("phase_one_analysis_error", None)
        except Exception as error:
            st.session_state["phase_one_analysis_error"] = str(error)
    with test_col:
        discord_test_button()

    if st.session_state.get("phase_one_analysis_error"):
        st.error(f"Analyse impossible : {st.session_state['phase_one_analysis_error']}")
    analysis = st.session_state.get("phase_one_analysis")
    if analysis:
        st.success(
            f"{analysis['snapshots_written']} relevé(s) ajouté(s), "
            f"{analysis['alerts_created']} alerte(s) créée(s), "
            f"{analysis['notifications_sent']} alerte(s) transmise(s) à Discord."
        )
        for field, label in (
            ("untracked", "Sans identifiant Scryfall"),
            ("missing_prices", "Sans prix de marché disponible"),
            ("first_observations", "Premier relevé : comparaison possible à la prochaine analyse"),
        ):
            names = analysis.get(field, [])
            if names:
                st.warning(f"{label} ({len(names)}) : " + ", ".join(names))

    owned_cards = [
        card for card in data_manager.get_collection()
        if card.get("status", "FOR_SALE") == "FOR_SALE" and card.get("scryfall_id")
    ]
    if not owned_cards:
        st.info("Ajoute des cartes au stock pour commencer à suivre leurs prix.")
    else:
        position = st.selectbox(
            "Carte en stock",
            range(len(owned_cards)),
            format_func=lambda index: (
                f"{owned_cards[index].get('name', 'Carte')} · "
                f"{owned_cards[index].get('set_code', '').upper()}"
                f"{' · Foil' if owned_cards[index].get('foil') else ''}"
            ),
            key="phase_one_price_card",
        )
        selected_card = owned_cards[position]
        history = data_manager.get_variant_price_history(
            selected_card["scryfall_id"], foil=selected_card.get("foil", False)
        )
        if history:
            chart_data = price_history_frame(history)
            figure = px.line(
                chart_data.sort_values("Date"), x="Date", y="price_eur",
                markers=True, title=f"{selected_card.get('name', 'Carte')} · prix EUR",
                labels={"price_eur": "Prix (€)"},
            )
            st.plotly_chart(figure, use_container_width=True)
        else:
            st.info("Aucun relevé de prix pour cette carte. Lance une analyse pour créer le premier point.")

    price_alerts = data_manager.get_price_alerts()
    if price_alerts:
        st.subheader("Alertes du stock")
        prioritized_alerts = alert_triage.prioritize(
            price_alerts, data_manager.get_collection(), data_manager.get_price_history()
        )
        st.dataframe(
            [{
                "Carte": alert.get("card_name"),
                "Type": alert.get("kind", "PRICE"),
                "Tendance": {"UP": "Hausse", "DOWN": "Baisse", "STABLE": "Stagnation"}.get(
                    alert.get("trend"), "—"
                ),
                "Priorité": alert["priority"],
                "Variation": f"{alert['change_pct']:+.1f} %" if "change_pct" in alert else "—",
                "Prix précédent": f"{alert['previous_price']:.2f} €" if "previous_price" in alert else "—",
                "Prix actuel": f"{alert['current_price']:.2f} €" if "current_price" in alert else "—",
                "Impact estimé": f"{alert['impact_eur']:.2f} €" if alert["impact_eur"] is not None else "À évaluer",
                "Part du stock": f"{alert['portfolio_share_pct']:.1f} %",
                "Fraîcheur": alert["freshness"],
                "Fiabilité": alert["reliability"],
                "Verdict": alert.get("verdict", ""),
                "État Discord": alert.get("status"),
                "Date": alert.get("created_at"),
            } for alert in prioritized_alerts],
            hide_index=True,
            use_container_width=True,
        )

    st.divider()
    st.subheader("Valeur du stock dans le temps")
    portfolio_prices = data_manager.get_price_history()
    history_events = data_manager.get_inventory_events() if portfolio_prices else []
    if history_events:
        st.caption(
            f"Mouvements enregistrés depuis le {history_events[0]['occurred_at'][:10]} (UTC). "
            "Avant cette date, la courbe utilise les quantités et coûts actuels : elle est approximative. "
            "Les coûts inconnus et les cartes sans prix disponible sont exclus des totaux correspondants."
        )
    value_points = portfolio_history.value_series(
        data_manager.get_collection(), portfolio_prices, history_events
    )
    if len(value_points) >= 1:
        value_frame = pd.DataFrame(value_points).rename(
            columns={"value": "Valeur marché (€)", "cost": "Capital investi (€)"}
        )
        st.plotly_chart(
            px.line(value_frame, x="date", y=["Valeur marché (€)", "Capital investi (€)"], markers=True,
                    labels={"date": "Date", "value": "€", "variable": ""}),
            use_container_width=True,
        )
    else:
        st.info("Pas encore de relevés : lance une analyse pour créer le premier point.")

    st.subheader("Suggestion de prix de vente")
    min_margin = st.slider("Marge nette minimale souhaitée (%)", 0, 100, 15)
    price_lines = pricing.suggestions(
        data_manager.get_collection(), data_manager.get_price_history(), float(min_margin)
    )
    if price_lines:
        st.dataframe(
            [{
                "Carte": line["name"] + (" ✨" if line["foil"] else ""),
                "Achat": f"{line['purchase_price']:.2f} €" if line["purchase_price"] is not None else "Inconnu",
                "Mon prix": f"{line['selling_price']:.2f} €",
                "Marché": f"{line['market_price']:.2f} €" if line["market_price"] else "—",
                "Suggestion": f"{line['suggested']:.2f} €" if line["suggested"] else "—",
                "Avis": line["label"],
            } for line in price_lines],
            hide_index=True, use_container_width=True,
        )

    st.divider()
    st.subheader("Actions GitHub")
    if not github_actions.is_configured():
        st.info(
            "Pour activer ces boutons : crée un jeton GitHub (Settings → Developer settings → "
            "Fine-grained tokens) limité à ce dépôt avec la permission « Actions : Read and write », "
            "puis ajoute dans les Secrets Streamlit : GITHUB_DISPATCH_TOKEN = \"ton_jeton\"."
        )
    for workflow_file, workflow_label in github_actions.WORKFLOWS.items():
        label_col, button_col, status_col = st.columns([2, 2, 3])
        label_col.write(workflow_label)
        if button_col.button("Lancer", key=f"gh_run_{workflow_file}", disabled=not github_actions.is_configured()):
            try:
                github_actions.trigger(workflow_file)
                github_latest_run.clear()
                st.session_state[f"gh_msg_{workflow_file}"] = "Lancé sur GitHub (démarre dans quelques secondes)."
            except github_actions.GitHubActionsError as error:
                st.session_state[f"gh_msg_{workflow_file}"] = f"Erreur : {error}"
        if github_actions.is_configured():
            try:
                status_col.write(github_actions.run_label(github_latest_run(workflow_file)))
            except github_actions.GitHubActionsError as error:
                status_col.write(f"Statut indisponible : {error}")
        message = st.session_state.get(f"gh_msg_{workflow_file}")
        if message:
            st.caption(message)

