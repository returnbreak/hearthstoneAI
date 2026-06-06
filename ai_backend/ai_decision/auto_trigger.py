from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from ai_backend.ai_decision.service import AiDecisionService
from ai_backend.state.replay_writer import ReplayWriter


class TurnStartAiDecisionTrigger:
    """Run decisions after own-turn hand growth, including repeated hand gains."""

    def __init__(
        self,
        decision_service: AiDecisionService,
        replay_writer: ReplayWriter | None = None,
        enable_prewarm: bool = True,
    ):
        self._decision_service = decision_service
        self._replay_writer = replay_writer
        self._enable_prewarm = enable_prewarm
        self._last_trigger_key: tuple[str, str] | None = None
        self._last_prewarm_key: tuple[str, str] | None = None
        self._last_trigger_hand_count_by_key: dict[tuple[str, str], int] = {}
        self._hand_refresh_keys: set[tuple[str, str]] = set()
        self._last_seen_hand_count_by_game: dict[str, int] = {}

    def maybe_decide(
        self,
        envelope: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        state = self.reserve_state(envelope, snapshot)
        if state is None:
            return None
        return self.decide_reserved_state(state)

    def reserve_state(
        self,
        envelope: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        state = self._state_for_trigger(envelope, snapshot)
        if not state:
            return None

        game_key = str(state.get("game_id") or "unknown_game")
        hand_count = self._hand_count(state)
        previous_hand_count = self._last_seen_hand_count_by_game.get(game_key)
        self._last_seen_hand_count_by_game[game_key] = hand_count

        if not self._is_my_turn_state(state):
            return None

        trigger_key = self._trigger_key(state)
        if trigger_key is None:
            return None

        if previous_hand_count is None or hand_count <= previous_hand_count:
            return None

        self._last_trigger_key = trigger_key
        self._last_trigger_hand_count_by_key[trigger_key] = hand_count
        reserved_state = deepcopy(state)
        reserved_state["_ai_trigger_kind"] = "hand_increased"
        return reserved_state

    def reserve_prewarm_state(
        self,
        envelope: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return None

    def decide_reserved_state(
        self,
        state: dict[str, Any],
        write_recommendation: bool = True,
    ) -> dict[str, Any]:
        decision = self.compute_decision(state)
        self.record_decision(state, decision, write_recommendation)
        return decision

    def compute_decision(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._decision_service.decide(state)

    def record_decision(
        self,
        state: Mapping[str, Any],
        decision: Mapping[str, Any],
        write_recommendation: bool = True,
    ) -> None:
        if self._replay_writer is not None:
            self._replay_writer.write(self._attempt_envelope(state, decision))
            if write_recommendation and self._should_write_decision(decision):
                self._replay_writer.write(recommendation_envelope(state, decision))

    def write_recommendation(self, state: Mapping[str, Any], decision: Mapping[str, Any]) -> None:
        if self._replay_writer is None:
            return
        if self._should_write_decision(decision):
            self._replay_writer.write(recommendation_envelope(state, decision))

    @staticmethod
    def _state_for_trigger(
        envelope: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if envelope.get("type") == "game_state":
            state = envelope.get("state")
            return state if isinstance(state, dict) else None

        state = snapshot.get("latest_state")
        return state if isinstance(state, dict) else None

    @staticmethod
    def _is_my_turn_state(state: dict[str, Any] | None) -> bool:
        if not state:
            return False
        if state.get("active_player") != "me":
            return False

        mana = state.get("my_mana") or state.get("mana") or {}
        current_mana = mana.get("current") if isinstance(mana, Mapping) else None
        max_mana = mana.get("max") if isinstance(mana, Mapping) else None
        if current_mana == 0 and isinstance(max_mana, int) and max_mana > 0:
            return False

        return True

    @classmethod
    def _is_opponent_spent_out_state(cls, state: dict[str, Any] | None) -> bool:
        if not state:
            return False
        if state.get("active_player") != "opponent":
            return False

        enemy_mana = state.get("enemy_mana") or {}
        if not isinstance(enemy_mana, Mapping) or enemy_mana.get("current") != 0:
            return False

        enemy_hero = state.get("enemy_hero") or {}
        if cls._can_attack(enemy_hero):
            return False

        for minion in state.get("enemy_board") or []:
            if cls._can_attack(minion):
                return False

        return True

    @classmethod
    def _predicted_next_turn_state(cls, state: Mapping[str, Any]) -> dict[str, Any]:
        predicted = deepcopy(state)
        current_turn = cls._as_int(state.get("turn"), default=0)
        current_my_mana = state.get("my_mana") or state.get("mana") or {}
        current_max_mana = (
            cls._as_int(current_my_mana.get("max"), default=0)
            if isinstance(current_my_mana, Mapping)
            else 0
        )
        next_max_mana = min(max(current_max_mana + 1, 0), 10)

        predicted["active_player"] = "me"
        predicted["turn"] = current_turn + 1
        predicted["my_mana"] = {"current": next_max_mana, "max": next_max_mana}
        predicted["_ai_trigger_kind"] = "opponent_spent_out"
        predicted["_prediction"] = {
            "source_turn": state.get("turn"),
            "reason": "opponent_spent_out",
        }
        return predicted

    @staticmethod
    def _trigger_key(state: Mapping[str, Any]) -> tuple[str, str] | None:
        turn = state.get("turn")
        if turn is None:
            return None
        return (str(state.get("game_id") or "unknown_game"), str(turn))

    @staticmethod
    def _hand_count(state: Mapping[str, Any]) -> int:
        hand = state.get("hand")
        return len(hand) if isinstance(hand, list) else 0

    @staticmethod
    def _prewarm_key(state: Mapping[str, Any]) -> tuple[str, str] | None:
        turn = state.get("turn")
        if turn is None:
            return None
        return (str(state.get("game_id") or "unknown_game"), f"prewarm:{turn}")

    @classmethod
    def _can_attack(cls, actor: Mapping[str, Any]) -> bool:
        if cls._as_int(actor.get("attack"), default=0) <= 0:
            return False
        if actor.get("frozen") or actor.get("dormant"):
            return False
        if actor.get("cant_attack") or actor.get("exhausted"):
            return False
        attacks_this_turn = cls._as_int(actor.get("attacks_this_turn"), default=0)
        max_attacks = cls._as_int(actor.get("max_attacks_per_turn"), default=1)
        return attacks_this_turn < max_attacks

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _should_write_decision(decision: Mapping[str, Any]) -> bool:
        return decision.get("plan") == "ai_decision"

    @staticmethod
    def _attempt_envelope(
        state: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        details = decision.get("details") or {}
        ai_debug = deepcopy(details.get("ai_debug")) if isinstance(details.get("ai_debug"), Mapping) else None
        if ai_debug is not None:
            ai_debug["metadata"] = {
                "game_id": state.get("game_id"),
                "turn": state.get("turn"),
                "snapshot_timestamp": state.get("timestamp"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "trigger": state.get("_ai_trigger_kind") or "own_turn",
            }
            ai_debug.setdefault("response", {})["validated_decision"] = compact_decision(decision)
            ai_debug["diagnostics"] = {
                "timing": details.get("timing"),
                "prompt_chars": details.get("prompt_chars"),
                "candidate_count": details.get("candidate_count"),
            }
        return {
            "type": "ai_decision_attempt",
            "game_id": state.get("game_id"),
            "turn": state.get("turn"),
            "snapshot_timestamp": state.get("timestamp"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "trigger": state.get("_ai_trigger_kind") or "own_turn",
            "plan": decision.get("plan"),
            "decision": compact_decision(decision),
            "matchup_context": details.get("matchup_context"),
            "high_confidence_matchup_rule": details.get("high_confidence_matchup_rule"),
            "timing": details.get("timing"),
            "prompt_chars": details.get("prompt_chars"),
            "candidate_count": details.get("candidate_count"),
            "ai_debug": ai_debug,
        }


def recommendation_envelope(
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    details = decision.get("details") or {}
    return {
        "type": "recommendation",
        "game_id": state.get("game_id"),
        "turn": state.get("turn"),
        "snapshot_timestamp": state.get("timestamp"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trigger": state.get("_ai_trigger_kind") or "own_turn",
        "recommendation": compact_decision(decision),
        "matchup_context": details.get("matchup_context"),
        "high_confidence_matchup_rule": details.get("high_confidence_matchup_rule"),
    }


def compact_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    validation = dict(decision.get("validation") or {})
    if validation.get("passed") is True:
        validation.pop("reason", None)

    compact = {
        "plan": decision.get("plan"),
        "summary": decision.get("summary"),
        "chosen_sequence_id": decision.get("chosen_sequence_id"),
        "actions": list(decision.get("actions") or []),
        "reason": decision.get("reason"),
        "risk": decision.get("risk"),
        "confidence": decision.get("confidence"),
        "validation": validation or None,
        "my_deck_context": _compact_deck_plan(
            (decision.get("details") or {}).get("my_deck_context")
        ),
    }
    if compact["reason"] == compact["summary"]:
        compact.pop("reason")
    return {
        key: value
        for key, value in compact.items()
        if value is not None
    }


def _compact_deck_plan(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    actual_deck = value.get("actual_deck") if isinstance(value.get("actual_deck"), Mapping) else {}
    strategy = value.get("strategy") if isinstance(value.get("strategy"), Mapping) else None
    compact = {
        "status": value.get("status"),
        "confidence": value.get("confidence"),
        "match_method": value.get("match_method"),
        "analysis_required": value.get("analysis_required"),
        "deck_name": actual_deck.get("name"),
        "strategy": {
            key: strategy.get(key)
            for key in ("name", "style", "win_condition", "burst_exception", "core_cards")
            if strategy.get(key) is not None
        } if strategy else None,
    }
    return {
        key: item
        for key, item in compact.items()
        if item is not None and item != [] and item != {}
    }
