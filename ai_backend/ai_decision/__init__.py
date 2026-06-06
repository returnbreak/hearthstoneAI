"""
AI 决策模块 (ai_decision)
==========================

本模块实现了炉石传说 AI 决策的核心功能, 通过调用大语言模型 (DeepSeek)
在合法的操作序列中选择最优的一手。

模块架构:
    clients.py   — AI 客户端接口与实现 (DeepSeek 调用、降级处理)
    prompt.py    — 提示词构建器 (游戏状态 + action_space → prompt)
    service.py   — 决策服务编排 (协调各组件完成完整决策流程)
    routes.py    — HTTP API 路由 (FastAPI 端点)

典型使用流程:
    1. 从环境变量创建 AI 客户端
       client = LangChainDeepSeekDecisionClient.from_env()

    2. 创建决策服务
       service = AiDecisionService(RecommendationEngine(), client)

    3. 发起决策
       decision = service.decide(game_state)

    4. (可选) 挂载 HTTP API
       router = create_ai_decision_router(store, service, writer)
       app.include_router(router)

导出的公共接口:
    - AiDecisionClient:              AI 决策客户端协议 (Protocol)
    - AiDecisionService:             决策服务编排类
    - LangChainDeepSeekDecisionClient: 基于 LangChain + DeepSeek 的客户端实现
    - UnavailableAiDecisionClient:   AI 不可用时的降级客户端
"""

from ai_backend.ai_decision.clients import (
    AiDecisionClient,
    LangChainDeepSeekDecisionClient,
    UnavailableAiDecisionClient,
)
from ai_backend.ai_decision.service import AiDecisionService

# ---------------------------------------------------------------------------
# 模块公开 API 列表
# ---------------------------------------------------------------------------
# __all__ 控制 from ai_backend.ai_decision import * 的行为
# 只导出面向外部使用者的核心类和接口
__all__ = [
    "AiDecisionClient",               # 客户端协议
    "AiDecisionService",              # 决策服务
    "LangChainDeepSeekDecisionClient", # DeepSeek 客户端实现
    "UnavailableAiDecisionClient",    # 降级客户端
]
