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
        self._game_metadata: Dict[str, Any] | None = None
        self._hand_card_overrides: Dict[tuple[str, int], Dict[str, Any]] = {}

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
            state_copy = deepcopy(state) if isinstance(state, dict) else None
            if isinstance(state_copy, dict):
                self._apply_hand_card_overrides(state_copy)
            self._latest_state = state_copy
            return

        if envelope_type == "game_event":
            # 事件：追加到环形队列，满时自动丢弃最旧的
            event = envelope.get("event")
            if isinstance(event, dict):
                if event.get("type") == "game_started":
                    self._latest_state = None
                    self._game_metadata = None
                    self._hand_card_overrides.clear()
                    self._recent_events.clear()
                self._record_hand_card_override(event)
                self._recent_events.append(deepcopy(event))
                if event.get("type") == "game_ended":
                    self._latest_state = None
            return

        if envelope_type == "game_metadata":
            self._game_metadata = deepcopy(dict(envelope))
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
            "game_metadata": deepcopy(self._game_metadata),
            "recent_events": list(deepcopy(self._recent_events)),
            "message_count": self._message_count,
        }

    def decision_state(self) -> Dict[str, Any] | None:
        if self._latest_state is None:
            return None
        state = deepcopy(self._latest_state)
        state["game_metadata"] = deepcopy(self._game_metadata)
        state["recent_events"] = list(deepcopy(self._recent_events))
        return state

    def _record_hand_card_override(self, event: Mapping[str, Any]) -> None:
        if event.get("player") != "me" or event.get("type") != "card_played":
            return
        if not _is_hand_transform_to_coin_event(event):
            return

        target = event.get("target")
        target_entity_id = None
        if isinstance(target, Mapping):
            target_entity_id = _as_positive_int(target.get("entity_id"))
        target_entity_id = target_entity_id or _as_positive_int(event.get("target_entity_id"))
        if target_entity_id is None:
            return

        game_id = str(event.get("game_id") or "")
        if not game_id:
            return

        self._hand_card_overrides[(game_id, target_entity_id)] = {
            "card_id": "GAME_005",
            "name": "幸运币",
            "cost": 0,
            "type": "SPELL",
            "text": "在本回合中，获得一个法力水晶。",
            "transformed_from_card_id": _target_value(target, "card_id"),
            "transformed_from_name": _target_value(target, "name"),
            "transform_source_card_id": event.get("card_id"),
            "transform_source_name": event.get("name"),
        }

    def _apply_hand_card_overrides(self, state: Dict[str, Any]) -> None:
        game_id = str(state.get("game_id") or "")
        if not game_id:
            return
        hand = state.get("hand")
        if not isinstance(hand, list):
            return

        for card in hand:
            if not isinstance(card, dict):
                continue
            entity_id = _as_positive_int(card.get("entity_id"))
            if entity_id is None:
                continue
            override = self._hand_card_overrides.get((game_id, entity_id))
            if not override:
                continue
            original_card_id = card.get("card_id")
            original_name = card.get("name")
            card.update({
                key: value
                for key, value in override.items()
                if value not in (None, "")
            })
            card.setdefault("transformed_from_card_id", original_card_id)
            card.setdefault("transformed_from_name", original_name)


def _is_hand_transform_to_coin_event(event: Mapping[str, Any]) -> bool:
    card_id = str(event.get("card_id") or "")
    name = str(event.get("name") or "")
    return card_id == "CATA_200" or name == "古神的眼线"


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else None


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
