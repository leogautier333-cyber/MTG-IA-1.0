import re
import unicodedata
from urllib.parse import urlparse

import requests

import scryfall


def parse_plain_decklist(text):
    cards = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "//")) or line.casefold() in {"commander", "sideboard", "mainboard"}:
            continue
        match = re.match(r"^\s*(\d+)\s*x?\s+(.+?)\s*$", line, flags=re.IGNORECASE)
        if not match:
            continue
        quantity = int(match.group(1))
        name = re.sub(r"\s+\([A-Za-z0-9]{2,6}\)\s+\S+\s*$", "", match.group(2)).strip()
        if quantity > 0 and name:
            cards.append({"name": name, "quantity": quantity})
    return cards


def fetch_decklist(source, session=requests):
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        return parse_plain_decklist(source)

    match = re.search(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", parsed.netloc + parsed.path)
    if match:
        response = session.get(
            f"https://api2.moxfield.com/v3/decks/all/{match.group(1)}",
            headers={"User-Agent": "MagicTradingProfitManager/3.0", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        mainboard = payload.get("boards", {}).get("mainboard", {}).get("cards", {})
        cards = [
            {
                "name": entry.get("card", {}).get("name", name),
                "quantity": int(entry.get("quantity", 1)),
            }
            for name, entry in mainboard.items()
        ]
        return cards

    match = re.search(r"archidekt\.com/(?:decks|api/decks)/([0-9]+)", parsed.netloc + parsed.path)
    if match:
        response = session.get(
            f"https://archidekt.com/api/decks/{match.group(1)}/",
            headers={"User-Agent": "MagicTradingProfitManager/3.0", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            {
                "name": entry.get("card", {}).get("oracleCard", {}).get("name")
                    or entry.get("card", {}).get("name", ""),
                "quantity": int(entry.get("quantity", 1)),
            }
            for entry in payload.get("cards", [])
            if entry.get("card")
        ]

    raise ValueError("URL non prise en charge. Utilise Moxfield, Archidekt ou une liste texte.")


def _normalize_name(name):
    normalized = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.casefold().split())


def analyze_decklist(cards, collection, fetch_prices=True):
    owned = {}
    for card in collection:
        if card.get("status") != "FOR_SALE":
            continue
        key = _normalize_name(card.get("name", ""))
        owned[key] = owned.get(key, 0) + int(card.get("quantity") or 1)

    inventory = dict(owned)
    missing = []
    owned_cards = []
    for card in cards:
        name = card["name"]
        quantity = max(1, int(card.get("quantity", 1)))
        key = _normalize_name(name)
        available = inventory.get(key, 0)
        in_stock = min(quantity, available)
        inventory[key] = available - in_stock
        owned_cards.append({"name": name, "required": quantity, "owned": in_stock})
        if in_stock < quantity:
            missing.append({"name": name, "quantity": quantity - in_stock, "price_eur": None})

    if fetch_prices:
        for card in missing:
            try:
                card_data = scryfall.get_card_by_name(card["name"])
            except requests.RequestException:
                card_data = None
            if card_data:
                price = (card_data.get("prices") or {}).get("eur")
                card["price_eur"] = float(price) if price is not None else None

    estimated_cost = sum(
        (card["price_eur"] or 0) * card["quantity"] for card in missing
    )
    deck_names = {_normalize_name(card["name"]) for card in cards}
    excluded_from_deck = [
        card for card in collection
        if card.get("status") == "FOR_SALE" and _normalize_name(card.get("name", "")) not in deck_names
    ]
    return {
        "required_count": sum(int(card.get("quantity", 1)) for card in cards),
        "owned_count": sum(item["owned"] for item in owned_cards),
        "missing": missing,
        "owned_cards": owned_cards,
        "estimated_acquisition_cost_eur": estimated_cost,
        "excluded_from_deck": excluded_from_deck,
        "play_history_available": False,
    }