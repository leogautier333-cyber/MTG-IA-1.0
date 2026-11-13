"""Filtrage et tri de l'inventaire sans modification des fiches."""

SORT_OPTIONS = (
    "Ordre d'ajout",
    "Prix de vente : décroissant",
    "Prix de vente : croissant",
    "Nom : A à Z",
    "Nom : Z à A",
    "Quantité : décroissante",
)


def filter_stock(collection, status="FOR_SALE", query="", set_code="", language="",
                 condition="", foil="", min_price=0.0, max_price=None,
                 sort_by="Ordre d'ajout"):
    """Retourne les indices d'origine et les fiches après filtrage et tri stable."""
    if max_price is not None and max_price < min_price:
        raise ValueError("Le prix maximum doit être supérieur ou égal au prix minimum.")
    if sort_by not in SORT_OPTIONS:
        raise ValueError("Tri du stock inconnu.")
    query = query.strip().casefold()
    result = []
    for index, card in enumerate(collection):
        if card.get("status", "FOR_SALE") != status:
            continue
        if query and not any(
            query in str(card.get(field) or "").casefold()
            for field in ("name", "set_code", "collector_number")
        ):
            continue
        if set_code and str(card.get("set_code") or "").casefold() != set_code.casefold():
            continue
        if language and str(card.get("language") or "FR") != language:
            continue
        if condition and str(card.get("condition") or "NM") != condition:
            continue
        if foil == "Foil" and not card.get("foil"):
            continue
        if foil == "Non foil" and card.get("foil"):
            continue
        price = float(card.get("selling_price") or 0)
        if price < min_price or (max_price is not None and price > max_price):
            continue
        result.append((index, card))

    if sort_by.startswith("Prix de vente"):
        result.sort(key=lambda item: float(item[1].get("selling_price") or 0),
                    reverse=sort_by.endswith("décroissant"))
    elif sort_by.startswith("Nom"):
        result.sort(key=lambda item: str(item[1].get("name") or "").casefold(),
                    reverse=sort_by.endswith("Z à A"))
    elif sort_by == "Quantité : décroissante":
        result.sort(key=lambda item: int(item[1].get("quantity") or 1), reverse=True)
    return result
