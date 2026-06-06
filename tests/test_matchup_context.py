import json
import tempfile
import unittest
from pathlib import Path

from ai_backend.ai_decision.matchup_context import MatchupContextBuilder
from ai_backend.ai_decision.prompt import DecisionPromptBuilder


HUNTER_ARCHETYPES = {
    "HUNTER": [
        {
            "name": "Companion Hunter",
            "style": "tempo",
            "base_confidence": 0.48,
            "signals": ["Companion", "Beast"],
            "win_condition": "Use early Hunter pressure, then let the companion package take over longer games.",
            "core_cards": [
                {
                    "name": "Animal Companion",
                    "role": "companion payoff and board pressure",
                    "play_timing": "Play on curve when board is not already lost.",
                    "keep_condition": "Keep with early curve or companion support.",
                    "counter_priority": "Remove companion bodies before buffs or repeat pressure compound.",
                }
            ],
            "game_plan": "Fight for board while tracking burst damage.",
            "sources": ["test-source"],
        },
        {
            "name": "Aggro Hunter",
            "style": "aggro",
            "base_confidence": 0.40,
            "signals": ["Arcane Shot", "cheap", "Beast"],
            "win_condition": "Spend mana efficiently and convert early damage into lethal pressure.",
            "core_cards": [
                {
                    "name": "cheap damage package",
                    "role": "early pressure and reach",
                    "play_timing": "Develop or send damage whenever it advances lethal.",
                    "keep_condition": "Keep cheap pressure cards.",
                }
            ],
            "game_plan": "Protect health and remove repeat damage first.",
            "sources": ["test-source"],
        },
    ]
}


class MatchupContextBuilderTests(unittest.TestCase):
    def test_uses_injected_external_archetypes(self):
        context = MatchupContextBuilder(HUNTER_ARCHETYPES).build({
            "turn": 2,
            "my_hero": {"class": "MAGE", "hp": 27},
            "enemy_hero": {"class": "HUNTER", "hp": 30},
            "enemy_board": [],
            "known_enemy_cards": [],
            "recent_events": [],
        })

        names = [item["name"] for item in context["possible_enemy_archetypes"]]

        self.assertEqual(["Companion Hunter", "Aggro Hunter"], names)
        self.assertEqual("HUNTER", context["enemy_class"])
        self.assertEqual("external_file_only", context["meta_source"]["mode"])
        self.assertEqual(
            "Use early Hunter pressure, then let the companion package take over longer games.",
            context["possible_enemy_archetypes"][0]["win_condition"],
        )
        self.assertEqual(
            "Animal Companion",
            context["possible_enemy_archetypes"][0]["core_cards"][0]["name"],
        )
        self.assertTrue(context["usage_rules"])

    def test_visible_early_pressure_raises_aggro_confidence(self):
        context = MatchupContextBuilder(HUNTER_ARCHETYPES).build({
            "turn": 2,
            "my_hero": {"class": "MAGE", "hp": 24},
            "enemy_hero": {"class": "HUNTER", "hp": 30},
            "enemy_board": [
                {"entity_id": 11, "name": "Cheap Beast", "cost": 1, "text": "Beast"},
                {"entity_id": 12, "name": "Attacking Minion", "cost": 2},
            ],
            "known_enemy_cards": [
                {"card_id": "TEST_FAST", "name": "Arcane Shot", "cost": 1, "text": "Deal 2 damage."}
            ],
            "recent_events": [],
        })

        aggro = next(item for item in context["possible_enemy_archetypes"] if item["name"] == "Aggro Hunter")

        self.assertGreaterEqual(aggro["confidence"], 0.58)
        self.assertIn("early_pressure_seen", aggro["evidence"])

    def test_from_default_sources_loads_external_meta_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            deck_dir = root / "hearthstone_data" / "decks"
            deck_dir.mkdir(parents=True)
            (deck_dir / "strategy_context.zhCN.json").write_text(
                json.dumps({"deck_archetypes": HUNTER_ARCHETYPES}, ensure_ascii=False),
                encoding="utf-8",
            )

            context = MatchupContextBuilder.from_default_sources(root).build({
                "enemy_hero": {"class": "HUNTER"},
                "my_hero": {"class": "MAGE"},
            })

            names = [item["name"] for item in context["possible_enemy_archetypes"]]
            self.assertEqual(["Companion Hunter", "Aggro Hunter"], names)
            self.assertEqual("loaded", context["meta_source"]["status"])
            self.assertTrue(context["meta_source"]["path"].endswith("strategy_context.zhCN.json"))

    def test_missing_external_meta_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = MatchupContextBuilder.from_default_sources(Path(temp_dir)).build({
                "enemy_hero": {"class": "HUNTER"},
                "my_hero": {"class": "MAGE"},
            })

            self.assertEqual([], context["possible_enemy_archetypes"])
            self.assertEqual("missing", context["meta_source"]["status"])

    def test_prompt_includes_matchup_context(self):
        prompt = DecisionPromptBuilder(MatchupContextBuilder(HUNTER_ARCHETYPES)).build({
            "turn": 2,
            "active_player": "me",
            "my_mana": {"current": 2, "max": 2},
            "my_hero": {"class": "MAGE", "hp": 27},
            "enemy_hero": {"class": "HUNTER", "hp": 30},
            "hand": [],
            "my_board": [],
            "enemy_board": [],
            "known_enemy_cards": [],
            "recent_events": [],
        }, {
            "available_mana": 2,
            "legal_sequences": [{"sequence_id": "seq-000", "actions": [{"type": "end_turn"}]}],
            "legal_actions": {},
        })
        payload = json.loads(prompt["user"])

        self.assertIn("matchup_context", payload)
        self.assertEqual("HUNTER", payload["matchup_context"]["enemy_class"])
        self.assertEqual("unconfirmed", payload["matchup_context"]["enemy_deck_status"])
        self.assertNotIn("identified_enemy_deck", payload["matchup_context"])
        self.assertEqual("Companion Hunter", payload["matchup_context"]["backup_enemy_deck"]["name"])
        self.assertNotIn("possible_enemy_archetypes", payload["matchup_context"])
        self.assertEqual(
            "Animal Companion",
            payload["matchup_context"]["backup_enemy_deck"]["core_cards"][0]["name"],
        )

    def test_prompt_adds_strong_rule_for_high_confidence_matchup(self):
        prompt = DecisionPromptBuilder(MatchupContextBuilder(HUNTER_ARCHETYPES)).build({
            "turn": 2,
            "active_player": "me",
            "my_mana": {"current": 2, "max": 2},
            "my_hero": {"class": "ROGUE", "hp": 27},
            "enemy_hero": {"class": "HUNTER", "hp": 30},
            "hand": [],
            "my_board": [],
            "enemy_board": [{"entity_id": 7, "name": "Beast Companion", "cost": 1, "text": "Beast Companion"}],
            "known_enemy_cards": [
                {"card_id": "MEND_300", "name": "Companion Setup", "text": "Companion Beast"},
                {"card_id": "MEND_301", "name": "Companion Caller", "text": "Summon a Companion Beast"},
            ],
            "recent_events": [
                {"player": "opponent", "card_id": "CORE_NEW1_031", "name": "Animal Companion", "text": "Companion Beast"},
            ],
        }, {
            "available_mana": 2,
            "legal_sequences": [{"sequence_id": "seq-000", "actions": [{"type": "end_turn"}]}],
            "legal_actions": {},
        })
        payload = json.loads(prompt["user"])

        self.assertEqual(0.9, payload["matchup_context"]["identified_enemy_deck"]["confidence"])
        self.assertIn("high_confidence_matchup_rule", payload)
        self.assertIn("Companion Hunter", payload["high_confidence_matchup_rule"])
        self.assertIn("Animal Companion", payload["high_confidence_matchup_rule"])


if __name__ == "__main__":
    unittest.main()
