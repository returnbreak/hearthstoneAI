"""
推荐验证器：校验 LLM 生成的推荐是否在后端生成的合法候选范围内。

本模块实现了一个安全校验层，用于验证外部推荐（如 LLM 大模型
产出的推荐）是否与后端基于规则生成的合法操作空间一致。

验证策略分为两个层级：
    1. 序列级验证：如果推荐直接引用了某个预生成的序列 ID
       （chosen_sequence_id），只需检查该序列是否存在于行动空间中。
    2. 操作级验证：如果推荐包含具体的操作列表（actions），
       逐一检查每个操作是否在合法操作集合中。

任何不合法的推荐都会被拒绝，返回 failed 状态及原因。
"""

from __future__ import annotations

from typing import Any


class RecommendationValidator:
    """校验 LLM 推荐是否在后端生成的合法候选集合内。

    核心职责：
        接收外部（例如 LLM）的推荐输出和后端生成的行动空间，
        验证推荐中的操作是否全部合法，防止推荐引擎执行
        违反游戏规则的操作。

    使用方式:
        validator = RecommendationValidator()
        result = validator.validate(llm_recommendation, action_space)
        if result["validation_status"] == "passed":
            # 推荐合法，可以执行
    """

    def validate(self, recommendation: dict[str, Any], action_space: dict[str, Any]) -> dict[str, Any]:
        """
        验证推荐操作是否全部在合法候选范围内。

        验证流程：
        1. 如果推荐包含 chosen_sequence_id，直接验证该序列是否存在。
        2. 如果推荐包含 actions 列表，逐一检查每个操作的合法性。

        参数:
            recommendation: 待验证的推荐字典，需包含：
                            - chosen_sequence_id（可选）：指向预生成序列的 ID
                            - actions（可选）：具体操作列表
            action_space: ActionPlanner.generate() 返回的合法行动空间。

        返回:
            验证结果字典，包含：
            - validation_status: "passed" 或 "failed"
            - reason: 验证通过/失败的具体原因
        """
        # 层级 1：序列级验证——推荐直接引用了预生成的序列 ID
        sequence_id = recommendation.get("chosen_sequence_id")
        if sequence_id:
            sequence = self._sequence_by_id(sequence_id, action_space)
            if sequence is not None:
                fatal_reason = self._fatal_sequence_reason(sequence.get("actions") or [], action_space)
                if fatal_reason:
                    return {"validation_status": "failed", "reason": fatal_reason}
                return {"validation_status": "passed", "reason": "chosen_sequence_id is legal"}
            return {"validation_status": "failed", "reason": f"unknown sequence_id: {sequence_id}"}

        # 层级 2：操作级验证——逐条检查每个操作是否在合法集合中
        actions = recommendation.get("actions") or []
        # 构建所有合法操作的键值集合，便于快速查找
        legal_action_keys = self._legal_action_keys(action_space)
        for action in actions:
            key = self._action_key(action)
            if key not in legal_action_keys:
                return {
                    "validation_status": "failed",
                    "reason": f"action {key} not in legal_actions",
                }

        fatal_reason = self._fatal_sequence_reason(actions, action_space)
        if fatal_reason:
            return {"validation_status": "failed", "reason": fatal_reason}

        return {"validation_status": "passed", "reason": "all actions are legal"}

    @staticmethod
    def _sequence_exists(sequence_id: str, action_space: dict[str, Any]) -> bool:
        """
        检查给定的序列 ID 是否存在于行动空间的合法序列列表中。

        参数:
            sequence_id: 待查找的序列标识符（如 "seq-001"）。
            action_space: 行动空间字典，需包含 "legal_sequences" 键。

        返回:
            True 表示序列存在且合法，False 表示不存在。
        """
        return any(
            sequence.get("sequence_id") == sequence_id
            for sequence in action_space.get("legal_sequences") or []
        )

    @staticmethod
    def _sequence_by_id(sequence_id: str, action_space: dict[str, Any]) -> dict[str, Any] | None:
        for sequence in action_space.get("legal_sequences") or []:
            if sequence.get("sequence_id") == sequence_id:
                return sequence
        return None

    def _fatal_sequence_reason(
        self,
        actions: list[dict[str, Any]],
        action_space: dict[str, Any],
    ) -> str | None:
        hero_effective_health = self._hero_effective_health(action_space)
        if hero_effective_health <= 0:
            return None

        total_self_damage = 0
        for action in actions:
            total_self_damage += self._self_damage_from_action(action)
            if total_self_damage >= hero_effective_health:
                return (
                    "sequence causes hero death: "
                    f"self_damage={total_self_damage} hero_effective_health={hero_effective_health}"
                )
        return None

    @staticmethod
    def _hero_effective_health(action_space: dict[str, Any]) -> int:
        hp = RecommendationValidator._as_int(action_space.get("hero_hp"))
        armor = RecommendationValidator._as_int(action_space.get("hero_armor"))
        return hp + armor

    @staticmethod
    def _self_damage_from_action(action: dict[str, Any]) -> int:
        action_type = str(action.get("type") or "")
        if action_type == "hero_attack":
            return RecommendationValidator._as_int(action.get("self_damage_risk"))
        if action_type == "hero_power":
            effect = action.get("effect") or {}
            if isinstance(effect, dict):
                return RecommendationValidator._as_int(effect.get("self_damage"))
        return 0

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _legal_action_keys(self, action_space: dict[str, Any]) -> set[tuple[Any, ...]]:
        """
        从行动空间中提取所有合法操作的唯一键值集合。

        遍历 legal_actions 中的所有操作类型和操作，
        为每个操作生成唯一标识符（action_key），
        用于与推荐操作进行快速比对。

        参数:
            action_space: 行动空间字典，需包含 "legal_actions" 键。

        返回:
            合法操作键值的集合，每个键值为元组类型。
        """
        keys: set[tuple[Any, ...]] = set()
        for actions in (action_space.get("legal_actions") or {}).values():
            for action in actions:
                keys.add(self._action_key(action))
        return keys

    @staticmethod
    def _action_key(action: dict[str, Any]) -> tuple[Any, ...]:
        """
        为单个操作生成唯一标识符元组。

        标识符由操作的核心属性组成：
        - 对于 end_turn 操作：仅使用类型本身作为键值（因为只有一个结束回合操作）
        - 对于其他操作：使用 (类型, 来源, 目标) 三元组作为键值，
          这三个属性足以唯一区分不同的操作实例。

        参数:
            action: 操作信息字典，至少需包含 "type" 字段。

        返回:
            作为唯一标识符的元组。
        """
        action_type = action.get("type")
        # 结束回合操作只需要类型即可唯一标识
        if action_type == "end_turn":
            return (action_type,)
        # 其他操作使用 (类型, 来源, 目标) 三元组
        return (
            action_type,
            action.get("source"),
            action.get("target"),
        )
