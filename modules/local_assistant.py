import re
import os
import requests

from agents.tools import execute_tool
from agents.tool_agent import ToolCallingAgent
from modules.market_analyzer import suggest_exchange_lot


def answer_local_query(query, collection, watchlist):
    if os.getenv("OPENAI_API_KEY"):
        try:
            return ToolCallingAgent().answer(query)
        except (requests.RequestException, RuntimeError, ValueError):
            pass

    text = query.casefold()
    collection_result = execute_tool("query_collection", {"query": "", "include_sold": True})
    summary = collection_result["portfolio"]

    if any(word in text for word in ("réimpression", "reprint")):
        card = next((card for card in collection if card.get("name", "").casefold() in text), None)
        if not card:
            return "Indique le nom d'une carte de ta collection pour consulter son risque de réimpression."
        risk = execute_tool("calculate_reprint_risk", {"scryfall_id": card["scryfall_id"]})
        if risk.get("score") is None:
            return risk["reason"]
        historical = risk.get("expected_change_30d_pct")
        estimate = f" Impact historique à 30 j : {historical:.1f} %." if historical is not None else " Aucun échantillon historique à 30/90 j."
        return f"Risque {risk['status']} ({risk['score']}/100), confiance {risk['confidence']:.0%}.{estimate} {risk['reason']}"

    if "historique" in text or "évolution" in text:
        card = next((card for card in collection if card.get("name", "").casefold() in text), None)
        if card:
            history = execute_tool("get_price_history", {
                "scryfall_id": card["scryfall_id"], "foil": card.get("foil", False),
                "language": card.get("language", "FR"), "condition": card.get("condition", "NM"),
            })
            if history["snapshots"]:
                latest = history["snapshots"][-1]
                return f"Dernier snapshot {latest['observed_at']} : {latest['price_eur']:.2f} €."
            return "Aucun snapshot de prix enregistré pour cette variante."

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