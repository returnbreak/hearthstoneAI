"""
HDT AI 助手后端入口——FastAPI 应用工厂。

启动方式：
    uv run uvicorn ai_backend.app:app --reload --host 127.0.0.1 --port 8765

架构概览：
  ┌──────────────────────────────────────────────────────┐
  │                  FastAPI 应用                        │
  │                                                      │
  │  /ws/hdt  ──► ingest.router  ──► StateStore         │
  │                  (WebSocket)     ReplayWriter        │
  │                                    BroadcastHub      │
  │                                                      │
  │  /ws/ui   ──► ui.router       ──► BroadcastHub      │
  │  /        ──► index.html           StateStore        │
  │  /api/state ─► REST API                               │
  │                                                      │
  │  /static/* ── 前端 CSS / JS 静态资源                 │
  └──────────────────────────────────────────────────────┘
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ai_backend.coach.recommendation_engine import RecommendationEngine
from ai_backend.coach.routes import create_coach_router
from ai_backend.core.config import LOG_DIR, STATIC_DIR
from ai_backend.ingest.routes import create_ingest_router
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub
from ai_backend.ui.routes import create_ui_router


# ── FastAPI 应用实例 ──────────────────────────────────
app = FastAPI(title="HDT AI Assistant Backend")

# ── 核心组件（应用级单例） ────────────────────────────
# StateStore: 在内存中维护最新游戏状态 + 近期事件环形缓冲区
state_store = StateStore()

# ReplayWriter: 将所有收到的消息追加写入 JSONL 文件（用于回放测试）
replay_writer = ReplayWriter(LOG_DIR)

# BroadcastHub: WebSocket 广播中心——把状态变更推送给所有连接的 UI 客户端
ui_hub = BroadcastHub()
recommendation_engine = RecommendationEngine()

# ── 注册路由 ───────────────────────────────────────────
# /static/* — 前端静态文件（index.html 引用的 CSS、JS 等）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# /ws/hdt、/api/state — HDT 插件数据接入
app.include_router(create_ingest_router(state_store, replay_writer, ui_hub))
app.include_router(create_coach_router(state_store, recommendation_engine, replay_writer))

# /、/ws/ui — 前端页面与 UI WebSocket 推送
app.include_router(create_ui_router(STATIC_DIR, state_store, ui_hub))
