"""Affichage du bilan des ventes."""
from datetime import date

import streamlit as st

import data_manager
from services import sales_report


def render_sales_tab():
    st.header("Bilan des ventes")
    sales_collection = data_manager.get_collection()
    sold_lines = sales_report.compare_to_market(
        sales_report.sale_lines(sales_collection), data_manager.get_price_history()
    )
    if not sold_lines:
        st.info("Aucune vente enregistrée pour le moment.")
        return

    total = sales_report.summarize(sold_lines)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Cartes vendues", total["cards_sold"])
    k2.metric("Chiffre d'affaires", f"{total['revenue']:.2f} €")
    profit = f"{total['net_profit']:+.2f} €" if total["net_profit"] is not None else "—"
    roi = f"{total['roi_percent']:+.1f} % ROI" if total["roi_percent"] is not None else "ROI indisponible"
    k3.metric("Bénéfice net", profit, roi)
    if total["unknown_cost_sales"]:
        st.warning(
            f"Bénéfice et ROI partiels : {total['unknown_cost_sales']} vente(s) "
            "sans prix d'achat connu sont exclues."
        )
    k4.metric("Frais Cardmarket", f"{total['fees']:.2f} €")
    if total["market_compared_sales"]:
        k5.metric(
            "Ventes vs référence marché",
            f"{total['sale_vs_market']:+.2f} €",
            f"{total['market_compared_sales']} vente(s) comparée(s)",
            help="Écart au dernier prix Scryfall connu à la date de vente; comparaison indicative.",
        )
    else:
        k5.metric("Ventes vs référence marché", "—", "aucun prix historique exploitable")

    months = sales_report.monthly_summary(sales_collection)
    if months:
        st.subheader("Par mois")
        st.dataframe(
            [{
                "Mois": m["month"], "Cartes": m["cards_sold"],
                "CA (€)": m["revenue"], "Frais (€)": m["fees"],
                "Coût (€)": m["cost"], "Bénéfice net (€)": m["net_profit"],
                "ROI (%)": m["roi_percent"],
            } for m in months],
            hide_index=True, width="stretch",
        )
        known_months = {m["month"]: m["net_profit"] for m in reversed(months)
                        if m["net_profit"] is not None}
        if known_months:
            st.bar_chart(known_months, y_label="Bénéfice net (€)")

    st.subheader("Détail des ventes")
    st.dataframe(
        [{
            "Date": line["sold_on"].isoformat() if line["sold_on"] else "—",
            "Carte": line["name"], "Édition": line["set_code"], "Qté": line["quantity"],
            "CA (€)": round(line["revenue"], 2),
            "Bénéfice net (€)": round(line["net_profit"], 2) if line["net_profit"] is not None else None,
            "Référence marché (€)": round(line["market_reference"], 2)
            if line["market_reference"] is not None else "—",
            "Écart vs marché (€)": line["sale_vs_market"]
            if line["sale_vs_market"] is not None else "—",
            "Date référence": line["market_reference_day"] or "—",
        } for line in sorted(sold_lines, key=lambda line: line["sold_on"] or date.min, reverse=True)],
        hide_index=True, width="stretch",
    )
