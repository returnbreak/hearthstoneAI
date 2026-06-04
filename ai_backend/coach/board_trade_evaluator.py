"""
场面交换评估器：评估并推荐最优的随从交换方案。

本模块实现了基于启发式评分的随从交换评估。当玩家需要考虑
用我方随从攻击敌方随从（而非打脸）时，交换评估器会遍历所有
合法的攻击选项，对每个"随从攻击随从"的方案进行评分，
并返回得分最高的交换方案。

评分考虑因素：
    - 能否击杀目标（核心因素）
    - 目标随从的攻击力和生命值（击杀收益）
    - 目标是否具有嘲讽（额外加分，因为嘲讽阻挡打脸）
    - 目标是否具有吸血（额外加分，阻止对手回复）
    - 剧毒/圣盾等特殊词条的交互
"""

from __future__ import annotations

from typing import Any

from ai_backend.coach.combat_analyzer import CombatAnalyzer


class BoardTradeEvaluator:
    """对合法的随从/英雄攻击中的场面交换进行评分和择优。

    核心职责：
        给定游戏状态，遍历所有合法的随从间攻击选项，
        为每个交换计算启发式分数，返回得分最高的方案。

    使用方式:
        evaluator = BoardTradeEvaluator(combat_analyzer)
        best = evaluator.best_trade(state)
    """

    def __init__(self, combat_analyzer: CombatAnalyzer | None = None):
        """
        初始化场面交换评估器。

        参数:
            combat_analyzer: 战斗分析器实例，用于计算合法攻击。
                             如果为 None，则自动创建默认实例。
        """
        self._combat_analyzer = combat_analyzer or CombatAnalyzer()

    def best_trade(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """
        找出当前局面下得分最高的随从交换方案。

        遍历所有合法攻击中的随从攻击（排除直接打脸的选项），
        为每个交换计算启发式分数，返回得分最高的方案。

        参数:
            state: 游戏状态字典，需包含敌我双方场面信息。

        返回:
            最佳交换方案字典，包含类型、攻击者、目标、得分和理由；
            如果没有合法的随从攻击目标，返回 None。
        """
        # 按 entity_id 索引敌方随从，便于快速查找目标属性
        targets_by_id = {
            minion.get("entity_id"): minion
            for minion in state.get("enemy_board") or []
        }
        # 按 entity_id 索引我方随从，便于快速查找攻击者属性
        attackers_by_id = {
            minion.get("entity_id"): minion
            for minion in state.get("my_board") or []
        }

        best: dict[str, Any] | None = None
        best_score = -999  # 初始化为极小值，确保任何合法交换都能覆盖
        for attack in self._combat_analyzer.legal_attacks(state):
            # 只考虑随从攻击随从的情况（忽略打脸选项）
            if attack["target_type"] != "minion":
                continue
            target = targets_by_id.get(attack["target"])
            if not target:
                continue
            attacker = attackers_by_id.get(attack["source"], {})
            # 计算该交换的启发式分数
            score = self._score_trade(attack, attacker, target)
            if score > best_score:
                best_score = score
                best = {
                    "type": "attack",
                    "source": attack["source"],
                    "target": attack["target"],
                    "score": score,
                    "reason": "Best available board trade.",
                }

        return best

    @staticmethod
    def _score_trade(attack: dict[str, Any], attacker: dict[str, Any], target: dict[str, Any]) -> int:
        """
        计算单次随从交换的启发式分数。

        评分规则：
        1. 能否击杀目标（核心因素）：
           - 伤害 ≥ 目标生命值 → 可以击杀
           - 攻击者有毒（poisonous/venomous）→ 可以击杀（无论双方数值）
           - 目标有圣盾（divine_shield）→ 无法一击击杀
        2. 击杀收益：
           - 基础收益 = 目标攻击力 + 目标生命值（消除的威胁）
           - 嘲讽随从额外 +3 分（嘲讽是打脸的障碍，清理价值高）
           - 吸血随从额外 +1 分（阻止对手回复生命）

        参数:
            attack: 攻击信息字典，包含 damage 字段。
            attacker: 攻击者（我方随从）信息字典。
            target: 目标（敌方随从）信息字典。

        返回:
            整数分数，越高表示交换越有利。
        """
        damage = int(attack.get("damage") or 0)
        target_health = int(target.get("health") or 0)
        target_attack = int(target.get("attack") or 0)
        # 检查攻击者是否具有剧毒词条（poisonous 或 venomous）
        attacker_has_poison = bool(attacker.get("poisonous") or attacker.get("venomous"))
        # 圣盾可以抵挡一次伤害，有圣盾时无法击杀
        shield_blocks_kill = bool(target.get("divine_shield"))
        # 判断能否击杀目标：无视圣盾阻挡 且 （伤害足够 或 攻击者有毒）
        kills_target = not shield_blocks_kill and (damage >= target_health or attacker_has_poison)

        score = 0
        if kills_target:
            # 基础收益：消除目标的攻击力和生命值所代表的威胁
            score += target_attack + target_health
        if target.get("taunt"):
            # 嘲讽随从有额外加分——清理嘲讽是通往打脸的关键
            score += 3
        if target.get("lifesteal"):
            # 吸血随从也有小幅加分——阻止对手回复生命
            score += 1
        return score
