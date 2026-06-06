"""
AI 决策模块的单元测试
=====================

测试范围:
- AiDecisionService:  决策流程编排逻辑 (接受/拒绝/无状态/不可用)
- API 路由:           HTTP 端点行为和回放日志写入
- 客户端解析:         LangChainDeepSeekDecisionClient 的 JSON 解析能力
- 降级客户端:         无 API Key 时的降级行为

测试策略:
- 使用静态 mock 客户端 (StaticAiClient) 替代真实 AI 调用
- 使用 FakeLangChainModel 模拟 LangChain 的响应格式
- 使用 tempfile 创建临时目录隔离文件系统操作
"""

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# ---- 被测模块 ----
from ai_backend.ai_decision.clients import (
    DEFAULT_DEEPSEEK_MODEL,
    LangChainDeepSeekDecisionClient,
    UnavailableAiDecisionClient,
)
from ai_backend.ai_decision.auto_trigger import TurnStartAiDecisionTrigger, compact_decision
from ai_backend.ai_decision.prompt import DecisionPromptBuilder
from ai_backend.ai_decision.routes import create_ai_decision_router
from ai_backend.ai_decision.service import AiDecisionService
from ai_backend.ingest import routes as ingest_routes

# ---- 依赖模块 ----
from ai_backend.coach.recommendation_engine import RecommendationEngine
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore


# =============================================================================
# AiDecisionService 测试
# =============================================================================

class AiDecisionServiceTests(unittest.TestCase):
    """
    测试 AiDecisionService 的决策流程编排逻辑。

    核心场景:
    1. AI 选择了合法的 sequence_id → 接受并返回操作列表
    2. AI 选择了非法的 sequence_id → 拒绝并返回验证失败信息
    3. 无活跃游戏状态 → 跳过决策, 不调用 AI 客户端
    """

    def test_accepts_ai_choice_when_sequence_id_is_legal(self):
        """
        测试: 当 AI 选择了一个合法的 sequence_id 时, 应当接受该决策。

        验证点:
        - plan 应为 "ai_decision"
        - validation_status 应为 "passed"
        - chosen_sequence_id 应为 AI 选择的 "seq-000"
        - actions 列表不应为空 (合法序列包含具体操作)
        - reason 应包含 AI 返回的战术理由
        """
        # ---- 准备: 创建静态 AI 客户端, 返回预定义的合法选择 ----
        service = AiDecisionService(
            RecommendationEngine(),
            StaticAiClient({
                "chosen_sequence_id": "seq-000",
                "reason": "Attack face while no taunt blocks it.",
                # 理由: 攻击敌方英雄, 因为没有嘲讽随从阻挡
                "risk": "Opponent may have removal next turn.",
                # 风险: 对手下回合可能有解牌
                "confidence": 0.72,
            }),
        )

        # ---- 执行: 调用决策 ----
        decision = service.decide(sample_state())

        # ---- 验证 ----
        self.assertEqual("ai_decision", decision["plan"])
        self.assertEqual("passed", decision["validation"]["validation_status"])
        self.assertEqual("seq-000", decision["chosen_sequence_id"])
        self.assertTrue(decision["actions"])  # 应有具体操作
        self.assertEqual(
            "Attack face while no taunt blocks it.", decision["reason"]
        )

    def test_decision_details_include_matchup_context_for_diagnostics(self):
        service = AiDecisionService(
            RecommendationEngine(),
            StaticAiClient({
                "chosen_sequence_id": "seq-000",
                "reason": "Legal line.",
                "risk": "Low.",
                "confidence": 0.72,
            }),
        )
        state = sample_state()
        state["enemy_hero"] = {"class": "HUNTER", "hp": 30, "armor": 0, "immune": False}
        state["known_enemy_cards"] = [
            {"card_id": "MEND_300", "name": "驯服宠物", "text": "将你此后的动物伙伴替换为随机野兽。"}
        ]

        decision = service.decide(state)

        matchup_context = decision["details"]["matchup_context"]
        self.assertEqual("HUNTER", matchup_context["enemy_class"])
        self.assertEqual("unconfirmed", matchup_context["enemy_deck_status"])
        self.assertNotIn("identified_enemy_deck", matchup_context)
        self.assertIn("backup_enemy_deck", matchup_context)

    def test_decision_details_include_pipeline_timing_and_prompt_size(self):
        service = AiDecisionService(
            RecommendationEngine(),
            StaticAiClient({
                "chosen_sequence_id": "seq-000",
                "reason": "Legal line.",
                "risk": "Low.",
                "confidence": 0.72,
            }),
        )

        decision = service.decide(sample_state())

        timing = decision["details"]["timing"]
        self.assertGreaterEqual(timing["planning_ms"], 0)
        self.assertGreaterEqual(timing["prompt_ms"], 0)
        self.assertGreaterEqual(timing["model_ms"], 0)
        self.assertGreaterEqual(timing["validation_ms"], 0)
        self.assertGreaterEqual(timing["total_ms"], 0)
        self.assertGreater(decision["details"]["prompt_chars"], 0)
        self.assertGreater(decision["details"]["candidate_count"], 0)

    def test_decision_details_include_exact_sanitized_ai_request_for_debugging(self):
        client = StaticAiClient({
            "chosen_sequence_id": "seq-000",
            "reason": "Legal line.",
            "risk": "Low.",
            "confidence": 0.72,
        })
        service = AiDecisionService(RecommendationEngine(), client)

        decision = service.decide(sample_state())

        debug = decision["details"]["ai_debug"]
        self.assertEqual(client.last_prompt["system"], debug["request"]["system_prompt"])
        self.assertNotIn("user_prompt", debug["request"])
        self.assertEqual(
            json.loads(client.last_prompt["user"]),
            debug["request"]["payload"],
        )
        self.assertEqual(client.response, debug["response"]["raw_model_output"])
        self.assertNotIn("raw_model_content", debug["response"])
        self.assertNotIn("DEEPSEEK_API_KEY", json.dumps(debug))

    def test_rejects_ai_choice_when_sequence_id_is_not_legal(self):
        """
        测试: 当 AI 选择了一个不存在的 sequence_id 时, 应当拒绝该决策。

        验证点:
        - plan 应为 "ai_decision_rejected"
        - validation_status 应为 "failed"
        - reason 中应包含 "unknown sequence_id" 提示
        """
        # ---- 准备: AI 返回一个不存在于 action space 中的序列 ID ----
        service = AiDecisionService(
            RecommendationEngine(),
            StaticAiClient({
                "chosen_sequence_id": "seq-missing",  # 不存在的 ID
                "reason": "Invalid choice.",
                "risk": "Invalid choice.",
                "confidence": 0.5,
            }),
        )

        # ---- 执行 ----
        decision = service.decide(sample_state())

        # ---- 验证 ----
        self.assertEqual("ai_decision_rejected", decision["plan"])
        self.assertEqual("failed", decision["validation"]["validation_status"])
        self.assertIn("unknown sequence_id", decision["validation"]["reason"])

    def test_does_not_call_ai_client_without_active_state(self):
        """
        测试: 当没有活跃游戏状态时, 不应调用 AI 客户端。

        这是重要的性能/成本优化:
        - AI API 调用可能产生费用, 不应浪费在没有对局时的无效调用上
        - 验证点: client.calls 应为 0 (说明 AI 客户端从未被调用)
        """
        # ---- 准备: 创建可记录调用次数的 mock 客户端 ----
        client = StaticAiClient({"chosen_sequence_id": "seq-000"})
        service = AiDecisionService(RecommendationEngine(), client)

        # ---- 执行: 传入 None 表示无活跃状态 ----
        decision = service.decide(None)

        # ---- 验证 ----
        self.assertEqual("no_state", decision["plan"])
        self.assertEqual(0, client.calls)  # 关键: 未调用 AI


# =============================================================================
# API 路由测试
# =============================================================================

class AiDecisionRouteTests(unittest.TestCase):
    """
    测试 AI 决策 HTTP 端点的行为。

    核心场景:
    1. 有效的 AI 决策应被写入 recommendations.jsonl 回放日志
    """

    def test_writes_valid_ai_decision_to_recommendation_log(self):
        """
        测试: 有效的 AI 决策被写入回放日志文件。

        验证点:
        - 端点返回 plan == "ai_decision"
        - recommendations.jsonl 文件被创建
        """
        # ---- 准备: 使用临时目录避免污染实际文件系统 ----
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建 StateStore 并注入游戏状态 (模拟 HDT 插件推送)
            store = StateStore()
            store.apply({"type": "game_state", "state": sample_state()})

            # 创建 AI 决策服务 (使用静态 mock 客户端)
            service = AiDecisionService(
                RecommendationEngine(),
                StaticAiClient({
                    "chosen_sequence_id": "seq-000",
                    "reason": "Legal line.",
                    "risk": "Low.",
                    "confidence": 0.8,
                }),
            )

            # 创建路由 (带 ReplayWriter, 指向临时目录)
            router = create_ai_decision_router(
                store, service, ReplayWriter(Path(temp_dir))
            )

            # ---- 执行: 调用异步端点 ----
            # 路由只有一个端点 (POST /api/ai/decision)
            response = asyncio.run(router.routes[0].endpoint())

            # ---- 验证 ----
            self.assertEqual("ai_decision", response["plan"])

            # 验证回放日志文件已创建
            # ReplayWriter 按 game_id 组织目录: {game_id}/recommendations.jsonl
            log_path = Path(temp_dir) / "match-1" / "recommendations.jsonl"
            self.assertTrue(log_path.exists())


# =============================================================================
# 自动回合触发测试
# =============================================================================

class TurnStartAiDecisionTriggerTests(unittest.TestCase):
    def test_does_not_trigger_ai_when_my_turn_state_arrives_without_hand_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = StaticAiClient({
                "chosen_sequence_id": "seq-000",
                "reason": "Legal line.",
                "risk": "Low.",
                "confidence": 0.8,
            })
            trigger = TurnStartAiDecisionTrigger(
                AiDecisionService(RecommendationEngine(), client),
                ReplayWriter(Path(temp_dir)),
            )
            state = sample_state()
            envelope = {"type": "game_state", "state": state}

            first = trigger.maybe_decide(envelope, {"latest_state": state})
            second = trigger.maybe_decide(envelope, {"latest_state": state})

            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertEqual(0, client.calls)
            self.assertFalse((Path(temp_dir) / "match-1" / "recommendations.jsonl").exists())

    def test_compact_decision_omits_duplicate_success_reasons(self):
        compact = compact_decision({
            "plan": "ai_decision",
            "summary": "Play the weapon.",
            "reason": "Play the weapon.",
            "actions": [],
            "validation": {"passed": True, "reason": "Recommendation validated."},
        })

        self.assertNotIn("reason", compact)
        self.assertEqual({"passed": True}, compact["validation"])

    def test_attempt_log_includes_matchup_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = StaticAiClient({
                "chosen_sequence_id": "seq-000",
                "reason": "Legal line.",
                "risk": "Low.",
                "confidence": 0.8,
            })
            trigger = TurnStartAiDecisionTrigger(
                AiDecisionService(RecommendationEngine(), client),
                ReplayWriter(Path(temp_dir)),
            )
            state = sample_state()
            state["turn"] = 5
            state["hand"] = []
            state["my_mana"] = {"current": 5, "max": 5}
            state["enemy_mana"] = {"current": 0, "max": 5}
            state["enemy_hero"] = {"class": "HUNTER", "hp": 30, "armor": 0, "immune": False}
            state["known_enemy_cards"] = [
                {"card_id": "MEND_300", "name": "驯服宠物", "text": "将你此后的动物伙伴替换为随机野兽。"}
            ]
            drawn_state = dict(state)
            drawn_state["hand"] = [
                {"entity_id": 10, "card_id": "CARD_10", "name": "Drawn Card", "cost": 1}
            ]

            first = trigger.reserve_state(
                {"type": "game_state", "state": state},
                {"latest_state": state},
            )
            reserved = trigger.reserve_state(
                {"type": "game_state", "state": drawn_state},
                {"latest_state": drawn_state},
            )
            self.assertIsNone(first)
            decision = trigger.decide_reserved_state(reserved)

            self.assertEqual("ai_decision", decision["plan"])
            match_dir = Path(temp_dir) / "match-1"
            self.assertFalse((match_dir / "ai_decision_attempts.jsonl").exists())
            debug_files = list((match_dir / "debug" / "ai_requests").glob("*.json"))
            self.assertEqual(1, len(debug_files))

            attempt = json.loads(debug_files[0].read_text(encoding="utf-8"))
            self.assertIn("Companion Hunter", json.dumps(attempt, ensure_ascii=False))
            self.assertIn("timing", attempt["diagnostics"])
            self.assertGreater(attempt["diagnostics"]["prompt_chars"], 0)
            self.assertGreater(attempt["diagnostics"]["candidate_count"], 0)

    def test_skips_enemy_turn_state(self):
        client = StaticAiClient({"chosen_sequence_id": "seq-000"})
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), client)
        )
        state = sample_state()
        state["active_player"] = "opponent"

        decision = trigger.maybe_decide({"type": "game_state", "state": state}, {"latest_state": state})

        self.assertIsNone(decision)
        self.assertEqual(0, client.calls)

    def test_does_not_trigger_when_mana_refreshes_at_turn_start(self):
        client = StaticAiClient({
            "chosen_sequence_id": "seq-000",
            "reason": "Legal line.",
            "risk": "Low.",
            "confidence": 0.8,
        })
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), client)
        )
        stale_state = sample_state()
        stale_state["turn"] = 8
        stale_state["my_mana"] = {"current": 0, "max": 8}
        refreshed_state = sample_state()
        refreshed_state["turn"] = 8
        refreshed_state["my_mana"] = {"current": 9, "max": 9}

        stale_decision = trigger.maybe_decide(
            {"type": "game_state", "state": stale_state},
            {"latest_state": stale_state},
        )
        refreshed_decision = trigger.maybe_decide(
            {"type": "game_state", "state": refreshed_state},
            {"latest_state": refreshed_state},
        )

        self.assertIsNone(stale_decision)
        self.assertIsNone(refreshed_decision)
        self.assertEqual(0, client.calls)

    def test_retriggers_every_time_hand_increases_during_my_turn(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"}))
        )
        first_state = sample_state()
        first_state["turn"] = 8
        first_state["hand"] = []
        updated_state = sample_state()
        updated_state["turn"] = 8
        updated_state["hand"] = [
            {"entity_id": 10, "card_id": "CARD_10", "name": "Drawn Card", "cost": 1}
        ]

        first = trigger.reserve_state(
            {"type": "game_state", "state": first_state},
            {"latest_state": first_state},
        )
        second = trigger.reserve_state(
            {"type": "game_state", "state": updated_state},
            {"latest_state": updated_state},
        )

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual("hand_increased", second["_ai_trigger_kind"])

        third_state = sample_state()
        third_state["turn"] = 8
        third_state["hand"] = [
            {"entity_id": 10, "card_id": "CARD_10", "name": "Drawn Card", "cost": 1},
            {"entity_id": 11, "card_id": "CARD_11", "name": "Another Card", "cost": 2},
        ]
        third = trigger.reserve_state(
            {"type": "game_state", "state": third_state},
            {"latest_state": third_state},
        )

        self.assertIsNotNone(third)
        self.assertEqual("hand_increased", third["_ai_trigger_kind"])

    def test_triggers_after_own_turn_draw_from_previous_opponent_hand_count(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"}))
        )
        opponent_state = sample_state()
        opponent_state["active_player"] = "opponent"
        opponent_state["turn"] = 7
        opponent_state["hand"] = [
            {"entity_id": 10, "card_id": "CARD_10", "name": "Existing Card", "cost": 1}
        ]
        my_turn_after_draw = sample_state()
        my_turn_after_draw["turn"] = 8
        my_turn_after_draw["hand"] = [
            {"entity_id": 10, "card_id": "CARD_10", "name": "Existing Card", "cost": 1},
            {"entity_id": 11, "card_id": "CARD_11", "name": "Drawn Card", "cost": 2},
        ]

        opponent = trigger.reserve_state(
            {"type": "game_state", "state": opponent_state},
            {"latest_state": opponent_state},
        )
        own_turn = trigger.reserve_state(
            {"type": "game_state", "state": my_turn_after_draw},
            {"latest_state": my_turn_after_draw},
        )

        self.assertIsNone(opponent)
        self.assertIsNotNone(own_turn)
        self.assertEqual("hand_increased", own_turn["_ai_trigger_kind"])

    def test_does_not_prewarm_when_opponent_spent_out(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"}))
        )
        state = sample_state()
        state["active_player"] = "opponent"
        state["turn"] = 8
        state["my_mana"] = {"current": 0, "max": 7}
        state["enemy_mana"] = {"current": 0, "max": 8}
        state["enemy_board"] = []
        state["enemy_hero"] = {
            "class": "HUNTER",
            "attack": 0,
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 1,
        }

        prewarm = trigger.reserve_prewarm_state(
            {"type": "game_state", "state": state},
            {"latest_state": state},
        )

        self.assertIsNone(prewarm)

    def test_does_not_prewarm_when_disabled(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"})),
            enable_prewarm=False,
        )
        state = sample_state()
        state["active_player"] = "opponent"
        state["turn"] = 8
        state["my_mana"] = {"current": 0, "max": 7}
        state["enemy_mana"] = {"current": 0, "max": 8}
        state["enemy_board"] = []
        state["enemy_hero"] = {
            "class": "HUNTER",
            "attack": 0,
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 1,
        }

        prewarm = trigger.reserve_prewarm_state(
            {"type": "game_state", "state": state},
            {"latest_state": state},
        )

        self.assertIsNone(prewarm)

    def test_opponent_spent_out_does_not_reserve_or_skip_later_own_turn_start(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"}))
        )
        opponent_state = sample_state()
        opponent_state["active_player"] = "opponent"
        opponent_state["turn"] = 8
        opponent_state["my_mana"] = {"current": 0, "max": 7}
        opponent_state["enemy_mana"] = {"current": 0, "max": 8}
        opponent_state["enemy_board"] = []
        opponent_state["enemy_hero"] = {
            "class": "HUNTER",
            "attack": 0,
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 1,
        }
        own_turn_state = sample_state()
        own_turn_state["turn"] = 9
        own_turn_state["my_mana"] = {"current": 8, "max": 8}

        prewarm = trigger.reserve_prewarm_state(
            {"type": "game_state", "state": opponent_state},
            {"latest_state": opponent_state},
        )
        own_turn = trigger.reserve_state(
            {"type": "game_state", "state": own_turn_state},
            {"latest_state": own_turn_state},
        )

        self.assertIsNone(prewarm)
        self.assertIsNone(own_turn)

    def test_does_not_prewarm_while_opponent_still_has_mana(self):
        trigger = TurnStartAiDecisionTrigger(
            AiDecisionService(RecommendationEngine(), StaticAiClient({"chosen_sequence_id": "seq-000"}))
        )
        state = sample_state()
        state["active_player"] = "opponent"
        state["enemy_mana"] = {"current": 1, "max": 8}

        prewarm = trigger.reserve_prewarm_state(
            {"type": "game_state", "state": state},
            {"latest_state": state},
        )

        self.assertIsNone(prewarm)


class DecisionPromptBuilderTests(unittest.TestCase):
    def test_prompt_omits_derived_attack_counters_and_compacts_nested_target(self):
        state = sample_state()
        state["my_board"][0].update({
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 2,
            "can_attack": True,
            "attacks_remaining": 2,
        })
        state["recent_events"] = [{
            "type": "card_played",
            "player": "me",
            "card_id": "CORE_SW_108",
            "name": "初始之火",
            "target": {
                "entity_id": 107,
                "card_id": "CORE_BT_480",
                "name": "火色魔印奔行者",
                "type": "minion",
            },
        }]

        prompt = DecisionPromptBuilder().build(
            state,
            {"available_mana": 1, "legal_sequences": []},
        )
        payload = json.loads(prompt["user"])
        minion_payload = payload["game_state"]["my_board"][0]
        event_payload = payload["game_state"]["recent_events"][0]

        self.assertNotIn("can_attack", minion_payload)
        self.assertNotIn("attacks_remaining", minion_payload)
        self.assertNotIn("attacks_this_turn", minion_payload)
        self.assertNotIn("max_attacks_per_turn", minion_payload)
        self.assertEqual("火色魔印奔行者", event_payload["target"]["name"])

    def test_prompt_omits_duplicate_legal_actions_for_speed(self):
        prompt = DecisionPromptBuilder().build(
            sample_state(),
            {
                "available_mana": 2,
                "legal_sequences": [{"sequence_id": "seq-000", "actions": [{"type": "end_turn"}]}],
                "legal_actions": {"end_turn": [{"type": "end_turn"}]},
                "tradeable_cards": [{"card_id": "TRADE"}],
                "board_effects": [],
                "modifiers": {},
            },
        )

        payload = json.loads(prompt["user"])

        self.assertIn("legal_sequences", payload["action_space"])
        self.assertNotIn("legal_actions", payload["action_space"])
        self.assertNotIn("tradeable_cards", payload["action_space"])

    def test_prompt_compacts_runtime_only_data(self):
        state = sample_state()
        state["recent_events"] = [
            {
                "type": "card_played",
                "player": "opponent",
                "card_id": f"CARD_{index}",
                "name": f"Card {index}",
                "timestamp": f"2026-06-05T00:00:{index:02d}Z",
                "diagnostic_blob": "x" * 100,
            }
            for index in range(10)
        ]
        matchup_builder = StaticMatchupContextBuilder({
            "enemy_class": "HUNTER",
            "my_class": "MAGE",
            "possible_enemy_archetypes": [{
                "name": "Fast Hunter",
                "style": "aggro",
                "confidence": 0.8,
                "evidence": ["enemy_class=HUNTER"],
                "game_plan_against_it": "Stabilize.",
                "sources": ["https://example.invalid/source"],
                "source_notes": "diagnostic only",
            }],
            "role_assessment": "Control the board.",
            "usage_rules": ["diagnostic only"],
            "meta_source": {"path": "large-file.json", "required_schema": {"large": True}},
        })
        action_space = {
            "available_mana": 2,
            "legal_sequences": [{
                "type": "sequence",
                "sequence_id": "seq-000",
                "total_cost": 2,
                "remaining_mana": 0,
                "actions": [{
                    "type": "play_card",
                    "source": 10,
                    "card_id": "CARD_10",
                    "name": "Card 10",
                    "cost": 2,
                    "priority": "normal",
                    "possible_targets": ["enemy_hero"],
                    "effect": {
                        "kind": "damage",
                        "damage": 2,
                        "raw_text": "Deal 2 damage.",
                    },
                }],
                "heuristics": {"enemy_hero_damage": 2, "spends_all_mana": True},
            }],
            "board_effects": [],
            "modifiers": {},
        }

        prompt = DecisionPromptBuilder(matchup_builder).build(state, action_space)
        payload = json.loads(prompt["user"])

        self.assertEqual(6, len(payload["game_state"]["recent_events"]))
        self.assertEqual("CARD_4", payload["game_state"]["recent_events"][0]["card_id"])
        self.assertNotIn("diagnostic_blob", payload["game_state"]["recent_events"][0])
        self.assertNotIn("meta_source", payload["matchup_context"])
        self.assertNotIn("usage_rules", payload["matchup_context"])
        archetype = payload["matchup_context"]["identified_enemy_deck"]
        self.assertNotIn("sources", archetype)
        self.assertNotIn("source_notes", archetype)
        action = payload["action_space"]["legal_sequences"][0]["actions"][0]
        self.assertNotIn("priority", action)
        self.assertEqual(["enemy_hero"], action["possible_targets"])
        self.assertNotIn("raw_text", action["effect"])

    def test_prompt_marks_lethal_sequence_ids(self):
        state = sample_state()
        state["enemy_hero"] = {"class": "HUNTER", "hp": 5, "armor": 0}
        prompt = DecisionPromptBuilder().build(
            state,
            {
                "available_mana": 4,
                "legal_sequences": [
                    {
                        "sequence_id": "seq-safe",
                        "actions": [{"type": "end_turn"}],
                        "heuristics": {"enemy_hero_damage": 0},
                    },
                    {
                        "sequence_id": "seq-lethal",
                        "actions": [{"type": "play_card", "name": "Fireball"}],
                        "heuristics": {"enemy_hero_damage": 6},
                    },
                ],
            },
        )

        payload = json.loads(prompt["user"])

        self.assertEqual(["seq-lethal"], payload["action_space"]["lethal_sequence_ids"])
        self.assertTrue(any("lethal_sequence_ids" in principle for principle in payload["principles"]))


# =============================================================================
# LangChainDeepSeekDecisionClient 测试
# =============================================================================

class LangChainDeepSeekDecisionClientTests(unittest.TestCase):
    """
    测试 LangChainDeepSeekDecisionClient 的核心能力。

    核心场景:
    1. JSON 响应的解析 (包括中文内容)
    2. 无 API Key 时的降级行为
    """

    def test_parses_json_response_from_langchain_model(self):
        """
        测试: 正确解析 LangChain 模型返回的 JSON 响应。

        使用 FakeLangChainModel 模拟 LangChain 的 invoke() 行为,
        验证客户端能正确提取 chosen_sequence_id, reason, confidence 等字段。

        特别验证了中文内容的正确处理 (理由、风险包含中文字符)。
        """
        # ---- 准备: 创建客户端, 注入返回 JSON 的假模型 ----
        client = LangChainDeepSeekDecisionClient(
            chat_model=FakeLangChainModel(
                '{"chosen_sequence_id":"seq-001","reason":"理由","risk":"风险","confidence":0.66}'
            )
        )

        # ---- 执行 ----
        response = client.decide({"system": "system", "user": "user"})

        # ---- 验证 ----
        self.assertEqual("seq-001", response["chosen_sequence_id"])
        self.assertEqual("理由", response["reason"])       # 中文理由正确保留
        self.assertEqual(0.66, response["confidence"])     # 浮点数正确解析

    def test_records_exact_messages_sent_to_langchain_model(self):
        model = FakeLangChainModel(
            '{"chosen_sequence_id":"seq-001","reason":"ok","risk":"low","confidence":0.8}'
        )
        client = LangChainDeepSeekDecisionClient(chat_model=model)

        client.decide({"system": "system prompt", "user": "{\"turn\":7}"})

        self.assertEqual(model.messages, client.last_request_debug["messages"])
        self.assertEqual("system prompt", client.last_request_debug["messages"][0][1])
        self.assertIn("Return only valid JSON", client.last_request_debug["messages"][1][1])

    def test_parses_json_response_from_streaming_langchain_model(self):
        client = LangChainDeepSeekDecisionClient(
            chat_model=FakeStreamingLangChainModel([
                '{"chosen_sequence_id":"seq-001",',
                '"reason":"fast",',
                '"risk":"low","confidence":0.9}',
            ]),
            use_streaming=True,
        )

        response = client.decide({"system": "system", "user": "user"})

        self.assertEqual("seq-001", response["chosen_sequence_id"])
        self.assertEqual("fast", response["reason"])
        self.assertEqual(0.9, response["confidence"])

    def test_uses_invoke_by_default_even_when_stream_is_available(self):
        model = FakeInvokeAndStreamingLangChainModel(
            '{"chosen_sequence_id":"seq-001","reason":"fast","risk":"low","confidence":0.9}'
        )
        client = LangChainDeepSeekDecisionClient(chat_model=model)

        response = client.decide({"system": "system", "user": "user"})

        self.assertEqual("seq-001", response["chosen_sequence_id"])
        self.assertEqual(1, model.invoke_calls)
        self.assertEqual(0, model.stream_calls)

    def test_default_model_is_flash(self):
        self.assertEqual("deepseek-v4-flash", DEFAULT_DEEPSEEK_MODEL)

    def test_from_env_configures_fast_non_thinking_json_request(self):
        captured = {}

        class FakeChatDeepSeek:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = types.ModuleType("langchain_deepseek")
        fake_module.ChatDeepSeek = FakeChatDeepSeek

        with patch.dict(sys.modules, {"langchain_deepseek": fake_module}):
            client = LangChainDeepSeekDecisionClient.from_env({
                "DEEPSEEK_API_KEY": "test-key",
            })

        self.assertIsInstance(client, LangChainDeepSeekDecisionClient)
        self.assertEqual(15, captured["timeout"])
        self.assertEqual(384, captured["max_tokens"])
        self.assertEqual({"thinking": {"type": "disabled"}}, captured["extra_body"])
        self.assertEqual(
            {"response_format": {"type": "json_object"}},
            captured["model_kwargs"],
        )
        self.assertEqual(
            15,
            getattr(ingest_routes, "DEFAULT_AI_DECISION_HARD_TIMEOUT_SECONDS", None),
        )

    def test_returns_unavailable_without_deepseek_api_key(self):
        """
        测试: 当没有 DEEPSEEK_API_KEY 环境变量时, 返回降级客户端。

        验证点:
        - from_env(env={}) 传入空字典模拟无 API Key 的环境
        - 应返回 UnavailableAiDecisionClient 实例
        """
        # ---- 执行: 空环境变量 → 无 API Key ----
        client = LangChainDeepSeekDecisionClient.from_env(env={})

        # ---- 验证 ----
        self.assertIsInstance(client, UnavailableAiDecisionClient)


# =============================================================================
# 测试辅助类 (Test Doubles)
# =============================================================================

class StaticAiClient:
    """
    静态 AI 客户端 (Test Double / Stub)。

    用于替代真实的 AI 客户端, 每次调用 decide() 返回预设的固定响应。
    同时记录调用信息 (调用次数、最后一次的 prompt), 供测试验证。

    属性:
        response:    预设的响应字典
        calls:       被调用的次数
        last_prompt: 最后一次调用时传入的 prompt 参数
    """

    def __init__(self, response):
        """
        参数:
            response: 预设的响应字典, 每次 decide() 都返回此值
        """
        self.response = response
        self.calls = 0           # 调用计数器, 初始为 0
        self.last_prompt = None  # 最后一次的 prompt, 初始为 None

    def decide(self, prompt):
        """
        模拟 AI 决策: 记录调用并返回预设响应。

        参数:
            prompt: 传入的提示词字典 (被记录但不影响返回值)

        返回:
            预设的响应字典
        """
        self.calls += 1               # 递增调用计数
        self.last_prompt = prompt     # 保存最后一次的 prompt 供断言检查
        return self.response


class StaticMatchupContextBuilder:
    def __init__(self, response):
        self.response = response

    def build(self, state):
        return self.response


class FakeLangChainModel:
    """
    伪 LangChain 模型 (Test Double / Fake)。

    模拟 LangChain ChatDeepSeek 的核心行为:
    - 接收消息列表
    - 返回包含预设 content 的响应对象

    属性:
        content:  预设的响应内容 (字符串)
        messages: 最后一次 invoke() 收到的消息列表
    """

    def __init__(self, content):
        """
        参数:
            content: 预设的响应内容, 模拟 DeepSeek API 返回的文本
        """
        self.content = content
        self.messages = None  # 记录最后收到的消息, 供测试验证

    def invoke(self, messages):
        """
        模拟 LangChain 的 invoke() 调用。

        参数:
            messages: (role, content) 元组列表

        返回:
            FakeLangChainResponse 对象, 包装预设的 content
        """
        self.messages = messages  # 记录消息供验证
        return FakeLangChainResponse(self.content)


class FakeStreamingLangChainModel:
    def __init__(self, chunks):
        self.chunks = chunks
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        for chunk in self.chunks:
            yield FakeLangChainResponse(chunk)


class FakeInvokeAndStreamingLangChainModel:
    def __init__(self, content):
        self.content = content
        self.invoke_calls = 0
        self.stream_calls = 0

    def invoke(self, messages):
        self.invoke_calls += 1
        return FakeLangChainResponse(self.content)

    def stream(self, messages):
        self.stream_calls += 1
        yield FakeLangChainResponse(self.content)


class FakeLangChainResponse:
    """
    伪 LangChain 响应对象 (Test Double / Fake)。

    模拟 LangChain 模型 invoke() 返回的响应对象,
    包含 content 属性供客户端提取。

    属性:
        content: 模拟的 AI 响应文本
    """

    def __init__(self, content):
        self.content = content


# =============================================================================
# 测试数据工厂
# =============================================================================

def sample_state():
    """
    创建一份标准化的测试用游戏状态。

    模拟场景:
    - 第 6 回合
    - 己方是法师 (MAGE), 有 2 点法力水晶
    - 对手是猎人 (HUNTER), 10 点生命值, 无嘲讽随从
    - 己方场上有一个 3/2 的随从 (可攻击)
    - 对手场上为空

    这个场景足够简单, 适合测试 AI 决策的基本流程,
    同时包含足够的数据供 RecommendationEngine 生成合法的 action space。

    返回:
        模拟的游戏状态字典
    """
    return {
        # ---- 对局元信息 ----
        "game_id": "match-1",                        # 对局唯一标识
        "turn": 6,                                    # 第 6 回合
        "timestamp": "2026-06-04T11:00:00+08:00",    # 状态时间戳 (东八区)
        "active_player": "me",                        # 当前行动方

        # ---- 法力水晶 ----
        "my_mana": {"current": 2, "max": 2},          # 己方: 2/2 法力
        "enemy_mana": {"current": 0, "max": 2},       # 对手: 0/2 法力 (上回合用完了)

        # ---- 英雄状态 ----
        "my_hero": {
            "class": "MAGE",        # 法师
            "hp": 20,               # 20 生命值
            "armor": 0,             # 无护甲
            "attack": 0,            # 无攻击力
            "attacks_this_turn": 0,
            "max_attacks_per_turn": 1,
            "frozen": False,
        },
        "enemy_hero": {
            "class": "HUNTER",      # 猎人
            "hp": 10,               # 10 生命值 (压低血量, 方便测试斩杀场景)
            "armor": 0,
            "immune": False,        # 不免疫
        },

        # ---- 手牌 ----
        "hand": [],                 # 空手牌 (简化场景)

        # ---- 己方战场 ----
        "my_board": [
            {
                "entity_id": 1,
                "card_id": "MINION_1",
                "name": "Attacker",         # 攻击者随从
                "attack": 3,                # 3 点攻击力
                "health": 2,                # 2 点生命值
                "damage": 0,                # 未受伤
                "attacks_this_turn": 0,
                "max_attacks_per_turn": 1,
                "exhausted": False,
                "taunt": False,             # 无嘲讽
                "stealth": False,           # 非潜行
                "immune": False,            # 不免疫
                "frozen": False,            # 未被冻结
                "dormant": False,           # 非休眠
            }
        ],

        # ---- 敌方战场 ----
        "enemy_board": [],          # 敌方无随从 (直伤可打脸)

        # ---- 已知敌方卡牌 ----
        "known_enemy_cards": [],    # 无已知敌方手牌信息
    }


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    # 直接运行此文件时, 执行所有测试
    unittest.main()
