import unittest

from ai_backend.coach.combat_analyzer import CombatAnalyzer
from ai_backend.coach.lethal_checker import LethalChecker
from ai_backend.coach.recommendation_engine import RecommendationEngine


class CombatAnalyzerTests(unittest.TestCase):
    def test_taunt_blocks_attacks_to_enemy_hero(self):
        state = sample_state(
            enemy_hero={"hp": 10, "immune": False},
            my_board=[minion(1, attack=3, health=2, can_attack=True)],
            enemy_board=[minion(2, attack=1, health=4, taunt=True)],
        )

        attacks = CombatAnalyzer().legal_attacks(state)

        self.assertEqual(
            [{"source": 1, "target": 2, "target_type": "minion", "damage": 3}],
            attacks,
        )

    def test_can_attack_enemy_hero_when_no_taunt_exists(self):
        state = sample_state(
            enemy_hero={"hp": 10, "immune": False},
            my_board=[minion(1, attack=3, health=2, can_attack=True)],
            enemy_board=[minion(2, attack=1, health=4)],
        )

        attacks = CombatAnalyzer().legal_attacks(state)

        self.assertIn(
            {"source": 1, "target": "enemy_hero", "target_type": "hero", "damage": 3},
            attacks,
        )

    def test_attack_limit_replaces_can_attack_and_attacks_remaining(self):
        ready = minion(1, attack=3, health=2)
        ready["attacks_this_turn"] = 1
        ready["max_attacks_per_turn"] = 2
        ready["exhausted"] = False
        exhausted = minion(2, attack=4, health=4)
        exhausted["attacks_this_turn"] = 1
        exhausted["max_attacks_per_turn"] = 1
        state = sample_state(
            enemy_hero={"hp": 10, "immune": False},
            my_board=[ready, exhausted],
            enemy_board=[],
        )

        attacks = CombatAnalyzer().legal_attacks(state)

        self.assertEqual({1}, {attack["source"] for attack in attacks})

    def test_attack_limit_or_remaining_attacks_takes_priority_over_stale_exhausted_flag(self):
        ready = minion(1, attack=6, health=4)
        ready["attacks_this_turn"] = 0
        ready["max_attacks_per_turn"] = 2
        ready["attacks_remaining"] = 2
        ready["exhausted"] = True
        ready["can_attack"] = False
        state = sample_state(
            enemy_hero={"hp": 7, "immune": False},
            my_board=[ready],
            enemy_board=[],
        )

        attacks = CombatAnalyzer().legal_attacks(state)

        self.assertIn(
            {"source": 1, "target": "enemy_hero", "target_type": "hero", "damage": 6},
            attacks,
        )


class LethalCheckerTests(unittest.TestCase):
    def test_finds_attack_lethal_when_face_damage_is_enough(self):
        state = sample_state(
            enemy_hero={"hp": 5, "armor": 0, "immune": False},
            my_board=[
                minion(1, attack=3, health=2, can_attack=True),
                minion(2, attack=2, health=2, can_attack=True),
            ],
            enemy_board=[],
        )

        result = LethalChecker(CombatAnalyzer()).check(state)

        self.assertTrue(result["is_lethal"])
        self.assertEqual(5, result["available_damage"])
        self.assertEqual(
            ["enemy_hero", "enemy_hero"],
            [action["target"] for action in result["actions"]],
        )

    def test_taunt_prevents_attack_lethal(self):
        state = sample_state(
            enemy_hero={"hp": 5, "armor": 0, "immune": False},
            my_board=[minion(1, attack=6, health=2, can_attack=True)],
            enemy_board=[minion(2, attack=1, health=4, taunt=True)],
        )

        result = LethalChecker(CombatAnalyzer()).check(state)

        self.assertFalse(result["is_lethal"])
        self.assertEqual(0, result["available_damage"])


class RecommendationEngineTests(unittest.TestCase):
    def test_returns_action_space_even_when_lethal_exists(self):
        state = sample_state(
            enemy_hero={"hp": 4, "armor": 0, "immune": False},
            my_board=[minion(1, attack=4, health=2, can_attack=True)],
            enemy_board=[minion(2, attack=8, health=8)],
        )

        recommendation = RecommendationEngine().recommend(state)

        self.assertEqual("action_space", recommendation["plan"])
        self.assertEqual([], recommendation["actions"])
        self.assertEqual(0.0, recommendation["confidence"])
        self.assertEqual("ai", recommendation["details"]["decision_owner"])
        action_space = recommendation["details"]["action_space"]
        self.assertIn("legal_actions", action_space)
        self.assertIn("legal_sequences", action_space)
        self.assertIn(
            {"source": 1, "target": "enemy_hero", "target_type": "hero", "damage": 4},
            action_space["legal_attacks"],
        )

    def test_returns_action_space_when_taunt_exists_without_clear_recommendation(self):
        state = sample_state(
            enemy_hero={"hp": 20, "armor": 0, "immune": False},
            my_board=[minion(1, attack=3, health=5, can_attack=True)],
            enemy_board=[minion(2, attack=2, health=3, taunt=True)],
        )

        recommendation = RecommendationEngine().recommend(state)

        self.assertEqual("action_space", recommendation["plan"])
        self.assertEqual([], recommendation["actions"])
        minion_attacks = recommendation["details"]["action_space"]["legal_actions"]["minion_attack"]
        self.assertEqual([2], [action["target"] for action in minion_attacks])


def sample_state(enemy_hero, my_board, enemy_board):
    return {
        "turn": 6,
        "my_mana": {"current": 0, "max": 0},
        "my_hero": {
            "class": "MAGE",
            "hp": 20,
            "armor": 0,
            "attack": 0,
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 1,
            "frozen": False,
        },
        "enemy_hero": enemy_hero,
        "my_board": my_board,
        "enemy_board": enemy_board,
        "hand": [],
    }


def minion(entity_id, attack, health, can_attack=False, taunt=False):
    return {
        "entity_id": entity_id,
        "attack": attack,
        "health": health,
        "damage": 0,
        "attacks_this_turn": 0 if can_attack else 1,
        "max_attacks_per_turn": 1,
        "exhausted": not can_attack,
        "taunt": taunt,
        "stealth": False,
        "immune": False,
        "frozen": False,
        "dormant": False,
        "divine_shield": False,
        "poisonous": False,
        "venomous": False,
    }


if __name__ == "__main__":
    unittest.main()
