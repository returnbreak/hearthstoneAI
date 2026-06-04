"""
行动规划器及相关组件的单元测试。

本测试文件覆盖教练模块（ai_backend.coach）的核心类：
    - ActionPlanner:           行动规划器——合法行动与序列生成
    - CardEffectRecognizer:    卡牌效果识别器——文本效果解析
    - LethalChecker:           斩杀检测器——斩杀判定逻辑
    - RecommendationValidator: 推荐验证器——LLM 推荐合法性校验

测试策略：
    - 使用 sample_state() 和辅助工厂函数（spell(), minion()）
      快速构建测试输入，减少样板代码。
    - 覆盖正常路径、边界条件和规则交互（如嘲讽阻挡、
      法术伤害加成、法力限制、序列生成等）。
"""

import unittest

from ai_backend.coach.action_planner import ActionPlanner
from ai_backend.coach.card_effects import CardEffectRecognizer
from ai_backend.coach.combat_analyzer import CombatAnalyzer
from ai_backend.coach.lethal_checker import LethalChecker
from ai_backend.coach.recommendation_validator import RecommendationValidator


# ============================================================================
# ActionPlanner 测试
# ============================================================================
class ActionPlannerTests(unittest.TestCase):
    """测试 ActionPlanner 的行动空间生成逻辑。"""

    def test_generates_card_combinations_within_available_mana(self):
        """
        测试卡牌组合生成——法力约束。

        场景：
        - 法力 4 点，手牌：2 费（便宜）、5 费（太贵）、1 费（buff）
        - 5 费卡牌超出法力上限，不应出现在可打出列表

        预期：
        - 可打出卡牌只有 2 费和 1 费两张（entity_id=10, 12）
        - 所有组合的总费用不超过 4
        - 5 费卡牌不在任何组合中
        """
        state = sample_state(
            mana=4,
            hand=[
                spell(10, "Cheap Shot", 2, "Deal 2 damage."),
                spell(11, "Big Spell", 5, "Deal 8 damage."),
                spell(12, "Small Buff", 1, "Give a minion +1/+1."),
            ],
        )

        action_space = ActionPlanner().generate(state)

        # 提取可打出卡牌的 entity_id
        playable_ids = [action["card"]["entity_id"] for action in action_space["playable_cards"]]
        # 提取所有组合的总费用
        combo_costs = [combo["total_cost"] for combo in action_space["card_combinations"]]

        # 断言：只有 entity_id 为 10 和 12 的卡牌可打出
        self.assertEqual([10, 12], playable_ids)
        # 断言：没有任何组合费用为 5（那张大法术太贵了）
        self.assertNotIn(5, combo_costs)
        # 断言：所有组合的费用都不超过 4 点法力
        self.assertTrue(all(cost <= 4 for cost in combo_costs))

    def test_includes_low_priority_hero_power_when_affordable(self):
        """
        测试英雄技能——猎人技能在法力足够时可用。

        场景：
        - 法力 2 点，猎人职业，空手牌

        预期：
        - 英雄技能类型为 "hero_power"
        - 优先级为 "low"
        - 伤害为 2（猎人稳固射击）
        """
        state = sample_state(mana=2, hero_class="HUNTER", hand=[])

        action_space = ActionPlanner().generate(state)

        self.assertEqual("hero_power", action_space["hero_power"]["type"])
        self.assertEqual("low", action_space["hero_power"]["priority"])
        self.assertEqual(2, action_space["hero_power"]["damage"])

    def test_includes_known_class_hero_powers_when_affordable(self):
        expected_kinds = {
            "HUNTER": "damage_enemy_hero",
            "MAGE": "damage",
            "PRIEST": "restore_health",
            "WARRIOR": "gain_armor",
            "WARLOCK": "draw_card_self_damage",
            "PALADIN": "summon_minion",
            "SHAMAN": "summon_totem",
            "ROGUE": "equip_weapon",
            "DRUID": "attack_and_armor",
            "DEMONHUNTER": "attack_gain",
            "DEATHKNIGHT": "summon_ghoul",
        }

        for hero_class, expected_kind in expected_kinds.items():
            with self.subTest(hero_class=hero_class):
                state = sample_state(mana=2, hero_class=hero_class, hand=[])

                action_space = ActionPlanner().generate(state)
                hero_power = action_space["legal_actions"]["hero_power"][0]

                self.assertEqual("hero_power", hero_power["type"])
                self.assertEqual(2, hero_power["cost"])
                self.assertEqual(hero_class, hero_power["hero_class"])
                self.assertEqual(expected_kind, hero_power["effect"]["kind"])

    def test_includes_board_text_effects(self):
        """
        测试棋盘效果识别——场上随从的法术伤害光环。

        场景：
        - 我方场上有一个带 "Spell Damage +1" 文本的随从

        预期：
        - modifiers 中的 spell_damage 为 1（全局法术伤害 +1）
        - board_effects 中识别出 kind="spell_damage" 的效果
        """
        state = sample_state(
            mana=4,
            hand=[],
            my_board=[minion(1, attack=1, health=3, text="Spell Damage +1")],
        )

        action_space = ActionPlanner().generate(state)

        self.assertEqual(1, action_space["modifiers"]["spell_damage"])
        self.assertEqual("spell_damage", action_space["board_effects"][0]["kind"])

    def test_groups_legal_actions_into_documented_categories(self):
        """
        测试合法操作分类——按类型分组为预设类别。

        场景：
        - 手牌有一张 4 费火球术，场上有一个可攻击的随从

        预期：
        - legal_actions 包含所有六种操作类型：activate_ability, end_turn,
          hero_attack, hero_power, minion_attack, play_card
        - 每种类型下的操作属性正确
        """
        state = sample_state(
            mana=4,
            hand=[spell(10, "Fireball", 4, "Deal 3 damage.")],
            my_board=[minion(1, attack=2, health=2, can_attack=True)],
        )

        action_space = ActionPlanner().generate(state)

        # 断言：六种操作分类全部存在
        self.assertEqual(
            ["activate_ability", "end_turn", "hero_attack", "hero_power", "minion_attack", "play_card"],
            sorted(action_space["legal_actions"].keys()),
        )
        # 断言：出牌操作的 source 正确
        self.assertEqual(10, action_space["legal_actions"]["play_card"][0]["source"])
        # 断言：随从攻击操作类型正确
        self.assertEqual("minion_attack", action_space["legal_actions"]["minion_attack"][0]["type"])
        # 断言：结束回合操作存在
        self.assertEqual("end_turn", action_space["legal_actions"]["end_turn"][0]["type"])

    def test_legal_sequences_respect_remaining_mana_for_hero_power(self):
        """
        测试序列生成——法力耗尽时不包含英雄技能。

        场景：
        - 法力 4 点，打出一张 4 费火球术（法力刚好用完）

        预期：
        - 存在总费用为 4 的序列（火球 + 攻击）
        - 这些法力耗尽的序列中不包含 hero_power（因为没有剩余法力）
        """
        state = sample_state(
            mana=4,
            hand=[spell(10, "Fireball", 4, "Deal 3 damage.")],
            my_board=[minion(1, attack=2, health=2, can_attack=True)],
        )

        action_space = ActionPlanner().generate(state)
        # 筛选出法力完全耗尽的序列
        full_spend_sequences = [
            sequence for sequence in action_space["legal_sequences"]
            if sequence["total_cost"] == 4
        ]

        # 断言：存在法力耗尽的序列
        self.assertTrue(full_spend_sequences)
        # 断言：法力耗尽的序列都不包含英雄技能（已无剩余法力）
        self.assertTrue(all(
            action["type"] != "hero_power"
            for sequence in full_spend_sequences
            for action in sequence["actions"]
        ))

    def test_legal_sequences_include_hero_power_when_mana_remains(self):
        """
        测试序列生成——法力有剩余时包含英雄技能。

        场景：
        - 法力 6 点，打出一张 4 费火球术（剩余 2 法力，刚好够英雄技能）

        预期：
        - 至少有一个序列包含 hero_power 操作
        """
        state = sample_state(
            mana=6,
            hand=[spell(10, "Fireball", 4, "Deal 3 damage.")],
            my_board=[],
        )

        action_space = ActionPlanner().generate(state)

        # 断言：存在包含英雄技能的序列
        self.assertTrue(any(
            any(action["type"] == "hero_power" for action in sequence["actions"])
            for sequence in action_space["legal_sequences"]
        ))


# ============================================================================
# CardEffectRecognizer 测试
# ============================================================================
class CardEffectRecognizerTests(unittest.TestCase):
    """测试 CardEffectRecognizer 的卡牌文本效果解析。"""

    def test_recognizes_damage_and_buff_text(self):
        """
        测试效果识别——直伤法术和属性增益。

        场景：
        - "Deal 3 damage." → 直伤法术（damage=3）
        - "Give a minion +2/+2." → 属性增益（+2/+2）

        预期：
        - 火球术识别为 damage 类型，伤害=3
        - Buff 卡识别为 buff 类型，+2 攻击力，+2 生命值
        """
        recognizer = CardEffectRecognizer()

        damage = recognizer.recognize(spell(1, "Fire", 2, "Deal 3 damage."))
        buff = recognizer.recognize(spell(2, "Blessing", 1, "Give a minion +2/+2."))

        self.assertEqual("damage", damage.kind)
        self.assertEqual(3, damage.damage)
        self.assertEqual("buff", buff.kind)
        self.assertEqual(2, buff.attack_buff)
        self.assertEqual(2, buff.health_buff)

    def test_recognizes_minion_spell_damage_text(self):
        """
        测试效果识别——随从的法术伤害光环。

        场景：
        - 随从卡牌，文本 "Spell Damage +1"

        预期：
        - 效果类型为 "spell_damage"
        - spell_damage 数值为 1
        """
        effect = CardEffectRecognizer().recognize({
            "card_id": "TEST_SPELL_DAMAGE",
            "name": "Spell Damage Minion",
            "type": "MINION",
            "text": "Spell Damage +1",
        })

        self.assertEqual("spell_damage", effect.kind)
        self.assertEqual(1, effect.spell_damage)


# ============================================================================
# LethalChecker 测试
# ============================================================================
class LethalWithHandTests(unittest.TestCase):
    """测试 LethalChecker 的斩杀判定（含手牌交互）。"""

    def test_counts_playable_damage_spell_for_lethal(self):
        """
        测试斩杀检测——手牌直伤 + 场攻 ≥ 敌方血量。

        场景：
        - 敌方英雄 7 血，0 护甲，非免疫
        - 我方场上一个 4 攻随从（可攻击）
        - 手牌一张 4 费火球术（3 点伤害）
        - 法力 4 点（足够打火球）

        预期：
        - is_lethal=True（4 + 3 = 7，恰好斩杀）
        - 可用伤害 = 7
        - 操作顺序：先出牌（火球），后攻击
        """
        state = sample_state(
            mana=4,
            enemy_hero={"hp": 7, "armor": 0, "immune": False},
            my_board=[minion(1, attack=4, health=2, can_attack=True)],
            hand=[spell(10, "Fireball", 4, "Deal 3 damage.")],
        )

        result = LethalChecker(CombatAnalyzer(), ActionPlanner()).check(state)

        self.assertTrue(result["is_lethal"])
        self.assertEqual(7, result["available_damage"])
        self.assertEqual(["play_card", "attack"], [action["type"] for action in result["actions"]])

    def test_ignores_unaffordable_damage_spell_for_lethal(self):
        """
        测试斩杀检测——法力不足的伤害法术不计入斩杀。

        场景：
        - 敌方英雄 7 血，0 护甲
        - 我方场上一个 4 攻随从（可攻击）
        - 手牌一张 4 费 3 伤法术，但法力只有 3（打不出）
        - 场攻 4 + 英雄技能 1（法师）= 5

        预期：
        - is_lethal=False（可用伤害 5 < 敌方血量 7）
        - 可用伤害 = 5（仅场攻 + 英雄技能，不包括打不出的法术）
        """
        state = sample_state(
            mana=3,
            enemy_hero={"hp": 7, "armor": 0, "immune": False},
            my_board=[minion(1, attack=4, health=2, can_attack=True)],
            hand=[spell(10, "Expensive Fireball", 4, "Deal 3 damage.")],
        )

        result = LethalChecker(CombatAnalyzer(), ActionPlanner()).check(state)

        self.assertFalse(result["is_lethal"])
        self.assertEqual(5, result["available_damage"])

    def test_counts_attack_buff_before_board_attacks(self):
        """
        测试斩杀检测——攻击力 Buff 先于攻击结算。

        场景：
        - 敌方英雄 6 血
        - 我方场上一个 4 攻随从（可攻击）
        - 手牌一张 2 费 +2/+2 buff
        - 法力 2 点（刚好打 buff）

        预期：
        - is_lethal=True（4 + 2 = 6，恰好斩杀）
        - 可用伤害 = 6
        - 第一个操作是出牌（buff），buff 目标为攻击力最高的随从
        """
        state = sample_state(
            mana=2,
            enemy_hero={"hp": 6, "armor": 0, "immune": False},
            my_board=[minion(1, attack=4, health=2, can_attack=True)],
            hand=[spell(10, "Blessing", 2, "Give a minion +2/+2.")],
        )

        result = LethalChecker(CombatAnalyzer(), ActionPlanner()).check(state)

        self.assertTrue(result["is_lethal"])
        self.assertEqual(6, result["available_damage"])
        # buff 操作先于攻击操作
        self.assertEqual("play_card", result["actions"][0]["type"])

    def test_counts_board_spell_damage_for_damage_spells(self):
        """
        测试斩杀检测——法术伤害光环对直伤法术的加成。

        场景：
        - 敌方英雄 4 血
        - 我方场上一个 0/3 随从（无法攻击），但有 "Spell Damage +1"
        - 手牌一张 2 费冰箭（基础 3 伤害，+1 法伤 = 4 伤害）
        - 法力 2 点

        预期：
        - is_lethal=True（3 + 1 法伤 = 4，恰好斩杀）
        - 可用伤害 = 4
        """
        state = sample_state(
            mana=2,
            enemy_hero={"hp": 4, "armor": 0, "immune": False},
            my_board=[minion(1, attack=0, health=3, text="Spell Damage +1")],
            hand=[spell(10, "Frostbolt", 2, "Deal 3 damage.")],
        )

        result = LethalChecker(CombatAnalyzer(), ActionPlanner()).check(state)

        self.assertTrue(result["is_lethal"])
        self.assertEqual(4, result["available_damage"])


# ============================================================================
# RecommendationValidator 测试
# ============================================================================
class RecommendationValidatorTests(unittest.TestCase):
    """测试 RecommendationValidator 的推荐合法性验证。"""

    def test_accepts_existing_sequence_id(self):
        """
        测试验证器——通过序列 ID 引用合法序列。

        场景：
        - 推荐直接引用了 legal_sequences 中存在的序列 ID

        预期：
        - validation_status="passed"（序列 ID 合法）
        """
        action_space = ActionPlanner().generate(sample_state(mana=2, hand=[]))
        recommendation = {"chosen_sequence_id": action_space["legal_sequences"][0]["sequence_id"], "actions": []}

        result = RecommendationValidator().validate(recommendation, action_space)

        self.assertEqual("passed", result["validation_status"])

    def test_rejects_illegal_face_attack_when_taunt_exists(self):
        """
        测试验证器——嘲讽存在时打脸应被拒绝。

        场景：
        - 敌方场上有一个嘲讽随从
        - 推荐操作试图绕过嘲讽直接攻击敌方英雄

        预期：
        - validation_status="failed"（打脸不在合法操作中，因为有嘲讽阻挡）
        - reason 包含 "not in legal_actions"
        """
        state = sample_state(
            mana=2,
            hand=[],
            my_board=[minion(1, attack=3, health=2, can_attack=True)],
            enemy_board=[minion(2, attack=1, health=4, taunt=True)],
        )
        action_space = ActionPlanner().generate(state)
        recommendation = {
            "actions": [
                {"type": "minion_attack", "source": 1, "target": "enemy_hero"}
            ]
        }

        result = RecommendationValidator().validate(recommendation, action_space)

        self.assertEqual("failed", result["validation_status"])
        self.assertIn("not in legal_actions", result["reason"])


# ============================================================================
# 测试辅助工厂函数
# ============================================================================

def sample_state(mana, hand, enemy_hero=None, my_board=None, enemy_board=None, hero_class="MAGE"):
    """
    构建一个简化的游戏状态字典，用于测试。

    该函数简化了测试用例的样板代码——测试只需提供变化的部分
    （法力值、手牌、场面、英雄职业等），其余字段使用合理的默认值。

    参数:
        mana:       当前可用法力水晶数。
        hand:       手牌列表。
        enemy_hero: 敌方英雄信息字典（默认 30 血，非免疫）。
        my_board:   我方场面随从列表（默认空）。
        enemy_board: 敌方场面随从列表（默认空）。
        hero_class: 我方英雄职业（默认 "MAGE" 法师）。

    返回:
        一个完整的游戏状态字典，可直接传入被测试的组件。
    """
    return {
        "turn": 6,  # 第 6 回合，游戏中期
        "my_mana": {"current": mana, "max": mana},
        "my_hero": {"class": hero_class, "hp": 20, "armor": 0, "attack": 0, "can_attack": False},
        "enemy_hero": enemy_hero or {"hp": 30, "armor": 0, "immune": False},
        "my_board": my_board or [],
        "enemy_board": enemy_board or [],
        "hand": hand,
    }


def spell(entity_id, name, cost, text):
    """
    创建一个简化版的法术卡牌字典，用于测试。

    参数:
        entity_id: 卡牌唯一标识符（整数）。
        name:      卡牌名称。
        cost:      法力消耗。
        text:      卡牌描述文本（英文或中文）。

    返回:
        一个包含完整属性的法术卡牌信息字典。
    """
    return {
        "entity_id": entity_id,
        "card_id": f"TEST_{entity_id}",
        "name": name,
        "cost": cost,
        "type": "SPELL",
        "text": text,
    }


def minion(entity_id, attack, health, can_attack=False, text=None, taunt=False):
    """
    创建一个简化版的随从实体字典，用于测试。

    该函数为随从提供合理的默认属性值（如无圣盾、无潜行等），
    测试只需指定与当前测试相关的属性，其余使用默认值。

    参数:
        entity_id:  随从唯一标识符（整数）。
        attack:     攻击力。
        health:     生命值。
        can_attack: 当前是否可以攻击（默认 False，模拟刚被召唤的随从）。
        text:       随从的卡牌描述文本（用于效果识别，如 "Spell Damage +1"）。
        taunt:      是否具有嘲讽属性（默认 False）。

    返回:
        一个包含完整属性的随从信息字典。
    """
    return {
        "entity_id": entity_id,
        "card_id": f"MINION_{entity_id}",
        "name": f"Minion {entity_id}",
        "text": text,
        "attack": attack,
        "health": health,
        "damage": 0,            # 尚未受到伤害
        "can_attack": can_attack,
        "attacks_remaining": 1 if can_attack else 0,  # 可攻击则剩余 1 次
        "taunt": taunt,
        "stealth": False,       # 无潜行
        "immune": False,        # 非免疫
        "frozen": False,        # 未被冻结
        "dormant": False,       # 非休眠
        "divine_shield": False, # 无圣盾
        "poisonous": False,     # 无毒
        "venomous": False,      # 非剧毒
    }


if __name__ == "__main__":
    unittest.main()
