from __future__ import annotations

import json
from typing import Any

from ai_backend.ai_decision.deck_strategy_context import DeckStrategyContextBuilder
from ai_backend.ai_decision.matchup_context import MatchupContextBuilder


class DecisionPromptBuilder:
    SYSTEM_PROMPT = (
        "You are a Hearthstone decision assistant. Choose exactly one sequence "
        "from legal_sequences. Do not invent actions. If action_space.lethal_sequence_ids "
        "is not empty, choose one of those IDs. Otherwise value board pressure, "
        "taunt constraints, mana efficiency, hero safety, card text interactions, "
        "resource engines, high-impact payoff turns, and matchup role assignment. "
        "Hero power is usually low priority. Answer in Chinese. Base your answer "
        "only on the provided game_state, matchup_context, action_space, and card text. "
        "When naming cards or minions in reason or risk, copy exact names from the prompt; "
        "if a name is missing, say 未知卡牌 or 未知随从 and do not guess. "
        "Use matchup_context.identified_enemy_deck only when present; treat "
        "backup_enemy_deck as an unconfirmed fallback, never as hidden information."
    )

    def __init__(
        self,
        matchup_context_builder: MatchupContextBuilder | None = None,
        deck_strategy_context_builder: DeckStrategyContextBuilder | None = None,
    ):
        self._matchup_context_builder = matchup_context_builder or MatchupContextBuilder.from_default_sources()
        self._deck_strategy_context_builder = (
            deck_strategy_context_builder
            or DeckStrategyContextBuilder.from_default_sources()
        )

    def build(self, state: dict[str, Any], action_space: dict[str, Any]) -> dict[str, str]:
        matchup_context = self._matchup_context_builder.build(state)
        compact_matchup_context = self._compact_matchup_context(matchup_context)
        my_deck_context = self._compact_my_deck_context(
            self._deck_strategy_context_builder.build(state)
        )
        compact_sequences = self._compact_sequences(action_space.get("legal_sequences") or [])
        lethal_sequence_ids = self._lethal_sequence_ids(state, compact_sequences)
        payload = {
            "principles": [
                "Only choose a sequence_id that appears in action_space.legal_sequences.",
                "If action_space.lethal_sequence_ids is not empty, choose one of those IDs. Do not choose value, board, or resource plays over listed lethal.",
                "Respect current mana, target legality, taunt, immunity, and possible_targets from the provided action_space.",
                "Do not infer hidden enemy hand cards.",
                "When mentioning a card or minion name, copy the exact visible name from game_state or action_space; do not invent or translate names.",
                "Use my_deck_context as my deck plan. Use matchup_context.identified_enemy_deck only when enemy_deck_status is identified; backup_enemy_deck is only a fallback possibility.",
                "Do not spend Coin or other temporary mana just to fill mana. Use it only for a stronger payoff turn, combo activation, lethal, urgent defense, or a high_impact/core payoff card.",
                "Avoid playing a low-impact Battlecry minion only to spend mana when its text has no useful current target or payoff, unless board tempo is clearly the plan.",
                "Early game: if there is no immediate board pressure or lethal race, prefer playing a playable core resource engine or payoff setup over low-impact hero power or holding it.",
                "If there is no lethal and no urgent defensive target, strongly consider legal sequences that use temporary mana to play high_impact_cards or my deck's core payoff cards this turn.",
                "Do not spend stolen, discovered, or premium removal just because it is playable. Hold it when current pressure is low and waiting can hit more minions or a more important threat.",
                "For non-burst decks, board presence and preserving existing minions usually matter more than spending damage inefficiently; burst/combo decks may preserve combo pieces instead.",
                "Answer reason and risk in Chinese, using only the information provided in this prompt.",
            ],
            "game_state": self._compact_state(state),
            "my_deck_context": my_deck_context,
            "matchup_context": compact_matchup_context,
            "action_space": {
                "available_mana": action_space.get("available_mana"),
                "lethal_sequence_ids": lethal_sequence_ids,
                "legal_sequences": compact_sequences,
                "board_effects": [
                    self._without_empty_values({
                        "source": effect.get("source"),
                        "card_id": effect.get("card_id"),
                        "name": effect.get("name"),
                        "kind": effect.get("kind"),
                        "spell_damage": effect.get("spell_damage"),
                        "aura_attack_buff": effect.get("aura_attack_buff"),
                    })
                    for effect in action_space.get("board_effects") or []
                ],
                "modifiers": action_space.get("modifiers") or {},
            },
            "required_output": {
                "chosen_sequence_id": "one legal sequence_id",
                "reason": "short tactical reason in Chinese",
                "risk": "short risk note in Chinese",
                "confidence": "number from 0 to 1",
            },
        }
        high_confidence_rule = self._high_confidence_matchup_rule(compact_matchup_context)
        if high_confidence_rule:
            payload["high_confidence_matchup_rule"] = high_confidence_rule
        return {
            "system": self.SYSTEM_PROMPT,
            "user": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }

    @staticmethod
    def _high_confidence_matchup_rule(matchup_context: dict[str, Any]) -> str | None:
        top = matchup_context.get("identified_enemy_deck") or {}
        if not top:
            return None
        confidence = float(top.get("confidence") or 0.0)
        if confidence < 0.8:
            return None
        name = top.get("name") or "top archetype"
        game_plan = top.get("game_plan_against_it") or ""
        win_condition = top.get("win_condition") or ""
        core_names = [
            str(card.get("name"))
            for card in top.get("core_cards") or []
            if isinstance(card, dict) and card.get("name")
        ][:4]
        return (
            f"Top visible-evidence matchup is {name} with confidence {confidence:.2f}. "
            "You must explicitly evaluate this archetype in reason and risk, "
            "including its win_condition and core-card timing when provided, "
            "apply its game_plan_against_it when choosing among legal_sequences, "
            "and only deviate when current board, lethal, taunt, mana, or hero-health facts justify it. "
            f"Win condition: {win_condition}. Core cards: {', '.join(core_names)}. Game plan: {game_plan}"
        )

    @staticmethod
    def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "game_id": state.get("game_id"),
            "turn": state.get("turn"),
            "active_player": state.get("active_player"),
            "my_mana": state.get("my_mana") or state.get("mana"),
            "enemy_mana": state.get("enemy_mana"),
            "my_hero": state.get("my_hero"),
            "enemy_hero": state.get("enemy_hero"),
            "hand": [DecisionPromptBuilder._card(card) for card in state.get("hand") or []],
            "my_board": [DecisionPromptBuilder._minion(minion) for minion in state.get("my_board") or []],
            "enemy_board": [DecisionPromptBuilder._minion(minion) for minion in state.get("enemy_board") or []],
            "known_enemy_cards": [
                DecisionPromptBuilder._card(card)
                for card in state.get("known_enemy_cards") or []
            ],
            "recent_events": DecisionPromptBuilder._compact_recent_events(
                state.get("recent_events") or []
            ),
        }

    @staticmethod
    def _compact_matchup_context(matchup_context: dict[str, Any]) -> dict[str, Any]:
        candidates = matchup_context.get("possible_enemy_archetypes") or []
        top = candidates[0] if candidates else {}
        identified_enemy_deck = {}
        backup_enemy_deck = {}
        confidence = float(top.get("confidence") or 0.0) if isinstance(top, dict) else 0.0
        if top and confidence >= 0.65:
            identified_enemy_deck = DecisionPromptBuilder._compact_enemy_deck(top)
            if len(candidates) > 1:
                backup_enemy_deck = DecisionPromptBuilder._compact_enemy_deck(candidates[1])
        elif top:
            backup_enemy_deck = DecisionPromptBuilder._compact_enemy_deck(top)
        return DecisionPromptBuilder._without_empty_values({
            "enemy_class": matchup_context.get("enemy_class"),
            "my_class": matchup_context.get("my_class"),
            "enemy_deck_status": "identified" if identified_enemy_deck else "unconfirmed",
            "identified_enemy_deck": identified_enemy_deck,
            "backup_enemy_deck": backup_enemy_deck,
            "role_assessment": matchup_context.get("role_assessment"),
        })

    @staticmethod
    def _compact_my_deck_context(my_deck_context: dict[str, Any]) -> dict[str, Any]:
        strategy = my_deck_context.get("strategy") if isinstance(my_deck_context.get("strategy"), dict) else {}
        actual_deck = my_deck_context.get("actual_deck") if isinstance(my_deck_context.get("actual_deck"), dict) else {}
        compact = {
            "status": my_deck_context.get("status"),
            "match_method": my_deck_context.get("match_method"),
            "confidence": my_deck_context.get("confidence"),
            "analysis_required": my_deck_context.get("analysis_required"),
        }
        if strategy:
            compact["strategy"] = DecisionPromptBuilder._compact_deck_strategy(strategy)
        elif actual_deck:
            compact["actual_deck"] = DecisionPromptBuilder._without_empty_values({
                "name": actual_deck.get("name"),
                "player_class": actual_deck.get("player_class"),
                "format": actual_deck.get("format"),
                "cards": [
                    DecisionPromptBuilder._without_empty_values({
                        "card_id": card.get("card_id"),
                        "name": card.get("name"),
                        "count": card.get("count"),
                        "cost": card.get("cost"),
                        "type": card.get("type"),
                        "text": card.get("text"),
                    })
                    for card in (actual_deck.get("cards") or [])[:30]
                    if isinstance(card, dict)
                ],
            })
        return DecisionPromptBuilder._without_empty_values(compact)

    @staticmethod
    def _compact_deck_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
        return DecisionPromptBuilder._without_empty_values({
            "name": strategy.get("name"),
            "style": strategy.get("style"),
            "burst_exception": strategy.get("burst_exception"),
            "win_condition": strategy.get("win_condition"),
            "core_cards": [
                DecisionPromptBuilder._without_empty_values({
                    "card_id": card.get("card_id"),
                    "name": card.get("name"),
                    "role": card.get("role"),
                    "play_timing": card.get("play_timing"),
                    "keep_condition": card.get("keep_condition"),
                    "counter_priority": card.get("counter_priority"),
                })
                for card in (strategy.get("core_cards") or [])[:6]
                if isinstance(card, dict)
            ],
            "game_plan": strategy.get("game_plan"),
        })

    @staticmethod
    def _compact_enemy_deck(deck: dict[str, Any]) -> dict[str, Any]:
        return DecisionPromptBuilder._without_empty_values({
            "name": deck.get("name"),
            "style": deck.get("style"),
            "confidence": deck.get("confidence"),
            "evidence": list(deck.get("evidence") or [])[:5],
            "win_condition": deck.get("win_condition"),
            "core_cards": [
                DecisionPromptBuilder._without_empty_values({
                    "card_id": card.get("card_id"),
                    "name": card.get("name"),
                    "role": card.get("role"),
                    "play_timing": card.get("play_timing"),
                    "keep_condition": card.get("keep_condition"),
                    "counter_priority": card.get("counter_priority"),
                })
                for card in (deck.get("core_cards") or [])[:4]
                if isinstance(card, dict)
            ],
            "game_plan_against_it": deck.get("game_plan_against_it"),
        })

    @staticmethod
    def _compact_recent_events(events: list[Any]) -> list[dict[str, Any]]:
        keys = (
            "type",
            "player",
            "controller",
            "source",
            "target",
            "card_id",
            "name",
            "cost",
            "damage",
            "timestamp",
            "target",
        )
        compact_events = []
        for event in events[-6:]:
            if not isinstance(event, dict):
                continue
            compact_events.append(DecisionPromptBuilder._without_empty_values({
                key: event.get(key) for key in keys
            }))
        return compact_events

    @staticmethod
    def _lethal_sequence_ids(state: dict[str, Any], sequences: list[dict[str, Any]]) -> list[str]:
        enemy_hero = state.get("enemy_hero") if isinstance(state.get("enemy_hero"), dict) else {}
        required_damage = int(enemy_hero.get("hp") or 0) + int(enemy_hero.get("armor") or 0)
        if required_damage <= 0:
            return []
        lethal_ids = []
        for sequence in sequences:
            heuristics = sequence.get("heuristics") if isinstance(sequence.get("heuristics"), dict) else {}
            damage = int(heuristics.get("enemy_hero_damage") or 0)
            if damage >= required_damage and sequence.get("sequence_id"):
                lethal_ids.append(str(sequence["sequence_id"]))
        return lethal_ids

    @classmethod
    def _compact_sequences(cls, sequences: list[Any]) -> list[dict[str, Any]]:
        compact_sequences = []
        for sequence in sequences:
            if not isinstance(sequence, dict):
                continue
            compact_sequences.append(cls._without_empty_values({
                "sequence_id": sequence.get("sequence_id"),
                "total_cost": sequence.get("total_cost"),
                "remaining_mana": sequence.get("remaining_mana"),
                "actions": [
                    cls._compact_action(action)
                    for action in sequence.get("actions") or []
                    if isinstance(action, dict)
                ],
                "heuristics": sequence.get("heuristics") or {},
            }))
        return compact_sequences

    @classmethod
    def _compact_action(cls, action: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "type",
            "source",
            "target",
            "target_type",
            "card_id",
            "name",
            "cost",
            "card_type",
            "weapon_attack",
            "weapon_durability",
            "mana_gain",
            "target_required",
            "possible_targets",
            "damage",
            "self_damage_risk",
            "from_played_weapon",
            "hero_class",
        )
        compact = {key: action.get(key) for key in keys}
        effect = action.get("effect")
        if isinstance(effect, dict):
            compact["effect"] = cls._without_empty_values({
                key: value
                for key, value in effect.items()
                if key != "raw_text"
            })
        return cls._without_empty_values(compact)

    @staticmethod
    def _without_empty_values(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in values.items()
            if value is not None and value != [] and value != {}
        }

    @staticmethod
    def _card(card: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": card.get("entity_id"),
            "card_id": card.get("card_id"),
            "name": card.get("name"),
            "cost": card.get("cost"),
            "type": card.get("type"),
            "text": card.get("text"),
            "mechanics": card.get("mechanics"),
            "attack": card.get("attack"),
            "health": card.get("health"),
            "durability": card.get("durability"),
        }

    @staticmethod
    def _minion(minion: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": minion.get("entity_id"),
            "card_id": minion.get("card_id"),
            "name": minion.get("name"),
            "text": minion.get("text"),
            "attack": minion.get("attack"),
            "health": minion.get("health"),
            "damage": minion.get("damage"),
            "taunt": minion.get("taunt"),
            "divine_shield": minion.get("divine_shield"),
            "stealth": minion.get("stealth"),
            "rush": minion.get("rush"),
            "charge": minion.get("charge"),
            "windfury": minion.get("windfury"),
            "mega_windfury": minion.get("mega_windfury"),
            "lifesteal": minion.get("lifesteal"),
            "poisonous": minion.get("poisonous"),
            "venomous": minion.get("venomous"),
            "frozen": minion.get("frozen"),
            "immune": minion.get("immune"),
            "dormant": minion.get("dormant"),
        }
