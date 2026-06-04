"""
Recommendation API facade.

The backend does not choose the best move. It only builds the legal action
space from public game state and leaves strategy selection to the AI layer.
"""

from __future__ import annotations

from typing import Any

from ai_backend.coach.action_planner import ActionPlanner
from ai_backend.coach.combat_analyzer import CombatAnalyzer


class RecommendationEngine:
    """Return legal candidates instead of rule-based recommendations."""

    def __init__(self):
        self._combat_analyzer = CombatAnalyzer()
        self._action_planner = ActionPlanner(self._combat_analyzer)

    def recommend(self, state: dict[str, Any] | None) -> dict[str, Any]:
        if not state:
            return {
                "plan": "no_state",
                "summary": "No active game state is available.",
                "actions": [],
                "confidence": 0.0,
                "details": {
                    "decision_owner": "ai",
                    "backend_scope": "legal_action_generation_only",
                },
            }

        action_space = self._action_planner.generate(state)
        return {
            "plan": "action_space",
            "summary": "Legal action space generated. AI must choose the final line.",
            "actions": [],
            "confidence": 0.0,
            "details": {
                "decision_owner": "ai",
                "backend_scope": "legal_action_generation_only",
                "action_space": action_space,
            },
        }
