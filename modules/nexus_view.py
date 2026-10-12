import streamlit as st

import data_manager
from modules.local_assistant import answer_local_query
from modules.market_analyzer import analyze_buyout_signal, portfolio_summary
from services.market_monitor import get_watchlist_observations, refresh_watchlist_prices
from services.spoiler_watcher import scan_spoilers_and_trends


def render_nexus_tab():
    st.header("Magic Intelligence Nexus")
    st.caption("Analyses locales et sources externes consultées à la demande.")

    collection = data_manager.get_collection()
    watchlist = data_manager.get_watchlist()
    overview = portfolio_summary(collection)
    left, right = st.columns(2)
    left.metric("Cartes en vente", overview["for_sale_count"])
    right.metric("Cartes en watchlist", len(watchlist))

    st.subheader("Veille de prix hors-stock")
    if st.button("Actualiser les prix de la watchlist", key="nexus_refresh_prices"):
        try:
            st.session_state["nexus_market_result"] = refresh_watchlist_prices(watchlist, collection)
        except Exception as error:
            st.session_state["nexus_market_error"] = str(error)

    market_error = st.session_state.pop("nexus_market_error", None)
    if market_error:
        st.error(f"Échec de la veille de prix : {market_error}")
    market_result = st.session_state.get("nexus_market_result")
    if market_result:
        st.caption(market_result["message"])
        if market_result["observed"] == 0:
            st.info("Aucun nouveau prix observé. Une comparaison de baisse sera possible après deux snapshots réussis.")
        for alert in market_result["alerts"]:
            st.warning(
                f"[OPPORTUNITÉ ACHAT] {alert['name']} baisse de {alert['change_pct']:.1f} % "
                f"({alert['previous_price']:.2f} € → {alert['current_price']:.2f} €; {alert['currency_source']})."
            )

    st.subheader("Veille spoilers & tendances Reddit")
    if st.button("Analyser Scryfall et r/mtgfinance", key="nexus_scan_spoilers"):
        try:
            st.session_state["nexus_spoiler_result"] = scan_spoilers_and_trends(watchlist, collection)
        except Exception as error:
            st.session_state["nexus_spoiler_error"] = str(error)
    spoiler_error = st.session_state.pop("nexus_spoiler_error", None)
    if spoiler_error:
        st.error(f"Échec de la veille : {spoiler_error}")
    spoiler_result = st.session_state.get("nexus_spoiler_result")
    if spoiler_result:
        if spoiler_result["errors"]:
            st.warning("Sources temporairement indisponibles : " + "; ".join(spoiler_result["errors"]))
        st.caption(
            f"{spoiler_result['spoiler_count']} carte(s) preview Scryfall · "
            f"{spoiler_result['reddit_posts_scanned']} posts Reddit analysés · "
            f"température observée : {spoiler_result['community_temperature']} "
            f"({spoiler_result['reddit_hype_posts']} post(s) avec signaux spéculatifs)."
        )
        if not spoiler_result["alerts"]:
            st.info("Aucun spoiler ou signal Reddit correspondant au stock et à la watchlist.")
        for alert in spoiler_result["alerts"]:
            st.warning(alert["message"])
            if alert["source_url"]:
                st.markdown(f"[Ouvrir la source]({alert['source_url']})")

    st.subheader("Sentinelle anti-buyout")
    st.info(
        "Les snapshots Scryfall ne comprennent pas les volumes de ventes Cardmarket. "
        "Une hausse de prix seule est donc classée comme indéterminée, jamais comme buyout confirmé."
    )
    observations = get_watchlist_observations()
    cards_with_snapshots = [
        (card, observations.get(card.get("scryfall_id"), []))
        for card in watchlist
        if card.get("scryfall_id") and len(observations.get(card.get("scryfall_id"), [])) >= 2
    ]
    if not cards_with_snapshots:
        st.caption("Deux snapshots de prix sont nécessaires avant d’évaluer une variation; les volumes de vente restent indisponibles.")
    for card, points in cards_with_snapshots:
        buyout_check = analyze_buyout_signal([point["price_eur"] for point in points])
        st.caption(f"{card.get('name', 'Carte')} · {buyout_check['status']} · {buyout_check['reason']}")

    st.subheader("Assistant local")
    history = st.session_state.setdefault("nexus_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    query = st.chat_input("Interroge ton stock, ton ROI, ta watchlist ou un lot d'échange…", key="nexus_chat_input")
    if query:
        response = answer_local_query(query, collection, watchlist)
        history.extend([
            {"role": "user", "content": query},
            {"role": "assistant", "content": response},
        ])
        with st.chat_message("user"):
            st.markdown(query)
        with st.chat_message("assistant"):
            st.markdown(response)