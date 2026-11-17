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


RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 10.0


def _retry_delay(response, attempt):
    try:
        delay = float(response.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        delay = 0.5 * 2 ** (attempt - 1)
    return min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)


def _send(method, url, **kwargs):
    """Envoie une requête Scryfall avec une courte reprise sur 429, 5xx et erreurs réseau.

    La réponse finale est renvoyée telle quelle ; l'appelant décide quoi faire d'un 404 ou
    appelle `raise_for_status()` : une panne durable n'est jamais confondue avec « introuvable »."""
    send = getattr(requests, method)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _rate_limit()
        try:
            response = send(url, headers=HEADERS, **kwargs)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(0.5 * 2 ** (attempt - 1))
            continue
        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS:
            time.sleep(_retry_delay(response, attempt))
            continue
        return response


def search_card_autocomplete(query: str) -> List[str]:
    """
    Retourne des suggestions de noms de cartes (FR ou EN) pour l'autocomplétion.
    """
    if not query or len(query) < 2:
        return []

    url = f"{BASE_URL}/cards/autocomplete"
    response = _send("get", url, params={"q": query}, timeout=10)

    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json().get("data", [])


def get_card_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Recherche une carte par son nom (recherche approximative / fuzzy).
    """
    url = f"{BASE_URL}/cards/named"
    response = _send("get", url, params={"fuzzy": name}, timeout=10)

    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def get_card_prints(oracle_id: str) -> List[Dict[str, Any]]:
    """
    Récupère toutes les éditions/impressions d'une carte à partir de son oracle_id.
    """
    url = f"{BASE_URL}/cards/search"
    params = {"q": f"oracle_id:{oracle_id}", "unique": "prints"}
    response = _send("get", url, params=params, timeout=10)

    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json().get("data", [])


def get_cards_by_identifiers(identifiers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Résout des identifiants Scryfall (id, set+numéro, nom) par lots de 75.

    Retourne {"found": [cartes], "not_found": [identifiants introuvables]}.
    """
    found, not_found = [], []
    for i in range(0, len(identifiers), 75):
        chunk = identifiers[i:i + 75]
        response = _send(
            "post", f"{BASE_URL}/cards/collection", json={"identifiers": chunk}, timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        found.extend(data.get("data", []))
        not_found.extend(data.get("not_found", []))
    return {"found": found, "not_found": not_found}


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

        url = f"{BASE_URL}/cards/collection"
        response = _send("post", url, json={"identifiers": identifiers}, timeout=20)

        response.raise_for_status()
        data = response.json()
        results.extend(data.get("data", []))

    return results