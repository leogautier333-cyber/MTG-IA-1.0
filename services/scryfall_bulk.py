import os
from pathlib import Path

import ijson
import requests

from database import storage


SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
HEADERS = {"User-Agent": "MagicTradingProfitManager/3.0", "Accept": "application/json"}
CACHE_PATH = Path(os.getenv(
    "SCRYFALL_BULK_CACHE",
    Path(__file__).resolve().parents[1] / "data" / "cache" / "scryfall_default_cards.json",
))


def _download_bulk_file(download_uri, session=requests):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    try:
        with session.get(download_uri, headers=HEADERS, stream=True, timeout=(15, 180)) as response:
            response.raise_for_status()
            with temporary_path.open("wb") as bulk_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        bulk_file.write(chunk)
        temporary_path.replace(CACHE_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


def _stream_cards(path):
    with path.open("rb") as bulk_file:
        yield from ijson.items(bulk_file, "item")


def sync_scryfall_bulk(session=requests, force=False):
    try:
        response = session.get(SCRYFALL_BULK_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        bulk_info = next(
            (item for item in response.json().get("data", []) if item.get("type") == "default_cards"),
            None,
        )
        if not bulk_info:
            raise ValueError("Le flux Bulk default_cards est absent de la réponse Scryfall.")

        updated_at = bulk_info.get("updated_at") or ""
        previous_update = storage.get_metadata("scryfall_bulk_updated_at")
        if not force and previous_update == updated_at and CACHE_PATH.exists():
            storage.record_source_health("scryfall_bulk", True, "Bulk inchangé depuis la dernière synchronisation.")
            return {"status": "UNCHANGED", "updated_at": updated_at}

        _download_bulk_file(bulk_info["download_uri"], session=session)
        diff = storage.sync_bulk_prints(_stream_cards(CACHE_PATH), synced_at=updated_at or None)
        storage.set_metadata("scryfall_bulk_updated_at", updated_at)
        storage.record_source_health("scryfall_bulk", True, "Bulk default_cards synchronisé.")
        return {"status": "UPDATED", "updated_at": updated_at, **diff}
    except Exception as error:
        storage.record_source_health("scryfall_bulk", False, str(error))
        raise


if __name__ == "__main__":
    result = sync_scryfall_bulk()
    print(
        "Bulk {status}: {new} nouvelle(s) carte(s), {reprints} réimpression(s), "
        "{legalities} changement(s) de légalité.".format(
            status=result["status"],
            new=len(result.get("new_cards", [])),
            reprints=len(result.get("reprints", [])),
            legalities=len(result.get("legality_changes", [])),
        )
    )