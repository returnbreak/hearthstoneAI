"""
AI 决策 API 路由模块 (AI Decision Routes)
==========================================

本模块定义了 AI 决策的 HTTP API 端点, 使用 FastAPI 的 APIRouter。

核心功能:
- 提供 POST /api/ai/decision 端点, 触发一次 AI 决策
- 将决策结果写入 ReplayWriter 的回放日志 (recommendations.jsonl)

架构设计:
    使用工厂函数 create_ai_decision_router() 创建路由,
    通过闭包捕获 state_store, decision_service, replay_writer 等依赖,
    实现轻量级的依赖注入, 便于测试和配置。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from ai_backend.ai_decision.service import AiDecisionService
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore


def create_ai_decision_router(
    state_store: StateStore,
    decision_service: AiDecisionService,
    replay_writer: ReplayWriter | None = None,
) -> APIRouter:
    """
    创建 AI 决策的 API 路由 (工厂函数)。

    使用工厂函数而非直接定义路由的原因是依赖注入:
    - state_store:       需要从外部注入, 不同环境可能使用不同的存储后端
    - decision_service:  需要从外部注入, 便于测试时替换为 mock
    - replay_writer:     可选注入, 不需要记录回放时可以传入 None

    参数:
        state_store:       游戏状态存储, 用于获取当前对局的快照
        decision_service:  AI 决策服务, 执行实际的决策逻辑
        replay_writer:     回放写入器 (可选), 用于将决策记录持久化到磁盘

    返回:
        配置好的 FastAPI APIRouter 实例, 可直接挂载到主应用上

    使用方式:
        from fastapi import FastAPI

        app = FastAPI()
        router = create_ai_decision_router(store, service, writer)
        app.include_router(router)
    """
    # 创建新的路由组
    router = APIRouter()

    # -------------------------------------------------------------------------
    # POST /api/ai/decision — 触发一次 AI 决策
    # -------------------------------------------------------------------------
    @router.post("/api/ai/decision")
    async def post_ai_decision() -> dict[str, Any]:
        """
        处理 AI 决策请求。

        流程:
        1. 从 StateStore 获取当前游戏状态快照
        2. 提取最新的游戏状态
        3. 调用 AiDecisionService.decide() 执行 AI 决策
        4. 如果配置了 replay_writer 且决策有效, 将决策写入回放日志
        5. 返回决策结果

        返回:
            AI 决策结果字典 (由 AiDecisionService.decide() 返回)

        注意:
            此端点不需要请求体 — 游戏状态来自 HDT 插件实时推送并存储在 StateStore 中。
            端点被 HDT 插件以轮询或事件触发的方式调用。
        """
        # ---- 步骤 1: 获取游戏状态快照 ----
        # snapshot() 返回完整的对局状态, 包含 latest_state, history 等
        snapshot = state_store.snapshot()

        # ---- 步骤 2: 提取最新状态 ----
        # latest_state 是 HDT 插件最近一次推送的 game_state 事件
        state = state_store.decision_state()

        # ---- 步骤 3: 执行 AI 决策 ----
        decision = decision_service.decide(state)

        # ---- 步骤 4: 写入回放日志 (可选) ----
        # 只有同时满足以下条件才写入:
        # 1. replay_writer 已配置 (不为 None)
        # 2. state 是有效的字典
        # 3. 决策被标记为应该写入 (_should_write_decision)
        if replay_writer is not None and isinstance(state, dict) and _should_write_decision(decision):
            # 构建决策日志条目并写入
            replay_writer.write(_decision_envelope(state, decision))

        # ---- 步骤 5: 返回决策结果 ----
        return decision

    return router


# =============================================================================
# 模块内部辅助函数
# =============================================================================

def _should_write_decision(decision: dict[str, Any]) -> bool:
    """
    判断一条决策是否应该被写入回放日志。

    当前策略: 只有 AI 成功做出合法决策 (plan == "ai_decision") 时才记录。
    - 被拒绝的决策 (ai_decision_rejected) 不记录
    - AI 不可用 (unavailable) 不记录
    - 无状态 (no_state) 不记录

    参数:
        decision: AiDecisionService.decide() 返回的决策结果字典

    返回:
        True 表示应该写入回放日志, False 表示跳过
    """
    return decision.get("plan") == "ai_decision"


def _decision_envelope(
    state: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    """
    将 AI 决策包装为回放日志条目 (envelope)。

    在原始决策结果基础上添加元数据:
    - type:                事件类型 (固定为 "recommendation")
    - game_id:             对局 ID (用于区分不同对局)
    - turn:                回合数 (用于回放时的时序定位)
    - snapshot_timestamp:  状态快照的时间戳
    - generated_at:        决策生成时间 (UTC ISO 8601 格式)
    - action_space:        当时的操作空间 (用于回放分析)

    参数:
        state:    决策时的游戏状态 (包含 game_id, turn, timestamp 等元信息)
        decision: AiDecisionService.decide() 返回的决策结果字典

    返回:
        包装后的日志条目字典, 可直接序列化为 JSON 并写入 JSONL 文件
    """
    # 提取 details 中的 action_space (如果存在)
    details = decision.get("details") or {}

    return {
        # 事件类型: 标记这是一条推荐/决策记录
        "type": "recommendation",

        # 对局上下文: 用于关联同一对局的多条决策记录
        "game_id": state.get("game_id"),
        "turn": state.get("turn"),

        # 状态快照时间: HDT 插件推送状态时的时间戳
        "snapshot_timestamp": state.get("timestamp"),

        # 决策生成时间: 当前时刻的 UTC 时间, 使用 ISO 8601 格式
        "generated_at": datetime.now(timezone.utc).isoformat(),

        # 完整的决策结果
        "recommendation": decision,

        # 当时的操作空间: 用于回放时分析"当时有哪些可选操作"
        "action_space": details.get("action_space"),
    }
