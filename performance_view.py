import pandas as pd
import plotly.express as px
import streamlit as st

import data_manager


def render_performance_tab():
    collection = data_manager.get_collection()

    if not collection:
        st.info("Aucune donnée financière disponible pour le moment.")
        return

    for_sale_cards = [c for c in collection if c.get("status") == "FOR_SALE"]
    sold_cards = [c for c in collection if c.get("status") == "SOLD"]

    total_value = sum(float(c.get("selling_price", 0)) * int(c.get("quantity", 1)) for c in for_sale_cards)
    total_invested = sum(float(c.get("purchase_price", 0)) * int(c.get("quantity", 1)) for c in for_sale_cards)
    turnover = sum(float(c.get("actual_sale_price", 0) or 0.0) * int(c.get("quantity", 1)) for c in sold_cards)
    total_net_profit = sum(
        data_manager.calculate_net_margin(
            c.get("actual_sale_price", 0), c.get("purchase_price", 0)
        )["net_profit"] * c.get("quantity", 1)
        for c in sold_cards
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Valeur du Stock", f"{total_value:.2f} €")
    col2.metric("Capital Investi", f"{total_invested:.2f} €")
    col3.metric("Chiffre d'Affaires", f"{turnover:.2f} €")
    col4.metric("Bénéfice Net Cumulé", f"{total_net_profit:.2f} €", delta=f"{total_net_profit:.2f} €")

    roi_global = (total_net_profit / total_invested * 100) if total_invested else 0.0

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
            unit_profit = data_manager.calculate_net_margin(
                sold_card.get("actual_sale_price", 0),
                sold_card.get("purchase_price", 0)
            )["net_profit"]
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
