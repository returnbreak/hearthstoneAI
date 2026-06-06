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
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


class ReplayWriter:
    """将消息信封追加写入 JSONL 文件。

    参数:
        log_dir: JSONL 文件输出目录（不存在时自动创建）。
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self._write_lock = threading.RLock()
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
            self._write_dual(envelope, "game_state")
            return
        if envelope_type == "game_event":
            self._write_dual(envelope, "events")
            return
        if envelope_type == "recommendation":
            self._write_dual(
                self._compact_recommendation_envelope(envelope),
                "recommendations",
            )
            return
        if envelope_type == "ai_decision_attempt":
            self._write_ai_decision_attempt(envelope)
            return
        if envelope_type == "game_metadata":
            self._write_dual(envelope, "game_metadata")
            return
        raise ValueError(f"Unsupported envelope type: {envelope_type}")

    def _write_ai_decision_attempt(self, envelope: Mapping[str, Any]) -> None:
        compact = deepcopy(dict(envelope))
        debug = compact.get("ai_debug")
        if isinstance(debug, Mapping):
            relative_path = self._ai_debug_relative_path(compact)
            self._write_pretty(
                self._compact_ai_debug(debug),
                relative_path,
                envelope=compact,
            )

    @staticmethod
    def _compact_recommendation_envelope(
        envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        compact = deepcopy(dict(envelope))
        recommendation = compact.get("recommendation")
        if isinstance(recommendation, dict):
            if recommendation.get("summary") == recommendation.get("reason"):
                recommendation.pop("reason", None)
            validation = recommendation.get("validation")
            if (
                isinstance(validation, dict)
                and validation.get("validation_status") == "passed"
            ):
                validation.pop("reason", None)
        return {
            key: value
            for key, value in compact.items()
            if value is not None and value != [] and value != {}
        }

    @staticmethod
    def _compact_ai_debug(debug: Mapping[str, Any]) -> dict[str, Any]:
        compact = deepcopy(dict(debug))
        request = compact.get("request")
        if isinstance(request, dict):
            request.pop("user_prompt", None)
            model_request = request.get("model_request")
            if isinstance(model_request, dict):
                model_request.pop("messages", None)
                model_request.pop("raw_response_content", None)
                request["model_request"] = {
                    key: value
                    for key, value in model_request.items()
                    if value is not None and value != [] and value != {}
                }
                if not request["model_request"]:
                    request.pop("model_request", None)
        response = compact.get("response")
        if isinstance(response, dict):
            response.pop("raw_model_content", None)
        return compact

    def _write_dual(self, envelope: Mapping[str, Any], basename: str) -> None:
        with self._write_lock:
            self._append(envelope, basename + ".jsonl")
            self._append_pretty_array(envelope, basename + ".json")

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

    def _append_pretty_array(self, envelope: Mapping[str, Any], filename: str) -> None:
        path = self._match_dir(envelope) / filename
        records: list[Any] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, list):
                    records = existing
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                records = []
        records.append(deepcopy(dict(envelope)))
        self._write_json_file(path, records)

    def _write_pretty(
        self,
        payload: Mapping[str, Any],
        filename: str | Path,
        envelope: Mapping[str, Any] | None = None,
    ) -> None:
        match_envelope = envelope or payload
        path = self._match_dir(match_envelope) / filename
        with self._write_lock:
            self._write_json_file(path, payload)

    @staticmethod
    def _write_json_file(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)

    @staticmethod
    def _ai_debug_relative_path(envelope: Mapping[str, Any]) -> Path:
        turn = ReplayWriter._safe_component(str(envelope.get("turn") or "unknown"))
        trigger = ReplayWriter._safe_component(str(envelope.get("trigger") or "unknown"))
        generated_at = ReplayWriter._safe_component(str(envelope.get("generated_at") or "unknown"))
        return Path("debug") / "ai_requests" / f"turn-{turn}-{trigger}-{generated_at}.json"

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)

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
