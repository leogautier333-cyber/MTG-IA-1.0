from datetime import date, timedelta
from html.parser import HTMLParser
import re
import time
import xml.etree.ElementTree as ET

import requests


SCRYFALL_API = "https://api.scryfall.com"
REDDIT_FEED = "https://www.reddit.com/r/mtgfinance/.rss"
HEADERS = {"User-Agent": "MagicTradingProfitManager/2.0", "Accept": "application/json"}
REDDIT_HEADERS = {"User-Agent": "MagicTradingProfitManager/2.0 (public RSS monitor)"}
REPRINT_SET_TYPES = {"expansion", "commander", "masters", "draft_innovation", "core", "promo"}
HYPE_TERMS = (
    "spike", "spiking", "surge", "surging", "hype", "buyout", "buy out",
    "price jump", "price spike", "supply drying", "sell out", "demand is",
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value):
    parser = _TextExtractor()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def _element_text(element, names):
    for child in element.iter():
        if child is element:
            continue
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def _element_link(element):
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == "link":
            if child.attrib.get("href"):
                return child.attrib["href"]
            if child.text:
                return child.text.strip()
    return ""


def _get_json(session, url, params=None):
    time.sleep(0.1)
    response = session.get(url, params=params, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def _fetch_tracked_oracle_ids(session, tracked_cards):
    identifiers = [
        {"id": card["scryfall_id"]}
        for card in tracked_cards
        if card.get("scryfall_id") and not card.get("oracle_id")
    ]
    oracle_by_id = {
        card["scryfall_id"]: card["oracle_id"]
        for card in tracked_cards
        if card.get("scryfall_id") and card.get("oracle_id")
    }
    for start in range(0, len(identifiers), 75):
        time.sleep(0.1)
        response = session.post(
            f"{SCRYFALL_API}/cards/collection",
            json={"identifiers": identifiers[start:start + 75]},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        for card in response.json().get("data", []):
            if card.get("id") and card.get("oracle_id"):
                oracle_by_id[card["id"]] = card["oracle_id"]
    return oracle_by_id


def fetch_scryfall_spoilers(session=requests, max_sets=6, max_pages_per_set=4, today=None):
    """Fetch previewed cards from the nearest upcoming Scryfall sets."""
    today = today or date.today()
    sets_payload = _get_json(session, f"{SCRYFALL_API}/sets")
    upcoming_sets = [
        item for item in sets_payload.get("data", [])
        if item.get("set_type") in REPRINT_SET_TYPES
        and item.get("released_at")
        and today < date.fromisoformat(item["released_at"]) <= today + timedelta(days=365)
    ]
    upcoming_sets.sort(key=lambda item: item["released_at"])

    spoilers = []
    for set_info in upcoming_sets[:max_sets]:
        page = 1
        while page <= max_pages_per_set:
            try:
                payload = _get_json(
                    session,
                    f"{SCRYFALL_API}/cards/search",
                    params={"q": f"set:{set_info['code']}", "unique": "prints", "page": page},
                )
            except requests.HTTPError as error:
                if error.response is not None and error.response.status_code == 404:
                    break
                raise
            for card in payload.get("data", []):
                preview = card.get("preview")
                if not preview:
                    continue
                spoilers.append({
                    "id": card.get("id"),
                    "name": card.get("name", "Carte inconnue"),
                    "oracle_id": card.get("oracle_id"),
                    "set_code": card.get("set"),
                    "set_name": card.get("set_name", set_info.get("name", "Extension à venir")),
                    "released_at": card.get("released_at", set_info.get("released_at")),
                    "preview": preview,
                    "scryfall_uri": card.get("scryfall_uri", ""),
                })
            if not payload.get("has_more") or not payload.get("data"):
                break
            page += 1
    return spoilers


def fetch_reddit_posts(session=requests, limit=50):
    response = session.get(REDDIT_FEED, headers=REDDIT_HEADERS, timeout=15)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    posts = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1].lower() not in {"entry", "item"}:
            continue
        title = _element_text(entry, {"title"})
        content = _element_text(entry, {"content", "summary", "description"})
        posts.append({
            "title": _plain_text(title),
            "text": _plain_text(content),
            "url": _element_link(entry),
        })
        if len(posts) >= limit:
            break
    return posts


def _name_aliases(name):
    return [part.strip().casefold() for part in str(name or "").split(" // ") if part.strip()]


def _contains_name(text, name):
    return any(
        re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text.casefold())
        for alias in _name_aliases(name)
        if len(alias) >= 3
    )


def analyze_spoilers_and_trends(spoilers, reddit_posts, watchlist, collection, oracle_by_id=None):
    oracle_by_id = oracle_by_id or {}
    tracked = []
    for source, cards in (("Watchlist", watchlist), ("Collection", collection)):
        for card in cards:
            tracked.append({
                "name": card.get("name", "Carte inconnue"),
                "source": source,
                "status": card.get("status", "WATCHED"),
                "scryfall_id": card.get("scryfall_id"),
                "oracle_id": card.get("oracle_id") or oracle_by_id.get(card.get("scryfall_id")),
                "set_code": str(card.get("set_code", "")).casefold(),
            })

    alerts = []
    for spoiler in spoilers:
        for card in tracked:
            same_oracle = bool(card.get("oracle_id") and card["oracle_id"] == spoiler.get("oracle_id"))
            same_name = _contains_name(spoiler.get("name"), card["name"])
            different_printing = (
                bool(card.get("set_code") and spoiler.get("set_code"))
                and card["set_code"] != str(spoiler["set_code"]).casefold()
            )
            if not ((same_oracle or same_name) and different_printing):
                continue
            scope = "ta Watchlist" if card["source"] == "Watchlist" else "ta collection"
            alerts.append({
                "id": f"spoiler:{spoiler.get('id') or spoiler.get('set_code')}:{card.get('scryfall_id') or card['name']}",
                "category": "SPOILER_REPRINT",
                "card_name": card["name"],
                "scope": scope,
                "title": spoiler.get("name", "Réimpression détectée"),
                "set_name": spoiler.get("set_name", "Extension à venir"),
                "released_at": spoiler.get("released_at", "date inconnue"),
                "source_url": spoiler.get("preview", {}).get("source_uri") or spoiler.get("scryfall_uri", ""),
                "message": (
                    f"[SPOILER / RÉIMPRESSION] {card['name']} de {scope} apparaît dans "
                    f"{spoiler.get('set_name', 'une extension à venir')} "
                    f"(sortie prévue le {spoiler.get('released_at', 'date inconnue')}). "
                    "Une pression baissière sur le prix est possible."
                ),
            })

    mention_counts = {}
    post_matches = {}
    hype_post_count = 0
    for post in reddit_posts:
        text = f"{post.get('title', '')} {post.get('text', '')}".casefold()
        if not any(term in text for term in HYPE_TERMS):
            continue
        matched_this_post = set()
        for card in tracked:
            if _contains_name(text, card["name"]):
                key = card.get("scryfall_id") or f"{card['source']}:{card['name']}"
                matched_this_post.add((key, card["name"], card["source"]))
                post_matches.setdefault(key, []).append(post)
        if matched_this_post:
            hype_post_count += 1
            for key, name, source in matched_this_post:
                mention_counts[(key, name, source)] = mention_counts.get((key, name, source), 0) + 1

    for (key, name, source), count in mention_counts.items():
        posts = post_matches[key]
        scope = "ta Watchlist" if source == "Watchlist" else "ta collection"
        first_post = posts[0]
        alerts.append({
            "id": f"reddit:{first_post.get('url') or key}:{key}",
            "category": "REDDIT_HYPE",
            "card_name": name,
            "scope": scope,
            "title": first_post.get("title", "Discussion r/mtgfinance"),
            "count": count,
            "source_url": first_post.get("url", ""),
            "message": (
                f"[OPPORTUNITÉ / HYPE À SURVEILLER] Discussion spéculative autour de {name} "
                f"({count} mention(s) pertinente(s) parmi les derniers posts r/mtgfinance); "
                f"carte présente dans {scope}. Signal communautaire, pas une prévision de prix."
            ),
        })

    temperature = "Élevée" if hype_post_count >= 5 else "Modérée" if hype_post_count else "Calme"
    return {
        "alerts": alerts,
        "spoiler_count": len(spoilers),
        "reddit_posts_scanned": len(reddit_posts),
        "reddit_hype_posts": hype_post_count,
        "community_temperature": temperature,
    }


def scan_spoilers_and_trends(watchlist, collection, session=requests, max_sets=6, reddit_limit=50):
    errors = []
    spoilers = []
    reddit_posts = []
    oracle_by_id = {}
    tracked_cards = [*watchlist, *collection]

    try:
        oracle_by_id = _fetch_tracked_oracle_ids(session, tracked_cards)
    except (requests.RequestException, ValueError) as error:
        errors.append(f"Scryfall (identifiants des cartes suivies): {error}")
    try:
        spoilers = fetch_scryfall_spoilers(session=session, max_sets=max_sets)
    except (requests.RequestException, ValueError, KeyError) as error:
        errors.append(f"Scryfall (spoilers): {error}")
    try:
        reddit_posts = fetch_reddit_posts(session=session, limit=reddit_limit)
    except (requests.RequestException, ET.ParseError) as error:
        errors.append(f"Reddit r/mtgfinance: {error}")

    result = analyze_spoilers_and_trends(spoilers, reddit_posts, watchlist, collection, oracle_by_id)
    result["errors"] = errors
    return result