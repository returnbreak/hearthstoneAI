using System;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistant.Core.Events
{
    public sealed class AttackParticipant
    {
        public int? EntityId { get; set; }
        public string CardId { get; set; }
        public int DbFId { get; set; }
        public string Name { get; set; }
        public string Type { get; set; }
        public int Attack { get; set; }
    }

    public static class AttackEventFactory
    {
        public static GameEvent Create(
            string gameId,
            int turn,
            string player,
            AttackParticipant attacker,
            AttackParticipant defender)
        {
            var attackerType = NormalizeParticipantType(attacker == null ? null : attacker.Type);
            return new GameEvent
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Turn = turn,
                Player = player,
                Type = attackerType == "hero" ? "hero_attack" : "minion_attack",
                EntityId = attacker == null ? null : attacker.EntityId,
                CardId = attacker == null ? null : attacker.CardId,
                DbFId = attacker == null ? 0 : attacker.DbFId,
                Name = attacker == null ? null : attacker.Name,
                Target = defender == null ? null : new EventTarget
                {
                    EntityId = defender.EntityId,
                    CardId = defender.CardId,
                    Name = defender.Name,
                    Type = NormalizeParticipantType(defender.Type)
                },
                DamageAmount = attacker == null ? 0 : attacker.Attack
            };
        }

        private static string NormalizeParticipantType(string type)
        {
            if(string.IsNullOrEmpty(type))
                return "unknown";

            var normalized = type.Trim().ToLowerInvariant();
            if(normalized == "hero")
                return "hero";
            if(normalized == "minion")
                return "minion";
            return normalized;
        }
    }
}
