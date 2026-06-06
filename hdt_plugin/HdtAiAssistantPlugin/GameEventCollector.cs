using System;
using Hearthstone_Deck_Tracker.API;
using Hearthstone_Deck_Tracker.Hearthstone;
using Hearthstone_Deck_Tracker.Hearthstone.Entities;
using HdtAiAssistant.Core.Events;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// 游戏事件收集器——将 HDT 的原生事件数据转换为平台无关的 GameEvent 模型。
    ///
    /// 提供工厂方法创建不同类型的事件：生命周期事件（对局开始/结束）、
    /// 回合开始事件、卡牌事件等。
    /// </summary>
    internal sealed class GameEventCollector
    {
        /// <summary>
        /// 创建生命周期事件（如 game_started、game_ended）。
        /// </summary>
        /// <param name="gameId">对局唯一标识。</param>
        /// <param name="turn">当前回合数。</param>
        /// <param name="type">事件类型字符串。</param>
        public GameEvent CreateLifecycleEvent(string gameId, int turn, string type, string reason = null, string result = null)
        {
            return new GameEvent
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Turn = turn,
                Type = type,
                Player = "system",
                Reason = reason,
                Result = result
            };
        }

        /// <summary>
        /// 创建回合开始事件。
        /// </summary>
        /// <param name="gameId">对局唯一标识。</param>
        /// <param name="turn">当前回合数。</param>
        /// <param name="activePlayer">活跃玩家（"me" 或 "opponent"）。</param>
        public GameEvent CreateTurnStartedEvent(string gameId, int turn, string activePlayer)
        {
            return new GameEvent
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Turn = turn,
                Type = "turn_started",
                Player = activePlayer
            };
        }

        /// <summary>
        /// 从 HDT Card 对象创建卡牌事件。
        /// </summary>
        /// <param name="gameId">对局唯一标识。</param>
        /// <param name="turn">当前回合数。</param>
        /// <param name="player">事件所属玩家（"me" / "opponent"）。</param>
        /// <param name="type">事件类型（card_played、card_drawn 等）。</param>
        /// <param name="card">HDT 卡牌对象。</param>
        public GameEvent CreateCardEvent(
            string gameId,
            int turn,
            string player,
            string type,
            Card card,
            int? entityId = null,
            EventTarget target = null)
        {
            return new GameEvent
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Turn = turn,
                Player = player,
                Type = type,
                EntityId = entityId,
                CardId = card == null ? null : card.get_Id(),
                DbFId = card == null ? 0 : card.DbfId,
                Name = card == null ? null : card.LocalizedName,
                Target = target
            };
        }

        /// <summary>
        /// 从 HDT Entity 对象创建卡牌事件（用于需要更多实体信息的场景）。
        /// </summary>
        /// <param name="gameId">对局唯一标识。</param>
        /// <param name="turn">当前回合数。</param>
        /// <param name="player">事件所属玩家。</param>
        /// <param name="type">事件类型。</param>
        /// <param name="entity">HDT 实体对象。</param>
        public GameEvent CreateEntityCardEvent(string gameId, int turn, string player, string type, Entity entity)
        {
            return new GameEvent
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Turn = turn,
                Player = player,
                Type = type,
                EntityId = entity == null ? 0 : entity.Id,
                CardId = entity == null ? null : entity.CardId,
                DbFId = entity == null || entity.Card == null ? 0 : entity.Card.DbfId,
                Name = entity == null ? null : entity.LocalizedName ?? entity.Name
            };
        }

        public GameEvent CreateAttackEvent(
            string gameId,
            int turn,
            string player,
            AttackInfo attackInfo,
            int? attackerEntityId = null,
            EventTarget defender = null)
        {
            var gameEvent = AttackEventFactory.Create(
                gameId,
                turn,
                player,
                ToAttackParticipant(attackInfo == null ? null : attackInfo.Attacker),
                ToAttackParticipant(attackInfo == null ? null : attackInfo.Defender));
            gameEvent.EntityId = attackerEntityId;
            if(defender != null)
                gameEvent.Target = defender;
            return gameEvent;
        }

        private static AttackParticipant ToAttackParticipant(Card card)
        {
            if(card == null)
                return null;

            return new AttackParticipant
            {
                CardId = card.get_Id(),
                DbFId = card.DbfId,
                Name = card.LocalizedName ?? card.Name,
                Type = ReadCardType(card),
                Attack = card.Attack
            };
        }

        private static string ReadCardType(Card card)
        {
            if(card == null)
                return null;
            if(card.TypeEnum.HasValue)
                return card.TypeEnum.Value.ToString();
            return card.Type;
        }
    }
}
