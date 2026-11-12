"""Lecture et préparation d'un export complet de stock Cardmarket."""
import csv
import io
import math
import re
import time

import requests

import scryfall

REQUIRED_COLUMNS = {
    "ArticleID", "idProduct", "Name", "SetCode", "Language", "Condition",
    "Foil", "Price_EUR", "Amount",
}


def is_cardmarket_export(text):
    """Reconnaît l'export Cardmarket par ses identifiants, même s'il est incomplet."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff\r\n")), delimiter=";")
    headers = next(reader, [])
    return {"articleid", "idproduct"}.issubset({header.strip().casefold() for header in headers})


def _identifier(value):
    value = value.strip()
    match = re.fullmatch(r'="?(\d+)"?', value)
    if not match:
        raise ValueError("identifiant numérique invalide")
    return match.group(1)


def parse(text):
    """Lit un export Cardmarket intégral; aucune ligne invalide n'est ignorée."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff\r\n")), delimiter=";")
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise ValueError("Colonnes Cardmarket manquantes : " + ", ".join(sorted(missing)))
    rows = []
    seen = set()
    for raw in reader:
        line = reader.line_num
        try:
            if None in raw or any(raw.get(field) is None for field in REQUIRED_COLUMNS):
                raise ValueError("colonnes incomplètes ou supplémentaires")
            article_id = _identifier(raw["ArticleID"])
            product_id = _identifier(raw["idProduct"])
            if article_id in seen:
                raise ValueError("ArticleID en double")
            seen.add(article_id)
            quantity = int(raw["Amount"])
            price = float(raw["Price_EUR"].strip().replace(",", "."))
            if quantity <= 0 or not math.isfinite(price) or price < 0:
                raise ValueError("quantité ou prix invalide")
            foil = raw["Foil"].strip().upper()
            if foil not in {"Y", "N"}:
                raise ValueError("valeur Foil inconnue")
            if raw.get("delete", "N").strip().upper() != "N":
                raise ValueError("ligne marquée pour suppression ou valeur delete inconnue")
            language = raw["Language"].strip()
            language_code = {
                "français": "FR", "francais": "FR", "anglais": "EN",
                "allemand": "DE", "espagnol": "ES", "italien": "IT",
                "japonais": "JA", "french": "FR", "english": "EN",
                "german": "DE", "spanish": "ES", "italian": "IT",
                "japanese": "JA",
            }.get(language.casefold())
            if not language_code:
                raise ValueError(f"langue inconnue : {language}")
            if not raw["Name"].strip() or not raw["Condition"].strip():
                raise ValueError("nom ou état manquant")
        except (ValueError, TypeError) as error:
            raise ValueError(f"Ligne {line} : {error}.") from error
        rows.append({
            "article_id": article_id, "product_id": product_id,
            "name": raw["Name"].strip(), "set_code": raw["SetCode"].strip(),
            "collector_number": (raw.get("CollectorNumber") or "").strip(),
            "language": language_code, "condition": raw["Condition"].strip().upper(),
            "foil": foil == "Y", "quantity": quantity, "selling_price": price,
        })
    if not rows:
        raise ValueError("Le fichier ne contient aucune carte : remplacement refusé.")
    return rows


def resolve(rows, session=requests):
    """Identifie les produits exacts, en signalant les produits absents de Scryfall."""
    products = {}
    errors = []
    for product_id in dict.fromkeys(row["product_id"] for row in rows):
        time.sleep(0.1)
        try:
            response = session.get(
                f"{scryfall.BASE_URL}/cards/cardmarket/{product_id}",
                headers=scryfall.HEADERS, timeout=15,
            )
        except requests.RequestException as error:
            raise RuntimeError(f"Scryfall inaccessible ({type(error).__name__}) ; stock inchangé.") from None
        if response.status_code == 404:
            products[product_id] = None
            continue
        if response.status_code != 200:
            raise RuntimeError(
                f"Scryfall a répondu {response.status_code} pour le produit {product_id} ; stock inchangé."
            )
        card = response.json()
        if not card.get("id") or str(card.get("cardmarket_id")) != product_id:
            errors.append(f"Réponse Scryfall invalide pour le produit {product_id}.")
            continue
        products[product_id] = card
    if errors:
        return [], errors
    cards = []
    for row in rows:
        card = products[row["product_id"]]
        if card is None:
            cards.append({
                "scryfall_id": "", "scryfall_resolution_pending": True,
                "name": row["name"], "set_code": row["set_code"].lower(),
                "collector_number": row["collector_number"],
                "language": row["language"], "condition": row["condition"], "foil": row["foil"],
                "quantity": row["quantity"], "selling_price": row["selling_price"],
                "cardmarket_article_id": row["article_id"], "cardmarket_product_id": row["product_id"],
                "image_url": "", "mana_cost": "", "type_line": "", "oracle_text": "",
                "legalities": {},
            })
            continue
        image = (card.get("image_uris") or {}).get("normal")
        if not image:
            image = next(
                ((face.get("image_uris") or {}).get("normal")
                 for face in card.get("card_faces", []) if (face.get("image_uris") or {}).get("normal")),
                "",
            )
        cards.append({
            "scryfall_id": card["id"], "scryfall_resolution_pending": False,
            "name": card.get("name") or row["name"],
            "set_code": card.get("set", ""), "collector_number": card.get("collector_number", ""),
            "language": row["language"], "condition": row["condition"], "foil": row["foil"],
            "quantity": row["quantity"], "selling_price": row["selling_price"],
            "cardmarket_article_id": row["article_id"], "cardmarket_product_id": row["product_id"],
            "image_url": image, "mana_cost": card.get("mana_cost", ""),
            "type_line": card.get("type_line", ""), "oracle_text": card.get("oracle_text", ""),
            "legalities": card.get("legalities", {}),
        })
    return cards, []
