"""
Generate legal action candidates from public Hearthstone game state.

This module does not rank or recommend moves. It only enumerates what can be
considered by the AI decision layer within visible rules and current mana.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from ai_backend.coach.card_effects import CardEffectRecognizer
from ai_backend.coach.combat_analyzer import CombatAnalyzer


class ActionPlanner:
    def __init__(
        self,
        combat_analyzer: CombatAnalyzer | None = None,
        effect_recognizer: CardEffectRecognizer | None = None,
        max_card_combinations: int = 128,
        max_sequences_for_prompt: int = 20,
    ):
        self._combat_analyzer = combat_analyzer or CombatAnalyzer()
        self._effect_recognizer = effect_recognizer or CardEffectRecognizer()
        self._max_card_combinations = max_card_combinations
        self._max_sequences_for_prompt = max_sequences_for_prompt

    def generate(self, state: dict[str, Any]) -> dict[str, Any]:
        available_mana = self._available_mana(state)
        playable_cards = self._playable_cards(state, available_mana)
        card_combinations = self._card_combinations(playable_cards, available_mana)
        board_effects = self._board_effects(state)
        legal_attacks = self._combat_analyzer.legal_attacks(state)
        hero_power = self._hero_power_action(state, available_mana)
        legal_actions = self._legal_actions(state, playable_cards, legal_attacks, hero_power)
        legal_sequences = self._legal_sequences(available_mana, card_combinations, legal_actions)

        return {
            "available_mana": available_mana,
            "playable_cards": playable_cards,
            "card_combinations": card_combinations,
            "legal_attacks": legal_attacks,
            "hero_power": hero_power,
            "legal_actions": legal_actions,
            "legal_sequences": legal_sequences,
            "board_effects": board_effects,
            "modifiers": {
                "spell_damage": sum(effect["spell_damage"] for effect in board_effects),
            },
        }

    def _playable_cards(self, state: dict[str, Any], available_mana: int) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for card in state.get("hand") or []:
            cost = int(card.get("cost") or 0)
            if cost > available_mana:
                continue

            effect = self._effect_recognizer.recognize(card)
            cards.append({
                "type": "play_card",
                "source": card.get("entity_id"),
                "card_id": card.get("card_id"),
                "name": card.get("name"),
                "card": card,
                "cost": cost,
                "target_required": self._target_required(effect),
                "possible_targets": self._possible_card_targets(state, effect),
                "effect": {
                    "kind": effect.kind,
                    "damage": effect.damage,
                    "attack_buff": effect.attack_buff,
                    "health_buff": effect.health_buff,
                    "spell_damage": effect.spell_damage,
                    "aura_attack_buff": effect.aura_attack_buff,
                    "can_target_enemy_hero": effect.can_target_enemy_hero,
                    "raw_text": effect.raw_text,
                },
                "priority": "normal",
            })
        return cards

    def _card_combinations(self, playable_cards: list[dict[str, Any]], available_mana: int) -> list[dict[str, Any]]:
        combos: list[dict[str, Any]] = []
        for size in range(1, len(playable_cards) + 1):
            for selected in combinations(playable_cards, size):
                total_cost = sum(int(action["cost"]) for action in selected)
                if total_cost > available_mana:
                    continue
                combos.append({
                    "type": "card_sequence",
                    "total_cost": total_cost,
                    "remaining_mana": available_mana - total_cost,
                    "actions": list(selected),
                })
                if len(combos) >= self._max_card_combinations:
                    return combos
        return combos

    def _legal_actions(
        self,
        state: dict[str, Any],
        playable_cards: list[dict[str, Any]],
        legal_attacks: list[dict[str, Any]],
        hero_power: dict[str, Any] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        minion_attacks: list[dict[str, Any]] = []
        hero_attacks: list[dict[str, Any]] = []
        for attack in legal_attacks:
            action_type = "hero_attack" if attack["source"] == "my_hero" else "minion_attack"
            action = {
                "type": action_type,
                "source": attack["source"],
                "target": attack["target"],
                "target_type": attack["target_type"],
                "damage": attack["damage"],
            }
            if action_type == "hero_attack":
                action["self_damage_risk"] = self._self_damage_risk(state, attack["target"])
                hero_attacks.append(action)
            else:
                minion_attacks.append(action)

        return {
            "play_card": [self._public_card_action(action) for action in playable_cards],
            "minion_attack": minion_attacks,
            "hero_attack": hero_attacks,
            "hero_power": [hero_power] if hero_power else [],
            "activate_ability": [],
            "end_turn": [{"type": "end_turn"}],
        }

    def _legal_sequences(
        self,
        available_mana: int,
        card_combinations: list[dict[str, Any]],
        legal_actions: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        sequences = [self._build_sequence("seq-000", available_mana, [], legal_actions)]
        for index, combo in enumerate(card_combinations, start=1):
            card_actions = [self._public_card_action(action) for action in combo["actions"]]
            sequences.append(self._build_sequence(
                f"seq-{index:03d}",
                int(combo["remaining_mana"]),
                card_actions,
                legal_actions,
            ))
            if len(sequences) >= self._max_sequences_for_prompt:
                break

        sequences.append({
            "type": "sequence",
            "sequence_id": "seq-end-turn",
            "total_cost": 0,
            "remaining_mana": available_mana,
            "actions": [{"type": "end_turn"}],
            "heuristics": {
                "enemy_hero_damage": 0,
                "spends_all_mana": available_mana == 0,
                "protects_my_hero": False,
            },
        })
        return sequences[:self._max_sequences_for_prompt]

    def _build_sequence(
        self,
        sequence_id: str,
        remaining_mana: int,
        card_actions: list[dict[str, Any]],
        legal_actions: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        actions = list(card_actions)
        actions.extend(legal_actions["minion_attack"])
        actions.extend(legal_actions["hero_attack"])
        if remaining_mana >= 2:
            actions.extend(legal_actions["hero_power"])

        total_cost = sum(int(action.get("cost") or 0) for action in actions)
        enemy_hero_damage = sum(
            int(action.get("damage") or 0)
            for action in actions
            if action.get("target") == "enemy_hero"
        )
        hero_power_cost = sum(
            int(action.get("cost") or 0)
            for action in legal_actions["hero_power"]
            if action in actions
        )
        return {
            "type": "sequence",
            "sequence_id": sequence_id,
            "total_cost": total_cost,
            "remaining_mana": remaining_mana - hero_power_cost,
            "actions": actions,
            "heuristics": {
                "enemy_hero_damage": enemy_hero_damage,
                "spends_all_mana": total_cost >= remaining_mana,
                "protects_my_hero": False,
            },
        }

    def _board_effects(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        for minion in state.get("my_board") or []:
            if minion.get("silenced") or minion.get("dormant"):
                continue
            effect = self._effect_recognizer.recognize({
                "card_id": minion.get("card_id"),
                "name": minion.get("name"),
                "type": "MINION",
                "text": minion.get("text"),
            })
            if effect.kind == "unknown":
                continue
            effects.append({
                "source": minion.get("entity_id"),
                "card_id": minion.get("card_id"),
                "name": minion.get("name"),
                "kind": effect.kind,
                "spell_damage": effect.spell_damage,
                "aura_attack_buff": effect.aura_attack_buff,
                "raw_text": effect.raw_text,
            })
        return effects

    @staticmethod
    def _available_mana(state: dict[str, Any]) -> int:
        mana = state.get("my_mana") or state.get("mana") or {}
        return int(mana.get("current") or 0)

    @staticmethod
    def _public_card_action(action: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "play_card",
            "source": action.get("source"),
            "card_id": action.get("card_id"),
            "name": action.get("name"),
            "cost": action.get("cost"),
            "target_required": action.get("target_required", False),
            "possible_targets": list(action.get("possible_targets") or []),
            "effect": dict(action.get("effect") or {}),
            "priority": action.get("priority", "normal"),
        }

    @staticmethod
    def _target_required(effect: Any) -> bool:
        return effect.kind in {"damage", "buff"}

    @staticmethod
    def _possible_card_targets(state: dict[str, Any], effect: Any) -> list[Any]:
        if effect.kind == "damage":
            targets = [
                minion.get("entity_id")
                for minion in state.get("enemy_board") or []
                if not minion.get("stealth") and not minion.get("dormant") and not minion.get("immune")
            ]
            enemy_hero = state.get("enemy_hero") or {}
            if effect.can_target_enemy_hero and not enemy_hero.get("immune"):
                targets.insert(0, "enemy_hero")
            return targets
        if effect.kind == "buff":
            return [minion.get("entity_id") for minion in state.get("my_board") or []]
        return []

    @staticmethod
    def _self_damage_risk(state: dict[str, Any], target: Any) -> int:
        for minion in state.get("enemy_board") or []:
            if minion.get("entity_id") == target:
                return int(minion.get("attack") or 0)
        return 0

    @staticmethod
    def _hero_power_action(state: dict[str, Any], available_mana: int) -> dict[str, Any] | None:
        if available_mana < 2:
            return None

        hero = state.get("my_hero") or {}
        hero_class = str(hero.get("class") or "").upper().replace(" ", "")
        enemy_hero = state.get("enemy_hero") or {}
        enemy_immune = bool(enemy_hero.get("immune"))
        board_full = len(state.get("my_board") or []) >= 7

        def visible_characters(include_enemy_hero: bool = True) -> list[Any]:
            targets: list[Any] = []
            if include_enemy_hero and not enemy_immune:
                targets.append("enemy_hero")
            targets.extend(
                minion.get("entity_id")
                for minion in state.get("enemy_board") or []
                if not minion.get("stealth") and not minion.get("dormant") and not minion.get("immune")
            )
            targets.append("my_hero")
            targets.extend(
                minion.get("entity_id")
                for minion in state.get("my_board") or []
                if not minion.get("stealth") and not minion.get("dormant") and not minion.get("immune")
            )
            return [target for target in targets if target is not None]

        target = None
        possible_targets: list[Any] = []
        target_required = False
        damage = 0

        if hero_class == "HUNTER":
            if enemy_immune:
                return None
            target = "enemy_hero"
            possible_targets = ["enemy_hero"]
            damage = 2
            effect = {"kind": "damage_enemy_hero", "damage": 2}
        elif hero_class == "MAGE":
            possible_targets = visible_characters()
            if not possible_targets:
                return None
            target = "enemy_hero" if "enemy_hero" in possible_targets else possible_targets[0]
            target_required = True
            damage = 1
            effect = {"kind": "damage", "damage": 1}
        elif hero_class == "PRIEST":
            possible_targets = visible_characters()
            if not possible_targets:
                return None
            target = possible_targets[0]
            target_required = True
            effect = {"kind": "restore_health", "healing": 2}
        elif hero_class == "WARRIOR":
            effect = {"kind": "gain_armor", "armor": 2}
        elif hero_class == "WARLOCK":
            effect = {"kind": "draw_card_self_damage", "cards": 1, "self_damage": 2}
        elif hero_class == "PALADIN":
            if board_full:
                return None
            effect = {"kind": "summon_minion", "summon_count": 1, "summon": "1/1 Silver Hand Recruit"}
        elif hero_class == "SHAMAN":
            if board_full:
                return None
            effect = {"kind": "summon_totem", "summon_count": 1}
        elif hero_class == "ROGUE":
            effect = {"kind": "equip_weapon", "attack": 1, "durability": 2}
        elif hero_class == "DRUID":
            effect = {"kind": "attack_and_armor", "attack_gain": 1, "armor": 1}
        elif hero_class in {"DEMONHUNTER", "DEMON_HUNTER"}:
            hero_class = "DEMONHUNTER"
            effect = {"kind": "attack_gain", "attack_gain": 1}
        elif hero_class in {"DEATHKNIGHT", "DEATH_KNIGHT"}:
            hero_class = "DEATHKNIGHT"
            if board_full:
                return None
            effect = {"kind": "summon_ghoul", "summon_count": 1, "summon": "1/1 Ghoul with Charge"}
        else:
            effect = {"kind": "unknown"}

        return {
            "type": "hero_power",
            "source": "my_hero_power",
            "cost": 2,
            "priority": "low",
            "hero_class": hero_class,
            "target_required": target_required,
            "target": target,
            "possible_targets": possible_targets,
            "damage": damage,
            "effect": effect,
        }
