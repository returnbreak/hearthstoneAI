import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from ai_backend.coach.routes import create_coach_router
from ai_backend.cards import CardCatalog
from ai_backend.ingest.filter import IngestFilter
from ai_backend.ingest.routes import (
    _is_stale_ai_decision_state,
    _run_ai_decision_with_timeout,
    _stale_ai_decision_payload,
)
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub
from tools.fetch_latest_hearthstone_cards import build_latest_outputs


class ReplayWriterTests(unittest.TestCase):
    def test_writes_game_state_and_event_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            writer.write({"type": "game_state", "state": {"game_id": "match-1", "turn": 3}})
            writer.write({"type": "game_event", "event": {"game_id": "match-1", "type": "card_played"}})

            match_dir = Path(temp_dir) / "match-1"
            state_lines = (match_dir / "game_state.jsonl").read_text(encoding="utf-8").splitlines()
            event_lines = (match_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual({"type": "game_state", "state": {"game_id": "match-1", "turn": 3}}, json.loads(state_lines[0]))
            self.assertEqual({"type": "game_event", "event": {"game_id": "match-1", "type": "card_played"}}, json.loads(event_lines[0]))
            self.assertEqual(
                [{"type": "game_state", "state": {"game_id": "match-1", "turn": 3}}],
                json.loads((match_dir / "game_state.json").read_text(encoding="utf-8")),
            )
            self.assertEqual(
                [{"type": "game_event", "event": {"game_id": "match-1", "type": "card_played"}}],
                json.loads((match_dir / "events.json").read_text(encoding="utf-8")),
            )

    def test_writes_recommendation_jsonl_per_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            writer.write({
                "type": "recommendation",
                "game_id": "match-1",
                "turn": 7,
                "snapshot_timestamp": "2026-06-03T21:00:00+08:00",
                "generated_at": "2026-06-03T21:00:01+08:00",
                "recommendation": {
                    "plan": "ai_decision",
                    "summary": "Use Fireball.",
                    "reason": "Use Fireball.",
                    "validation": {
                        "validation_status": "passed",
                        "reason": "chosen_sequence_id is legal",
                    },
                },
                "high_confidence_matchup_rule": None,
            })

            lines = (Path(temp_dir) / "match-1" / "recommendations.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual("recommendation", json.loads(lines[0])["type"])
            logged = json.loads(lines[0])
            self.assertNotIn("reason", logged["recommendation"])
            self.assertNotIn("reason", logged["recommendation"]["validation"])
            self.assertNotIn("high_confidence_matchup_rule", logged)
            pretty = json.loads(
                (Path(temp_dir) / "match-1" / "recommendations.json")
                .read_text(encoding="utf-8")
            )
            self.assertNotIn("reason", pretty[0]["recommendation"])

    def test_ai_decision_attempt_without_debug_data_creates_no_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            writer.write({
                "type": "ai_decision_attempt",
                "game_id": "match-1",
                "turn": 7,
                "decision": {"plan": "unavailable"},
            })

            match_dir = Path(temp_dir) / "match-1"
            self.assertFalse((match_dir / "ai_decision_attempts.jsonl").exists())
            self.assertFalse((match_dir / "ai_decision_attempts.json").exists())

    def test_writes_pretty_ai_debug_file_without_expanding_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))
            writer.write({
                "type": "ai_decision_attempt",
                "game_id": "match-1",
                "turn": 7,
                "trigger": "opponent_spent_out",
                "generated_at": "2026-06-05T12:34:56+00:00",
                "decision": {"plan": "ai_decision"},
                "ai_debug": {
                    "request": {
                        "system_prompt": "system",
                        "user_prompt": "{\"turn\":7}",
                        "payload": {"turn": 7},
                        "model_request": {
                            "model": "deepseek-chat",
                            "messages": ["duplicate"],
                            "raw_response_content": "{\"chosen_sequence_id\":\"seq-001\"}",
                        },
                    },
                    "response": {
                        "raw_model_output": {"chosen_sequence_id": "seq-001"},
                        "raw_model_content": "{\"chosen_sequence_id\":\"seq-001\"}",
                    },
                },
            })

            match_dir = Path(temp_dir) / "match-1"
            debug_files = list((match_dir / "debug" / "ai_requests").glob("*.json"))
            self.assertEqual(1, len(debug_files))
            debug_path = debug_files[0]
            debug_text = debug_path.read_text(encoding="utf-8")
            debug = json.loads(debug_text)

            self.assertFalse((match_dir / "ai_decision_attempts.jsonl").exists())
            self.assertIn("\n  \"request\"", debug_text)
            self.assertEqual("system", debug["request"]["system_prompt"])
            self.assertNotIn("user_prompt", debug["request"])
            self.assertNotIn("messages", debug["request"]["model_request"])
            self.assertNotIn("raw_response_content", debug["request"]["model_request"])
            self.assertEqual("seq-001", debug["response"]["raw_model_output"]["chosen_sequence_id"])
            self.assertNotIn("raw_model_content", debug["response"])

    def test_writes_pretty_game_metadata_per_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))
            writer.write({
                "type": "game_metadata",
                "game_id": "match-1",
                "captured_at": "2026-06-05T12:00:00+08:00",
                "deck": {
                    "deck_available": True,
                    "deck_id": "deck-1",
                    "name": "Test Deck",
                    "cards": [{"card_id": "CS2_029", "count": 2}],
                },
            })

            path = Path(temp_dir) / "match-1" / "game_metadata.json"
            text = path.read_text(encoding="utf-8")
            metadata = json.loads(text)[0]

            self.assertIn("\n    \"deck\"", text)
            self.assertEqual("Test Deck", metadata["deck"]["name"])
            lines = (
                Path(temp_dir) / "match-1" / "game_metadata.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual("Test Deck", json.loads(lines[0])["deck"]["name"])

    def test_pretty_array_json_accumulates_multiple_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))
            writer.write({"type": "game_event", "event": {"game_id": "match-1", "type": "draw"}})
            writer.write({"type": "game_event", "event": {"game_id": "match-1", "type": "play"}})

            records = json.loads(
                (Path(temp_dir) / "match-1" / "events.json").read_text(encoding="utf-8")
            )

            self.assertEqual(["draw", "play"], [item["event"]["type"] for item in records])

    def test_rejects_unknown_envelope_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            with self.assertRaises(ValueError):
                writer.write({"type": "unexpected"})

    def test_sanitizes_game_id_for_match_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            writer.write({"type": "game_state", "state": {"game_id": "match:bad/name", "turn": 1}})

            self.assertTrue((Path(temp_dir) / "match_bad_name" / "game_state.jsonl").exists())


class StateStoreTests(unittest.TestCase):
    def test_tracks_latest_state_and_recent_events(self):
        store = StateStore(max_recent_events=2)

        store.apply({"type": "game_state", "state": {"turn": 1}})
        store.apply({"type": "game_event", "event": {"type": "draw"}})
        store.apply({"type": "game_event", "event": {"type": "play"}})
        store.apply({"type": "game_event", "event": {"type": "end_turn"}})

        snapshot = store.snapshot()

        self.assertEqual({"turn": 1}, snapshot["latest_state"])
        self.assertEqual([{"type": "play"}, {"type": "end_turn"}], snapshot["recent_events"])
        self.assertEqual(4, snapshot["message_count"])

    def test_game_end_clears_latest_state_but_keeps_end_event(self):
        store = StateStore()

        store.apply({"type": "game_state", "state": {"game_id": "match-1", "turn": 7}})
        store.apply({"type": "game_event", "event": {"game_id": "match-1", "type": "game_ended"}})

        snapshot = store.snapshot()

        self.assertIsNone(snapshot["latest_state"])
        self.assertEqual([{"game_id": "match-1", "type": "game_ended"}], snapshot["recent_events"])

    def test_game_start_clears_previous_match_events(self):
        store = StateStore()

        store.apply({"type": "game_event", "event": {"game_id": "old", "type": "game_ended"}})
        store.apply({"type": "game_event", "event": {"game_id": "new", "type": "game_started"}})

        self.assertEqual([{"game_id": "new", "type": "game_started"}], store.snapshot()["recent_events"])

    def test_tracks_game_metadata_separately_from_game_state(self):
        store = StateStore()

        store.apply({
            "type": "game_metadata",
            "game_id": "match-1",
            "deck": {"deck_available": True, "name": "Test Deck"},
        })

        snapshot = store.snapshot()
        self.assertEqual("Test Deck", snapshot["game_metadata"]["deck"]["name"])
        self.assertIsNone(snapshot["latest_state"])

    def test_decision_state_merges_game_metadata_and_recent_events_without_mutation(self):
        store = StateStore()
        store.apply({
            "type": "game_metadata",
            "game_id": "match-1",
            "deck": {"deck_available": True, "name": "Test Deck"},
        })
        store.apply({"type": "game_state", "state": {"game_id": "match-1", "turn": 2}})
        store.apply({
            "type": "game_event",
            "event": {"game_id": "match-1", "type": "card_drawn", "card_id": "TEST_001"},
        })

        decision_state = store.decision_state()
        decision_state["game_metadata"]["deck"]["name"] = "Changed"
        decision_state["recent_events"].append({"type": "mutated"})

        snapshot = store.snapshot()
        self.assertEqual("Test Deck", snapshot["game_metadata"]["deck"]["name"])
        self.assertEqual(1, len(snapshot["recent_events"]))
        self.assertEqual("Test Deck", store.decision_state()["game_metadata"]["deck"]["name"])

    def test_targeted_hand_transform_rewrites_stale_hand_card_snapshot(self):
        store = StateStore()

        store.apply({
            "type": "game_event",
            "event": {
                "game_id": "match-1",
                "turn": 4,
                "player": "me",
                "type": "card_played",
                "card_id": "CATA_200",
                "name": "古神的眼线",
                "target": {
                    "entity_id": 57,
                    "card_id": "TIME_875",
                    "name": "半兽人迦罗娜",
                    "type": "spell",
                },
            },
        })
        store.apply({
            "type": "game_state",
            "state": {
                "game_id": "match-1",
                "turn": 4,
                "hand": [{
                    "entity_id": 57,
                    "card_id": "TIME_875",
                    "name": "半兽人迦罗娜",
                    "cost": 4,
                    "type": "MINION",
                    "text": "战吼：如果莱恩国王在对手手牌中...",
                }],
            },
        })

        card = store.decision_state()["hand"][0]
        self.assertEqual("GAME_005", card["card_id"])
        self.assertEqual("幸运币", card["name"])
        self.assertEqual(0, card["cost"])
        self.assertEqual("TIME_875", card["transformed_from_card_id"])

    def test_recommendation_ui_has_large_primary_area_and_deck_plan(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "ai_backend" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
        css = (root / "ai_backend" / "ui" / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="panel recommendation recommendation-primary"', html)
        self.assertIn('id="recommendation-deck-plan"', html)
        self.assertIn('class="panel status-summary"', html)
        self.assertIn("grid-column: span 3", css)


class IngestFilterTests(unittest.TestCase):
    def test_rejects_duplicate_event_without_using_timestamp(self):
        ingest_filter = IngestFilter()
        event = {
            "type": "game_event",
            "event": {
                "game_id": "match-1",
                "turn": 3,
                "player": "me",
                "type": "card_played",
                "entity_id": 42,
                "card_id": "CS2_029",
                "timestamp": "2026-06-03T21:00:00+08:00",
            },
        }
        duplicate = deepcopy_json(event)
        duplicate["event"]["timestamp"] = "2026-06-03T21:00:01+08:00"

        self.assertTrue(ingest_filter.accept(event))
        self.assertFalse(ingest_filter.accept(duplicate))

    def test_rejects_state_turn_rollback_in_same_match(self):
        ingest_filter = IngestFilter()

        self.assertTrue(ingest_filter.accept({
            "type": "game_state",
            "state": {"game_id": "match-1", "turn": 5},
        }))
        self.assertFalse(ingest_filter.accept({
            "type": "game_state",
            "state": {"game_id": "match-1", "turn": 0},
        }))

    def test_rejects_event_turn_rollback_in_same_match(self):
        ingest_filter = IngestFilter()

        self.assertTrue(ingest_filter.accept({
            "type": "game_state",
            "state": {"game_id": "match-1", "turn": 5},
        }))
        self.assertFalse(ingest_filter.accept({
            "type": "game_event",
            "event": {"game_id": "match-1", "turn": 2, "type": "turn_started", "player": "me"},
        }))

    def test_game_started_resets_turn_rollback_protection(self):
        ingest_filter = IngestFilter()

        self.assertTrue(ingest_filter.accept({
            "type": "game_state",
            "state": {"game_id": "match-1", "turn": 5},
        }))
        self.assertTrue(ingest_filter.accept({
            "type": "game_event",
            "event": {"game_id": "match-2", "turn": 0, "type": "game_started"},
        }))
        self.assertTrue(ingest_filter.accept({
            "type": "game_state",
            "state": {"game_id": "match-2", "turn": 0},
        }))


class AiDecisionFreshnessTests(unittest.TestCase):
    def test_hard_timeout_stops_waiting_for_slow_ai_decision(self):
        class SlowTrigger:
            def compute_decision(self, state):
                time.sleep(0.05)
                return {"plan": "ai_decision"}

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(
                _run_ai_decision_with_timeout(
                    SlowTrigger(),
                    {"game_id": "match-1"},
                    timeout_seconds=0.001,
                )
            )

    def test_marks_decision_state_stale_when_hand_changed(self):
        decision_state = {
            "game_id": "match-1",
            "turn": 4,
            "active_player": "me",
            "my_mana": {"current": 4, "max": 4},
            "my_hero": {"hp": 20},
            "enemy_hero": {"hp": 20},
            "hand": [{"entity_id": 10, "card_id": "CARD_10", "name": "Old", "cost": 2}],
            "my_board": [],
            "enemy_board": [],
        }
        latest_state = deepcopy_json(decision_state)
        latest_state["hand"].append({"entity_id": 11, "card_id": "CARD_11", "name": "New", "cost": 1})

        self.assertTrue(_is_stale_ai_decision_state(decision_state, {"latest_state": latest_state}))

    def test_keeps_decision_state_fresh_when_only_timestamp_changed(self):
        decision_state = {
            "game_id": "match-1",
            "turn": 4,
            "timestamp": "old",
            "active_player": "me",
            "my_mana": {"current": 4, "max": 4},
            "my_hero": {"hp": 20},
            "enemy_hero": {"hp": 20},
            "hand": [],
            "my_board": [],
            "enemy_board": [],
        }
        latest_state = deepcopy_json(decision_state)
        latest_state["timestamp"] = "new"

        self.assertFalse(_is_stale_ai_decision_state(decision_state, {"latest_state": latest_state}))

    def test_keeps_predicted_prewarm_decision_fresh(self):
        decision_state = {
            "game_id": "match-1",
            "turn": 5,
            "active_player": "me",
            "my_mana": {"current": 5, "max": 5},
            "_prediction": {"reason": "opponent_spent_out"},
        }
        latest_state = {
            "game_id": "match-1",
            "turn": 4,
            "active_player": "opponent",
            "my_mana": {"current": 0, "max": 4},
        }

        self.assertFalse(_is_stale_ai_decision_state(decision_state, {"latest_state": latest_state}))

    def test_marks_predicted_prewarm_decision_stale_after_game_ends(self):
        decision_state = {
            "game_id": "match-1",
            "turn": 5,
            "active_player": "me",
            "_prediction": {"reason": "opponent_spent_out"},
        }

        self.assertTrue(
            _is_stale_ai_decision_state(
                decision_state,
                {"latest_state": None, "recent_events": [{"type": "game_ended"}]},
            )
        )

    def test_stale_ai_decision_payload_marks_recommendation_expired(self):
        decision_state = {
            "game_id": "match-1",
            "turn": 4,
            "timestamp": "old",
            "active_player": "me",
        }
        latest_state = {
            "game_id": "match-1",
            "turn": 4,
            "timestamp": "new",
            "active_player": "me",
        }
        payload = _stale_ai_decision_payload(
            decision_state,
            {"latest_state": latest_state, "message_count": 12},
            {"plan": "ai_decision", "chosen_sequence_id": "seq-old"},
        )

        self.assertEqual("ai_decision_update", payload["type"])
        self.assertEqual("stale", payload["recommendation"]["plan"])
        self.assertEqual("expired_state", payload["recommendation"]["validation"]["reason"])
        self.assertEqual("old", payload["recommendation"]["snapshot_timestamp"])
        self.assertEqual("new", payload["recommendation"]["latest_snapshot_timestamp"])


class CardCatalogTests(unittest.TestCase):
    def test_from_latest_data_reads_cards_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cards_dir = root / "hearthstone_data" / "cards"
            cards_dir.mkdir(parents=True)
            (cards_dir / "card_index.zhCN.json").write_text(
                json.dumps(
                    {
                        "TEST_CARD": {
                            "id": "TEST_CARD",
                            "name": "测试牌",
                            "text": "测试文本",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            catalog = CardCatalog.from_latest_data(root)
            envelope = catalog.enrich_envelope({
                "type": "game_event",
                "event": {"card_id": "TEST_CARD"},
            })

        self.assertEqual("测试文本", envelope["event"]["text"])

    def test_enriches_game_event_with_card_text_and_image(self):
        catalog = CardCatalog({
            "END_011": {
                "id": "END_011",
                "dbfId": 121150,
                "name": "加速光环",
                "cost": 2,
                "type": "SPELL",
                "set": "EMERALD_DREAM",
                "cardClass": "PALADIN",
                "text": "在你的回合开始时，获得一个临时的法力水晶。持续3回合。",
                "mechanics": ["AURA"],
                "image": {"render_256x_path": "images/render_256x/END_011.png"},
            }
        })

        envelope = catalog.enrich_envelope({
            "type": "game_event",
            "event": {"type": "card_played", "card_id": "END_011", "name": "加速光环"},
        })

        event = envelope["event"]
        self.assertEqual("在你的回合开始时，获得一个临时的法力水晶。持续3回合。", event["text"])
        self.assertEqual("EMERALD_DREAM", event["set"])
        self.assertEqual(["AURA"], event["mechanics"])
        self.assertEqual({"render_256x_path": "images/render_256x/END_011.png"}, event["image"])

    def test_enriches_known_enemy_cards_in_state(self):
        catalog = CardCatalog({
            "END_011": {
                "id": "END_011",
                "text": "在你的回合开始时，获得一个临时的法力水晶。持续3回合。",
                "image": {"render_256x_path": "images/render_256x/END_011.png"},
            }
        })

        envelope = catalog.enrich_envelope({
            "type": "game_state",
            "state": {"known_enemy_cards": [{"card_id": "END_011"}]},
        })

        known_card = envelope["state"]["known_enemy_cards"][0]
        self.assertEqual("在你的回合开始时，获得一个临时的法力水晶。持续3回合。", known_card["text"])
        self.assertEqual({"render_256x_path": "images/render_256x/END_011.png"}, known_card["image"])

    def test_enriches_weapon_attack_and_durability(self):
        catalog = CardCatalog({
            "WEAPON_1": {
                "id": "WEAPON_1",
                "name": "Test Weapon",
                "cost": 2,
                "type": "WEAPON",
                "attack": 2,
                "durability": 2,
                "text": "After your hero attacks, draw a card.",
            }
        })

        envelope = catalog.enrich_envelope({
            "type": "game_state",
            "state": {"hand": [{"card_id": "WEAPON_1"}]},
        })

        weapon_card = envelope["state"]["hand"][0]
        self.assertEqual(2, weapon_card["attack"])
        self.assertEqual(2, weapon_card["durability"])

    def test_enriches_cards_in_game_metadata(self):
        catalog = CardCatalog({
            "CS2_029": {
                "id": "CS2_029",
                "dbfId": 315,
                "name": "Fireball",
                "cost": 4,
                "type": "SPELL",
                "text": "Deal 6 damage.",
                "image": {"render_256x_path": "images/render_256x/CS2_029.png"},
            }
        })

        envelope = catalog.enrich_envelope({
            "type": "game_metadata",
            "game_id": "match-1",
            "deck": {
                "cards": [{"card_id": "CS2_029", "count": 2}],
            },
        })

        card = envelope["deck"]["cards"][0]
        self.assertEqual("Fireball", card["name"])
        self.assertEqual("Deal 6 damage.", card["text"])
        self.assertEqual(2, card["count"])

    def test_records_missing_card_ids_for_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "card_catalog_missing_ids.json"
            catalog = CardCatalog({}, missing_report_path=report_path)

            catalog.enrich_envelope({
                "type": "game_event",
                "event": {
                    "type": "card_played",
                    "card_id": "UNKNOWN_CARD",
                    "dbf_id": 123,
                    "name": "未知卡牌",
                },
            })
            catalog.enrich_envelope({
                "type": "game_state",
                "state": {
                    "hand": [
                        {"card_id": "UNKNOWN_CARD", "dbf_id": 123, "name": "未知卡牌"},
                    ],
                },
            })

            report = json.loads(report_path.read_text(encoding="utf-8"))
            missing = report["missing_ids"]["UNKNOWN_CARD"]
            self.assertEqual(2, missing["count"])
            self.assertEqual("未知卡牌", missing["last_name"])
            self.assertEqual(123, missing["last_dbf_id"])
            self.assertEqual("game_state.hand", missing["last_context"])


class LatestCardFetchTests(unittest.TestCase):
    def test_build_latest_outputs_indexes_full_cards_without_expanding_wild_pool(self):
        collectible_cards = [
            {"id": "CORE_CARD", "name": "可收藏牌", "set": "CORE", "collectible": True},
        ]
        full_cards = [
            {"id": "CORE_CARD", "name": "可收藏牌", "set": "CORE", "collectible": True},
            {"id": "TOKEN_CARD", "name": "衍生物", "set": "CORE", "collectible": False, "text": "衍生物描述"},
        ]

        outputs = build_latest_outputs(collectible_cards, full_cards, Path("out"))

        self.assertIn("TOKEN_CARD", outputs["card_index"])
        self.assertEqual("衍生物描述", outputs["card_index"]["TOKEN_CARD"]["text"])
        self.assertEqual(["CORE_CARD"], [card["id"] for card in outputs["wild_cards"]])
        self.assertEqual(["CORE_CARD"], [card["id"] for card in outputs["standard_cards"]])


class BroadcastHubTests(unittest.TestCase):
    def test_broadcast_sends_to_connected_clients(self):
        async def scenario():
            hub = BroadcastHub()
            client = RecordingClient()

            await hub.connect(client)
            await hub.broadcast({"type": "game_state", "state": {"turn": 2}})

            self.assertEqual([{"type": "game_state", "state": {"turn": 2}}], client.messages)

        asyncio.run(scenario())

    def test_broadcast_removes_failed_clients(self):
        async def scenario():
            hub = BroadcastHub()
            failed = RecordingClient(raise_on_send=True)
            healthy = RecordingClient()

            await hub.connect(failed)
            await hub.connect(healthy)
            await hub.broadcast({"type": "game_event"})

            self.assertEqual([{"type": "game_event"}], healthy.messages)
            self.assertEqual(1, hub.client_count)

        asyncio.run(scenario())


class AppWiringTests(unittest.TestCase):
    def test_app_import_keeps_expected_title(self):
        from ai_backend.app import app

        self.assertEqual("HDT AI Assistant Backend", app.title)

    def test_app_exposes_recommendation_endpoint(self):
        from ai_backend.app import app

        paths = {route.path for route in app.routes}

        self.assertIn("/api/recommendation", paths)


class CoachRouteLoggingTests(unittest.TestCase):
    def test_action_space_response_is_not_written_as_recommendation_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore()
            store.apply({
                "type": "game_state",
                "state": {
                    "game_id": "match-1",
                    "turn": 3,
                    "timestamp": "2026-06-03T21:00:00+08:00",
                    "my_mana": {"current": 2, "max": 2},
                    "my_hero": {"class": "MAGE"},
                    "enemy_hero": {"hp": 30, "armor": 0, "immune": False},
                    "my_board": [],
                    "enemy_board": [],
                    "hand": [],
                },
            })
            router = create_coach_router(
                store,
                StaticRecommendationEngine({"plan": "action_space", "details": {"action_space": {}}}),
                ReplayWriter(Path(temp_dir)),
            )

            response = asyncio.run(router.routes[0].endpoint())

            self.assertEqual("action_space", response["plan"])
            self.assertFalse((Path(temp_dir) / "match-1" / "recommendations.jsonl").exists())


class RecordingClient:
    def __init__(self, raise_on_send=False):
        self.raise_on_send = raise_on_send
        self.messages = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self.raise_on_send:
            raise RuntimeError("send failed")
        self.messages.append(payload)


class StaticRecommendationEngine:
    def __init__(self, recommendation):
        self.recommendation = recommendation

    def recommend(self, state):
        return self.recommendation


def deepcopy_json(value):
    return json.loads(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
