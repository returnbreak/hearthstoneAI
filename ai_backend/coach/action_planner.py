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
        max_card_combinations: int = 32,
        max_sequences_for_prompt: int = 10,
        max_attack_plans: int = 3,
    ):
        self._combat_analyzer = combat_analyzer or CombatAnalyzer()
        self._effect_recognizer = effect_recognizer or CardEffectRecognizer()
        self._max_card_combinations = max_card_combinations
        self._max_sequences_for_prompt = max_sequences_for_prompt
        self._max_attack_plans = max_attack_plans

    def generate(self, state: dict[str, Any]) -> dict[str, Any]:
        available_mana = self._available_mana(state)
        temporary_mana_available = self._temporary_mana_available(state)
        playable_cards = self._playable_cards(state, available_mana + temporary_mana_available)
        tradeable_cards = self._tradeable_cards(state, available_mana)
        card_combinations = self._ordered_card_combinations(
            self._card_combinations(playable_cards, available_mana),
            playable_cards,
            available_mana,
        )
        board_effects = self._board_effects(state)
        legal_attacks = self._combat_analyzer.legal_attacks(state)
        hero_power = self._hero_power_action(state, available_mana)
        legal_actions = self._legal_actions(state, playable_cards, tradeable_cards, legal_attacks, hero_power)
        legal_sequences = self._legal_sequences(state, available_mana, card_combinations, legal_actions)

        return {
            "available_mana": available_mana,
            "hero_hp": int((state.get("my_hero") or {}).get("hp") or 0),
            "hero_armor": int((state.get("my_hero") or {}).get("armor") or 0),
            "playable_cards": playable_cards,
            "tradeable_cards": tradeable_cards,
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
            if not self._has_card_identity(card):
                continue
            cost = int(card.get("cost") or 0)
            if cost > available_mana:
                continue

            effect = self._effect_recognizer.recognize(card)
            mana_gain = self._temporary_mana_gain(card)
            cards.append({
                "type": "play_card",
                "source": card.get("entity_id"),
                "card_id": card.get("card_id"),
                "name": card.get("name"),
                "card": card,
                "cost": cost,
                "card_type": card.get("type"),
                "weapon_attack": card.get("attack") if self._is_weapon_card(card) else None,
                "weapon_durability": card.get("durability") or card.get("health") if self._is_weapon_card(card) else None,
                "target_required": self._target_required(effect),
                "possible_targets": self._possible_card_targets(state, effect),
                "effect": self._effect_payload(card, effect),
                "mana_gain": mana_gain,
                "setup_only": self._is_setup_only_card(card),
                "priority": "normal",
            })
        return cards

    def _tradeable_cards(self, state: dict[str, Any], available_mana: int) -> list[dict[str, Any]]:
        if available_mana < 1:
            return []

        actions: list[dict[str, Any]] = []
        for card in state.get("hand") or []:
            if not self._is_tradeable_card(card):
                continue
            actions.append({
                "type": "trade_card",
                "source": card.get("entity_id"),
                "card_id": card.get("card_id"),
                "name": card.get("name"),
                "cost": 1,
                "card_type": card.get("type"),
                "effect": {
                    "kind": "tradeable",
                    "draw": 1,
                    "raw_text": CardEffectRecognizer._normalize_text(str(card.get("text") or "")),
                },
                "priority": "cycle",
            })
        return actions

    def _card_combinations(self, playable_cards: list[dict[str, Any]], available_mana: int) -> list[dict[str, Any]]:
        combos: list[dict[str, Any]] = []
        for size in range(1, len(playable_cards) + 1):
            for selected in combinations(playable_cards, size):
                if not self._setup_cards_have_payoff(selected):
                    continue
                total_cost = sum(int(action["cost"]) for action in selected)
                mana_generated = sum(int(action.get("mana_gain") or 0) for action in selected)
                effective_cost = total_cost - mana_generated
                if effective_cost > available_mana:
                    continue
                combos.append({
                    "type": "card_sequence",
                    "total_cost": total_cost,
                    "mana_generated": mana_generated,
                    "remaining_mana": available_mana - effective_cost,
                    "actions": self._ordered_actions_for_mana(list(selected)),
                })
                if len(combos) >= self._max_card_combinations:
                    return combos
        return combos

    @staticmethod
    def _ordered_card_combinations(
        combinations_to_order: list[dict[str, Any]],
        playable_cards: list[dict[str, Any]],
        available_mana: int,
    ) -> list[dict[str, Any]]:
        preferred_combos = [
            combo for combo in combinations_to_order
            if not ActionPlanner._uses_low_impact_temporary_mana(combo)
        ]
        delayed_combos = [
            combo for combo in combinations_to_order
            if ActionPlanner._uses_low_impact_temporary_mana(combo)
        ]
        remaining = list(preferred_combos)
        ordered: list[dict[str, Any]] = []
        uncovered_sources = {
            action.get("source")
            for action in playable_cards
            if action.get("source") is not None
            and int(action.get("mana_gain") or 0) <= 0
        }

        while uncovered_sources and remaining:
            best = max(
                remaining,
                key=lambda combo: (
                    len(ActionPlanner._combination_sources(combo) & uncovered_sources),
                    int(combo.get("total_cost") or 0) == available_mana,
                    int(combo.get("total_cost") or 0),
                    len(combo.get("actions") or []),
                ),
            )
            newly_covered = ActionPlanner._combination_sources(best) & uncovered_sources
            if not newly_covered:
                break
            ordered.append(best)
            remaining.remove(best)
            uncovered_sources -= newly_covered

        remaining.sort(
            key=lambda combo: (
                int(combo.get("total_cost") or 0) == available_mana,
                int(combo.get("total_cost") or 0),
                len(combo.get("actions") or []),
            ),
            reverse=True,
        )
        ordered.extend(remaining)
        delayed_combos.sort(
            key=lambda combo: (
                int(combo.get("total_cost") or 0) == available_mana,
                int(combo.get("total_cost") or 0),
                len(combo.get("actions") or []),
            ),
            reverse=True,
        )
        ordered.extend(delayed_combos)
        return ordered

    @staticmethod
    def _combination_sources(combo: dict[str, Any]) -> set[Any]:
        return {
            action.get("source")
            for action in combo.get("actions") or []
            if action.get("source") is not None
        }

    @staticmethod
    def _uses_low_impact_temporary_mana(combo: dict[str, Any]) -> bool:
        actions = [
            action for action in combo.get("actions") or []
            if isinstance(action, dict)
        ]
        if not any(int(action.get("mana_gain") or 0) > 0 for action in actions):
            return False
        return not ActionPlanner._high_impact_cards(actions)

    def _legal_actions(
        self,
        state: dict[str, Any],
        playable_cards: list[dict[str, Any]],
        tradeable_cards: list[dict[str, Any]],
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
            "trade_card": list(tradeable_cards),
            "minion_attack": minion_attacks,
            "hero_attack": hero_attacks,
            "hero_power": [hero_power] if hero_power else [],
            "activate_ability": [],
            "end_turn": [{"type": "end_turn"}],
        }

    def _legal_sequences(
        self,
        state: dict[str, Any],
        available_mana: int,
        card_combinations: list[dict[str, Any]],
        legal_actions: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        sequences: list[dict[str, Any]] = []
        sequence_index = 0
        attack_plans = self._attack_plans(legal_actions)
        limit_without_end_turn = max(self._max_sequences_for_prompt - 1, 0)
        trade_actions = list(legal_actions.get("trade_card") or [])
        trade_budget = min(len(trade_actions), 2)
        alternate_attack_budget = 1 if len(attack_plans) > 1 else 0
        no_card_budget = 1
        card_budget = max(
            limit_without_end_turn
            - trade_budget
            - alternate_attack_budget
            - no_card_budget,
            0,
        )
        selected_card_plans: list[tuple[list[dict[str, Any]], int]] = []

        for combo in card_combinations[:card_budget]:
            card_actions = [self._public_card_action(action) for action in combo["actions"]]
            selected_card_plans.append((card_actions, int(combo["remaining_mana"])))
            sequences.append(self._build_sequence(
                state,
                f"seq-{sequence_index:03d}",
                int(combo["remaining_mana"]),
                card_actions,
                legal_actions,
                attack_plans[0],
            ))
            sequence_index += 1

        for trade_action in trade_actions[:trade_budget]:
            sequences.append({
                "type": "sequence",
                "sequence_id": f"seq-trade-{sequence_index:03d}",
                "total_cost": 1,
                "remaining_mana": available_mana - 1,
                "actions": [trade_action],
                "heuristics": {
                    "enemy_hero_damage": 0,
                    "spends_all_mana": available_mana == 1,
                    "protects_my_hero": False,
                },
            })
            sequence_index += 1

        if len(sequences) < limit_without_end_turn:
            sequences.append(self._build_sequence(
                state,
                f"seq-{sequence_index:03d}",
                available_mana,
                [],
                legal_actions,
                attack_plans[0],
            ))
            sequence_index += 1

        if alternate_attack_budget and len(sequences) < limit_without_end_turn:
            alternative_sources = selected_card_plans or [([], available_mana)]
            card_actions, remaining_mana = alternative_sources[0]
            sequences.append(self._build_sequence(
                state,
                f"seq-{sequence_index:03d}",
                remaining_mana,
                card_actions,
                legal_actions,
                attack_plans[1],
            ))

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
        state: dict[str, Any],
        sequence_id: str,
        remaining_mana: int,
        card_actions: list[dict[str, Any]],
        legal_actions: dict[str, list[dict[str, Any]]],
        attack_plan: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actions = list(card_actions)
        actions.extend(attack_plan)
        actions.extend(self._post_weapon_attacks(state, card_actions))
        if remaining_mana >= 2 and not self._sequence_equips_weapon(card_actions):
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
        final_remaining_mana = remaining_mana - hero_power_cost
        return {
            "type": "sequence",
            "sequence_id": sequence_id,
            "total_cost": total_cost,
            "remaining_mana": final_remaining_mana,
            "actions": actions,
            "heuristics": {
                "enemy_hero_damage": enemy_hero_damage,
                "spends_all_mana": final_remaining_mana == 0,
                "protects_my_hero": False,
                "uses_temporary_mana": any(int(action.get("mana_gain") or 0) > 0 for action in card_actions),
                "mana_generated": sum(int(action.get("mana_gain") or 0) for action in card_actions),
                "high_impact_cards": self._high_impact_cards(card_actions),
            },
        }

    def _attack_plans(self, legal_actions: dict[str, list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
        attacks = list(legal_actions.get("minion_attack") or [])
        attacks.extend(legal_actions.get("hero_attack") or [])
        if not attacks:
            return [[]]

        by_source: dict[Any, list[dict[str, Any]]] = {}
        for attack in attacks:
            by_source.setdefault(attack.get("source"), []).append(attack)

        preferred_plan: list[dict[str, Any]] = []
        for source_attacks in by_source.values():
            preferred_plan.append(self._preferred_attack(source_attacks))

        plans = [preferred_plan]
        for source_index, source_attacks in enumerate(by_source.values()):
            preferred = preferred_plan[source_index]
            for attack in source_attacks:
                if attack == preferred:
                    continue
                alternative = list(preferred_plan)
                alternative[source_index] = attack
                plans.append(alternative)
                if len(plans) >= self._max_attack_plans:
                    return self._dedupe_attack_plans(plans)
        return self._dedupe_attack_plans(plans)

    @staticmethod
    def _preferred_attack(source_attacks: list[dict[str, Any]]) -> dict[str, Any]:
        face_attacks = [attack for attack in source_attacks if attack.get("target") == "enemy_hero"]
        if face_attacks:
            return face_attacks[0]
        return source_attacks[0]

    @staticmethod
    def _dedupe_attack_plans(plans: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
        deduped: list[list[dict[str, Any]]] = []
        seen: set[tuple[tuple[Any, Any], ...]] = set()
        for plan in plans:
            key = tuple((attack.get("source"), attack.get("target")) for attack in plan)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(plan)
        return deduped

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

    @classmethod
    def _temporary_mana_available(cls, state: dict[str, Any]) -> int:
        return sum(cls._temporary_mana_gain(card) for card in state.get("hand") or [])

    @staticmethod
    def _ordered_actions_for_mana(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(actions, key=lambda action: int(action.get("mana_gain") or 0), reverse=True)

    @staticmethod
    def _public_card_action(action: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "play_card",
            "source": action.get("source"),
            "card_id": action.get("card_id"),
            "name": action.get("name"),
            "cost": action.get("cost"),
            "card_type": action.get("card_type"),
            "weapon_attack": action.get("weapon_attack"),
            "weapon_durability": action.get("weapon_durability"),
            "target_required": action.get("target_required", False),
            "possible_targets": list(action.get("possible_targets") or []),
            "effect": dict(action.get("effect") or {}),
            "mana_gain": action.get("mana_gain"),
            "setup_only": action.get("setup_only"),
            "priority": action.get("priority", "normal"),
        }

    @staticmethod
    def _effect_payload(card: dict[str, Any], effect: Any) -> dict[str, Any]:
        if ActionPlanner._is_weapon_card(card):
            return {
                "kind": "weapon",
                "attack": card.get("attack"),
                "durability": card.get("durability") or card.get("health"),
                "raw_text": effect.raw_text,
            }
        temporary_mana = ActionPlanner._temporary_mana_gain(card)
        if temporary_mana:
            return {
                "kind": "temporary_mana",
                "mana_gain": temporary_mana,
                "raw_text": effect.raw_text,
            }
        return {
            "kind": effect.kind,
            "damage": effect.damage,
            "attack_buff": effect.attack_buff,
            "health_buff": effect.health_buff,
            "spell_damage": effect.spell_damage,
            "aura_attack_buff": effect.aura_attack_buff,
            "can_target_enemy_hero": effect.can_target_enemy_hero,
            "raw_text": effect.raw_text,
        }

    @staticmethod
    def _is_weapon_card(card: dict[str, Any]) -> bool:
        return str(card.get("type") or "").upper() == "WEAPON"

    @staticmethod
    def _has_card_identity(card: dict[str, Any]) -> bool:
        return bool(card.get("card_id") or card.get("name"))

    @classmethod
    def _setup_cards_have_payoff(cls, actions: tuple[dict[str, Any], ...]) -> bool:
        if not any(action.get("setup_only") for action in actions):
            return True
        return any(cls._is_setup_payoff_action(action) for action in actions)

    @staticmethod
    def _is_setup_payoff_action(action: dict[str, Any]) -> bool:
        if action.get("setup_only"):
            return False
        if int(action.get("mana_gain") or 0) > 0:
            return False
        if str(action.get("card_type") or "").upper() != "SPELL":
            return False
        return True

    @staticmethod
    def _is_setup_only_card(card: dict[str, Any]) -> bool:
        text = CardEffectRecognizer._normalize_text(str(card.get("text") or "")).lower()
        if "next spell" in text:
            return True
        return "下一个法术" in text or "下一张法术" in text

    @staticmethod
    def _temporary_mana_gain(card: dict[str, Any]) -> int:
        card_id = str(card.get("card_id") or "").upper()
        name = str(card.get("name") or "").strip().lower()
        text = CardEffectRecognizer._normalize_text(str(card.get("text") or "")).lower()
        if card_id in {"GAME_005", "CORE_GAME_005"}:
            return 1
        if "COIN" in card_id and "coin" in name:
            return 1
        if name in {"coin", "the coin", "幸运币"}:
            return 1
        if "gain 1 mana crystal" in text or "gain a mana crystal" in text:
            return 1
        if "获得一个" in text and "法力水晶" in text:
            return 1
        return 0

    @staticmethod
    def _is_tradeable_card(card: dict[str, Any]) -> bool:
        mechanics = {str(value).upper() for value in card.get("mechanics") or []}
        text = CardEffectRecognizer._normalize_text(str(card.get("text") or ""))
        return "TRADEABLE" in mechanics or "可交易" in text or "Tradeable" in text

    @classmethod
    def _sequence_equips_weapon(cls, card_actions: list[dict[str, Any]]) -> bool:
        return any(action.get("card_type") == "WEAPON" for action in card_actions)

    @classmethod
    def _post_weapon_attacks(
        cls,
        state: dict[str, Any],
        card_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        weapon_actions = [action for action in card_actions if action.get("card_type") == "WEAPON"]
        if not weapon_actions:
            return []

        hero = state.get("my_hero") or {}
        if (
            int(hero.get("attacks_this_turn") or 0)
            >= int(hero.get("max_attacks_per_turn") or 1)
        ):
            return []
        if hero.get("frozen") or hero.get("cant_attack") or hero.get("exhausted"):
            return []

        weapon = weapon_actions[-1]
        attack = int(weapon.get("weapon_attack") or 0)
        if attack <= 0:
            return []

        attacks: list[dict[str, Any]] = []
        enemy_targets = [
            target for target in state.get("enemy_board") or []
            if not target.get("stealth") and not target.get("dormant") and not target.get("immune")
        ]
        taunts = [target for target in enemy_targets if target.get("taunt")]
        if taunts:
            enemy_targets = taunts
            include_hero = False
        else:
            enemy_hero = state.get("enemy_hero") or {}
            include_hero = not enemy_hero.get("immune")

        for target in enemy_targets:
            attacks.append({
                "type": "hero_attack",
                "source": "my_hero",
                "target": target.get("entity_id"),
                "target_type": "minion",
                "damage": attack,
                "self_damage_risk": int(target.get("attack") or 0),
                "from_played_weapon": weapon.get("card_id"),
            })
        if include_hero:
            attacks.insert(0, {
                "type": "hero_attack",
                "source": "my_hero",
                "target": "enemy_hero",
                "target_type": "hero",
                "damage": attack,
                "self_damage_risk": 0,
                "from_played_weapon": weapon.get("card_id"),
            })
        return attacks[:1]

    @staticmethod
    def _high_impact_cards(card_actions: list[dict[str, Any]]) -> list[str]:
        cards: list[str] = []
        for action in card_actions:
            if action.get("type") != "play_card":
                continue
            cost = int(action.get("cost") or 0)
            effect = action.get("effect") if isinstance(action.get("effect"), dict) else {}
            raw_text = CardEffectRecognizer._normalize_text(str(effect.get("raw_text") or ""))
            name = action.get("name")
            if not name:
                continue
            if cost >= 8:
                cards.append(str(name))
                continue
            if "兆示" in raw_text or "Battlecry" in raw_text or "战吼" in raw_text:
                if cost >= 6:
                    cards.append(str(name))
        return cards

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
