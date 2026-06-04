from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from ai_backend.coach.recommendation_engine import RecommendationEngine
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore


def create_coach_router(
    state_store: StateStore,
    recommendation_engine: RecommendationEngine,
    replay_writer: ReplayWriter | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/recommendation")
    async def get_recommendation() -> dict[str, Any]:
        snapshot = state_store.snapshot()
        state = snapshot.get("latest_state")
        recommendation = recommendation_engine.recommend(state)
        if (
            replay_writer is not None
            and isinstance(state, dict)
            and state.get("game_id")
            and _should_write_recommendation(recommendation)
        ):
            replay_writer.write(_recommendation_envelope(state, recommendation))
        return recommendation

    return router


def _should_write_recommendation(recommendation: dict[str, Any]) -> bool:
    plan = recommendation.get("plan")
    return plan not in {"action_space", "no_state", "unavailable"}


def _recommendation_envelope(state: dict[str, Any], recommendation: dict[str, Any]) -> dict[str, Any]:
    details = recommendation.get("details") or {}
    return {
        "type": "recommendation",
        "game_id": state.get("game_id"),
        "turn": state.get("turn"),
        "snapshot_timestamp": state.get("timestamp"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recommendation": recommendation,
        "action_space": details.get("action_space"),
    }
