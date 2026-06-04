"""
卡牌效果识别器：从 HDT 快照或 HearthstoneJSON 数据中识别卡牌效果。

本模块通过正则表达式匹配卡牌描述文本（支持中英文），提取卡牌的核心效果信息。
识别流程分为四个层次，按优先级从高到低：

    1. 法术伤害光环 (spell_damage)：如"法术伤害 +1" / "Spell Damage +1"
    2. 攻击力光环 (attack_aura)：如"相邻的随从获得 +1 攻击力" / "adjacent minions have +1 attack"
    3. 直接伤害 (damage)：如"造成 3 点伤害" / "Deal 3 damage"
    4. 属性增益 (buff)：如"获得 +2/+2" / "Give a minion +2/+2"

同时通过 HearthstoneJSON 卡牌数据库补充卡牌元数据（如卡牌类型），
以辅助效果判定（例如，伤害模式在法术卡上更常见）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ai_backend.core.config import PROJECT_ROOT


@dataclass(frozen=True)
class CardEffect:
    """
    表示一张卡牌被识别出的核心效果的不可变数据类。

    使用 frozen=True 确保效果实例在创建后不可修改，
    便于在多处安全地共享和传递。

    属性:
        kind:                 效果类型（"damage", "buff", "spell_damage", "attack_aura", "unknown"）
        damage:               直接伤害数值（默认 0）
        attack_buff:          攻击力增益数值（默认 0）
        health_buff:          生命值增益数值（默认 0）
        spell_damage:         法术伤害加成（持续性光环，默认 0）
        aura_attack_buff:     攻击力光环加成（如"相邻随从+1攻"，默认 0）
        can_target_enemy_hero: 伤害/效果是否可以指定敌方英雄为目标（默认 False）
        raw_text:             卡牌的原始描述文本（用于调试和展示）
    """
    kind: str
    damage: int = 0
    attack_buff: int = 0
    health_buff: int = 0
    spell_damage: int = 0
    aura_attack_buff: int = 0
    can_target_enemy_hero: bool = False
    raw_text: str = ""


class CardEffectRecognizer:
    """从 HDT 快照或 HearthstoneJSON 卡牌数据中识别简单效果。

    核心职责：
        接收卡牌信息字典，通过正则匹配卡牌描述文本，
        返回结构化的 CardEffect 对象。

    正则模式分为中英文两组，覆盖炉石传说常见的卡牌描述语法。
    模式按优先级排序：先检查光环效果，再检查直接伤害，最后检查增益。

    使用方式:
        recognizer = CardEffectRecognizer()
        effect = recognizer.recognize(card_dict)
    """

    # ==========================================================================
    # 正则表达式模式定义
    # 每组包含英文和中文两个正则模式，覆盖炉石常见卡牌描述格式
    # ==========================================================================

    # 直接伤害模式：匹配 "Deal 3 damage" / "造成 3 点伤害"
    _damage_patterns = (
        re.compile(r"\bdeal[s]?\s+\$?(\d+)\s+damage\b", re.IGNORECASE),
        re.compile(r"造成\s*\$?(\d+)\s*点伤害"),
    )
    # 双围 buff 模式：匹配 "+X/+Y" / "获得 X/Y"
    _stat_buff_patterns = (
        re.compile(r"([+＋]\s*\d+)\s*/\s*([+＋]\s*\d+)"),
        re.compile(r"获得\s*[+＋]?\s*(\d+)\s*/\s*[+＋]?\s*(\d+)"),
    )
    # 攻击力 buff 模式：匹配 "+X attack" / "获得 X 点攻击力"
    _attack_buff_patterns = (
        re.compile(r"[+＋]\s*(\d+)\s+attack", re.IGNORECASE),
        re.compile(r"获得\s*[+＋]?\s*(\d+)\s*点攻击力"),
    )
    # 法术伤害光环模式：匹配 "Spell Damage +1" / "法术伤害 +1"
    _spell_damage_patterns = (
        re.compile(r"\bspell\s+damage\s*[+＋]\s*(\d+)", re.IGNORECASE),
        re.compile(r"法术伤害(?:值)?\s*[+＋]\s*(\d+)"),
    )
    # 相邻随从攻击力光环模式：匹配 "adjacent minions have +1 attack" / "相邻的随从获得 +1 攻击力"
    _adjacent_attack_aura_patterns = (
        re.compile(r"\badjacent\s+minions\s+have\s+[+＋]\s*(\d+)\s+attack", re.IGNORECASE),
        re.compile(r"相邻的随从.*[+＋]\s*(\d+).*攻击力"),
    )

    def __init__(self, card_data_paths: Iterable[Path] | None = None):
        """
        初始化卡牌效果识别器，并加载卡牌数据库。

        卡牌数据库（来自 HearthstoneJSON）提供额外的元数据
        （如卡牌类型），用于辅助效果判定——例如，伤害模式
        仅在卡牌类型为 SPELL 时才返回 damage 效果。

        参数:
            card_data_paths: 卡牌 JSON 数据文件路径的可迭代对象。
                             如果为 None，则自动从默认目录加载。
        """
        paths = card_data_paths if card_data_paths is not None else self._default_card_data_paths()
        # 加载卡牌数据，构建 card_id → 卡牌信息的索引
        self._cards_by_id = self._load_card_data(paths)

    def recognize(self, card: dict[str, Any]) -> CardEffect:
        """
        识别单张卡牌的核心效果。

        识别流程（按优先级从高到低）：
        1. 合并卡牌元数据（将 HDT 快照与卡牌数据库合并）
        2. 规范化描述文本（去除 HTML 标签、换行符等）
        3. 依次尝试匹配：法术伤害光环 → 攻击力光环 → 直接伤害 → 属性增益
        4. 如果以上都不匹配，返回 kind="unknown"

        参数:
            card: 卡牌信息字典，至少需包含 "text" 和 "card_id" 字段。

        返回:
            CardEffect 实例，包含识别出的效果类型和数值。
        """
        # 合并 HDT 快照数据与卡牌数据库元数据
        merged = self._merge_catalog_data(card)
        # 规范化文本：去除 HTML 标签、统一换行符
        text = self._normalize_text(str(merged.get("text") or ""))
        card_type = str(merged.get("type") or "").upper()

        # 优先级 1：检查法术伤害光环
        spell_damage = self._read_first_int(text, self._spell_damage_patterns)
        if spell_damage:
            return CardEffect(kind="spell_damage", spell_damage=spell_damage, raw_text=text)

        # 优先级 2：检查相邻随从攻击力光环
        aura_attack = self._read_first_int(text, self._adjacent_attack_aura_patterns)
        if aura_attack:
            return CardEffect(kind="attack_aura", aura_attack_buff=aura_attack, raw_text=text)

        # 优先级 3：检查直接伤害（仅法术卡）
        damage = self._read_first_int(text, self._damage_patterns)
        if damage and card_type == "SPELL":
            return CardEffect(
                kind="damage",
                damage=damage,
                can_target_enemy_hero=self._can_target_enemy_hero(text),
                raw_text=text,
            )

        # 优先级 4：检查属性增益（攻击力 / 生命值）
        attack_buff, health_buff = self._read_buff(text)
        if attack_buff or health_buff:
            return CardEffect(
                kind="buff",
                attack_buff=attack_buff,
                health_buff=health_buff,
                raw_text=text,
            )

        # 无法识别任何已知效果
        return CardEffect(kind="unknown", raw_text=text)

    def _merge_catalog_data(self, card: dict[str, Any]) -> dict[str, Any]:
        """
        将 HDT 快照中的卡牌数据与卡牌数据库中的元数据合并。

        卡牌数据库（来自 HearthstoneJSON）提供完整的卡牌信息
        （类型、标准描述文本等），HDT 快照提供运行时的动态数据
        （如被 buff 后的费用、血量等）。合并策略为：
        以数据库数据为基础，用 HDT 快照中的非空值覆盖。

        参数:
            card: HDT 快照中的卡牌信息字典。

        返回:
            合并后的卡牌信息字典。
        """
        catalog = self._cards_by_id.get(str(card.get("card_id") or ""))
        if not catalog:
            # 卡牌数据库中无此卡牌，直接使用 HDT 快照数据
            return card
        # 以数据库数据为基底
        merged = dict(catalog)
        # 用 HDT 快照中的非空、非空字符串值覆盖
        merged.update({key: value for key, value in card.items() if value not in (None, "")})
        return merged

    @classmethod
    def _load_card_data(cls, paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
        """
        从指定路径加载卡牌 JSON 数据并构建索引。

        遍历所有提供的 JSON 文件路径，解析其中的卡牌数组，
        构建 card_id → 卡牌信息的索引字典。

        支持多种 JSON 格式（cards.collectible.json、standard.zhCN.json 等）。

        参数:
            paths: 卡牌 JSON 文件路径的可迭代对象。

        返回:
            card_id 到卡牌信息字典的映射。
        """
        cards_by_id: dict[str, dict[str, Any]] = {}
        for path in paths:
            # 跳过不存在的文件
            if not path.exists() or not path.is_file():
                continue
            try:
                cards = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                # 文件读取或 JSON 解析失败则跳过
                continue
            if not isinstance(cards, list):
                continue
            for card in cards:
                if not isinstance(card, dict):
                    continue
                # 支持 "id" 和 "card_id" 两种字段名
                card_id = card.get("id") or card.get("card_id")
                if card_id:
                    # 只保留需要的字段，减少内存占用
                    cards_by_id[str(card_id)] = {
                        "card_id": card_id,
                        "name": card.get("name"),
                        "cost": card.get("cost"),
                        "type": card.get("type"),
                        "text": card.get("text"),
                        "attack": card.get("attack"),
                        "health": card.get("health"),
                    }
        return cards_by_id

    @staticmethod
    def _default_card_data_paths() -> list[Path]:
        """
        获取默认的卡牌数据文件路径列表。

        在项目根目录的 hearthstone_data 子目录下搜索公认的
        卡牌数据 JSON 文件。支持的文件包括标准/狂野模式的
        中英文卡牌数据，以及收藏用卡牌数据。

        返回:
            匹配的卡牌 JSON 文件路径列表。
        """
        data_dir = PROJECT_ROOT / "hearthstone_data"
        if not data_dir.exists():
            return []
        # 公认的卡牌数据文件名（来自 HearthstoneJSON）
        allowed_names = {
            "cards.collectible.json",
            "standard.zhCN.json",
            "wild.zhCN.json",
            "standard.enUS.json",
            "wild.enUS.json",
        }
        return [path for path in data_dir.rglob("*.json") if path.name in allowed_names]

    @staticmethod
    def _normalize_text(text: str) -> str:
        """
        规范化卡牌描述文本。

        处理操作：
        1. 去除 HTML 标签（如 <b>、</b>、<i> 等）
        2. 将转义的换行符 \\n 替换为空格
        3. 将实际换行符替换为空格
        4. 去除首尾空白字符

        参数:
            text: 原始卡牌描述文本。

        返回:
            规范化后的纯文本。
        """
        return re.sub(r"<[^>]+>", "", text).replace("\\n", " ").replace("\n", " ").strip()

    @staticmethod
    def _read_first_int(text: str, patterns: tuple[re.Pattern[str], ...]) -> int:
        """
        使用给定的一组正则模式依次匹配文本，返回第一个匹配的整数值。

        模式按顺序尝试：先英文、后中文，或按调用者指定的优先级顺序。
        一旦某个模式匹配成功，立即返回捕获的数字。

        参数:
            text: 待匹配的卡牌描述文本。
            patterns: 正则模式元组，按优先级排序。

        返回:
            匹配到的第一个整数值，如果没有匹配则返回 0。
        """
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return int(match.group(1))
        return 0

    @classmethod
    def _read_buff(cls, text: str) -> tuple[int, int]:
        """
        尝试从文本中解析属性增益（攻击力 / 生命值）。

        匹配策略：
        1. 先尝试匹配双围 buff 格式（"+X/+Y" 或 "获得 X/Y"），
           同时返回攻击力和生命值增益。
        2. 如果双围不匹配，再尝试匹配纯攻击力 buff 格式
           （"+X attack" 或 "获得 X 点攻击力"），此时生命值增益为 0。

        参数:
            text: 卡牌描述文本。

        返回:
            (攻击力增益, 生命值增益) 元组，单位为整数值。
        """
        # 尝试双围 buff 格式
        for pattern in cls._stat_buff_patterns:
            match = pattern.search(text)
            if match:
                return cls._read_signed_number(match.group(1)), cls._read_signed_number(match.group(2))
        # 尝试纯攻击力 buff 格式
        for pattern in cls._attack_buff_patterns:
            match = pattern.search(text)
            if match:
                return int(match.group(1)), 0
        return 0, 0

    @staticmethod
    def _read_signed_number(value: str) -> int:
        """
        从带符号的字符串中提取整数值。

        去除所有非数字和非减号字符后解析为整数，
        能正确处理 "+2"、"＋2"（全角加号）、"-1" 等格式。

        参数:
            value: 可能包含符号、空格等格式字符的数值字符串。

        返回:
            解析后的整数值。
        """
        return int(re.sub(r"[^\d-]", "", value))

    @staticmethod
    def _can_target_enemy_hero(text: str) -> bool:
        """
        判断卡牌效果是否可以指定敌方英雄为目标。

        判断规则：
        - 如果文本只提到 "minion"（随从）而未提到 "hero"（英雄），
          则不能打脸（返回 False）。
        - 如果文本只提到 "随从" 而未提到 "英雄"，
          则不能打脸（返回 False）。
        - 其他情况默认可以打脸（返回 True）。

        参数:
            text: 卡牌描述文本（已转为小写/原始中文）。

        返回:
            True 表示可以指定敌方英雄，False 表示不能。
        """
        lowered = text.lower()
        # 英文：只提到随从没提到英雄 → 不能打脸
        if "minion" in lowered and "hero" not in lowered:
            return False
        # 中文：只提到随从没提到英雄 → 不能打脸
        if "随从" in text and "英雄" not in text:
            return False
        return True
