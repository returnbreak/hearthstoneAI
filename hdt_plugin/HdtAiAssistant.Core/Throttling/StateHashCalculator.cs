using System;
using System.Security.Cryptography;
using System.Text;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistant.Core.Throttling
{
    /// <summary>
    /// 状态哈希计算器——将 GameState 序列化为确定性字符串后计算 SHA-256 哈希，
    /// 用于判断两次快照之间是否有实质性变化（忽略 GameId、Timestamp 等易变字段）。
    /// </summary>
    public static class StateHashCalculator
    {
        /// <summary>
        /// 计算游戏状态的 SHA-256 哈希值。相同的游戏状态产生相同的哈希，
        /// 忽略 GameId、Timestamp 和 RecentEvents 等不影响决策的字段。
        /// </summary>
        /// <param name="state">游戏状态快照。</param>
        /// <returns>64 位十六进制哈希字符串。</returns>
        public static string Calculate(GameState state)
        {
            if(state == null)
                return string.Empty;

            // 按固定顺序拼接关键字段，确保相同状态产生相同字符串
            var builder = new StringBuilder();
            Append(builder, "turn", state.Turn);
            Append(builder, "active", state.ActivePlayer);
            AppendMana(builder, "mana", state.Mana);
            AppendMana(builder, "my_mana", state.MyMana);
            AppendMana(builder, "enemy_mana", state.EnemyMana);
            AppendHero(builder, "my_hero", state.MyHero);
            AppendHero(builder, "enemy_hero", state.EnemyHero);
            Append(builder, "my_deck_count", state.MyDeckCount);
            Append(builder, "enemy_hand_count", state.EnemyHandCount);
            Append(builder, "enemy_deck_count", state.EnemyDeckCount);
            AppendCards(builder, "hand", state.Hand);
            AppendMinions(builder, "my_board", state.MyBoard);
            AppendMinions(builder, "enemy_board", state.EnemyBoard);
            AppendCards(builder, "known_enemy_cards", state.KnownEnemyCards);

            // 对拼接后的字符串计算 SHA-256 哈希
            using(var sha = SHA256.Create())
            {
                var bytes = Encoding.UTF8.GetBytes(builder.ToString());
                var hash = sha.ComputeHash(bytes);
                var text = new StringBuilder(hash.Length * 2);
                for(var i = 0; i < hash.Length; i++)
                    text.Append(hash[i].ToString("x2"));
                return text.ToString();
            }
        }

        /// <summary>将英雄快照的字段拼接到哈希输入中。</summary>
        private static void AppendHero(StringBuilder builder, string name, HeroSnapshot hero)
        {
            builder.Append(name).Append('=');
            if(hero != null)
            {
                builder.Append(Safe(hero.Class)).Append('|');
                builder.Append(hero.Hp).Append('|');
                builder.Append(hero.Armor).Append('|');
                builder.Append(hero.Attack).Append('|');
                builder.Append(hero.CanAttack ? 1 : 0).Append('|');
                builder.Append(hero.AttacksThisTurn).Append('|');
                builder.Append(hero.AttacksRemaining).Append('|');
                builder.Append(hero.Immune ? 1 : 0).Append('|');
                builder.Append(hero.Frozen ? 1 : 0);
            }
            builder.AppendLine();
        }

        /// <summary>将卡牌列表的字段拼接到哈希输入中。</summary>
        private static void AppendMana(StringBuilder builder, string name, ManaSnapshot mana)
        {
            builder.Append(name).Append('=');
            if(mana != null)
                builder.Append(mana.Current).Append('/').Append(mana.Max);
            builder.AppendLine();
        }

        private static void AppendCards(StringBuilder builder, string name, System.Collections.Generic.IEnumerable<CardSnapshot> cards)
        {
            builder.Append(name).Append('=');
            if(cards != null)
            {
                foreach(var card in cards)
                {
                    if(card == null)
                        continue;
                    builder.Append(card.EntityId).Append(':');
                    builder.Append(Safe(card.CardId)).Append(':');
                    builder.Append(card.DbFId).Append(':');
                    builder.Append(card.Cost).Append(':');
                    builder.Append(Safe(card.Type)).Append(':');
                    builder.Append(Safe(card.Text)).Append(';');
                }
            }
            builder.AppendLine();
        }

        /// <summary>将随从列表的字段拼接到哈希输入中。</summary>
        private static void AppendMinions(StringBuilder builder, string name, System.Collections.Generic.IEnumerable<MinionSnapshot> minions)
        {
            builder.Append(name).Append('=');
            if(minions != null)
            {
                foreach(var minion in minions)
                {
                    if(minion == null)
                        continue;
                    builder.Append(minion.EntityId).Append(':');
                    builder.Append(Safe(minion.CardId)).Append(':');
                    builder.Append(Safe(minion.Text)).Append(':');
                    builder.Append(minion.Attack).Append(':');
                    builder.Append(minion.Health).Append(':');
                    builder.Append(minion.Damage).Append(':');
                    builder.Append(minion.ZonePosition).Append(':');
                    builder.Append(minion.CanAttack ? 1 : 0).Append(':');
                    builder.Append(minion.AttacksThisTurn).Append(':');
                    builder.Append(minion.AttacksRemaining).Append(':');
                    builder.Append(minion.Taunt ? 1 : 0).Append(':');
                    builder.Append(minion.DivineShield ? 1 : 0).Append(':');
                    builder.Append(minion.Stealth ? 1 : 0).Append(':');
                    builder.Append(minion.Immune ? 1 : 0).Append(':');
                    builder.Append(minion.Frozen ? 1 : 0).Append(':');
                    builder.Append(minion.Rush ? 1 : 0).Append(':');
                    builder.Append(minion.Charge ? 1 : 0).Append(':');
                    builder.Append(minion.Windfury ? 1 : 0).Append(':');
                    builder.Append(minion.MegaWindfury ? 1 : 0).Append(':');
                    builder.Append(minion.Lifesteal ? 1 : 0).Append(':');
                    builder.Append(minion.Poisonous ? 1 : 0).Append(':');
                    builder.Append(minion.Venomous ? 1 : 0).Append(':');
                    builder.Append(minion.Reborn ? 1 : 0).Append(':');
                    builder.Append(minion.Deathrattle ? 1 : 0).Append(':');
                    builder.Append(minion.Dormant ? 1 : 0).Append(':');
                    builder.Append(minion.Silenced ? 1 : 0).Append(':');
                    builder.Append(minion.CantAttack ? 1 : 0).Append(':');
                    builder.Append(minion.Exhausted ? 1 : 0).Append(';');
                }
            }
            builder.AppendLine();
        }

        /// <summary>将单个键值对拼接到哈希输入中。</summary>
        private static void Append(StringBuilder builder, string name, object value)
        {
            builder.Append(name).Append('=').Append(value == null ? string.Empty : value).AppendLine();
        }

        /// <summary>安全地将可能为 null 的字符串转为空字符串。</summary>
        private static string Safe(string value)
        {
            return value ?? string.Empty;
        }
    }
}
