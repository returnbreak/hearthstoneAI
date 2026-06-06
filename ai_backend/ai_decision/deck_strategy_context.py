from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_backend.core.config import PROJECT_ROOT


STRATEGY_CONTEXT_PATH = Path("hearthstone_data") / "decks" / "strategy_context.zhCN.json"
LEGACY_DECK_STRATEGY_PATH = Path("hearthstone_data") / "meta" / "deck_strategies.zhCN.json"


class DeckStrategyContextBuilder:
    def __init__(
        self,
        strategies: Sequence[Mapping[str, Any]] | None = None,
        source_path: Path | None = None,
        source_status: str = "loaded",
    ):
        self._strategies = [dict(strategy) for strategy in strategies or []]
        self._source_path = source_path
        self._source_status = source_status

    @classmethod
    def from_default_sources(cls, root: Path | None = None) -> "DeckStrategyContextBuilder":
        root = root or PROJECT_ROOT
        path = root / STRATEGY_CONTEXT_PATH
        strategies, status = cls._load(path)
        if status == "missing":
            path = root / LEGACY_DECK_STRATEGY_PATH
            strategies, status = cls._load(path)
        return cls(strategies, path, status)

    def build(self, state: Mapping[str, Any]) -> dict[str, Any]:
        metadata = state.get("game_metadata")
        deck = metadata.get("deck") if isinstance(metadata, Mapping) else None
        if not isinstance(deck, Mapping) or not deck.get("deck_available"):
            actual_deck = self._compact_visible_deck(state)
            if actual_deck["cards"]:
                candidate = self._best_visible_candidate(actual_deck)
                if candidate is not None:
                    return {
                        "status": "matched",
                        "analysis_required": False,
                        "match_method": candidate["match_method"],
                        "confidence": candidate["confidence"],
                        "evidence": candidate["evidence"],
                        "actual_deck": actual_deck,
                        "strategy": self._compact_strategy(candidate["strategy"]),
                        "source_status": self._source_status,
                    }
            return {
                "status": "unavailable",
                "analysis_required": True,
                "source_status": self._source_status,
            }

        actual_deck = self._compact_actual_deck(deck)
        candidates = [
            self._score(strategy, actual_deck)
            for strategy in self._strategies
            if self._compatible(strategy, actual_deck)
        ]
        candidates = [candidate for candidate in candidates if candidate is not None]
        candidates.sort(key=lambda item: item["confidence"], reverse=True)

        if not candidates:
            return {
                "status": "unmatched",
                "analysis_required": True,
                "actual_deck": actual_deck,
                "source_status": self._source_status,
            }

        best = candidates[0]
        return {
            "status": "matched",
            "analysis_required": False,
            "match_method": best["match_method"],
            "confidence": best["confidence"],
            "evidence": best["evidence"],
            "actual_deck": actual_deck,
            "strategy": self._compact_strategy(best["strategy"]),
            "source_status": self._source_status,
        }

    @classmethod
    def _load(cls, path: Path) -> tuple[list[dict[str, Any]], str]:
        if not path.exists():
            return [], "missing"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return [], "invalid"
        strategies = None
        if isinstance(value, dict):
            archetypes = value.get("deck_archetypes")
            if isinstance(archetypes, dict):
                strategies = cls._flatten_archetypes(archetypes)
            else:
                strategies = value.get("own_deck_strategies")
            if strategies is None:
                strategies = value.get("decks")
        if not isinstance(strategies, list):
            return [], "invalid"
        valid = [dict(item) for item in strategies if isinstance(item, dict) and item.get("name")]
        return valid, "loaded" if valid else "empty"

    def _best_visible_candidate(self, actual_deck: Mapping[str, Any]) -> dict[str, Any] | None:
        candidates = [
            self._score_visible(strategy, actual_deck)
            for strategy in self._strategies
            if self._compatible(strategy, actual_deck)
        ]
        candidates = [candidate for candidate in candidates if candidate is not None]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return candidates[0]

    @classmethod
    def _flatten_archetypes(cls, value: Mapping[str, Any]) -> list[dict[str, Any]]:
        strategies: list[dict[str, Any]] = []
        for hero_class, archetypes in value.items():
            if not isinstance(archetypes, list):
                continue
            for archetype in archetypes:
                if not isinstance(archetype, Mapping) or not archetype.get("name"):
                    continue
                item = dict(archetype)
                item.setdefault("class", cls._normalize_class(hero_class))
                strategies.append(item)
        return strategies

    @classmethod
    def _score(
        cls,
        strategy: Mapping[str, Any],
        deck: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        deck_name = cls._normalize_text(deck.get("name"))
        known_names = {
            cls._normalize_text(strategy.get("name")),
            *(cls._normalize_text(name) for name in strategy.get("deck_names") or []),
            *(cls._normalize_text(name) for name in strategy.get("aliases") or []),
        }
        if deck_name and deck_name in known_names:
            return {
                "strategy": dict(strategy),
                "match_method": "exact_name",
                "confidence": 0.98,
                "evidence": [f"deck_name={deck.get('name')}"],
            }

        actual_ids = {
            str(card.get("card_id"))
            for card in deck.get("cards") or []
            if isinstance(card, Mapping) and card.get("card_id")
        }
        signatures = {str(value) for value in strategy.get("signature_cards") or [] if value}
        matched_signatures = sorted(actual_ids & signatures)
        required_signatures = max(1, min(2, len(signatures)))
        if len(matched_signatures) < required_signatures:
            return None

        signature_ratio = len(matched_signatures) / max(len(signatures), 1)
        listed_ids = {str(value) for value in strategy.get("deck_card_ids") or [] if value}
        overlap_ratio = (
            len(actual_ids & listed_ids) / max(len(listed_ids), 1)
            if listed_ids
            else 0.0
        )
        confidence = min(0.95, 0.5 + signature_ratio * 0.4 + overlap_ratio * 0.05)
        return {
            "strategy": dict(strategy),
            "match_method": "signature_cards",
            "confidence": round(confidence, 2),
            "evidence": [f"signature_card={card_id}" for card_id in matched_signatures[:5]],
        }

    @classmethod
    def _score_visible(
        cls,
        strategy: Mapping[str, Any],
        deck: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        actual_ids = {
            str(card.get("card_id"))
            for card in deck.get("cards") or []
            if isinstance(card, Mapping) and card.get("card_id")
        }
        signatures = {str(value) for value in strategy.get("signature_cards") or [] if value}
        matched_signatures = sorted(actual_ids & signatures)
        if not matched_signatures:
            return None
        confidence = min(0.9, 0.55 + 0.15 * len(matched_signatures))
        return {
            "strategy": dict(strategy),
            "match_method": "visible_signature_cards",
            "confidence": round(confidence, 2),
            "evidence": [f"visible_signature_card={card_id}" for card_id in matched_signatures[:5]],
        }

    @classmethod
    def _compatible(cls, strategy: Mapping[str, Any], deck: Mapping[str, Any]) -> bool:
        strategy_class = cls._normalize_class(strategy.get("class"))
        deck_class = cls._normalize_class(deck.get("player_class"))
        if strategy_class and deck_class and strategy_class != deck_class:
            return False
        strategy_format = cls._normalize_text(strategy.get("format"))
        deck_format = cls._normalize_text(deck.get("format"))
        return not strategy_format or not deck_format or strategy_format == deck_format

    @staticmethod
    def _compact_actual_deck(deck: Mapping[str, Any]) -> dict[str, Any]:
        cards = []
        for card in deck.get("cards") or []:
            if not isinstance(card, Mapping):
                continue
            cards.append({
                key: card.get(key)
                for key in ("card_id", "name", "count", "cost", "type", "text", "mechanics")
                if card.get(key) is not None
            })
        return {
            "deck_id": deck.get("deck_id"),
            "name": deck.get("name"),
            "player_class": deck.get("player_class"),
            "format": deck.get("format"),
            "cards": cards,
        }

    @staticmethod
    def _compact_visible_deck(state: Mapping[str, Any]) -> dict[str, Any]:
        cards = []
        for key in ("hand", "my_board", "known_my_cards"):
            values = state.get(key)
            if not isinstance(values, list):
                continue
            for card in values:
                if not isinstance(card, Mapping) or not card.get("card_id"):
                    continue
                cards.append({
                    field: card.get(field)
                    for field in ("card_id", "name", "count", "cost", "type", "text", "mechanics")
                    if card.get(field) is not None
                })
        return {
            "deck_id": None,
            "name": None,
            "player_class": DeckStrategyContextBuilder._hero_class(state.get("my_hero")),
            "format": state.get("mode") or state.get("format"),
            "cards": cards,
        }

    @staticmethod
    def _compact_strategy(strategy: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": strategy.get("name"),
            "style": strategy.get("style"),
            "win_condition": strategy.get("win_condition"),
            "burst_exception": bool(strategy.get("burst_exception")),
            "core_cards": [
                {
                    key: card.get(key)
                    for key in (
                        "card_id",
                        "name",
                        "role",
                        "play_timing",
                        "keep_condition",
                    )
                    if card.get(key) is not None
                }
                for card in strategy.get("core_cards") or []
                if isinstance(card, Mapping)
            ],
        }

    @staticmethod
    def _normalize_class(value: Any) -> str:
        return str(value or "").upper().replace(" ", "_")

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip().casefold()

    @staticmethod
    def _hero_class(hero: Any) -> str:
        if not isinstance(hero, Mapping):
            return ""
        return DeckStrategyContextBuilder._normalize_class(hero.get("class") or hero.get("card_class"))
