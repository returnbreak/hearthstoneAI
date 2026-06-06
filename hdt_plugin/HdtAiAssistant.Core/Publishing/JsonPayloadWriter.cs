using System;
using System.Collections.Generic;
using System.Text;
using HdtAiAssistant.Core.Models;
using HdtAiAssistant.Core.Throttling;

namespace HdtAiAssistant.Core.Publishing
{
    /// <summary>
    /// JSON 负载写入器——使用 StringBuilder 手工构建 JSON 字符串，
    /// 避免引入第三方 JSON 库依赖，同时保证输出格式完全可控。
    /// </summary>
    public static class JsonPayloadWriter
    {
        /// <summary>
        /// 将游戏状态快照包装为完整的 JSON 信封。
        /// 格式：{"type":"game_state","trigger":"...","state":{...}}
        /// </summary>
        public static string WriteGameStateEnvelope(GameState state, RecommendationTrigger trigger)
        {
            var builder = new StringBuilder();
            builder.Append('{');
            WriteProperty(builder, "type", "game_state", true);
            WriteProperty(builder, "trigger", TriggerToWireValue(trigger), false);
            builder.Append(",\"state\":");
            WriteGameState(builder, state);
            builder.Append('}');
            return builder.ToString();
        }

        /// <summary>
        /// 将游戏事件包装为完整的 JSON 信封。
        /// 格式：{"type":"game_event","event":{...}}
        /// </summary>
        public static string WriteGameEventEnvelope(GameEvent gameEvent)
        {
            var builder = new StringBuilder();
            builder.Append('{');
            WriteProperty(builder, "type", "game_event", true);
            builder.Append(",\"event\":");
            WriteGameEvent(builder, gameEvent);
            builder.Append('}');
            return builder.ToString();
        }

        public static string WriteGameMetadataEnvelope(GameMetadata metadata)
        {
            var builder = new StringBuilder();
            builder.Append('{');
            WriteProperty(builder, "type", "game_metadata", true);
            WriteProperty(builder, "game_id", metadata == null ? null : metadata.GameId, false);
            WriteProperty(builder, "captured_at", metadata == null ? null : metadata.CapturedAt, false);
            builder.Append(",\"deck\":");
            WriteDeckMetadata(builder, metadata);
            builder.Append('}');
            return builder.ToString();
        }

        /// <summary>将 GameState 对象序列化为 JSON 对象。</summary>
        public static void WriteGameState(StringBuilder builder, GameState state)
        {
            if(state == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "game_id", state.GameId, true);
            WriteProperty(builder, "timestamp", state.Timestamp, false);
            WriteProperty(builder, "mode", state.Mode, false);
            WriteProperty(builder, "turn", state.Turn, false);
            WriteProperty(builder, "active_player", state.ActivePlayer, false);
            builder.Append(",\"my_hero\":");
            WriteHero(builder, state.MyHero);
            builder.Append(",\"enemy_hero\":");
            WriteHero(builder, state.EnemyHero);
            builder.Append(",\"my_mana\":");
            WriteMana(builder, state.MyMana);
            builder.Append(",\"enemy_mana\":");
            WriteMana(builder, state.EnemyMana);
            builder.Append(",\"hand\":");
            WriteCards(builder, state.Hand);
            builder.Append(",\"my_board\":");
            WriteMinions(builder, state.MyBoard);
            builder.Append(",\"enemy_board\":");
            WriteMinions(builder, state.EnemyBoard);
            WriteProperty(builder, "my_deck_count", state.MyDeckCount, false);
            WriteProperty(builder, "enemy_hand_count", state.EnemyHandCount, false);
            WriteProperty(builder, "enemy_deck_count", state.EnemyDeckCount, false);
            builder.Append(",\"known_enemy_cards\":");
            WriteCards(builder, state.KnownEnemyCards);
            builder.Append(",\"recent_events\":");
            WriteEvents(builder, state.RecentEvents);
            builder.Append('}');
        }

        /// <summary>将 GameEvent 对象序列化为 JSON 对象。</summary>
        public static void WriteGameEvent(StringBuilder builder, GameEvent gameEvent)
        {
            if(gameEvent == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            var first = true;
            WriteOptionalProperty(builder, "game_id", gameEvent.GameId, ref first);
            WriteOptionalProperty(builder, "timestamp", gameEvent.Timestamp, ref first);
            WriteProperty(builder, "turn", gameEvent.Turn, first);
            first = false;
            WriteOptionalProperty(builder, "player", gameEvent.Player, ref first);
            WriteOptionalProperty(builder, "type", gameEvent.Type, ref first);
            WriteOptionalPositiveProperty(builder, "entity_id", gameEvent.EntityId, ref first);
            WriteOptionalProperty(builder, "card_id", gameEvent.CardId, ref first);
            WriteOptionalPositiveProperty(builder, "dbf_id", gameEvent.DbFId, ref first);
            WriteOptionalProperty(builder, "name", gameEvent.Name, ref first);
            if(gameEvent.Target != null)
            {
                if(!first)
                    builder.Append(',');
                builder.Append("\"target\":");
                WriteEventTarget(builder, gameEvent.Target);
                first = false;
            }
            WriteOptionalPositiveProperty(builder, "damage_amount", gameEvent.DamageAmount, ref first);
            WriteOptionalProperty(builder, "reason", gameEvent.Reason, ref first);
            WriteOptionalProperty(builder, "result", gameEvent.Result, ref first);
            builder.Append('}');
        }

        /// <summary>将 RecommendationTrigger 枚举转为后端约定的字符串值。</summary>
        public static string TriggerToWireValue(RecommendationTrigger trigger)
        {
            switch(trigger)
            {
                case RecommendationTrigger.MyTurnStarted:
                    return "my_turn_started";
                case RecommendationTrigger.SignificantStateChange:
                    return "significant_state_change";
                case RecommendationTrigger.Manual:
                    return "manual";
                case RecommendationTrigger.GameStarted:
                    return "game_started";
                case RecommendationTrigger.GameEnded:
                    return "game_ended";
                default:
                    return "none";
            }
        }

        /// <summary>序列化英雄快照为 JSON 对象。</summary>
        private static void WriteHero(StringBuilder builder, HeroSnapshot hero)
        {
            if(hero == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "class", hero.Class, true);
            WriteProperty(builder, "hp", hero.Hp, false);
            WriteProperty(builder, "armor", hero.Armor, false);
            WriteProperty(builder, "attack", hero.Attack, false);
            WriteProperty(builder, "attacks_this_turn", hero.AttacksThisTurn, false);
            WriteProperty(builder, "max_attacks_per_turn", hero.MaxAttacksPerTurn, false);
            WriteProperty(builder, "immune", hero.Immune, false);
            WriteProperty(builder, "frozen", hero.Frozen, false);
            builder.Append('}');
        }

        /// <summary>序列化法力快照为 JSON 对象。</summary>
        private static void WriteMana(StringBuilder builder, ManaSnapshot mana)
        {
            if(mana == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "current", mana.Current, true);
            WriteProperty(builder, "max", mana.Max, false);
            builder.Append('}');
        }

        /// <summary>序列化卡牌列表为 JSON 数组。</summary>
        private static void WriteCards(StringBuilder builder, IEnumerable<CardSnapshot> cards)
        {
            builder.Append('[');
            var first = true;
            if(cards != null)
            {
                foreach(var card in cards)
                {
                    if(!first)
                        builder.Append(',');
                    first = false;
                    WriteCard(builder, card);
                }
            }
            builder.Append(']');
        }

        /// <summary>序列化单张卡牌快照为 JSON 对象。</summary>
        private static void WriteCard(StringBuilder builder, CardSnapshot card)
        {
            if(card == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "entity_id", card.EntityId, true);
            WriteProperty(builder, "card_id", card.CardId, false);
            WriteProperty(builder, "dbf_id", card.DbFId, false);
            WriteProperty(builder, "name", card.Name, false);
            WriteProperty(builder, "cost", card.Cost, false);
            WriteProperty(builder, "type", card.Type, false);
            WriteProperty(builder, "text", card.Text, false);
            builder.Append('}');
        }

        private static void WriteDeckMetadata(StringBuilder builder, GameMetadata metadata)
        {
            if(metadata == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "deck_available", metadata.DeckAvailable, true);
            WriteProperty(builder, "deck_id", metadata.DeckId, false);
            WriteProperty(builder, "name", metadata.DeckName, false);
            WriteProperty(builder, "player_class", metadata.PlayerClass, false);
            WriteProperty(builder, "format", metadata.Format, false);
            builder.Append(",\"cards\":");
            WriteDeckCards(builder, metadata.Cards);
            builder.Append('}');
        }

        private static void WriteDeckCards(StringBuilder builder, IEnumerable<DeckCardMetadata> cards)
        {
            builder.Append('[');
            var first = true;
            if(cards != null)
            {
                foreach(var card in cards)
                {
                    if(!first)
                        builder.Append(',');
                    first = false;
                    WriteDeckCard(builder, card);
                }
            }
            builder.Append(']');
        }

        private static void WriteDeckCard(StringBuilder builder, DeckCardMetadata card)
        {
            if(card == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "card_id", card.CardId, true);
            WriteProperty(builder, "dbf_id", card.DbFId, false);
            WriteProperty(builder, "name", card.Name, false);
            WriteProperty(builder, "cost", card.Cost, false);
            WriteProperty(builder, "type", card.Type, false);
            WriteProperty(builder, "count", card.Count, false);
            builder.Append('}');
        }

        /// <summary>序列化随从列表为 JSON 数组。</summary>
        private static void WriteMinions(StringBuilder builder, IEnumerable<MinionSnapshot> minions)
        {
            builder.Append('[');
            var first = true;
            if(minions != null)
            {
                foreach(var minion in minions)
                {
                    if(!first)
                        builder.Append(',');
                    first = false;
                    WriteMinion(builder, minion);
                }
            }
            builder.Append(']');
        }

        /// <summary>序列化单个随从快照为 JSON 对象。</summary>
        private static void WriteMinion(StringBuilder builder, MinionSnapshot minion)
        {
            if(minion == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "entity_id", minion.EntityId, true);
            WriteProperty(builder, "card_id", minion.CardId, false);
            WriteProperty(builder, "dbf_id", minion.DbFId, false);
            WriteProperty(builder, "name", minion.Name, false);
            WriteProperty(builder, "text", minion.Text, false);
            WriteProperty(builder, "attack", minion.Attack, false);
            WriteProperty(builder, "health", minion.Health, false);
            WriteProperty(builder, "damage", minion.Damage, false);
            WriteProperty(builder, "zone_position", minion.ZonePosition, false);
            WriteProperty(builder, "attacks_this_turn", minion.AttacksThisTurn, false);
            WriteProperty(builder, "max_attacks_per_turn", minion.MaxAttacksPerTurn, false);
            WriteProperty(builder, "taunt", minion.Taunt, false);
            WriteProperty(builder, "divine_shield", minion.DivineShield, false);
            WriteProperty(builder, "stealth", minion.Stealth, false);
            WriteProperty(builder, "immune", minion.Immune, false);
            WriteProperty(builder, "frozen", minion.Frozen, false);
            WriteProperty(builder, "rush", minion.Rush, false);
            WriteProperty(builder, "charge", minion.Charge, false);
            WriteProperty(builder, "windfury", minion.Windfury, false);
            WriteProperty(builder, "mega_windfury", minion.MegaWindfury, false);
            WriteProperty(builder, "lifesteal", minion.Lifesteal, false);
            WriteProperty(builder, "poisonous", minion.Poisonous, false);
            WriteProperty(builder, "venomous", minion.Venomous, false);
            WriteProperty(builder, "reborn", minion.Reborn, false);
            WriteProperty(builder, "deathrattle", minion.Deathrattle, false);
            WriteProperty(builder, "dormant", minion.Dormant, false);
            WriteProperty(builder, "silenced", minion.Silenced, false);
            WriteProperty(builder, "cant_attack", minion.CantAttack, false);
            WriteProperty(builder, "exhausted", minion.Exhausted, false);
            builder.Append('}');
        }

        /// <summary>序列化游戏事件列表为 JSON 数组。</summary>
        private static void WriteEvents(StringBuilder builder, IEnumerable<GameEvent> events)
        {
            builder.Append('[');
            var first = true;
            if(events != null)
            {
                foreach(var gameEvent in events)
                {
                    if(!first)
                        builder.Append(',');
                    first = false;
                    WriteGameEvent(builder, gameEvent);
                }
            }
            builder.Append(']');
        }

        private static void WriteEventTarget(StringBuilder builder, EventTarget target)
        {
            if(target == null)
            {
                builder.Append("null");
                return;
            }

            builder.Append('{');
            WriteProperty(builder, "entity_id", target.EntityId, true);
            WriteProperty(builder, "card_id", target.CardId, false);
            WriteProperty(builder, "name", target.Name, false);
            WriteProperty(builder, "type", target.Type, false);
            builder.Append('}');
        }

        /// <summary>写入一个字符串类型的 JSON 属性，null 值输出为 null。</summary>
        private static void WriteProperty(StringBuilder builder, string name, string value, bool first)
        {
            if(!first)
                builder.Append(',');
            builder.Append('"').Append(Escape(name)).Append("\":");
            if(value == null)
                builder.Append("null");
            else
                builder.Append('"').Append(Escape(value)).Append('"');
        }

        /// <summary>写入一个整数类型的 JSON 属性。</summary>
        private static void WriteProperty(StringBuilder builder, string name, int value, bool first)
        {
            if(!first)
                builder.Append(',');
            builder.Append('"').Append(Escape(name)).Append("\":").Append(value);
        }

        private static void WriteProperty(StringBuilder builder, string name, int? value, bool first)
        {
            if(!first)
                builder.Append(',');
            builder.Append('"').Append(Escape(name)).Append("\":");
            if(value.HasValue)
                builder.Append(value.Value);
            else
                builder.Append("null");
        }

        /// <summary>写入一个布尔类型的 JSON 属性。</summary>
        private static void WriteProperty(StringBuilder builder, string name, bool value, bool first)
        {
            if(!first)
                builder.Append(',');
            builder.Append('"').Append(Escape(name)).Append("\":").Append(value ? "true" : "false");
        }

        private static void WriteOptionalProperty(
            StringBuilder builder,
            string name,
            string value,
            ref bool first)
        {
            if(string.IsNullOrEmpty(value))
                return;
            WriteProperty(builder, name, value, first);
            first = false;
        }

        private static void WriteOptionalPositiveProperty(
            StringBuilder builder,
            string name,
            int value,
            ref bool first)
        {
            if(value <= 0)
                return;
            WriteProperty(builder, name, value, first);
            first = false;
        }

        private static void WriteOptionalPositiveProperty(
            StringBuilder builder,
            string name,
            int? value,
            ref bool first)
        {
            if(!value.HasValue || value.Value <= 0)
                return;
            WriteProperty(builder, name, value, first);
            first = false;
        }

        /// <summary>
        /// 对 JSON 字符串值中的特殊字符进行转义。
        /// 处理反斜杠、双引号、换行符、回车符、制表符以及 ASCII 控制字符。
        /// </summary>
        private static string Escape(string value)
        {
            if(value == null)
                return string.Empty;

            var builder = new StringBuilder(value.Length + 8);
            for(var i = 0; i < value.Length; i++)
            {
                var c = value[i];
                switch(c)
                {
                    case '\\':
                        builder.Append("\\\\");
                        break;
                    case '"':
                        builder.Append("\\\"");
                        break;
                    case '\n':
                        builder.Append("\\n");
                        break;
                    case '\r':
                        builder.Append("\\r");
                        break;
                    case '\t':
                        builder.Append("\\t");
                        break;
                    default:
                        // ASCII 控制字符使用 \uXXXX 转义
                        if(c < 32)
                            builder.Append("\\u").Append(((int)c).ToString("x4"));
                        else
                            builder.Append(c);
                        break;
                }
            }
            return builder.ToString();
        }
    }
}
