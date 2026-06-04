from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


CARD_COLLECTIBLE_JSON_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.collectible.json"
CARD_FULL_JSON_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json"
IMAGE_URL_TEMPLATE = "https://art.hearthstonejson.com/v1/render/latest/zhCN/256x/{card_id}.png"
DEFAULT_STANDARD_SETS = {
    "CORE",
    "EVENT",
    "EMERALD_DREAM",
    "THE_LOST_CITY",
    "TIME_TRAVEL",
    "CATACLYSM",
}
EXCLUDED_WILD_SETS = {"HERO_SKINS", "PLACEHOLDER_202204"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch latest HearthstoneJSON zhCN card data and 256x rendered card images.")
    parser.add_argument("--output", default="hearthstone_data/latest", help="Output directory.")
    parser.add_argument("--download-images", action="store_true", help="Download rendered 256x zhCN card images.")
    parser.add_argument("--image-scope", choices=("standard", "wild", "all"), default="all")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    image_dir = output / "images" / "render_256x"

    collectible_raw_path = output / "cards.collectible.zhCN.json"
    full_raw_path = output / "cards.zhCN.json"
    collectible_headers = download_json(CARD_COLLECTIBLE_JSON_URL, collectible_raw_path, args.timeout)
    full_headers = download_json(CARD_FULL_JSON_URL, full_raw_path, args.timeout)
    collectible_cards = read_card_array(collectible_raw_path, "Downloaded collectible card data is not a JSON array.")
    full_cards = read_card_array(full_raw_path, "Downloaded full card data is not a JSON array.")

    outputs = build_latest_outputs(collectible_cards, full_cards, output)
    wild_cards = outputs["wild_cards"]
    standard_cards = outputs["standard_cards"]
    card_index = outputs["card_index"]

    write_json(output / "wild.zhCN.json", wild_cards)
    write_json(output / "standard.zhCN.json", standard_cards)
    write_json(output / "card_index.zhCN.json", card_index)

    metadata = {
        "source": CARD_COLLECTIBLE_JSON_URL,
        "full_source": CARD_FULL_JSON_URL,
        "fetched_at_epoch": int(time.time()),
        "last_modified": collectible_headers.get("last-modified"),
        "etag": collectible_headers.get("etag"),
        "content_length": collectible_headers.get("content-length"),
        "full_last_modified": full_headers.get("last-modified"),
        "full_etag": full_headers.get("etag"),
        "full_content_length": full_headers.get("content-length"),
        "locale": "zhCN",
        "standard_sets": sorted(DEFAULT_STANDARD_SETS),
        "excluded_wild_sets": sorted(EXCLUDED_WILD_SETS),
        "all_collectible_count": len(collectible_cards),
        "full_card_count": len(full_cards),
        "card_index_count": len(card_index),
        "wild_count": len(wild_cards),
        "standard_count": len(standard_cards),
        "image_resolution": "256x",
        "image_source": "HearthstoneJSON render API",
    }
    write_json(output / "metadata.json", metadata)

    image_manifest: dict[str, Any] = {
        "downloaded": [],
        "failed": [],
        "skipped_existing": [],
        "existing_files": [],
        "scope": args.image_scope,
        "resolution": "256x",
    }
    if args.download_images:
        if args.image_scope == "standard":
            image_cards = standard_cards
        elif args.image_scope == "wild":
            image_cards = wild_cards
        else:
            image_cards = wild_cards
        image_manifest = download_images(image_cards, image_dir, args.workers, args.timeout)
    else:
        image_manifest = existing_image_manifest(image_dir, args.image_scope)

    write_json(output / "image_manifest.zhCN.json", image_manifest)

    print(json.dumps({
        "metadata": metadata,
        "files": {
            "collectible_raw": str(collectible_raw_path),
            "full_raw": str(full_raw_path),
            "standard": str(output / "standard.zhCN.json"),
            "wild": str(output / "wild.zhCN.json"),
            "index": str(output / "card_index.zhCN.json"),
            "images": str(image_dir),
            "image_manifest": str(output / "image_manifest.zhCN.json"),
        },
        "image_summary": {
            "downloaded": len(image_manifest.get("downloaded", [])),
            "failed": len(image_manifest.get("failed", [])),
            "skipped_existing": len(image_manifest.get("skipped_existing", [])),
        },
    }, ensure_ascii=False, indent=2))
    return 0


def download_json(url: str, path: Path, timeout: int) -> dict[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "hearthstoneAI-card-fetcher/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        path.write_bytes(data)
        return {key.lower(): value for key, value in response.headers.items()}


def read_card_array(path: Path, error_message: str) -> list[dict[str, Any]]:
    cards = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cards, list):
        raise RuntimeError(error_message)
    return [card for card in cards if isinstance(card, dict)]


def build_latest_outputs(
    collectible_cards: list[dict[str, Any]],
    full_cards: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    image_dir = output / "images" / "render_256x"
    wild_cards = [copy.deepcopy(card) for card in collectible_cards if is_wild_candidate(card)]
    standard_cards = [copy.deepcopy(card) for card in wild_cards if card.get("set") in DEFAULT_STANDARD_SETS]
    indexed_cards = [copy.deepcopy(card) for card in full_cards if card.get("id")]

    for card in wild_cards:
        add_image_fields(card, output, image_dir)
    for card in standard_cards:
        add_image_fields(card, output, image_dir)
    for card in indexed_cards:
        add_image_fields(card, output, image_dir)

    return {
        "wild_cards": wild_cards,
        "standard_cards": standard_cards,
        "card_index": {card["id"]: card for card in indexed_cards if card.get("id")},
    }


def is_wild_candidate(card: dict[str, Any]) -> bool:
    if card.get("collectible") is False:
        return False
    return card.get("set") not in EXCLUDED_WILD_SETS


def add_image_fields(card: dict[str, Any], output: Path, image_dir: Path) -> None:
    card_id = card.get("id")
    if not card_id:
        return
    image_path = image_dir / f"{card_id}.png"
    card["image"] = {
        "render_256x_url": IMAGE_URL_TEMPLATE.format(card_id=quote_card_id(card_id)),
        "render_256x_path": path_for_json(image_path, output),
    }


def download_images(cards: list[dict[str, Any]], image_dir: Path, workers: int, timeout: int) -> dict[str, Any]:
    image_dir.mkdir(parents=True, exist_ok=True)
    unique_cards = {card["id"]: card for card in cards if card.get("id")}
    downloaded: list[str] = []
    failed: list[dict[str, str]] = []
    skipped: list[str] = []

    def fetch(card_id: str) -> tuple[str, str, str | None]:
        target = image_dir / f"{card_id}.png"
        if target.exists() and target.stat().st_size > 0:
            return "skipped", card_id, None
        url = IMAGE_URL_TEMPLATE.format(card_id=quote_card_id(card_id))
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "hearthstoneAI-card-fetcher/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                target.write_bytes(response.read())
            return "downloaded", card_id, None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            if target.exists() and target.stat().st_size == 0:
                target.unlink()
            return "failed", card_id, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch, card_id) for card_id in sorted(unique_cards)]
        done = 0
        total = len(futures)
        for future in as_completed(futures):
            status, card_id, error = future.result()
            if status == "downloaded":
                downloaded.append(card_id)
            elif status == "skipped":
                skipped.append(card_id)
            else:
                failed.append({"card_id": card_id, "error": error or "unknown"})
            done += 1
            if done % 250 == 0 or done == total:
                print(f"images {done}/{total} downloaded={len(downloaded)} skipped={len(skipped)} failed={len(failed)}", flush=True)

    return {
        "downloaded": downloaded,
        "failed": failed,
        "skipped_existing": skipped,
        "existing_files": sorted(set(downloaded + skipped)),
        "scope_count": len(unique_cards),
        "resolution": "256x",
    }


def existing_image_manifest(image_dir: Path, scope: str) -> dict[str, Any]:
    existing = sorted(path.stem for path in image_dir.glob("*.png")) if image_dir.exists() else []
    return {
        "downloaded": [],
        "failed": [],
        "skipped_existing": [],
        "existing_files": existing,
        "existing_count": len(existing),
        "scope": scope,
        "resolution": "256x",
    }


def quote_card_id(card_id: str) -> str:
    return urllib.parse.quote(card_id, safe="")


def path_for_json(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
