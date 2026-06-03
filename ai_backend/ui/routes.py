"""
前端 UI 路由——提供网页入口和 WebSocket 推送通道。

路由清单：
  GET  /           — 返回 index.html（单页面应用首页）
  WS   /ws/ui      — UI 客户端 WebSocket 通道（接收后端推送）

/ws/ui 与 /ws/hdt 的区别：
  - /ws/hdt：HDT 插件连接，负责"写"（向 StateStore 推送数据）
  - /ws/ui： 浏览器连接，负责"读"（接收 BroadcastHub 的广播）
  UI 端发送的消息会被忽略（仅维持心跳连接）。
"""

from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub


def create_ui_router(
    static_dir: Path,
    state_store: StateStore,
    ui_hub: BroadcastHub,
) -> APIRouter:
    """创建 UI 前端路由。

    参数:
        static_dir:  静态文件目录（包含 index.html）。
        state_store: 对局状态存储（用于首次连接时发送当前快照）。
        ui_hub:      UI 广播中心（管理客户端连接与消息推送）。

    返回:
        配置好的 APIRouter。
    """
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """首页——返回 ai_backend/ui/static/index.html 的内容。

        使用 HTMLResponse 而非直接返回文件路径，
        确保浏览器正确解析 HTML 而非下载文件。
        """
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @router.websocket("/ws/ui")
    async def ui_updates(websocket: WebSocket) -> None:
        """UI 客户端 WebSocket 通道。

        连接建立后：
          1. 注册到 BroadcastHub（此后自动接收所有后端推送）
          2. 立即发送一次当前快照（确保新打开的页面有初始数据）
          3. 进入被动等待——UI 发送的消息被忽略（保持连接活性）

        连接断开时自动从 BroadcastHub 注销。
        """
        # Step 1: 注册客户端到广播中心
        await ui_hub.connect(websocket)

        try:
            # Step 2: 立即发送当前快照（新打开的页面需要初始状态）
            await websocket.send_json({
                "type": "backend_update",
                "snapshot": state_store.snapshot(),
            })

            # Step 3: 维持连接——接收但不处理 UI 发来的消息
            # receive_text 会阻塞直到有消息或断开
            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            # 浏览器关闭或刷新页面时触发——从广播中心注销
            ui_hub.disconnect(websocket)

    return router
