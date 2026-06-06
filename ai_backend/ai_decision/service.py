"""
AI 决策服务模块 (AI Decision Service)
======================================

本模块是 AI 决策层的核心编排逻辑, 负责协调以下组件完成一次完整的 AI 决策:

1. RecommendationEngine  — 根据游戏状态生成所有合法操作序列 (action space)
2. DecisionPromptBuilder — 将状态和 action space 组装为 AI 提示词
3. AiDecisionClient      — 调用 AI 大模型选择最优操作序列
4. RecommendationValidator — 验证 AI 返回的序列是否合法

决策流程:
    game_state → action_space → prompt → AI response → validation → final decision
        │            │            │           │             │              │
        │    Recommendation   Prompt      AI Client    Validator     返回给调用方
        │       Engine       Builder

返回结果有三种可能:
    - "ai_decision":         AI 选择了合法的操作序列 ✓
    - "ai_decision_rejected": AI 选择了非法/不存在的序列 ✗
    - "no_state":            无活跃游戏状态, 跳过决策
    - "unavailable":         AI 服务不可用 (API Key 缺失、网络错误等)
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from ai_backend.ai_decision.clients import AiDecisionClient
from ai_backend.ai_decision.prompt import DecisionPromptBuilder
from ai_backend.coach.recommendation_engine import RecommendationEngine
from ai_backend.coach.recommendation_validator import RecommendationValidator


class AiDecisionService:
    """
    AI 决策服务 — 编排完整的 AI 决策流程。

    职责:
    1. 检查游戏状态是否有效
    2. 委托 RecommendationEngine 生成 action space
    3. 委托 DecisionPromptBuilder 构建 prompt
    4. 委托 AiDecisionClient 获取 AI 选择
    5. 委托 RecommendationValidator 验证 AI 选择是否合法
    6. 根据验证结果返回相应的决策响应

    使用方式:
        from ai_backend.ai_decision.clients import LangChainDeepSeekDecisionClient

        client = LangChainDeepSeekDecisionClient.from_env()
        service = AiDecisionService(
            recommendation_engine=RecommendationEngine(),
            ai_client=client,
        )
        decision = service.decide(game_state)
    """

    def __init__(
        self,
        recommendation_engine: RecommendationEngine,
        ai_client: AiDecisionClient,
        prompt_builder: DecisionPromptBuilder | None = None,
        validator: RecommendationValidator | None = None,
    ):
        """
        初始化 AI 决策服务。

        参数:
            recommendation_engine: 推荐引擎, 负责从游戏状态生成 action space
                                   (所有合法操作序列)
            ai_client:            AI 决策客户端, 负责调用大模型选择最优序列
            prompt_builder:       提示词构建器 (可选, 默认使用 DecisionPromptBuilder)
            validator:            验证器, 负责验证 AI 返回的序列是否在 action space 中
                                  (可选, 默认使用 RecommendationValidator)
        """
        self._recommendation_engine = recommendation_engine
        self._ai_client = ai_client
        # 使用默认实例或注入的实例 (依赖注入模式, 方便测试)
        self._prompt_builder = prompt_builder or DecisionPromptBuilder()
        self._validator = validator or RecommendationValidator()

    # -------------------------------------------------------------------------
    # 核心方法: 执行一次完整的 AI 决策
    # -------------------------------------------------------------------------

    def decide(self, state: dict[str, Any] | None) -> dict[str, Any]:
        """
        执行一次完整的 AI 决策流程。

        流程:
            1. 状态检查    → state 为空则返回 "no_state"
            2. 生成操作空间 → 调用 RecommendationEngine.recommend()
            3. 构建提示词   → 调用 DecisionPromptBuilder.build()
            4. AI 选择      → 调用 AiDecisionClient.decide()
            5. 错误检查     → AI 返回 error 则返回 "unavailable"
            6. 验证选择     → 调用 RecommendationValidator.validate()
            7. 查找序列     → 在 action_space 中查找 AI 选择的序列
            8. 组装结果     → 返回最终决策

        参数:
            state: 当前游戏状态字典, 或 None (表示无活跃对局)

        返回:
            决策结果字典, plan 字段表示决策状态:
            - "no_state":            无活跃游戏状态
            - "unavailable":         AI 服务不可用
            - "ai_decision_rejected": AI 选择被验证器拒绝 (非法序列)
            - "ai_decision":         AI 做出了合法的选择

        返回字典的通用结构:
            {
                "plan":         str,       # 决策计划类型
                "summary":      str,       # 人类可读的摘要
                "actions":      list,      # 实际执行的操作列表
                "confidence":   float,     # 置信度 (0.0~1.0)
                "validation":   dict,      # 验证结果
                "details":      dict,      # 详细信息 (action_space, raw_ai_output 等)
            }
        """
        # =====================================================================
        # 步骤 1: 状态检查
        # =====================================================================
        # 如果没有活跃的游戏状态, 无法做出任何决策
        total_started = perf_counter()
        timing = {
            "planning_ms": 0.0,
            "prompt_ms": 0.0,
            "model_ms": 0.0,
            "validation_ms": 0.0,
            "total_ms": 0.0,
        }

        if not state:
            return {
                "plan": "no_state",                              # 计划类型: 无状态
                "summary": "No active game state is available.", # 摘要说明
                "actions": [],                                   # 无操作
                "confidence": 0.0,                               # 置信度为 0
                "validation": {
                    "validation_status": "skipped",              # 验证状态: 已跳过
                    "reason": "no_state",                        # 跳过原因: 无状态
                },
                "details": {
                    "decision_owner": "ai",
                    "timing": timing,
                    "prompt_chars": 0,
                    "candidate_count": 0,
                },
            }

        # =====================================================================
        # 步骤 2: 生成操作空间 (Action Space)
        # =====================================================================
        # RecommendationEngine 分析游戏状态, 枚举所有合法操作序列
        # 返回格式: {"details": {"action_space": {"legal_sequences": [...], ...}}}
        stage_started = perf_counter()
        action_space_response = self._recommendation_engine.recommend(state)
        timing["planning_ms"] = self._elapsed_ms(stage_started)

        # 从响应中提取 action_space, 如果嵌套路径不存在则使用空字典
        action_space = (action_space_response.get("details") or {}).get("action_space") or {}

        # =====================================================================
        # 步骤 3: 构建提示词 (Prompt)
        # =====================================================================
        # 将游戏状态和 action space 打包为 AI 可以理解的 JSON 格式
        stage_started = perf_counter()
        prompt = self._prompt_builder.build(state, action_space)
        timing["prompt_ms"] = self._elapsed_ms(stage_started)
        prompt_context = self._prompt_context(prompt)
        prompt_chars = len(prompt.get("system") or "") + len(prompt.get("user") or "")
        candidate_count = len(action_space.get("legal_sequences") or [])

        def diagnostics() -> dict[str, Any]:
            timing["total_ms"] = self._elapsed_ms(total_started)
            return {
                "timing": dict(timing),
                "prompt_chars": prompt_chars,
                "candidate_count": candidate_count,
            }

        # =====================================================================
        # 步骤 4: AI 选择
        # =====================================================================
        # 调用 AI 客户端, 让大模型从 legal_sequences 中选择一个最优序列
        stage_started = perf_counter()
        ai_output = self._ai_client.decide(prompt)
        timing["model_ms"] = self._elapsed_ms(stage_started)
        client_request_debug = getattr(self._ai_client, "last_request_debug", None)
        ai_debug = {
            "request": {
                "system_prompt": prompt.get("system"),
                "payload": self._parse_prompt_payload(prompt),
                "model_request": self._compact_model_request_debug(
                    client_request_debug
                ),
            },
            "response": {
                "raw_model_output": ai_output,
            },
        }

        # =====================================================================
        # 步骤 5: 错误检查 — AI 服务不可用
        # =====================================================================
        # 如果 AI 返回了 error 字段, 说明调用失败 (API Key 缺失、网络错误等)
        if ai_output.get("error"):
            return {
                "plan": "unavailable",                                   # 计划类型: 不可用
                "summary": ai_output.get("reason") or "AI decision is unavailable.",
                "actions": [],
                "confidence": 0.0,
                "validation": {
                    "validation_status": "skipped",                      # 验证已跳过
                    "reason": ai_output.get("error"),                    # 附带原始错误信息
                },
                "details": {
                    "decision_owner": "ai",
                    "action_space": action_space,                        # 保留 action_space 供调试
                    "matchup_context": prompt_context.get("matchup_context"),
                    "my_deck_context": prompt_context.get("my_deck_context"),
                    "high_confidence_matchup_rule": prompt_context.get("high_confidence_matchup_rule"),
                    "ai_debug": ai_debug,
                    **diagnostics(),
                },
            }

        # =====================================================================
        # 步骤 6: 验证 AI 的选择
        # =====================================================================
        # RecommendationValidator 检查:
        # - chosen_sequence_id 是否在 legal_sequences 中存在
        # - 序列中的操作是否合法 (费用、目标、先后顺序等)
        stage_started = perf_counter()
        validation = self._validator.validate(ai_output, action_space)
        timing["validation_ms"] = self._elapsed_ms(stage_started)

        # =====================================================================
        # 步骤 7: 在 action space 中查找 AI 选择的序列
        # =====================================================================
        # 根据 chosen_sequence_id 找到对应的完整序列对象
        # (包含具体的 actions 列表)
        sequence = self._find_sequence(
            str(ai_output.get("chosen_sequence_id") or ""), action_space
        )

        # =====================================================================
        # 步骤 8a: 验证失败 — AI 选择了非法的序列
        # =====================================================================
        if validation["validation_status"] != "passed":
            return {
                "plan": "ai_decision_rejected",                          # 计划类型: AI 被拒绝
                "summary": "AI returned an illegal action sequence.",    # 摘要: AI 返回了非法序列
                "chosen_sequence_id": ai_output.get("chosen_sequence_id"),
                "actions": [],                                           # 不执行任何操作
                "reason": ai_output.get("reason"),                       # AI 的原始理由
                "risk": ai_output.get("risk"),                           # AI 的原始风险评估
                "confidence": float(ai_output.get("confidence") or 0.0),
                "validation": validation,                                # 附上完整的验证结果
                "details": {
                    "decision_owner": "ai",
                    "action_space": action_space,                        # 保留 action_space 供调试
                    "raw_ai_output": ai_output,                          # 保留 AI 原始输出供分析
                    "matchup_context": prompt_context.get("matchup_context"),
                    "my_deck_context": prompt_context.get("my_deck_context"),
                    "high_confidence_matchup_rule": prompt_context.get("high_confidence_matchup_rule"),
                    "ai_debug": ai_debug,
                    **diagnostics(),
                },
            }

        # =====================================================================
        # 步骤 8b: 验证通过 — AI 做出了合法的选择 ✓
        # =====================================================================
        return {
            "plan": "ai_decision",                                       # 计划类型: AI 决策
            "summary": ai_output.get("reason") or "AI selected a legal sequence.",
            "chosen_sequence_id": ai_output.get("chosen_sequence_id"),   # 被选中的序列 ID
            "actions": list((sequence or {}).get("actions") or []),      # 序列中的具体操作列表
            "reason": ai_output.get("reason"),                           # AI 的战术理由
            "risk": ai_output.get("risk"),                               # AI 的风险评估
            "confidence": float(ai_output.get("confidence") or 0.0),    # 置信度
            "validation": validation,                                    # 验证结果 (passed)
            "details": {
                "decision_owner": "ai",
                "action_space": action_space,                            # 操作空间
                "selected_sequence": sequence,                           # 被选中的完整序列对象
                "matchup_context": prompt_context.get("matchup_context"),
                "my_deck_context": prompt_context.get("my_deck_context"),
                "high_confidence_matchup_rule": prompt_context.get("high_confidence_matchup_rule"),
                "ai_debug": ai_debug,
                **diagnostics(),
            },
        }

    # -------------------------------------------------------------------------
    # 辅助方法: 根据 sequence_id 查找对应的序列
    # -------------------------------------------------------------------------

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((perf_counter() - started) * 1000, 2)

    @staticmethod
    def _find_sequence(
        sequence_id: str, action_space: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        在 action space 的 legal_sequences 列表中查找指定 ID 的序列。

        参数:
            sequence_id:  要查找的序列 ID (如 "seq-001")
            action_space: 包含 legal_sequences 列表的操作空间字典

        返回:
            匹配的序列字典, 如果未找到则返回 None。
            序列字典的结构: {"sequence_id": "...", "actions": [...], "mana_cost": N, ...}
        """
        # 遍历所有合法序列, 找到 ID 匹配的那个
        for sequence in action_space.get("legal_sequences") or []:
            if sequence.get("sequence_id") == sequence_id:
                return sequence
        # 未找到匹配的序列 → AI 可能编造了一个不存在的 sequence_id
        return None

    @staticmethod
    def _prompt_context(prompt: dict[str, str]) -> dict[str, Any]:
        try:
            payload = json.loads(prompt.get("user") or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "matchup_context": payload.get("matchup_context"),
            "my_deck_context": payload.get("my_deck_context"),
            "high_confidence_matchup_rule": payload.get("high_confidence_matchup_rule"),
        }

    @staticmethod
    def _parse_prompt_payload(prompt: dict[str, str]) -> Any:
        try:
            return json.loads(prompt.get("user") or "{}")
        except json.JSONDecodeError:
            return prompt.get("user")

    @staticmethod
    def _compact_model_request_debug(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            key: item
            for key, item in value.items()
            if key not in {"messages", "raw_response_content"}
            and item is not None
            and item != []
            and item != {}
        } or None
