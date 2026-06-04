"""
斩杀检测器：判断当前回合是否具备斩杀条件。

本模块综合评估手牌直伤、随从打脸、英雄技能和法术伤害光环，
判断在本回合内是否能够造成足够的伤害来击败敌方英雄。

斩杀检测考虑的因素：
    - 手牌中的直伤法术（包括法术伤害加成）
    - 场上的随从攻击力（包括增益 buff 后）
    - 英雄技能的额外伤害
    - 法术伤害光环的全局加成

对于每种可能的手牌组合，都会计算其最大打脸伤害，
并选出伤害最高的序列作为最优斩杀方案。
"""

from __future__ import annotations

from typing import Any

from ai_backend.coach.action_planner import ActionPlanner
from ai_backend.coach.combat_analyzer import CombatAnalyzer


class LethalChecker:
    """检查当前回合是否可以完成斩杀（对敌方英雄造成致命伤害）。

    核心职责：
        给定游戏状态，评估手牌、场面和英雄技能能造成的总伤害，
        判断是否大于等于敌方英雄的有效生命值（血量+护甲）。

    使用方式:
        checker = LethalChecker(combat_analyzer, action_planner)
        result = checker.check(state)
        if result["is_lethal"]:
            # 执行 result["actions"] 中的操作即可获胜
    """

    def __init__(self, combat_analyzer: CombatAnalyzer | None = None, action_planner: ActionPlanner | None = None):
        """
        初始化斩杀检测器。

        参数:
            combat_analyzer: 战斗分析器实例，用于计算合法攻击。
                             如果为 None，则自动创建默认实例。
            action_planner: 行动规划器实例，用于生成行动空间。
                            如果为 None，则自动创建默认实例。
        """
        self._combat_analyzer = combat_analyzer or CombatAnalyzer()
        self._action_planner = action_planner or ActionPlanner(self._combat_analyzer)

    def check(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        检查当前回合是否存在斩杀。

        计算流程：
        1. 获取敌方英雄的有效生命值（血量 + 护甲）
        2. 生成完整的行动空间（含所有候选操作）
        3. 遍历所有手牌组合，计算每种组合的最大打脸伤害
        4. 选出伤害最高的序列，判断是否 ≥ 敌方有效生命值

        参数:
            state: 游戏状态字典，需包含敌方英雄信息和场面。

        返回:
            斩杀检查结果字典，包含：
            - is_lethal:        是否可斩杀（True/False）
            - required_damage:  敌方有效生命值（需要造成的伤害）
            - available_damage: 最优序列能造成的最大伤害
            - actions:          实现该伤害的操作序列
            - action_space:     完整的行动空间（供下游参考）
        """
        enemy_hero = state.get("enemy_hero") or {}
        # 有效生命值 = 血量 + 护甲
        enemy_health = int(enemy_hero.get("hp") or 0) + int(enemy_hero.get("armor") or 0)
        # 生成行动空间
        action_space = self._action_planner.generate(state)
        # 找出能造成最高打脸伤害的操作序列
        best_sequence = self._best_lethal_sequence(action_space)

        return {
            # 有效生命值 > 0（防止除零等边界情况）且 可用伤害 ≥ 敌方生命值
            "is_lethal": enemy_health > 0 and best_sequence["damage"] >= enemy_health,
            "required_damage": enemy_health,
            "available_damage": best_sequence["damage"],
            "actions": best_sequence["actions"],
            "action_space": action_space,
        }

    def _best_lethal_sequence(self, action_space: dict[str, Any]) -> dict[str, Any]:
        """
        从行动空间中找出造成最高打脸伤害的操作序列。

        遍历所有手牌组合（包括不出牌的基线方案），
        为每种组合计算最大打脸伤害，返回得分最高的序列。

        参数:
            action_space: ActionPlanner.generate() 返回的行动空间。

        返回:
            伤害最高的序列字典，包含 damage 和 actions 字段。
        """
        # 筛选所有能打脸的随从/英雄攻击
        base_attacks = self._face_attacks(action_space)
        hero_power = action_space.get("hero_power")
        available_mana = int(action_space.get("available_mana") or 0)
        # 提取法术伤害光环加成
        spell_damage = int((action_space.get("modifiers") or {}).get("spell_damage") or 0)
        # 基线方案：不出任何手牌，仅用场攻 + 英雄技能
        candidates = [self._sequence_from_cards([], base_attacks, hero_power, available_mana, spell_damage)]

        # 遍历每种手牌组合
        for combo in action_space.get("card_combinations") or []:
            candidates.append(self._sequence_from_cards(
                combo["actions"],
                base_attacks,
                hero_power,
                int(combo.get("remaining_mana") or 0),
                spell_damage,
            ))

        # 返回伤害最高的序列
        return max(candidates, key=lambda candidate: candidate["damage"])

    def _sequence_from_cards(
        self,
        card_actions: list[dict[str, Any]],
        base_attacks: list[dict[str, Any]],
        hero_power: dict[str, Any] | None,
        remaining_mana: int,
        spell_damage: int,
    ) -> dict[str, Any]:
        """
        计算给定手牌组合下能造成的最大打脸伤害。

        伤害计算规则：
        1. 直伤法术：基础伤害 + 法术伤害光环加成
        2. 攻击力 buff：加在攻击力最高的打脸随从上，buff 后的伤害
           加入总伤害
        3. 随从/英雄打脸：基础攻击力 + 可能的 buff 加成
        4. 英雄技能：如果剩余法力足够且能造成伤害，加入总伤害

        参数:
            card_actions: 要打出的手牌操作列表。
            base_attacks: 可打脸的随从/英雄攻击列表。
            hero_power: 英雄技能信息（或 None）。
            remaining_mana: 打完手牌后的剩余法力。
            spell_damage: 法术伤害光环加成值。

        返回:
            包含总伤害（damage）和操作序列（actions）的字典。
        """
        damage = 0
        actions: list[dict[str, Any]] = []
        # 统计手牌中攻击力 buff 的总和
        attack_bonus = sum(int(action["effect"].get("attack_buff") or 0) for action in card_actions)
        # 将攻击力 buff 加在攻击力最高的打脸随从上（最大化收益）
        buff_target = self._best_attack_source(base_attacks)

        for action in card_actions:
            effect = action["effect"]
            # 处理直伤法术：计入法术伤害光环加成
            if effect["kind"] == "damage" and effect["can_target_enemy_hero"]:
                card_damage = int(effect.get("damage") or 0) + spell_damage
                damage += card_damage
                actions.append({
                    "type": "play_card",
                    "source": action["card"].get("entity_id"),
                    "card_id": action["card"].get("card_id"),
                    "name": action["card"].get("name"),
                    "target": "enemy_hero",
                    "damage": card_damage,
                    "cost": action["cost"],
                    "reason": "Playable damage card contributes to lethal.",
                })
            # 处理攻击力 buff：记录为增益操作（不直接造成伤害）
            elif effect["kind"] == "buff" and int(effect.get("attack_buff") or 0) > 0 and buff_target is not None:
                actions.append({
                    "type": "play_card",
                    "source": action["card"].get("entity_id"),
                    "card_id": action["card"].get("card_id"),
                    "name": action["card"].get("name"),
                    "target": buff_target,  # buff 目标为攻击力最高的打脸随从
                    "damage": 0,
                    "cost": action["cost"],
                    "reason": "Attack buff is applied before board attacks.",
                })

        # 计算随从/英雄打脸伤害（含 buff 加成）
        for attack in base_attacks:
            attack_damage = int(attack.get("damage") or 0)
            # 如果该攻击者是 buff 目标，加上攻击力增益
            if attack["source"] == buff_target:
                attack_damage += attack_bonus
            damage += attack_damage
            actions.append({
                "type": "attack",
                "source": attack["source"],
                "target": "enemy_hero",
                "damage": attack_damage,
                "reason": "Face attack contributes to lethal.",
            })

        # 如果剩余法力足够且英雄技能能造成伤害，加入英雄技能
        if hero_power and remaining_mana >= int(hero_power.get("cost") or 0) and int(hero_power.get("damage") or 0) > 0:
            damage += int(hero_power["damage"])
            actions.append({
                "type": "hero_power",
                "source": "my_hero",
                "target": hero_power.get("target"),
                "damage": hero_power["damage"],
                "cost": hero_power["cost"],
                "reason": "Hero power is available but low priority.",
            })

        return {"damage": damage, "actions": actions}

    @staticmethod
    def _face_attacks(action_space: dict[str, Any]) -> list[dict[str, Any]]:
        """
        从行动空间中筛选出所有能攻击敌方英雄（打脸）的攻击。

        从 legal_attacks 中过滤出 target 为 "enemy_hero" 的攻击，
        这些攻击代表随从或英雄可以直接对敌方英雄造成的伤害。

        参数:
            action_space: 行动空间字典。

        返回:
            可打脸的攻击列表。
        """
        return [
            attack for attack in action_space.get("legal_attacks") or []
            if attack["target"] == "enemy_hero"
        ]

    @staticmethod
    def _best_attack_source(face_attacks: list[dict[str, Any]]) -> Any:
        """
        找出打脸攻击中攻击力最高的攻击者。

        用于确定应将攻击力 buff 加在哪个随从身上。
        选择攻击力最高的随从可以最大化 buff 的收益。

        参数:
            face_attacks: 可打脸的攻击列表。

        返回:
            攻击力最高的攻击者的 source 标识符；
            如果列表为空则返回 None。
        """
        if not face_attacks:
            return None
        return max(face_attacks, key=lambda attack: int(attack.get("damage") or 0))["source"]
