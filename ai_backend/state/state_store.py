"""
对局状态内存存储——维护 HDT 插件推送的最新游戏状态与近期事件。

职责：
  1. 接收 game_state 消息 → 覆盖式更新 latest_state（只保留最新一帧）
  2. 接收 game_event 消息 → 追加到 recent_events 环形队列
  3. 提供 snapshot() 快照方法，返回 {latest_state, recent_events, message_count}

设计要点：
  - latest_state 总是被最新一帧覆盖（不是合并），保证内存中不会无限制增长。
  - recent_events 用 collections.deque 实现，超过 maxlen 自动丢弃最旧条目。
  - 所有输出经过 deepcopy，外部修改不会污染内部状态。
"""

from collections import deque
from copy import deepcopy
from typing import Any, Deque, Dict, Mapping


class StateStore:
    """在内存中缓存当前对局状态与最近的游戏事件。

    参数:
        max_recent_events: 环形缓冲区最大容量，默认 50。
    """

    def __init__(self, max_recent_events: int = 50):
        # 最新一帧 game_state（Dict），为 None 表示尚未收到
        self._latest_state: Dict[str, Any] | None = None

        # 近期事件环形队列，每个元素是一个 game_event Dict
        self._recent_events: Deque[Dict[str, Any]] = deque(maxlen=max_recent_events)

        # 收件计数——统计自启动以来共收到多少条消息
        self._message_count = 0

    def apply(self, envelope: Mapping[str, Any]) -> None:
        """处理一条从 HDT 插件发来的 JSON 信封。

        根据 envelope["type"] 的值决定处理方式：
          - "game_state"  → 覆盖 _latest_state
          - "game_event"  → 追加到 _recent_events 末尾
          - 其他类型      → 抛出 ValueError

        参数:
            envelope: 顶层 JSON 对象，必须包含 "type" 字段。

        异常:
            ValueError: 信封类型不受支持。
        """
        envelope_type = envelope.get("type")
        self._message_count += 1

        if envelope_type == "game_state":
            # 状态快照：直接覆盖（不是合并），保证是最新一帧
            state = envelope.get("state")
            self._latest_state = deepcopy(state) if isinstance(state, dict) else None
            return

        if envelope_type == "game_event":
            # 事件：追加到环形队列，满时自动丢弃最旧的
            event = envelope.get("event")
            if isinstance(event, dict):
                if event.get("type") == "game_started":
                    self._latest_state = None
                    self._recent_events.clear()
                self._recent_events.append(deepcopy(event))
                if event.get("type") == "game_ended":
                    self._latest_state = None
            return

        # 未知类型——拒绝处理，便于及时发现协议不匹配
        raise ValueError(f"Unsupported envelope type: {envelope_type}")

    def snapshot(self) -> Dict[str, Any]:
        """生成当前状态的只读快照。

        返回一个全新的 Dict，包含：
          - latest_state:  最新游戏状态（可能为 None）
          - recent_events: 近期事件列表（最新在前）
          - message_count: 已处理消息总数

        所有值经过 deepcopy，调用方可以随意修改返回值而不影响内部状态。
        """
        return {
            "latest_state": deepcopy(self._latest_state),
            "recent_events": list(deepcopy(self._recent_events)),
            "message_count": self._message_count,
        }
