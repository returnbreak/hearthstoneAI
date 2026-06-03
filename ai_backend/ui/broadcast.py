"""
UI WebSocket 广播中心——管理所有前端 UI 客户端的连接与消息推送。

职责：
  - 维护已连接 UI 客户端的集合
  - 提供 connect / disconnect 连接生命周期管理
  - broadcast() 向所有客户端广播 JSON 消息
  - 发送失败时自动清理断开的客户端

设计要点：
  - 广播过程中若有客户端已断开（send_json 抛异常），
    不会中断对其他客户端的广播，而是在广播结束后统一清理。
  - 线程安全方面依赖 FastAPI / asyncio 的单线程事件循环模型，
    在同一个事件循环中天然安全。
"""

from typing import Any, Dict, Set


class BroadcastHub:
    """WebSocket 广播中心——一对多向 UI 客户端推送消息。"""

    def __init__(self):
        # 已连接的 WebSocket 客户端集合
        # 使用 set 保证 O(1) 的加入/移除/存在性检查
        self._clients: Set[Any] = set()

    @property
    def client_count(self) -> int:
        """当前连接的 UI 客户端数量。"""
        return len(self._clients)

    async def connect(self, websocket: Any) -> None:
        """接受一个新的 WebSocket 连接并加入广播列表。

        参数:
            websocket: fastapi.WebSocket 实例。
        """
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: Any) -> None:
        """手动移除一个 WebSocket 连接。

        参数:
            websocket: 要移除的 fastapi.WebSocket 实例。
        """
        self._clients.discard(websocket)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        """向所有已连接客户端广播一条 JSON 消息。

        实现策略：
          1. 遍历所有客户端尝试发送。
          2. 发送失败（客户端已断开）的加入 failed 列表。
          3. 遍历结束后统一移除所有 failed 客户端。

        这样做的好处：一个客户端断开不会阻止其他客户端收到消息，
        也不会在遍历过程中修改集合导致 RuntimeError。

        参数:
            payload: 待广播的 JSON 可序列化 Dict。
        """
        # 收集发送失败的客户端
        failed = []

        for client in list(self._clients):
            try:
                await client.send_json(payload)
            except Exception:
                # 发送失败 → 标记为待清理
                failed.append(client)

        # 统一清理断开的客户端
        for client in failed:
            self.disconnect(client)
