"""
AI 决策客户端模块 (AI Decision Clients)
==========================================

本模块定义了与 AI 大语言模型交互的客户端接口和实现, 用于在炉石传说游戏中
根据当前局面和合法操作序列, 让 AI 选择最优的一手。

核心组件:
- AiDecisionClient:    协议接口, 定义 decide() 方法的签名
- UnavailableAiDecisionClient: 当 AI 服务不可用时返回的降级客户端
- LangChainDeepSeekDecisionClient: 通过 LangChain 调用 DeepSeek 大模型的正式客户端

使用方式:
    # 从环境变量自动创建 (推荐)
    client = LangChainDeepSeekDecisionClient.from_env()

    # 或手动传入 ChatDeepSeek 实例
    from langchain_deepseek import ChatDeepSeek
    chat_model = ChatDeepSeek(model="deepseek-v4-flash", api_key="...")
    client = LangChainDeepSeekDecisionClient(chat_model)
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# 默认模型名称
# ---------------------------------------------------------------------------

# 默认使用的 DeepSeek 模型标识符, 可通过环境变量 DEEPSEEK_MODEL 覆盖
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_AI_DECISION_TIMEOUT_SECONDS = 15
DEFAULT_AI_DECISION_MAX_TOKENS = 384

# ---------------------------------------------------------------------------
# AI 决策输出的 JSON Schema (期望的响应格式)
# ---------------------------------------------------------------------------

DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,  # 禁止额外字段, 确保输出可控
    "properties": {
        "chosen_sequence_id": {
            "type": "string",
            # 描述: AI 从 legal_sequences 中选择的序列 ID
        },
        "reason": {
            "type": "string",
            # 描述: 选择该序列的战术理由 (简短说明)
        },
        "risk": {
            "type": "string",
            # 描述: 该选择的风险提示
        },
        "confidence": {
            "type": "number",
            "minimum": 0,   # 最低置信度
            "maximum": 1,   # 最高置信度 (1.0 表示完全确定)
        },
    },
    "required": [
        "chosen_sequence_id",  # 必填: 必须选择一个序列
        "reason",              # 必填: 必须给出理由
        "risk",                # 必填: 必须评估风险
        "confidence",          # 必填: 必须给出置信度
    ],
}


# =============================================================================
# AiDecisionClient 协议
# =============================================================================

class AiDecisionClient(Protocol):
    """
    AI 决策客户端的协议接口 (Protocol Class)。

    任何实现了 decide() 方法的类都可以作为 AI 决策客户端使用。
    使用 Protocol 而非 ABC 可以利用 Python 的结构类型 (structural typing),
    无需显式继承即可通过类型检查。

    方法:
        decide(prompt) -> dict:
            接收一个包含 "system" 和 "user" 键的提示词字典,
            返回 AI 的决策结果字典。
    """

    def decide(self, prompt: dict[str, str]) -> dict[str, Any]:
        """
        根据提示词让 AI 做出决策。

        参数:
            prompt: 字典, 包含两个键:
                - "system": 系统提示词, 定义 AI 的角色和行为规则
                - "user":   用户提示词, 包含当前游戏状态和可选操作

        返回:
            字典, 应包含 chosen_sequence_id, reason, risk, confidence 等字段
        """
        ...


# =============================================================================
# UnavailableAiDecisionClient — AI 服务不可用时的降级方案
# =============================================================================

class UnavailableAiDecisionClient:
    """
    AI 决策服务不可用时的降级客户端 (Fallback Client)。

    当 DeepSeek API Key 未配置或依赖库未安装时, 使用此客户端替代。
    它的 decide() 方法始终返回一个表示"不可用"的固定结果,
    不会抛出异常, 确保上层调用方可以正常处理。

    属性:
        _reason: 不可用的原因描述 (面向用户的说明)
        _error:  具体的错误信息 (面向开发者的诊断信息)
    """

    def __init__(
        self,
        reason: str = "Set DEEPSEEK_API_KEY to enable LangChain DeepSeek decisions.",
        error: str = "AI decision client is not configured.",
    ):
        """
        初始化降级客户端。

        参数:
            reason: 向用户展示的不可用原因
            error:  向开发者展示的错误详情 (如缺少依赖、API Key 未设置等)
        """
        self._reason = reason
        self._error = error

    def decide(self, prompt: dict[str, str]) -> dict[str, Any]:
        """
        返回一个表示 AI 决策不可用的固定响应。

        参数:
            prompt: 提示词字典 (在此客户端中被忽略, 仅保持接口一致)

        返回:
            包含 error 标记和默认值的字典, 所有字段均为空或默认值
        """
        return {
            "error": self._error,                     # 错误信息, 供上层判断
            "chosen_sequence_id": "",                 # 空字符串表示未选择任何序列
            "reason": self._reason,                   # 不可用的原因说明
            "risk": "No AI decision was generated.",  # 无 AI 决策时的风险提示
            "confidence": 0.0,                        # 置信度为 0, 表示完全不可信
        }


# =============================================================================
# LangChainDeepSeekDecisionClient — 正式的 DeepSeek AI 决策客户端
# =============================================================================

class LangChainDeepSeekDecisionClient:
    """
    基于 LangChain + DeepSeek 的 AI 决策客户端。

    通过 LangChain 的 ChatDeepSeek 模型与 DeepSeek 大语言模型通信,
    将游戏状态和可选操作序列打包成提示词发送给模型,
    并解析模型返回的 JSON 决策结果。

    典型用法:
        # 方式一: 从环境变量自动配置
        client = LangChainDeepSeekDecisionClient.from_env()

        # 方式二: 手动传入已配置的 ChatDeepSeek 实例
        from langchain_deepseek import ChatDeepSeek
        chat_model = ChatDeepSeek(model="deepseek-v4-flash", api_key="...")
        client = LangChainDeepSeekDecisionClient(chat_model)

        # 调用决策
        prompt = {"system": "你是一个炉石传说决策助手...", "user": "{...}"}
        result = client.decide(prompt)
        # result: {"chosen_sequence_id": "seq-001", "reason": "...", ...}

    属性:
        _chat_model: LangChain ChatDeepSeek 模型实例
        _model_name: 当前使用的模型名称
    """

    def __init__(
        self,
        chat_model: Any,
        model_name: str = DEFAULT_DEEPSEEK_MODEL,
        use_streaming: bool = False,
    ):
        """
        初始化 DeepSeek 决策客户端。

        参数:
            chat_model: LangChain ChatDeepSeek 实例, 负责实际的 API 调用
            model_name: 模型名称标识符 (用于日志和调试)
        """
        self._chat_model = chat_model
        self._model_name = model_name
        self._use_streaming = use_streaming
        self.last_request_debug: dict[str, Any] | None = None

    # -------------------------------------------------------------------------
    # 工厂方法: 从环境变量创建客户端
    # -------------------------------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AiDecisionClient:
        """
        工厂方法: 从环境变量读取配置并创建客户端实例。

        这是推荐的创建方式。它会按顺序检查:
        1. DEEPSEEK_API_KEY 是否存在 → 不存在则返回 UnavailableAiDecisionClient
        2. langchain_deepseek 依赖是否可导入 → 不可导入则返回降级客户端
        3. 读取可选的 DEEPSEEK_MODEL, AI_DECISION_TEMPERATURE, AI_DECISION_TIMEOUT_SECONDS

        参数:
            env: 可选的字典式环境变量映射 (默认为 os.environ)。
                 支持传入自定义字典, 便于测试。

        返回:
            AiDecisionClient 实例:
            - 配置成功时返回 LangChainDeepSeekDecisionClient
            - 配置失败时返回 UnavailableAiDecisionClient

        环境变量:
            DEEPSEEK_API_KEY:           必填, DeepSeek API 密钥
            DEEPSEEK_MODEL:             可选, 模型名称 (默认 "deepseek-v4-flash")
            AI_DECISION_TEMPERATURE:     可选, 模型温度参数 (默认 0.2, 越低越确定)
            AI_DECISION_TIMEOUT_SECONDS: 可选, API 超时秒数 (默认 15)
        """
        # 获取环境变量源: 优先使用传入的 env, 否则使用系统环境变量
        values = os.environ if env is None else env

        # ---- 步骤 1: 检查 API Key 是否存在 ----
        api_key = values.get("DEEPSEEK_API_KEY")
        if not api_key:
            # API Key 缺失 → 返回降级客户端
            return UnavailableAiDecisionClient()

        # ---- 步骤 2: 检查 langchain_deepseek 依赖是否可导入 ----
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError as exc:
            # 依赖缺失 → 返回降级客户端, 附带安装提示
            return UnavailableAiDecisionClient(
                reason="Install langchain and langchain-deepseek to enable AI decisions.",
                error=f"LangChain DeepSeek dependency is missing: {exc}",
            )

        # ---- 步骤 3: 读取可选配置并创建 ChatDeepSeek 实例 ----
        model_name = values.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)

        # 温度 (temperature): 控制输出的随机性
        # - 0.0 = 完全确定性 (总是选择概率最高的 token)
        # - 1.0 = 高随机性 (适合创意生成)
        # - 这里默认 0.2, 在确定性和灵活性之间取得平衡
        temperature = float(values.get("AI_DECISION_TEMPERATURE", "0.2"))

        # 超时时间: API 调用等待的最大秒数
        timeout = int(values.get(
            "AI_DECISION_TIMEOUT_SECONDS",
            str(DEFAULT_AI_DECISION_TIMEOUT_SECONDS),
        ))
        max_tokens = int(values.get(
            "AI_DECISION_MAX_TOKENS",
            str(DEFAULT_AI_DECISION_MAX_TOKENS),
        ))
        use_streaming = str(values.get("AI_DECISION_STREAMING", "false")).lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

        # 创建 LangChain ChatDeepSeek 模型实例
        chat_model = ChatDeepSeek(
            model=model_name,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
            model_kwargs={"response_format": {"type": "json_object"}},
        )

        return cls(chat_model=chat_model, model_name=model_name, use_streaming=use_streaming)

    # -------------------------------------------------------------------------
    # 核心方法: 发送决策请求
    # -------------------------------------------------------------------------

    def decide(self, prompt: dict[str, str]) -> dict[str, Any]:
        """
        向 DeepSeek 模型发送决策请求并解析响应。

        将 system 和 user 提示词组装成 LangChain 消息格式,
        调用 ChatDeepSeek 模型, 然后解析返回的 JSON 结果。

        参数:
            prompt: 字典, 包含:
                - "system": 系统提示词 (定义 AI 角色和行为规则)
                - "user":   用户提示词 (包含游戏状态和可选序列的 JSON)

        返回:
            解析后的决策字典。如果 AI 调用失败, 返回包含 error 字段的降级结果。

        异常处理:
            任何异常都会被捕获并转换为包含 error 字段的响应字典,
            确保上层调用方不需要处理异常。
        """
        # ---- 组装 LangChain 消息格式 ----
        # LangChain 使用 (role, content) 元组列表作为消息格式
        # system 消息: 定义 AI 助手的行为规则
        # human 消息:  包含游戏状态 + 输出格式要求
        messages = [
            ("system", prompt["system"]),
            (
                "human",
                # 在用户提示词末尾追加输出格式指令, 引导模型返回正确的 JSON
                prompt["user"]
                + "\n\nReturn only valid JSON with keys: "
                "chosen_sequence_id, reason, risk, confidence. "
                "The JSON string values for reason and risk must be Chinese.",
            ),
        ]
        self.last_request_debug = {
            "model": self._model_name,
            "streaming": self._use_streaming,
            "messages": messages,
        }

        # ---- 调用模型并解析响应 ----
        try:
            if self._use_streaming and hasattr(self._chat_model, "stream"):
                content = self._stream_response_content(messages)
            else:
                # invoke() 是 LangChain 的标准调用方法, 发送消息并获取响应
                response = self._chat_model.invoke(messages)
                # ChatDeepSeek 返回的响应对象可能有 content 属性, 也可能直接是字符串
                # 使用 getattr 安全地获取 content, 兼容两种返回格式
                content = getattr(response, "content", response)
            if self.last_request_debug is not None:
                self.last_request_debug["raw_response_content"] = self._content_to_text(content)
            return self._parse_response_content(content)
        except Exception as exc:
            # ---- 异常处理: 返回降级结果 ----
            # 捕获所有异常 (网络错误、超时、API 限流等),
            # 转换为主流兼容的响应格式
            return {
                "error": str(exc),                       # 原始异常信息
                "chosen_sequence_id": "",                # 无有效选择
                "reason": "AI request failed.",          # 通用失败原因
                "risk": "No AI decision was generated.", # 无 AI 决策时的风险
                "confidence": 0.0,                       # 置信度为 0
            }

    def _stream_response_content(self, messages: list[tuple[str, str]]) -> str:
        parts: list[str] = []
        for chunk in self._chat_model.stream(messages):
            parts.append(self._content_to_text(getattr(chunk, "content", chunk)))
        return "".join(parts)

    # -------------------------------------------------------------------------
    # 响应解析方法 (类方法, 可独立测试)
    # -------------------------------------------------------------------------

    @classmethod
    def _parse_response_content(cls, content: Any) -> dict[str, Any]:
        """
        解析 AI 模型返回的原始内容, 提取 JSON 决策结果。

        处理以下情况:
        1. 内容外包裹了 Markdown 代码块 (```json ... ```)
        2. JSON 被嵌入在文本中间 (提取第一个 { 到最后一个 })
        3. 纯文本内容 (转换为字符串后再解析)

        参数:
            content: AI 模型返回的原始内容 (可能是 str, list, 或其他类型)

        返回:
            解析后的字典。如果解析失败, 返回包含 error 字段的默认字典。
        """
        # ---- 步骤 1: 将内容统一转换为纯文本 ----
        text = cls._content_to_text(content).strip()

        # ---- 步骤 2: 去除可能的 Markdown 代码块标记 ----
        # 有些模型会在 JSON 外面包裹 ```json ... ``` 或 ``` ... ```
        if text.startswith("```"):
            text = cls._strip_markdown_fence(text)

        # ---- 步骤 3: 提取 JSON 对象 ----
        # 找到文本中第一个 { 和最后一个 } 之间的内容
        # 这样可以处理模型在 JSON 前后附加多余文字的情况
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start : end + 1]

        # ---- 步骤 4: 尝试 JSON 解析 ----
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # JSON 格式错误 → 返回错误信息
            return {
                "error": f"AI response was not valid JSON: {exc}",
                "chosen_sequence_id": "",
                "reason": "AI response could not be parsed.",
                "risk": "No AI decision was generated.",
                "confidence": 0.0,
            }

        # ---- 步骤 5: 验证解析结果是否为字典 ----
        # 即使 JSON 合法, 也可能解析出数组、字符串等非对象类型
        if not isinstance(parsed, dict):
            return {
                "error": "AI response JSON root was not an object.",
                "chosen_sequence_id": "",
                "reason": "AI response had the wrong shape.",
                "risk": "No AI decision was generated.",
                "confidence": 0.0,
            }

        return parsed

    # -------------------------------------------------------------------------
    # 辅助方法: 内容类型转换
    # -------------------------------------------------------------------------

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """
        将 AI 模型返回的各种类型的内容统一转换为纯文本字符串。

        LangChain 不同模型可能返回不同格式的 content:
        - str:  直接返回
        - list: 可能是 [{"text": "..."}, {"text": "..."}] 或混合格式
        - 其他: 使用 str() 转换

        参数:
            content: 任意类型的响应内容

        返回:
            提取出的纯文本字符串
        """
        # 情况 1: 已经是字符串 → 直接返回
        if isinstance(content, str):
            return content

        # 情况 2: 是列表 → 遍历提取文本片段
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    # 列表元素是纯字符串
                    parts.append(item)
                elif isinstance(item, dict):
                    # 列表元素是字典, 尝试提取 "text" 或 "content" 字段
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
            # 用换行符连接所有文本片段
            return "\n".join(parts)

        # 情况 3: 其他类型 → 强制转换为字符串
        return str(content)

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        """
        去除 Markdown 代码块标记 (fenced code block)。

        处理形如:
            ```json
            {"chosen_sequence_id": "seq-001", ...}
            ```

        转换为:
            {"chosen_sequence_id": "seq-001", ...}

        参数:
            text: 可能包含 Markdown 代码块标记的文本

        返回:
            去除代码块标记后的纯内容
        """
        lines = text.splitlines()

        # 去除开头的代码块标记行 (如 ```json 或 ```)
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        # 去除末尾的代码块结束标记 (```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        # 重新拼接并去除首尾空白
        return "\n".join(lines).strip()
