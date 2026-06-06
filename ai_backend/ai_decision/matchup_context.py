from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ai_backend.core.config import PROJECT_ROOT


STRATEGY_CONTEXT_PATH = Path("hearthstone_data") / "decks" / "strategy_context.zhCN.json"
LEGACY_ARCHETYPE_PATH = Path("hearthstone_data") / "meta" / "archetypes.zhCN.json"


class MatchupContextBuilder:
    def __init__(
        self,
        archetypes_by_class: Mapping[str, list[Mapping[str, Any]]] | None = None,
        source_path: Path | None = None,
        source_status: str = "loaded",
    ):
        self._archetypes_by_class = {
            self._normalize_class(hero_class): [dict(item) for item in archetypes]
            for hero_class, archetypes in (archetypes_by_class or {}).items()
        }
        self._source_path = source_path
        self._source_status = source_status

    @classmethod
    def from_default_sources(cls, root: Path | None = None) -> "MatchupContextBuilder":
        root = root or PROJECT_ROOT
        external_path = root / STRATEGY_CONTEXT_PATH
        archetypes, status = cls._load_external_archetypes(external_path)
        if status == "missing":
            external_path = root / LEGACY_ARCHETYPE_PATH
            archetypes, status = cls._load_external_archetypes(external_path)
        return cls(archetypes, source_path=external_path, source_status=status)

    def build(self, state: Mapping[str, Any]) -> dict[str, Any]:
        enemy_class = self._hero_class(state.get("enemy_hero"))
        my_class = self._hero_class(state.get("my_hero"))
        archetypes = self._archetypes_by_class.get(enemy_class, [])
        visible_enemy_cards = self._visible_enemy_cards(state)
        candidates = [
            self._score_archetype(archetype, enemy_class, visible_enemy_cards, state)
            for archetype in archetypes
        ]
        candidates.sort(key=lambda item: item["confidence"], reverse=True)

        return {
            "enemy_class": enemy_class or "UNKNOWN",
            "my_class": my_class or "UNKNOWN",
            "possible_enemy_archetypes": candidates[:3],
            "role_assessment": self._role_assessment(my_class, candidates, state),
            "usage_rules": [
                "This context is a probabilistic matchup hint, not confirmed hidden information.",
                "Prefer visible board state, current mana, legal_sequences, and revealed card text over archetype assumptions.",
                "Mention archetypes as possibilities only when evidence is weak.",
            ],
            "meta_source": {
                "mode": "external_file_only",
                "path": str(self._source_path or STRATEGY_CONTEXT_PATH),
                "status": self._source_status,
                "required_schema": {
                    "deck_archetypes": {
                        "HUNTER": [
                            {
                                "name": "example archetype",
                                "class": "HUNTER",
                                "style": "aggro | tempo | control | combo | value | burn | midrange | quest",
                                "base_confidence": 0.3,
                                "signals": ["visible card name or text keyword"],
                                "win_condition": "how this archetype usually wins",
                                "core_cards": [
                                    {
                                        "name": "representative core card",
                                        "role": "why the card matters",
                                        "play_timing": "when the deck wants to play it",
                                        "keep_condition": "when it is usually kept or prioritized",
                                    }
                                ],
                                "game_plan": "short matchup plan",
                                "sources": ["source name or URL"],
                                "source_notes": "why this source is trusted",
                            }
                        ]
                    }
                },
            },
        }

    def _score_archetype(
        self,
        archetype: Mapping[str, Any],
        enemy_class: str,
        visible_enemy_cards: list[Mapping[str, Any]],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        confidence = float(archetype.get("base_confidence") or 0.2)
        evidence = [f"enemy_class={enemy_class}"]
        signals = [str(value).lower() for value in archetype.get("signals") or []]

        for card in visible_enemy_cards:
            card_text = self._card_search_text(card)
            matched = [signal for signal in signals if signal and signal in card_text]
            if not matched:
                continue
            confidence += 0.12
            evidence.append(f"visible_card:{card.get('name') or card.get('card_id')} matched {matched[0]}")

        if archetype.get("style") == "aggro" and self._early_pressure_seen(state, visible_enemy_cards):
            confidence += 0.12
            evidence.append("early_pressure_seen")

        confidence = max(0.0, min(confidence, 0.9))
        return {
            "name": archetype.get("name"),
            "style": archetype.get("style"),
            "confidence": round(confidence, 2),
            "evidence": evidence[:5],
            "win_condition": archetype.get("win_condition"),
            "core_cards": self._compact_core_cards(archetype.get("core_cards")),
            "game_plan_against_it": archetype.get("game_plan"),
            "sources": list(archetype.get("sources") or []),
            "source_notes": archetype.get("source_notes"),
        }

    @staticmethod
    def _compact_core_cards(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        cards: list[dict[str, Any]] = []
        for card in value:
            if not isinstance(card, Mapping):
                continue
            compact = {
                key: card.get(key)
                for key in (
                    "card_id",
                    "name",
                    "role",
                    "play_timing",
                    "keep_condition",
                    "counter_priority",
                )
                if card.get(key) is not None
            }
            if compact.get("name") or compact.get("role"):
                cards.append(compact)
        return cards[:5]

    @classmethod
    def _load_external_archetypes(cls, path: Path) -> tuple[dict[str, list[dict[str, Any]]], str]:
        if not path.exists():
            return {}, "missing"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}, "invalid"
        if not isinstance(data, dict):
            return {}, "invalid"
        if isinstance(data.get("deck_archetypes"), dict):
            data = data["deck_archetypes"]
        elif isinstance(data.get("opponent_archetypes"), dict):
            data = data["opponent_archetypes"]
        result: dict[str, list[dict[str, Any]]] = {}
        for hero_class, archetypes in data.items():
            if not isinstance(archetypes, list):
                continue
            valid = [dict(item) for item in archetypes if isinstance(item, dict) and item.get("name")]
            if valid:
                result[cls._normalize_class(hero_class)] = valid
        return result, "loaded" if result else "empty"

    @classmethod
    def _visible_enemy_cards(cls, state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        cards: list[Mapping[str, Any]] = []
        for key in ("known_enemy_cards", "enemy_board"):
            values = state.get(key)
            if isinstance(values, list):
                cards.extend(value for value in values if isinstance(value, Mapping))

        recent_events = state.get("recent_events")
        if isinstance(recent_events, list):
            for event in recent_events:
                if not isinstance(event, Mapping):
                    continue
                if event.get("player") in {"opponent", "enemy"} or event.get("controller") in {"opponent", "enemy"}:
                    cards.append(event)
        return cards

    @staticmethod
    def _card_search_text(card: Mapping[str, Any]) -> str:
        values = [
            card.get("card_id"),
            card.get("name"),
            card.get("text"),
            card.get("type"),
            " ".join(str(item) for item in card.get("mechanics") or []),
        ]
        return " ".join(str(value).lower() for value in values if value)

    @staticmethod
    def _early_pressure_seen(state: Mapping[str, Any], visible_enemy_cards: list[Mapping[str, Any]]) -> bool:
        turn = MatchupContextBuilder._as_int(state.get("turn"), default=0)
        cheap_visible_cards = [
            card for card in visible_enemy_cards
            if MatchupContextBuilder._as_int(card.get("cost"), default=99) <= 2
        ]
        enemy_board = state.get("enemy_board") if isinstance(state.get("enemy_board"), list) else []
        return turn <= 4 and (len(cheap_visible_cards) >= 1 or len(enemy_board) >= 2)

    @staticmethod
    def _role_assessment(
        my_class: str,
        candidates: list[dict[str, Any]],
        state: Mapping[str, Any],
    ) -> str:
        top = candidates[0] if candidates else {}
        enemy_style = top.get("style")
        my_fast_classes = {"HUNTER", "ROGUE", "DEMONHUNTER", "DEMON_HUNTER"}
        my_hero = state.get("my_hero") if isinstance(state.get("my_hero"), Mapping) else {}
        my_hp = MatchupContextBuilder._as_int(my_hero.get("hp"), default=30)

        if enemy_style == "aggro" and my_class not in my_fast_classes:
            return "对手可能偏快，我方默认先稳住血量和场面；只有接近斩杀时再降低解场优先级。"
        if enemy_style == "aggro" and my_class in my_fast_classes:
            return "双方都可能偏快，需要比较谁能先形成斩杀；落后场面时仍要解关键攻击源。"
        if enemy_style == "control":
            return "对手可能偏控制，我方应避免资源一次性投入过多，同时规划持续伤害和关键回合爆发。"
        if my_hp <= 10:
            return "我方血量偏低，任何套牌判断都不能覆盖保命和解场需求。"
        if not candidates:
            return "未加载到该职业的外部套牌数据，优先根据当前场面、费用和合法动作选择。"
        return "套牌判断证据有限，优先根据当前场面、费用和合法动作选择。"

    @staticmethod
    def _hero_class(hero: Any) -> str:
        if not isinstance(hero, Mapping):
            return ""
        return MatchupContextBuilder._normalize_class(hero.get("class") or hero.get("card_class"))

    @staticmethod
    def _normalize_class(value: Any) -> str:
        return str(value or "").upper().replace(" ", "_")

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
