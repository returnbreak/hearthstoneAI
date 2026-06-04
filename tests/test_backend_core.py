import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ai_backend.coach.routes import create_coach_router
from ai_backend.cards import CardCatalog
from ai_backend.ingest.filter import IngestFilter
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

    def test_writes_recommendation_jsonl_per_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ReplayWriter(Path(temp_dir))

            writer.write({
                "type": "recommendation",
                "game_id": "match-1",
                "turn": 7,
                "snapshot_timestamp": "2026-06-03T21:00:00+08:00",
                "generated_at": "2026-06-03T21:00:01+08:00",
                "recommendation": {"plan": "lethal"},
                "action_space": {"legal_sequences": []},
            })

            lines = (Path(temp_dir) / "match-1" / "recommendations.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual("recommendation", json.loads(lines[0])["type"])
            self.assertEqual({"plan": "lethal"}, json.loads(lines[0])["recommendation"])

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


class CardCatalogTests(unittest.TestCase):
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
