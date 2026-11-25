"""Veille communautaire légale : flux publics MTGGoldfish et chaînes YouTube configurées.

Seuls titre, résumé court, lien et date sont lus. Aucun texte intégral n'est conservé, aucune IA n'est
utilisée, et un flux en échec n'interrompt jamais l'analyse (état enregistré dans `source_health`).
Les mentions sont un simple indice de contexte, jamais une alerte ni une prévision de prix.
"""
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import requests

from database import storage

USER_AGENT = "CarnetDeMana/1.0 (personal RSS reader)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9"}
MTGGOLDFISH_FEED = "https://www.mtggoldfish.com/feed"
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_CHANNELS_VARIABLE = "MTG_YOUTUBE_CHANNEL_IDS"
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
TIMEOUT_SECONDS = 15
MAX_FEED_BYTES = 2_000_000
MAX_ENTRIES_PER_FEED = 30
MAX_AGE_DAYS = 14
MIN_NAME_LENGTH = 4
SUMMARY_LIMIT = 300


class FeedError(RuntimeError):
    """Flux illisible ou indisponible ; le message ne contient jamais d'URL sensible."""


def youtube_channel_ids(raw=None):
    """Identifiants de chaînes valides et dédoublonnés ; les valeurs invalides sont ignorées."""
    raw = os.getenv(YOUTUBE_CHANNELS_VARIABLE, "") if raw is None else raw
    return list(dict.fromkeys(
        item.strip() for item in raw.replace(";", ",").split(",") if CHANNEL_ID.match(item.strip())
    ))


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _plain_text(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def _child_text(entry, names):
    for child in entry.iter():
        if child is not entry and _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _entry_link(entry):
    for child in entry.iter():
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href") or (child.text or "").strip()
        if href and child.attrib.get("rel", "alternate") == "alternate":
            return href
    return ""


def _published(entry):
    raw = _child_text(entry, {"published", "updated", "pubdate", "date"})
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_feed(content, now=None, max_age_days=MAX_AGE_DAYS):
    """Entrées récentes d'un flux Atom ou RSS : titre, résumé court, lien et date."""
    if len(content) > MAX_FEED_BYTES:
        raise FeedError("Flux trop volumineux.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise FeedError("Flux XML invalide.") from None
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
    entries = []
    for entry in root.iter():
        if _local(entry.tag) not in {"entry", "item"}:
            continue
        published = _published(entry)
        if published is not None and published < cutoff:
            continue
        title = _plain_text(_child_text(entry, {"title"}))
        if not title:
            continue
        summary = _plain_text(_child_text(entry, {"summary", "description", "content"}))[:SUMMARY_LIMIT]
        entries.append({
            "title": title, "summary": summary, "url": _entry_link(entry),
            "published": published.isoformat(timespec="seconds") if published else None,
        })
        if len(entries) >= MAX_ENTRIES_PER_FEED:
            break
    return entries


def fetch_feed(url, session=requests):
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as error:
        raise FeedError(f"Flux indisponible ({type(error).__name__}).") from None
    return response.content


def _names(card_name):
    return [alias.strip().casefold() for alias in str(card_name or "").split("//")
            if len(alias.strip()) >= MIN_NAME_LENGTH]


def find_mentions(entries, stock_cards, source, label):
    """Mentions d'une carte du stock dans le titre ou le résumé (mot entier), sans doublon."""
    mentions = {}
    for entry in entries:
        text = f"{entry['title']} {entry['summary']}".casefold()
        for card in stock_cards:
            if not card.get("scryfall_id") or not any(
                re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in _names(card.get("name"))
            ):
                continue
            mentions.setdefault((card["scryfall_id"], entry["url"] or entry["title"]), {
                "scryfall_id": card["scryfall_id"], "card_name": card.get("name", "Carte inconnue"),
                "source": source, "source_label": label, "title": entry["title"],
                "url": entry["url"], "published": entry["published"],
            })
    return list(mentions.values())


def scan_community(stock_cards, session=requests, youtube_ids=None, now=None):
    """Lit chaque flux indépendamment ; retourne {"mentions": [...], "errors": [...]}."""
    youtube_ids = youtube_channel_ids() if youtube_ids is None else youtube_ids
    feeds = [("mtggoldfish", "MTGGoldfish", MTGGOLDFISH_FEED)]
    feeds += [("youtube", f"YouTube ({channel_id})", YOUTUBE_FEED.format(channel_id=channel_id))
              for channel_id in youtube_ids]
    mentions, errors, outcome = [], [], {}
    for source, label, url in feeds:
        try:
            entries = parse_feed(fetch_feed(url, session=session), now=now)
        except FeedError as error:
            errors.append(f"{label} : {error}")
            outcome.setdefault(source, []).append(f"{label} : {error}")
            continue
        outcome.setdefault(source, [])
        mentions.extend(find_mentions(entries, stock_cards, source, label))
    for source, failures in outcome.items():
        try:
            storage.record_source_health(source, not failures, "; ".join(failures))
        except Exception as error:  # l'état de santé ne doit jamais faire échouer la veille
            errors.append(f"État de santé non enregistré ({type(error).__name__}).")
    return {"mentions": mentions, "errors": errors}
