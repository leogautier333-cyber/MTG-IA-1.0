import streamlit as st

import data_manager
from modules.local_assistant import answer_local_query
from modules.market_analyzer import portfolio_summary
from services.market_monitor import refresh_watchlist_prices
from services.source_health import get_source_statuses
from services.backtesting import backtest_price_threshold, evaluate_alerts
from services.market_signals import analyze_snapshot_buyout, calculate_reprint_risk
from services.deck_importer import analyze_decklist, fetch_decklist
from services.spoiler_watcher import scan_spoilers_and_trends


def render_nexus_tab():
    st.header("Magic Intelligence Nexus")
    st.caption("Analyses locales et sources externes consultées à la demande.")

    st.subheader("Santé des sources")
    health_columns = st.columns(3)
    for column, (source, health) in zip(health_columns, get_source_statuses().items()):
        label = {
            "scryfall": "Scryfall API",
            "scryfall_bulk": "Scryfall Bulk",
            "reddit": "Reddit r/mtgfinance",
        }.get(source, source)
        checked_at = health.get("checked_at")
        details = f"Dernier contrôle : {checked_at}" if checked_at else health["details"]
        if health["status"] == "HEALTHY":
            column.success(f"{label} · {health['status']}\n\n{details}")
        elif health["status"] in {"FAILED", "STALE"}:
            column.error(f"{label} · {health['status']}\n\n{health['details'] or details}")
        else:
            column.warning(f"{label} · {health['status']}\n\n{details}")

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
        for event in spoiler_result.get("events", []):
            st.caption(
                f"{event['event_type']} · {event['card_name']} · "
                f"confiance {event['confidence'] * 100:.0f} % · {event['source']}"
            )

    st.subheader("Sentinelle anti-buyout")
    st.info(
        "Indice proxy calculé à partir des snapshots de prix, des événements Scryfall "
        "et de l’évolution du rang EDHREC. Ce score signale une anomalie, pas une preuve de manipulation."
    )
    tracked_variants = {}
    for card in [*watchlist, *collection]:
        scryfall_id = card.get("scryfall_id")
        if not scryfall_id:
            continue
        variant_key = (
            scryfall_id, bool(card.get("foil")), card.get("language", "FR"),
            card.get("condition", "NM"),
        )
        tracked_variants.setdefault(variant_key, card)
    buyout_rows = []
    for card in tracked_variants.values():
        result = analyze_snapshot_buyout(
            card["scryfall_id"], foil=card.get("foil", False),
            language=card.get("language", "FR"), condition=card.get("condition", "NM"),
        )
        buyout_rows.append({
            "Carte": card.get("name", "Carte"),
            "Indice": result.get("status", "INSUFFICIENT_DATA"),
            "Score": result.get("score"),
            "Variation 7 j": (
                f"{result['price_change_pct']:+.1f} %"
                if result.get("price_change_pct") is not None else "n/d"
            ),
            "Rang EDHREC": result.get("edhrec_rank", "n/d"),
            "Analyse": result["reason"],
        })
    if buyout_rows:
        st.dataframe(buyout_rows, hide_index=True, use_container_width=True)
    else:
        st.caption("Aucune variante de collection ou de watchlist à analyser.")

    st.subheader("Évaluation des signaux")
    if st.button("Évaluer les alertes échues", key="nexus_evaluate_alerts"):
        try:
            st.session_state["nexus_alert_evaluation"] = evaluate_alerts()
        except Exception as error:
            st.error(f"Échec de l'évaluation : {error}")
    evaluation_result = st.session_state.get("nexus_alert_evaluation")
    if evaluation_result:
        st.caption(f"{evaluation_result['evaluations_written']} résultat(s) à +7/+30 jours enregistré(s).")
    metrics = data_manager.storage.get_alert_metrics()
    if metrics:
        st.dataframe(
            [{
                "Signal": row["signal_type"],
                "Horizon (jours)": row["horizon_days"],
                "Échantillon": row["sample_count"],
                "Précision": f"{row['accuracy'] * 100:.1f} %",
                "Variation moyenne": f"{row['average_change_pct']:.1f} %",
            } for row in metrics],
            hide_index=True,
            use_container_width=True,
        )

    backtest_cards = [card for card in [*collection, *watchlist] if card.get("scryfall_id")]
    if backtest_cards:
        backtest_position = st.selectbox(
            "Carte à backtester",
            range(len(backtest_cards)),
            format_func=lambda index: (
                f"{backtest_cards[index].get('name', 'Carte')} · "
                f"{backtest_cards[index].get('set_code', 'watchlist')}"
            ),
            key="nexus_backtest_card",
        )
        selected_backtest_card = backtest_cards[backtest_position]
        backtest_controls = st.columns(3)
        threshold = backtest_controls[0].number_input(
            "Seuil (%)", min_value=1.0, value=15.0, step=1.0, key="nexus_backtest_threshold"
        )
        horizon = backtest_controls[1].selectbox(
            "Horizon", [7, 30, 90], key="nexus_backtest_horizon"
        )
        direction = backtest_controls[2].selectbox(
            "Signal", ["drop", "spike"], format_func=lambda value: "Baisse" if value == "drop" else "Hausse",
            key="nexus_backtest_direction",
        )
        if st.button("Lancer le backtest", key="nexus_run_backtest"):
            st.session_state["nexus_backtest_result"] = backtest_price_threshold(
                selected_backtest_card["scryfall_id"], threshold_pct=threshold,
                horizon_days=horizon, direction=direction,
                foil=selected_backtest_card.get("foil", False),
                language=selected_backtest_card.get("language", "FR"),
                condition=selected_backtest_card.get("condition", "NM"),
            )
        backtest_result = st.session_state.get("nexus_backtest_result")
        if backtest_result:
            if backtest_result["sample_count"]:
                st.metric(
                    "Précision historique",
                    f"{backtest_result['accuracy'] * 100:.1f} %",
                    delta=f"{backtest_result['sample_count']} signal(s) évalué(s)",
                )
            else:
                st.info("Historique insuffisant pour ce seuil et cet horizon.")

        st.subheader("Risque de réimpression")
        if st.button("Calculer le risque", key="nexus_reprint_risk"):
            try:
                st.session_state["nexus_reprint_risk"] = calculate_reprint_risk(
                    selected_backtest_card["scryfall_id"]
                )
            except Exception as error:
                st.error(f"Calcul impossible : {error}")
        reprint_risk = st.session_state.get("nexus_reprint_risk")
        if reprint_risk:
            if reprint_risk["score"] is None:
                st.info(reprint_risk["reason"])
            else:
                st.metric(
                    f"{reprint_risk['status']} · {reprint_risk['score']}/100",
                    f"Confiance {reprint_risk['confidence'] * 100:.0f} %",
                )
                st.caption(reprint_risk["reason"])
                historical_30 = reprint_risk.get("expected_change_30d_pct")
                historical_90 = reprint_risk.get("expected_change_90d_pct")
                if historical_30 is not None or historical_90 is not None:
                    impact_30 = f"{historical_30:.1f} %" if historical_30 is not None else "n/d"
                    impact_90 = f"{historical_90:.1f} %" if historical_90 is not None else "n/d"
                    st.caption(
                        "Variation moyenne observée après réimpression : "
                        f"30 j {impact_30} · 90 j {impact_90}"
                    )

    st.subheader("Importer une decklist")
    deck_source = st.text_area("Liste ou URL Moxfield / Archidekt", key="nexus_deck_source")
    if st.button("Analyser la decklist", key="nexus_import_deck") and deck_source.strip():
        try:
            imported_cards = fetch_decklist(deck_source)
            st.session_state["nexus_deck_analysis"] = analyze_decklist(imported_cards, collection)
        except Exception as error:
            st.error(f"Import impossible : {error}")
    deck_analysis = st.session_state.get("nexus_deck_analysis")
    if deck_analysis:
        deck_metrics = st.columns(3)
        deck_metrics[0].metric("Cartes requises", deck_analysis["required_count"])
        deck_metrics[1].metric("Déjà en stock", deck_analysis["owned_count"])
        deck_metrics[2].metric("Acquisition estimée", f"{deck_analysis['estimated_acquisition_cost_eur']:.2f} €")
        if deck_analysis["missing"]:
            st.dataframe(deck_analysis["missing"], hide_index=True, use_container_width=True)
        if deck_analysis["excluded_from_deck"]:
            st.caption("Cartes de ton stock absentes de cette liste (ceci ne prouve pas qu'elles ne sont jamais jouées).")
            st.dataframe(
                [{"Carte": card.get("name"), "Édition": card.get("set_code"), "Quantité": card.get("quantity", 1)}
                 for card in deck_analysis["excluded_from_deck"]],
                hide_index=True,
                use_container_width=True,
            )

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