"""
HDT 插件数据接入路由——暴露 WebSocket 端点供 HDT 插件连接。

路由清单：
  GET  /api/state          — REST 风格的状态查询（调试用）
  WS   /ws/hdt             — HDT 插件 WebSocket 接入点（主通道）

数据流（/ws/hdt）：
  插件 → WebSocket → envelope JSON
    ├─→ StateStore.apply(envelope)     # 更新内存中的状态 / 事件
    ├─→ ReplayWriter.write(envelope)   # 追加写入 JSONL 文件
    ├─→ BroadcastHub.broadcast(...)    # 推送给所有 UI 客户端
    └─→ 回复 "ack" 给插件               # 确认收到
"""

from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub


def create_ingest_router(
    state_store: StateStore,
    replay_writer: ReplayWriter,
    ui_hub: BroadcastHub,
) -> APIRouter:
    """创建数据接入路由。

    参数:
        state_store:   内存中的对局状态存储。
        replay_writer: JSONL 回放日志写入器。
        ui_hub:        UI 客户端广播中心。

    返回:
        配置好的 APIRouter，可直接 include_router。
    """
    router = APIRouter()

    @router.get("/api/state")
    async def get_state() -> Dict[str, Any]:
        """REST 接口：获取当前对局状态快照（调试用）。

        GET /api/state → {"latest_state": {...}, "recent_events": [...], "message_count": N}
        """
        return state_store.snapshot()

    @router.websocket("/ws/hdt")
    async def hdt_ingest(websocket: WebSocket) -> None:
        """HDT 插件 WebSocket 主通道。

        插件连接到此端点后持续发送 JSON 信封，
        后端解析后分流到 StateStore、ReplayWriter、BroadcastHub。

        每条消息的回复：
          - 成功: {"type": "ack", "message_count": N}
          - 错误: {"type": "error", "message": "..."}

        连接断开时自动清理，不抛出异常。
        """
        # Step 1: 接受 WebSocket 连接
        await websocket.accept()

        # Step 2: 循环接收消息
        while True:
            try:
                # 从 WebSocket 读取一条 JSON 消息
                envelope = await websocket.receive_json()

                # 基础校验：必须是 JSON 对象，不能是数组或标量
                if not isinstance(envelope, dict):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Envelope must be a JSON object."
                    })
                    continue

                # Step 3: 更新内存状态（game_state → 覆盖, game_event → 追加入队）
                state_store.apply(envelope)

                # Step 4: 追加写入 JSONL 文件（用于回放测试）
                replay_writer.write(envelope)

                # Step 5: 生成当前快照并广播给所有 UI 客户端
                snapshot = state_store.snapshot()
                await ui_hub.broadcast({
                    "type": "backend_update",
                    "snapshot": snapshot,
                    "envelope": envelope,
                })

                # Step 6: 回复插件确认收到
                await websocket.send_json({
                    "type": "ack",
                    "message_count": snapshot["message_count"],
                })

            except WebSocketDisconnect:
                # 插件断开连接（正常退出或网络中断）
                break

            except ValueError as exc:
                # 应用层错误（如不支持的 envelope type）
                # 这类错误只影响当前消息，不中断连接
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })

    return router
