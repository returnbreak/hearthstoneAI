from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_backend.ai_decision.auto_trigger import (
    TurnStartAiDecisionTrigger,
    compact_decision,
)
from ai_backend.cards import CardCatalog
from ai_backend.ingest.filter import IngestFilter
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub


DEFAULT_AI_DECISION_HARD_TIMEOUT_SECONDS = 15


async def _run_ai_decision_with_timeout(
    ai_turn_trigger: TurnStartAiDecisionTrigger,
    state: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    return await asyncio.wait_for(
        asyncio.to_thread(ai_turn_trigger.compute_decision, state),
        timeout=timeout_seconds,
    )


def create_ingest_router(
    state_store: StateStore,
    replay_writer: ReplayWriter,
    ui_hub: BroadcastHub,
    card_catalog: CardCatalog | None = None,
    ai_turn_trigger: TurnStartAiDecisionTrigger | None = None,
) -> APIRouter:
    router = APIRouter()
    ingest_filter = IngestFilter()
    card_catalog = card_catalog or CardCatalog.from_latest_data()
    ai_timeout_seconds = float(os.environ.get(
        "AI_DECISION_HARD_TIMEOUT_SECONDS",
        str(DEFAULT_AI_DECISION_HARD_TIMEOUT_SECONDS),
    ))

    async def run_ai_decision(
        state: dict[str, Any],
        write_recommendation: bool = True,
    ) -> None:
        if ai_turn_trigger is None:
            return
        try:
            decision = await _run_ai_decision_with_timeout(
                ai_turn_trigger,
                state,
                ai_timeout_seconds,
            )
        except asyncio.TimeoutError:
            ai_turn_trigger.record_decision(
                state,
                {
                    "plan": "unavailable",
                    "summary": "AI decision timed out.",
                    "actions": [],
                    "confidence": 0.0,
                    "validation": {
                        "validation_status": "failed",
                        "reason": "hard_timeout",
                    },
                    "details": {"decision_owner": "ai"},
                },
                False,
            )
            return
        except Exception as exc:
            decision = {
                "plan": "unavailable",
                "summary": "AI decision background task failed.",
                "actions": [],
                "confidence": 0.0,
                "validation": {"validation_status": "failed", "reason": str(exc)},
                "details": {"decision_owner": "ai"},
            }
        ai_turn_trigger.record_decision(state, decision, False)
        snapshot = state_store.snapshot()
        if _is_stale_ai_decision_state(state, snapshot):
            return

        if write_recommendation:
            ai_turn_trigger.write_recommendation(state, decision)

        await ui_hub.broadcast({
            "type": "ai_decision_update",
            "snapshot": snapshot,
            "recommendation": compact_decision(decision),
        })

    @router.get("/api/state")
    async def get_state() -> Dict[str, Any]:
        return state_store.snapshot()

    @router.websocket("/ws/hdt")
    async def hdt_ingest(websocket: WebSocket) -> None:
        await websocket.accept()

        while True:
            try:
                envelope = await websocket.receive_json()

                if not isinstance(envelope, dict):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Envelope must be a JSON object.",
                    })
                    continue

                if not ingest_filter.accept(envelope):
                    await websocket.send_json({
                        "type": "ack",
                        "filtered": True,
                        "message_count": state_store.snapshot()["message_count"],
                    })
                    continue

                envelope = card_catalog.enrich_envelope(envelope)
                state_store.apply(envelope)
                replay_writer.write(envelope)

                snapshot = state_store.snapshot()
                if ai_turn_trigger is not None:
                    decision_state = state_store.decision_state()
                    ai_snapshot = dict(snapshot)
                    ai_snapshot["latest_state"] = decision_state
                    ai_envelope = dict(envelope)
                    if envelope.get("type") == "game_state":
                        ai_envelope["state"] = decision_state
                    reserved_state = ai_turn_trigger.reserve_state(ai_envelope, ai_snapshot)
                    write_recommendation = True
                    if reserved_state is None:
                        reserved_state = ai_turn_trigger.reserve_prewarm_state(ai_envelope, ai_snapshot)
                        write_recommendation = True
                    if reserved_state is not None:
                        asyncio.create_task(run_ai_decision(reserved_state, write_recommendation))

                update_payload = {
                    "type": "backend_update",
                    "snapshot": snapshot,
                    "envelope": envelope,
                }

                await ui_hub.broadcast(update_payload)

                await websocket.send_json({
                    "type": "ack",
                    "message_count": snapshot["message_count"],
                })

            except WebSocketDisconnect:
                break

            except ValueError as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })

    return router


def _is_stale_ai_decision_state(
    decision_state: dict[str, Any],
    snapshot: dict[str, Any],
) -> bool:
    latest_state = snapshot.get("latest_state")
    if not isinstance(latest_state, dict):
        return True

    if decision_state.get("_prediction"):
        if decision_state.get("game_id") != latest_state.get("game_id"):
            return True
        predicted_turn = _as_int(decision_state.get("turn"))
        latest_turn = _as_int(latest_state.get("turn"))
        return latest_turn > predicted_turn

    return _actionable_state_signature(decision_state) != _actionable_state_signature(latest_state)


def _stale_ai_decision_payload(
    decision_state: dict[str, Any],
    snapshot: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    latest_state = snapshot.get("latest_state") if isinstance(snapshot, dict) else None
    latest_timestamp = latest_state.get("timestamp") if isinstance(latest_state, dict) else None
    return {
        "type": "ai_decision_update",
        "snapshot": snapshot,
        "recommendation": {
            "plan": "stale",
            "summary": "AI decision expired because the game state changed before the model returned.",
            "actions": [],
            "confidence": 0.0,
            "risk": "请等待下一次自动推荐；不要按旧推荐操作。",
            "validation": {
                "validation_status": "skipped",
                "reason": "expired_state",
            },
            "stale_decision": {
                "plan": decision.get("plan"),
                "chosen_sequence_id": decision.get("chosen_sequence_id"),
            },
            "snapshot_timestamp": decision_state.get("timestamp"),
            "latest_snapshot_timestamp": latest_timestamp,
        },
    }


def _actionable_state_signature(state: dict[str, Any]) -> tuple[Any, ...]:
    return (
        state.get("game_id"),
        state.get("turn"),
        state.get("active_player"),
        _mana_signature(state.get("my_mana") or state.get("mana")),
        _mana_signature(state.get("enemy_mana")),
        _hero_signature(state.get("my_hero")),
        _hero_signature(state.get("enemy_hero")),
        tuple(_card_signature(card) for card in state.get("hand") or []),
        tuple(_minion_signature(minion) for minion in state.get("my_board") or []),
        tuple(_minion_signature(minion) for minion in state.get("enemy_board") or []),
    )


def _mana_signature(mana: Any) -> tuple[Any, Any]:
    if not isinstance(mana, dict):
        return (None, None)
    return (mana.get("current"), mana.get("max"))


def _hero_signature(hero: Any) -> tuple[Any, ...]:
    if not isinstance(hero, dict):
        return ()
    return (
        hero.get("class"),
        hero.get("hp"),
        hero.get("armor"),
        hero.get("attack"),
        hero.get("attacks_this_turn"),
        hero.get("max_attacks_per_turn"),
        hero.get("immune"),
        hero.get("frozen"),
    )


def _card_signature(card: Any) -> tuple[Any, ...]:
    if not isinstance(card, dict):
        return ()
    return (
        card.get("entity_id"),
        card.get("card_id"),
        card.get("name"),
        card.get("cost"),
        card.get("type"),
    )


def _minion_signature(minion: Any) -> tuple[Any, ...]:
    if not isinstance(minion, dict):
        return ()
    return (
        minion.get("entity_id"),
        minion.get("card_id"),
        minion.get("name"),
        minion.get("attack"),
        minion.get("health"),
        minion.get("damage"),
        minion.get("attacks_this_turn"),
        minion.get("max_attacks_per_turn"),
        minion.get("taunt"),
        minion.get("stealth"),
        minion.get("divine_shield"),
        minion.get("frozen"),
        minion.get("immune"),
        minion.get("dormant"),
    )


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
