"""Import CSV du stock (export Cardmarket, tableur…) : lecture tolérante puis résolution via Scryfall."""
import csv
import io

import scryfall

ALIASES = {
    "name": ("name", "nom", "card", "carte", "card name", "nom de la carte"),
    "set_code": ("set", "set_code", "edition", "édition", "extension", "set code"),
    "collector_number": ("collector_number", "collector number", "numero", "numéro", "number", "n°"),
    "quantity": ("quantity", "qty", "quantité", "quantite", "count", "amount"),
    "purchase_price": ("purchase_price", "prix_achat", "prix d'achat", "prix achat", "cost", "buy price"),
    "selling_price": ("selling_price", "prix_vente", "prix de vente", "prix vente", "price", "sell price"),
    "condition": ("condition", "état", "etat"),
    "language": ("language", "langue", "lang"),
    "foil": ("foil", "is foil"),
}
LANGUAGE_CODES = {
    "french": "FR", "français": "FR", "francais": "FR", "english": "EN", "anglais": "EN",
    "german": "DE", "allemand": "DE", "spanish": "ES", "espagnol": "ES", "italian": "IT",
    "italien": "IT", "japanese": "JA", "japonais": "JA",
}
TRUE_VALUES = {"1", "true", "yes", "oui", "x", "foil", "vrai"}


def _number(value, default=0.0):
    text = str(value or "").strip().replace("€", "").replace(" ", "").replace(",", ".")
    try:
        return float(text) if text else default
    except ValueError:
        return None


def parse_csv(text):
    """Retourne (lignes normalisées, erreurs). Séparateur `;` ou `,` détecté automatiquement."""
    text = text.lstrip("\ufeff")
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = {(h or "").strip().casefold(): h for h in (reader.fieldnames or [])}
    column = {
        field: next((headers[a] for a in aliases if a in headers), None)
        for field, aliases in ALIASES.items()
    }
    if not column["name"] and not (column["set_code"] and column["collector_number"]):
        return [], ["Colonnes introuvables : il faut au minimum « nom » (ou « édition » + « numéro »)."]

    rows, errors = [], []
    for line_number, raw in enumerate(reader, start=2):
        def get(field):
            return (raw.get(column[field]) or "").strip() if column[field] else ""

        quantity = _number(get("quantity"), 1)
        purchase = _number(get("purchase_price"))
        selling = _number(get("selling_price"))
        if None in (quantity, purchase, selling):
            errors.append(f"Ligne {line_number} ignorée : nombre invalide.")
            continue
        if not (get("name") or (get("set_code") and get("collector_number"))):
            continue
        language = get("language") or "FR"
        rows.append({
            "line": line_number,
            "name": get("name"),
            "set_code": get("set_code").casefold(),
            "collector_number": get("collector_number"),
            "quantity": max(1, int(quantity)),
            "purchase_price": purchase,
            "selling_price": selling,
            "condition": get("condition") or "NM",
            "language": LANGUAGE_CODES.get(language.casefold(), language.upper()[:2]),
            "foil": get("foil").casefold() in TRUE_VALUES,
        })
    return rows, errors


def _identifier(row):
    if row["set_code"] and row["collector_number"]:
        return {"set": row["set_code"], "collector_number": row["collector_number"]}
    if row["name"] and row["set_code"]:
        return {"name": row["name"], "set": row["set_code"]}
    return {"name": row["name"]}


def _matches(card, row):
    if row["set_code"] and row["collector_number"]:
        return (str(card.get("set", "")).casefold() == row["set_code"]
                and str(card.get("collector_number", "")) == row["collector_number"])
    names = {part.strip().casefold() for part in str(card.get("name", "")).split("//")}
    names.add(str(card.get("name", "")).casefold())
    name_ok = row["name"].casefold() in names
    return name_ok and (not row["set_code"] or str(card.get("set", "")).casefold() == row["set_code"])


def resolve(rows, fetch=scryfall.get_cards_by_identifiers):
    """Associe chaque ligne à une carte Scryfall. Retourne (cartes prêtes à ajouter, erreurs)."""
    if not rows:
        return [], []
    result = fetch([_identifier(row) for row in rows])
    found = result.get("found", [])
    cards, errors = [], []
    for row in rows:
        card = next((c for c in found if _matches(c, row)), None)
        if not card:
            errors.append(f"Ligne {row['line']} : « {row['name'] or row['set_code']} » introuvable sur Scryfall.")
            continue
        image = (card.get("image_uris") or {}).get("normal") or next(
            ((face.get("image_uris") or {}).get("normal") for face in card.get("card_faces", [])
             if face.get("image_uris")), "")
        cards.append({
            "scryfall_id": card["id"], "name": card.get("name", row["name"]),
            "set_code": card.get("set", row["set_code"]),
            "collector_number": card.get("collector_number", row["collector_number"]),
            "language": row["language"], "condition": row["condition"], "foil": row["foil"],
            "quantity": row["quantity"], "purchase_price": row["purchase_price"],
            "selling_price": row["selling_price"], "image_url": image,
            "mana_cost": card.get("mana_cost", ""), "type_line": card.get("type_line", ""),
            "oracle_text": card.get("oracle_text", ""), "legalities": card.get("legalities", {}),
        })
    return cards, errors
