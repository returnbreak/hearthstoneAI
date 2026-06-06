import json
import tempfile
import unittest
from pathlib import Path

from ai_backend.ai_decision.deck_strategy_context import DeckStrategyContextBuilder
from ai_backend.ai_decision.prompt import DecisionPromptBuilder


QUEST_MAGE = {
    "name": "任务发现法",
    "class": "MAGE",
    "format": "standard",
    "style": "quest_value",
    "deck_names": ["任务发现法", "Quest Discover Mage"],
    "win_condition": "完成任务后依靠持续发现和高质量资源取得优势。",
    "signature_cards": ["TLC_460", "TLC_461", "CORE_BAR_541"],
    "burst_exception": False,
    "core_cards": [
        {
            "card_id": "TLC_460",
            "name": "禁忌序列",
            "role": "任务与核心赢法",
            "play_timing": "通常第一回合尽早打出，开始累计发现进度。",
            "keep_condition": "起手必留。",
        }
    ],
}

OMEN_ROGUE = {
    "name": "兆示贼",
    "aliases": ["Omen Rogue"],
    "class": "ROGUE",
    "format": "standard",
    "style": "resource_tempo",
    "win_condition": "用兆示牌制造跨职业法术资源，前期能打资源牌就打，中后期依靠减费法术和潜行随从滚雪球。",
    "signature_cards": ["CATA_158", "CATA_785", "TLC_522"],
    "burst_exception": False,
    "core_cards": [
        {
            "card_id": "CATA_158",
            "name": "癫狂的追随者",
            "role": "兆示资源启动牌",
            "play_timing": "前期能安全站场时优先打出，开始积累跨职业法术资源。",
            "keep_condition": "起手可留。",
        }
    ],
}


def metadata(name="任务发现法", cards=None):
    return {
        "type": "game_metadata",
        "game_id": "match-1",
        "deck": {
            "deck_available": True,
            "deck_id": "deck-1",
            "name": name,
            "player_class": "MAGE",
            "format": "standard",
            "cards": cards or [
                {"card_id": "TLC_460", "name": "禁忌序列", "count": 1, "text": "任务：发现7张牌。"},
                {"card_id": "TLC_461", "name": "拾荒清道夫", "count": 2, "text": "发现一张牌。"},
                {"card_id": "CORE_BAR_541", "name": "符文宝珠", "count": 2, "text": "造成2点伤害。发现一张法术牌。"},
            ],
        },
    }


class DeckStrategyContextBuilderTests(unittest.TestCase):
    def test_exact_deck_name_match_includes_win_condition_and_core_timing(self):
        context = DeckStrategyContextBuilder([QUEST_MAGE]).build({
            "game_metadata": metadata(),
        })

        self.assertEqual("matched", context["status"])
        self.assertEqual("exact_name", context["match_method"])
        self.assertEqual("任务发现法", context["strategy"]["name"])
        self.assertEqual(QUEST_MAGE["win_condition"], context["strategy"]["win_condition"])
        self.assertEqual(
            "通常第一回合尽早打出，开始累计发现进度。",
            context["strategy"]["core_cards"][0]["play_timing"],
        )

    def test_signature_cards_match_when_deck_name_is_custom(self):
        context = DeckStrategyContextBuilder([QUEST_MAGE]).build({
            "game_metadata": metadata(name="我的自定义套牌"),
        })

        self.assertEqual("matched", context["status"])
        self.assertEqual("signature_cards", context["match_method"])
        self.assertGreaterEqual(context["confidence"], 0.8)

    def test_unmatched_deck_keeps_full_deck_for_model_analysis(self):
        cards = [
            {"card_id": "OTHER_001", "name": "未知构筑牌", "count": 2, "text": "召唤一个随从。"}
        ]
        context = DeckStrategyContextBuilder([QUEST_MAGE]).build({
            "game_metadata": metadata(name="新套牌", cards=cards),
        })

        self.assertEqual("unmatched", context["status"])
        self.assertEqual(cards, context["actual_deck"]["cards"])
        self.assertTrue(context["analysis_required"])
        self.assertNotIn("strategy", context)

    def test_default_source_loads_external_strategy_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "hearthstone_data" / "decks"
            path.mkdir(parents=True)
            (path / "strategy_context.zhCN.json").write_text(
                json.dumps({"deck_archetypes": {"MAGE": [QUEST_MAGE]}}, ensure_ascii=False),
                encoding="utf-8",
            )

            context = DeckStrategyContextBuilder.from_default_sources(root).build({
                "game_metadata": metadata(),
            })

        self.assertEqual("matched", context["status"])
        self.assertEqual("loaded", context["source_status"])

    def test_visible_own_cards_match_deck_when_metadata_missing(self):
        context = DeckStrategyContextBuilder([OMEN_ROGUE]).build({
            "mode": "standard",
            "my_hero": {"class": "ROGUE"},
            "hand": [
                {"card_id": "CATA_158", "name": "癫狂的追随者", "cost": 3},
                {"card_id": "CATA_785", "name": "暮光祭礼", "cost": 2},
            ],
            "my_board": [],
        })

        self.assertEqual("matched", context["status"])
        self.assertEqual("visible_signature_cards", context["match_method"])
        self.assertEqual("兆示贼", context["strategy"]["name"])

    def test_default_omen_rogue_core_cards_focus_on_omen_payoffs(self):
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "hearthstone_data" / "decks" / "strategy_context.zhCN.json")
            .read_text(encoding="utf-8")
        )
        omen = next(
            deck for deck in data["deck_archetypes"]["ROGUE"]
            if deck["name"] == "Omen Rogue"
        )
        core_ids = {card["card_id"] for card in omen["core_cards"]}

        self.assertIn("CATA_190h", core_ids)
        self.assertIn("CATA_158", core_ids)
        self.assertNotIn("TLC_522", core_ids)
        self.assertNotIn("TLC_100", core_ids)

    def test_prompt_sends_compact_omen_plan_without_signature_noise(self):
        prompt = DecisionPromptBuilder().build({
            "turn": 2,
            "active_player": "me",
            "my_mana": {"current": 2, "max": 2},
            "my_hero": {"class": "ROGUE", "hp": 30},
            "enemy_hero": {"class": "HUNTER", "hp": 30},
            "hand": [],
            "my_board": [],
            "enemy_board": [],
            "game_metadata": {
                "deck": {
                    "deck_available": True,
                    "name": "Omen Rogue",
                    "player_class": "ROGUE",
                    "format": "standard",
                    "cards": [{"card_id": "CATA_158", "name": "癫狂的追随者", "count": 2}],
                }
            },
        }, {
            "available_mana": 2,
            "legal_sequences": [{"sequence_id": "seq-000", "actions": [{"type": "end_turn"}]}],
        })
        payload = json.loads(prompt["user"])
        deck_context = payload["my_deck_context"]
        serialized = json.dumps(deck_context, ensure_ascii=False)
        core_ids = {card["card_id"] for card in deck_context["strategy"]["core_cards"]}

        self.assertEqual("Omen Rogue", deck_context["strategy"]["name"])
        self.assertIn("CATA_190h", core_ids)
        self.assertNotIn("signature_cards", serialized)
        self.assertNotIn("TLC_522", serialized)
        self.assertNotIn("TLC_100", serialized)

    def test_prompt_contains_deck_plan_and_board_first_principle(self):
        prompt = DecisionPromptBuilder(
            deck_strategy_context_builder=DeckStrategyContextBuilder([QUEST_MAGE])
        ).build({
            "turn": 1,
            "active_player": "me",
            "my_mana": {"current": 1, "max": 1},
            "my_hero": {"class": "MAGE", "hp": 30},
            "enemy_hero": {"class": "WARRIOR", "hp": 30},
            "hand": [],
            "my_board": [],
            "enemy_board": [],
            "game_metadata": metadata(),
        }, {
            "available_mana": 1,
            "legal_sequences": [{"sequence_id": "seq-000", "actions": [{"type": "end_turn"}]}],
        })
        payload = json.loads(prompt["user"])

        self.assertEqual("任务发现法", payload["my_deck_context"]["strategy"]["name"])
        self.assertEqual(
            QUEST_MAGE["win_condition"],
            payload["my_deck_context"]["strategy"]["win_condition"],
        )
        self.assertIn("play_timing", payload["my_deck_context"]["strategy"]["core_cards"][0])
        self.assertTrue(any(
            "resource engine" in principle and "payoff setup" in principle
            for principle in payload["principles"]
        ))
        self.assertTrue(any(
            "stolen" in principle and "premium removal" in principle
            for principle in payload["principles"]
        ))
        self.assertTrue(any(
            "temporary mana" in principle and "just to fill mana" in principle
            for principle in payload["principles"]
        ))
        self.assertTrue(any(
            "low-impact Battlecry minion" in principle
            for principle in payload["principles"]
        ))


if __name__ == "__main__":
    unittest.main()
