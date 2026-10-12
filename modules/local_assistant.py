import re

from modules.market_analyzer import portfolio_summary, suggest_exchange_lot


def answer_local_query(query, collection, watchlist):
    text = query.casefold()
    summary = portfolio_summary(collection)

    if any(word in text for word in ("watchlist", "surveill", "suivies")):
        if not watchlist:
            return "Ta watchlist est vide. Ajoute des cartes pour pouvoir comparer leur évolution de prix."
        names = [card.get("name", "Carte inconnue") for card in watchlist]
        return f"Ta watchlist contient {len(watchlist)} carte(s) : {', '.join(names)}."

    if any(word in text for word in ("échange", "echange", "lot")):
        amount_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|euros?)", text)
        if not amount_match:
            return "Pour composer un lot, indique une valeur cible, par exemple : « compose un lot autour de 40 € »."
        target = float(amount_match.group(1).replace(",", "."))
        lots = suggest_exchange_lot(collection, target)
        if not lots:
            return "Je n'ai pas assez de cartes en vente avec un prix pour composer un lot."
        return "Lots indicatifs, calculés avec tes prix de vente affichés :\n" + "\n".join(
            f"- {', '.join(lot['cards'])} : {lot['value']:.2f} € (écart {lot['difference']:.2f} €)"
            for lot in lots
        )

    if any(word in text for word in ("roi", "rentabil", "bénéfice", "benefice", "profit")):
        return (
            f"Sur les cartes vendues enregistrées : bénéfice net estimé {summary['realized_profit']:.2f} €, "
            f"ROI réalisé {summary['realized_roi_pct']:.1f} % sur le coût d'achat. "
            "Le calcul applique une commission estimée de 5 % et exclut les frais d'envoi."
        )

    if any(word in text for word in ("stock", "collection", "possède", "possede", "possédé", "possedees")):
        cards = [card for card in collection if card.get("status") == "FOR_SALE"]
        if not cards:
            return "Aucune carte en vente n'est enregistrée dans ta collection."
        return (
            f"Tu as {summary['for_sale_count']} référence(s) en vente, pour une valeur affichée de "
            f"{summary['stock_value']:.2f} € et {summary['invested_capital']:.2f} € de coût d'achat. "
            f"Cartes : {', '.join(card.get('name', 'Carte') for card in cards[:12])}."
        )

    return "Je peux analyser ton stock, ton ROI réalisé, ta watchlist ou composer un lot à partir de tes prix affichés. Je n'utilise que les données locales de l'application."