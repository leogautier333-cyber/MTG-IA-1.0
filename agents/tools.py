import json

import data_manager
from database import storage
from services.market_signals import calculate_reprint_risk
from services.portfolio_analytics import simulate_sale
from services.portfolio_analytics import analyze_portfolio


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_collection",
            "description": "Query the locally stored inventory and portfolio summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "include_sold": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Read stored daily price snapshots for one Scryfall printing and variant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scryfall_id": {"type": "string"},
                    "foil": {"type": "boolean", "default": False},
                    "language": {"type": "string", "default": "FR"},
                    "condition": {"type": "string", "default": "NM"},
                },
                "required": ["scryfall_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_sale",
            "description": "Calculate net sale proceeds after Cardmarket fees, shipping, and condition discount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scryfall_id": {"type": "string"},
                    "sale_price": {"type": "number"},
                    "shipping": {"type": "number", "default": 0},
                    "cm_fee_pct": {"type": "number", "default": 0.05},
                },
                "required": ["scryfall_id", "sale_price"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_reprint_risk",
            "description": "Calculate evidence-based reprint risk from Scryfall bulk data and stored outcomes.",
            "parameters": {
                "type": "object",
                "properties": {"scryfall_id": {"type": "string"}},
                "required": ["scryfall_id"],
                "additionalProperties": False,
            },
        },
    },
]


def query_collection(query="", include_sold=False):
    full_collection = data_manager.get_collection()
    normalized_query = str(query).casefold().strip()
    cards = [
        card for card in full_collection
        if (include_sold or card.get("status") == "FOR_SALE")
        and (not normalized_query or normalized_query in str(card.get("name", "")).casefold())
    ]
    return {
        "count": len(cards),
        "portfolio": analyze_portfolio(full_collection),
        "cards": [{
            key: card.get(key) for key in (
                "scryfall_id", "name", "set_code", "quantity", "foil", "language",
                "condition", "status", "purchase_price", "selling_price", "actual_sale_price",
            )
        } for card in cards],
    }


def get_price_history(scryfall_id, foil=False, language="FR", condition="NM"):
    return {
        "scryfall_id": scryfall_id,
        "variant": {"foil": foil, "language": language, "condition": condition},
        "snapshots": storage.get_variant_price_history(scryfall_id, foil, language, condition),
    }


def tool_simulate_sale(scryfall_id, sale_price, shipping=0, cm_fee_pct=0.05):
    card = next(
        (item for item in data_manager.get_collection() if item.get("scryfall_id") == scryfall_id),
        None,
    )
    if not card:
        return {"error": "Carte absente de la collection."}
    return {"scryfall_id": scryfall_id, **simulate_sale(card, sale_price, shipping, cm_fee_pct)}


TOOL_FUNCTIONS = {
    "query_collection": query_collection,
    "get_price_history": get_price_history,
    "simulate_sale": tool_simulate_sale,
    "calculate_reprint_risk": calculate_reprint_risk,
}


def execute_tool(name, arguments):
    function = TOOL_FUNCTIONS.get(name)
    if not function:
        raise ValueError(f"Outil non autorisé : {name}")
    if not isinstance(arguments, dict):
        raise ValueError("Les arguments d'outil doivent être un objet JSON.")
    return function(**arguments)


def serialize_tool_result(result):
    return json.dumps(result, ensure_ascii=False, default=str)