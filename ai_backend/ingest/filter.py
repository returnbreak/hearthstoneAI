from __future__ import annotations

import json
from collections import deque
from copy import deepcopy
from typing import Any, Deque, Mapping


class IngestFilter:
    """Drop duplicate events and obvious historical replay messages."""

    def __init__(self, max_recent_event_fingerprints: int = 1024):
        self._game_id: str | None = None
        self._max_turn = -1
        self._event_fingerprints: set[tuple[Any, ...]] = set()
        self._recent_event_fingerprints: Deque[tuple[Any, ...]] = deque(maxlen=max_recent_event_fingerprints)

    def accept(self, envelope: Mapping[str, Any]) -> bool:
        envelope_type = envelope.get("type")
        if envelope_type == "game_state":
            state = envelope.get("state")
            return isinstance(state, Mapping) and self._accept_state(state)
        if envelope_type == "game_event":
            event = envelope.get("event")
            return isinstance(event, Mapping) and self._accept_event(event)
        return True

    def _accept_state(self, state: Mapping[str, Any]) -> bool:
        game_id = self._read_game_id(state)
        if game_id and game_id != self._game_id:
            self._reset_match(game_id)

        turn = self._read_turn(state)
        if turn is not None and self._is_historical_turn(turn):
            return False
        if turn is not None:
            self._max_turn = max(self._max_turn, turn)
        return True

    def _accept_event(self, event: Mapping[str, Any]) -> bool:
        event_type = str(event.get("type") or "")
        game_id = self._read_game_id(event)

        if event_type == "game_started":
            self._reset_match(game_id)
            self._remember_turn(event)
            return self._remember_event(event)

        if game_id and game_id != self._game_id:
            self._reset_match(game_id)

        turn = self._read_turn(event)
        if turn is not None and self._is_historical_turn(turn):
            return False

        self._remember_turn(event)
        return self._remember_event(event)

    def _remember_turn(self, payload: Mapping[str, Any]) -> None:
        turn = self._read_turn(payload)
        if turn is not None:
            self._max_turn = max(self._max_turn, turn)

    def _remember_event(self, event: Mapping[str, Any]) -> bool:
        fingerprint = self._event_fingerprint(event)
        if fingerprint in self._event_fingerprints:
            return False

        if len(self._recent_event_fingerprints) == self._recent_event_fingerprints.maxlen:
            old = self._recent_event_fingerprints.popleft()
            self._event_fingerprints.discard(old)

        self._event_fingerprints.add(fingerprint)
        self._recent_event_fingerprints.append(fingerprint)
        return True

    def _reset_match(self, game_id: str | None) -> None:
        self._game_id = game_id
        self._max_turn = -1
        self._event_fingerprints.clear()
        self._recent_event_fingerprints.clear()

    def _is_historical_turn(self, turn: int) -> bool:
        # Allow same-turn updates and normal progression. Reject only clear rollback,
        # such as turn 5 followed by replayed turn 0/1/2 messages.
        return self._max_turn >= 0 and turn < self._max_turn

    @staticmethod
    def _read_game_id(payload: Mapping[str, Any]) -> str | None:
        value = payload.get("game_id")
        return str(value) if value else None

    @staticmethod
    def _read_turn(payload: Mapping[str, Any]) -> int | None:
        value = payload.get("turn")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _event_fingerprint(event: Mapping[str, Any]) -> tuple[Any, ...]:
        # Timestamp is intentionally excluded: replayed historical events often
        # arrive with fresh timestamps but the same stable event identity.
        stable = deepcopy(dict(event))
        stable.pop("timestamp", None)
        return (json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")),)
