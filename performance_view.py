import pandas as pd
import plotly.express as px
import streamlit as st

import data_manager
from services.portfolio_analytics import analyze_portfolio, simulate_sale, suggest_arbitrage


def render_performance_tab():
    collection = data_manager.get_collection()

    if not collection:
        st.info("Aucune donnée financière disponible pour le moment.")
        return

    for_sale_cards = [c for c in collection if c.get("status") == "FOR_SALE"]
    sold_cards = [c for c in collection if c.get("status") == "SOLD"]

    analytics = analyze_portfolio(collection)
    total_value = analytics["stock_value"]
    total_invested = analytics["invested_capital"]
    turnover = analytics["turnover"]
    total_net_profit = analytics["realized_profit"]

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Valeur du Stock", f"{total_value:.2f} €")
    col2.metric("Capital Investi", f"{total_invested:.2f} €")
    col3.metric("Chiffre d'Affaires", f"{turnover:.2f} €")
    col4.metric("Bénéfice Net Cumulé", f"{total_net_profit:.2f} €", delta=f"{total_net_profit:.2f} €")
    col5.metric("P&L latent", f"{analytics['unrealized_profit']:.2f} €", delta=f"ROI {analytics['unrealized_roi_pct']:.1f} %")

    roi_global = analytics["realized_roi_pct"]

    stock_history_rows = []
    for card in collection:
        added_date = pd.to_datetime(card.get("date_added"), errors="coerce")
        if pd.notna(added_date):
            stock_history_rows.append({
                "Date": added_date.normalize(),
                "Indicateur": "Valeur du stock",
                "Montant (€)": float(card.get("selling_price", 0)) * int(card.get("quantity", 1))
            })

    sold_history_rows = []
    for sold_card in sold_cards:
        sold_date = pd.to_datetime(sold_card.get("date_sold"), errors="coerce")
        if pd.notna(sold_date):
            unit_profit = simulate_sale(
                sold_card,
                sold_card.get("actual_sale_price", 0),
                shipping=sold_card.get("sale_shipping") or 0,
                condition_discount_pct=0.0,
            )["profit_per_card"]
            sold_history_rows.append({
                "Date": sold_date.normalize(),
                "Indicateur": "Bénéfice cumulé",
                "Montant (€)": unit_profit * int(sold_card.get("quantity", 1))
            })

    stock_history = pd.DataFrame(stock_history_rows)
    if not stock_history.empty:
        stock_history = stock_history.groupby(["Date", "Indicateur"], as_index=False)["Montant (€)"].sum()
        stock_history["Montant (€) cumulés"] = stock_history["Montant (€)"].cumsum()

    sold_history = pd.DataFrame(sold_history_rows)
    if not sold_history.empty:
        sold_history = sold_history.groupby(["Date", "Indicateur"], as_index=False)["Montant (€)"].sum()
        sold_history["Montant (€) cumulés"] = sold_history["Montant (€)"].cumsum()

    line_df = pd.concat(
        [
            stock_history[["Date", "Indicateur", "Montant (€) cumulés"]].rename(columns={"Montant (€) cumulés": "Montant (€)"})
            if not stock_history.empty else pd.DataFrame(columns=["Date", "Indicateur", "Montant (€)"]),
            sold_history[["Date", "Indicateur", "Montant (€) cumulés"]].rename(columns={"Montant (€) cumulés": "Montant (€)"})
            if not sold_history.empty else pd.DataFrame(columns=["Date", "Indicateur", "Montant (€)"])
        ],
        ignore_index=True
    )

    with st.container(border=True):
        st.subheader("Graphique & Analyse Financière")
        if line_df.empty:
            st.caption("Aucune donnée historique pour tracer la courbe. Les ventes ou ajouts de cartes feront apparaître la performance ici.")
        else:
            line_fig = px.line(
                line_df,
                x="Date",
                y="Montant (€)",
                color="Indicateur",
                color_discrete_sequence=["#c9a96e", "#82ad92"],
                title="Évolution de la performance"
            )
            line_fig.update_traces(mode="lines", line=dict(width=3))
            line_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(family="DM Sans, sans-serif", color="#eee9dc"),
                title_font=dict(family="Playfair Display, Georgia, serif", size=19),
                margin=dict(l=12, r=12, t=52, b=12),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                xaxis_title=None,
                yaxis_title="Euros"
            )
            line_fig.update_xaxes(showgrid=False)
            line_fig.update_yaxes(gridcolor="rgba(238,233,220,.08)", zeroline=False)
            st.plotly_chart(line_fig, use_container_width=True)

    with st.container(border=True):
        st.subheader("Synthèse Financière & Performance")
        summary = pd.DataFrame([
            {"Indicateur": "Valeur du Stock", "Valeur": f"{total_value:.2f} €"},
            {"Indicateur": "Capital Investi", "Valeur": f"{total_invested:.2f} €"},
            {"Indicateur": "Chiffre d'Affaires", "Valeur": f"{turnover:.2f} €"},
            {"Indicateur": "Bénéfice Net Cumulé", "Valeur": f"{total_net_profit:.2f} €"},
            {"Indicateur": "ROI global", "Valeur": f"{roi_global:.2f}%"},
        ])
        st.dataframe(
            summary,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Indicateur": st.column_config.TextColumn("Indicateur", width="large"),
                "Valeur": st.column_config.TextColumn("Valeur", width="medium")
            }
        )

    st.subheader("Risque et détention")
    risk_columns = st.columns(3)
    risk_columns[0].metric("Concentration top 10", f"{analytics['top_ten_concentration_pct']:.1f} %")
    risk_columns[1].metric("Indice de liquidité", f"{analytics['liquidity_index']:.0f}/100")
    risk_columns[2].metric("Détention moyenne", f"{analytics['average_holding_days']:.0f} jours")
    if analytics["by_set"]:
        st.dataframe(
            [{"Édition": name, "P&L": f"{values['profit']:.2f} €", "ROI": f"{values['roi_pct']:.1f} %"}
             for name, values in analytics["by_set"].items()],
            hide_index=True,
            use_container_width=True,
        )
    arbitrage = suggest_arbitrage(collection)
    if arbitrage:
        st.subheader("Arbitrage entre éditions")
        st.dataframe(
            [{
                "Carte": item["card_name"],
                "Vendre": item["sell_set"],
                "Racheter": item["buy_set"],
                "Qté": item["quantity"],
                "Écart indicatif": f"{item['estimated_spread_eur']:.2f} €",
            } for item in arbitrage],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(arbitrage[0]["source"])

    if for_sale_cards:
        st.subheader("Simulation de revente nette")
        selected_sale = st.selectbox(
            "Carte", range(len(for_sale_cards)),
            format_func=lambda index: f"{for_sale_cards[index].get('name', 'Carte')} · {for_sale_cards[index].get('set_code', '')}",
            key="performance_sale_card",
        )
        sale_card = for_sale_cards[selected_sale]
        sale_price = st.number_input(
            "Prix brut estimé par carte (€)", min_value=0.0,
            value=float(sale_card.get("selling_price") or 0), step=0.5,
            key="performance_sale_price",
        )
        shipping = st.number_input(
            "Port à ta charge par carte (€)", min_value=0.0, value=0.0,
            step=0.25, key="performance_sale_shipping",
        )
        sale_result = simulate_sale(sale_card, sale_price, shipping=shipping)
        st.metric("Produit net estimé", f"{sale_result['net_total']:.2f} €", delta=f"P&L {sale_result['profit_total']:.2f} € · ROI {sale_result['roi_pct']:.1f} %")
