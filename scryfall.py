import time
import requests
from typing import List, Dict, Optional, Any

BASE_URL = "https://api.scryfall.com"
HEADERS = {
    "User-Agent": "MTGTradingManager/2.0",
    "Accept": "application/json"
}


def _rate_limit():
    """Pause de 100 ms entre chaque requête pour respecter l'API Scryfall."""
    time.sleep(0.1)


def search_card_autocomplete(query: str) -> List[str]:
    """
    Retourne des suggestions de noms de cartes (FR ou EN) pour l'autocomplétion.
    """
    if not query or len(query) < 2:
        return []

    _rate_limit()
    url = f"{BASE_URL}/cards/autocomplete"
    response = requests.get(url, params={"q": query}, headers=HEADERS)

    if response.status_code == 200:
        return response.json().get("data", [])
    return []


def get_card_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Recherche une carte par son nom (recherche approximative / fuzzy).
    """
    _rate_limit()
    url = f"{BASE_URL}/cards/named"
    response = requests.get(url, params={"fuzzy": name}, headers=HEADERS)

    if response.status_code == 200:
        return response.json()
    return None


def get_card_prints(oracle_id: str) -> List[Dict[str, Any]]:
    """
    Récupère toutes les éditions/impressions d'une carte à partir de son oracle_id.
    """
    _rate_limit()
    url = f"{BASE_URL}/cards/search"
    params = {"q": f"oracle_id:{oracle_id}", "unique": "prints"}
    response = requests.get(url, params=params, headers=HEADERS)

    if response.status_code == 200:
        return response.json().get("data", [])
    return []


def get_cards_batch(scryfall_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Récupère jusqu'à 75 cartes en une seule requête POST.
    Découpe automatiquement si la liste dépasse 75 cartes.
    """
    if not scryfall_ids:
        return []

    results = []
    chunk_size = 75

    for i in range(0, len(scryfall_ids), chunk_size):
        chunk = scryfall_ids[i:i + chunk_size]
        identifiers = [{"id": card_id} for card_id in chunk]

        _rate_limit()
        url = f"{BASE_URL}/cards/collection"
        response = requests.post(
            url, json={"identifiers": identifiers}, headers=HEADERS
        )

        if response.status_code == 200:
            data = response.json()
            results.extend(data.get("data", []))

    return results