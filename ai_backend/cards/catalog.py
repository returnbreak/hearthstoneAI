from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ai_backend.core.config import PROJECT_ROOT


class CardCatalog:
    """Local Hearthstone card catalog used to enrich public game data."""

    def __init__(
        self,
        cards_by_id: Mapping[str, Mapping[str, Any]] | None = None,
        missing_report_path: Path | None = None,
    ):
        self._cards_by_id = {str(card_id): dict(card) for card_id, card in (cards_by_id or {}).items()}
        self._missing_report_path = missing_report_path

    @classmethod
    def from_latest_data(cls, root: Path | None = None) -> "CardCatalog":
        root = root or PROJECT_ROOT
        index_path = root / "hearthstone_data" / "latest" / "card_index.zhCN.json"
        missing_report_path = root / "data" / "card_catalog_missing_ids.json"
        if not index_path.exists():
            return cls(missing_report_path=missing_report_path)
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return cls(missing_report_path=missing_report_path)
        if not isinstance(data, dict):
            return cls(missing_report_path=missing_report_path)
        return cls(
            {key: value for key, value in data.items() if isinstance(value, dict)},
            missing_report_path=missing_report_path,
        )

    def enrich_envelope(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        enriched = deepcopy(dict(envelope))
        envelope_type = enriched.get("type")
        if envelope_type == "game_event":
            event = enriched.get("event")
            if isinstance(event, dict):
                self.enrich_card_like(event, self._event_context(event))
        elif envelope_type == "game_state":
            state = enriched.get("state")
            if isinstance(state, dict):
                self.enrich_state(state)
        return enriched

    def enrich_state(self, state: dict[str, Any]) -> None:
        for key in ("hand", "known_enemy_cards", "my_board", "enemy_board"):
            values = state.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict):
                    self.enrich_card_like(value, f"game_state.{key}")

        for key in ("my_hero", "enemy_hero"):
            value = state.get(key)
            if isinstance(value, dict):
                self.enrich_card_like(value, f"game_state.{key}")

        recent_events = state.get("recent_events")
        if isinstance(recent_events, list):
            for event in recent_events:
                if isinstance(event, dict):
                    self.enrich_card_like(event, self._event_context(event, prefix="game_state.recent_events"))

    def enrich_card_like(self, value: dict[str, Any], context: str = "unknown") -> None:
        card_id = value.get("card_id") or value.get("id")
        if not card_id:
            return
        card = self._cards_by_id.get(str(card_id))
        if not card:
            self._record_missing_card(str(card_id), value, context)
            return

        self._fill_if_missing(value, "card_id", card.get("id"))
        self._fill_if_missing(value, "dbf_id", card.get("dbfId"))
        self._fill_if_missing(value, "name", card.get("name"))
        self._fill_if_missing(value, "cost", card.get("cost"))
        self._fill_if_missing(value, "type", card.get("type"))
        self._fill_if_missing(value, "set", card.get("set"))
        self._fill_if_missing(value, "card_class", card.get("cardClass"))
        self._fill_if_missing(value, "rarity", card.get("rarity"))
        self._fill_if_missing(value, "text", card.get("text"))
        self._fill_if_missing(value, "mechanics", card.get("mechanics"))
        self._fill_if_missing(value, "race", card.get("race"))
        self._fill_if_missing(value, "races", card.get("races"))
        self._fill_if_missing(value, "spell_school", card.get("spellSchool"))
        self._fill_if_missing(value, "image", card.get("image"))

    @staticmethod
    def _fill_if_missing(target: dict[str, Any], key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        if target.get(key) in (None, "", [], {}):
            target[key] = deepcopy(value)

    def _record_missing_card(self, card_id: str, value: Mapping[str, Any], context: str) -> None:
        if self._missing_report_path is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        report = self._read_missing_report()
        missing_ids = report.setdefault("missing_ids", {})
        item = missing_ids.setdefault(card_id, {
            "count": 0,
            "first_seen": now,
        })
        item["count"] = int(item.get("count") or 0) + 1
        item["last_seen"] = now
        item["last_context"] = context
        item["last_name"] = value.get("name")
        item["last_dbf_id"] = value.get("dbf_id") or value.get("dbfId")
        report["updated_at"] = now

        self._write_missing_report(report)

    def _read_missing_report(self) -> dict[str, Any]:
        if self._missing_report_path is None or not self._missing_report_path.exists():
            return {"missing_ids": {}}
        try:
            data = json.loads(self._missing_report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {"missing_ids": {}}
        if not isinstance(data, dict):
            return {"missing_ids": {}}
        if not isinstance(data.get("missing_ids"), dict):
            data["missing_ids"] = {}
        return data

    def _write_missing_report(self, report: Mapping[str, Any]) -> None:
        if self._missing_report_path is None:
            return
        try:
            self._missing_report_path.parent.mkdir(parents=True, exist_ok=True)
            self._missing_report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            return

    @staticmethod
    def _event_context(event: Mapping[str, Any], prefix: str = "game_event") -> str:
        event_type = event.get("type")
        if event_type:
            return f"{prefix}.{event_type}"
        return prefix
