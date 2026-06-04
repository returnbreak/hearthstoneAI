"""
JSONL 回放日志写入器——将所有 HDT 推送的消息原样追加到磁盘文件。

输出文件（位于 LOG_DIR）：
  - game_state.jsonl — 每行一个 game_state 信封
  - events.jsonl    — 每行一个 game_event 信封

JSONL 格式（每行一个完整 JSON 对象，无缩进，无换行）：
    {"type":"game_state","state":{...}}
    {"type":"game_event","event":{...}}

用途：
  - 调试：回放对局时不需要启动炉石或 HDT。
  - 回放测试（第二版）：读取 JSONL 重建时间线，对每个回合重新运行建议链路。
  - 数据沉淀：用于分析 AI 建议质量、Prompt 效果等。
"""

import json
import re
from pathlib import Path
from typing import Any, Mapping


class ReplayWriter:
    """将消息信封追加写入 JSONL 文件。

    参数:
        log_dir: JSONL 文件输出目录（不存在时自动创建）。
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        # 确保目录存在（包括上级目录）
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write(self, envelope: Mapping[str, Any]) -> None:
        """按信封类型分流写入对应的 JSONL 文件。

        参数:
            envelope: 顶层 JSON 对象，type 决定写入哪个文件。

        异常:
            ValueError: 信封类型不受支持（非 game_state 也非 game_event）。
        """
        envelope_type = envelope.get("type")
        if envelope_type == "game_state":
            self._append(envelope, "game_state.jsonl")
            return
        if envelope_type == "game_event":
            self._append(envelope, "events.jsonl")
            return
        if envelope_type == "recommendation":
            self._append(envelope, "recommendations.jsonl")
            return
        raise ValueError(f"Unsupported envelope type: {envelope_type}")

    def _append(self, envelope: Mapping[str, Any], filename: str) -> None:
        """追加一行 JSON 到指定文件。

        使用 ensure_ascii=False 保留中文原文，
        separators=(",", ":") 压缩空格以减小文件体积。
        每行以 \\n 结尾，确保 JSONL 格式规范。
        """
        path = self._match_dir(envelope) / filename
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")

    def _match_dir(self, envelope: Mapping[str, Any]) -> Path:
        game_id = self._game_id(envelope)
        safe_game_id = re.sub(r"[^A-Za-z0-9_.-]", "_", game_id or "unknown_game")
        match_dir = self.log_dir / safe_game_id
        match_dir.mkdir(parents=True, exist_ok=True)
        return match_dir

    @staticmethod
    def _game_id(envelope: Mapping[str, Any]) -> str | None:
        value = envelope.get("game_id")
        if value:
            return str(value)

        state = envelope.get("state")
        if isinstance(state, Mapping):
            value = state.get("game_id")
            if value:
                return str(value)

        event = envelope.get("event")
        if isinstance(event, Mapping):
            value = event.get("game_id")
            if value:
                return str(value)

        return None
