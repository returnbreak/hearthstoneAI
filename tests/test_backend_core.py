import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub


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


if __name__ == "__main__":
    unittest.main()
