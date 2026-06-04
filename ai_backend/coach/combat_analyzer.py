"""
战斗分析器：基于公开游戏状态计算合法攻击目标。

本模块实现了炉石传说战斗系统的核心规则判断，包括：
    - 嘲讽（Taunt）机制：有嘲讽随从在场时，必须先攻击嘲讽随从
    - 免疫（Immune）机制：免疫角色不能成为攻击目标
    - 潜行（Stealth）机制：潜行随从不能成为攻击目标
    - 冻结（Frozen）机制：被冻结的角色不能攻击
    - 休眠（Dormant）机制：休眠随从不参与战斗交互
    - 英雄攻击：英雄装备武器后也可以进行攻击

所有判断仅依赖公开状态，不涉及任何隐藏信息。
"""

from __future__ import annotations

from typing import Any


class CombatAnalyzer:
    """计算当前公开状态下所有合法攻击目标的战斗分析器。

    核心职责：
        给定一个游戏状态快照（包含我方场面、敌方场面、双方英雄信息），
        列出我方每个可攻击角色（随从和英雄）可以攻击的所有合法目标。

    使用方式:
        analyzer = CombatAnalyzer()
        legal_attacks = analyzer.legal_attacks(state)
    """

    def legal_attacks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """
        计算当前状态下所有合法的攻击组合。

        攻击目标选择的规则（炉石传说核心战斗规则）：
        1. 如果敌方场上有嘲讽（Taunt）随从，则只能攻击嘲讽随从，
           不能攻击英雄或其他随从。
        2. 如果敌方场上没有嘲讽随从，则可以攻击任意非潜行、非休眠、
           非免疫的敌方随从，以及非免疫的敌方英雄。
        3. 潜行（Stealth）、休眠（Dormant）、免疫（Immune）的随从
           不能成为攻击目标。

        参数:
            state: 游戏状态字典，需包含：
                   - enemy_board: 敌方随从列表
                   - enemy_hero:  敌方英雄信息
                   - my_board:    我方随从列表
                   - my_hero:     我方英雄信息

        返回:
            合法攻击列表，每个元素包含：
            - source:      攻击者 entity_id
            - target:      目标 entity_id 或 "enemy_hero"
            - target_type: 目标类型（"minion" 或 "hero"）
            - damage:      造成的伤害值
        """
        # 获取敌方场面随从列表
        enemy_board = list(state.get("enemy_board") or [])
        # 获取所有可以作为攻击目标的敌方随从（排除潜行、休眠、免疫）
        targets = self._attackable_minions(enemy_board)

        # 检查是否存在嘲讽随从
        taunts = [target for target in targets if target.get("taunt")]
        if taunts:
            # 有嘲讽随从在场：只能攻击嘲讽随从，不能攻击英雄
            targets = taunts
            include_hero = False
        else:
            # 无嘲讽随从：可以攻击英雄（前提是英雄非免疫状态）
            include_hero = not self._hero_is_immune(state.get("enemy_hero"))

        # 为每个攻击者生成攻击方案
        attacks: list[dict[str, Any]] = []
        for attacker in self._attackers(state):
            # 攻击每个合法随从目标
            for target in targets:
                attacks.append({
                    "source": attacker["entity_id"],
                    "target": target.get("entity_id"),
                    "target_type": "minion",
                    "damage": attacker.get("attack", 0),
                })
            # 如果没有嘲讽阻挡，也可以攻击敌方英雄
            if include_hero:
                attacks.append({
                    "source": attacker["entity_id"],
                    "target": "enemy_hero",
                    "target_type": "hero",
                    "damage": attacker.get("attack", 0),
                })
        return attacks

    def _attackers(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """
        收集当前状态下所有可以攻击的我方角色。

        包括：
        - 我方场上可以攻击的随从（can_attack=True、攻击力>0、
          未被冻结、非休眠、非免疫、有剩余攻击次数）
        - 我方英雄（如果装备了武器且满足类似条件）

        参数:
            state: 游戏状态字典。

        返回:
            可攻击角色的列表，英雄的 entity_id 会被设为 "my_hero"。
        """
        attackers: list[dict[str, Any]] = []

        # 检查我方场上的每一个随从
        for minion in state.get("my_board") or []:
            if self._can_attack(minion):
                attackers.append(minion)

        # 检查我方英雄是否可以攻击（例如装备了武器）
        hero = state.get("my_hero") or {}
        if self._can_attack(hero):
            # 复制英雄信息，并为其赋予唯一定位符
            hero_attacker = dict(hero)
            hero_attacker["entity_id"] = "my_hero"
            attackers.append(hero_attacker)

        return attackers

    @staticmethod
    def _can_attack(entity: dict[str, Any]) -> bool:
        """
        判断一个实体（随从或英雄）当前是否可以执行攻击。

        角色不能攻击的条件包括：
        - can_attack 标志为 False（例如刚被召唤的随从、已攻击过的角色）
        - 剩余攻击次数为 0
        - 攻击力为 0
        - 被冻结（Frozen）
        - 处于休眠状态（Dormant）
        - 自身免疫（Immune）状态下通常也不能攻击

        参数:
            entity: 实体信息字典。

        返回:
            True 表示该实体可以攻击，False 表示不能。
        """
        return (
            bool(entity.get("can_attack"))
            and int(entity.get("attacks_remaining") or 0) > 0
            and int(entity.get("attack") or 0) > 0
            and not entity.get("frozen")       # 被冻结的角色跳过攻击阶段
            and not entity.get("dormant")       # 休眠中的随从无法行动
            and not entity.get("immune")        # 免疫角色通常也不能主动攻击
        )

    @staticmethod
    def _attackable_minions(minions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        从随从列表中筛选出可以作为攻击目标的随从。

        被排除的目标类型：
        - 潜行（Stealth）：无法被指定为攻击目标
        - 休眠（Dormant）：尚未激活，无法被交互
        - 免疫（Immune）：无法受到伤害或成为目标

        参数:
            minions: 待筛选的随从列表。

        返回:
            可以作为攻击目标的随从列表。
        """
        return [
            minion for minion in minions
            if not minion.get("stealth")
            and not minion.get("dormant")
            and not minion.get("immune")
        ]

    @staticmethod
    def _hero_is_immune(hero: dict[str, Any] | None) -> bool:
        """
        检查敌方英雄是否处于免疫状态。

        如果英雄存在且其 immune 属性为 True，则返回 True。
        免疫英雄不能成为攻击目标。

        参数:
            hero: 英雄信息字典，可能为 None。

        返回:
            True 表示英雄免疫，不可攻击。
        """
        return bool(hero and hero.get("immune"))
