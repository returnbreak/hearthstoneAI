using Hearthstone_Deck_Tracker;
using Hearthstone_Deck_Tracker.Hearthstone;
using Hearthstone_Deck_Tracker.Hearthstone.Entities;
using HdtAiAssistant.Core.Events;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistantPlugin
{
    internal sealed class ResolvedPlay
    {
        public int? SourceEntityId { get; set; }
        public EventTarget Target { get; set; }
    }

    internal sealed class PowerLogEventResolver
    {
        public ResolvedPlay ResolveLatestPlay(Card card)
        {
            var game = Core.Game;
            if(game == null || card == null)
                return new ResolvedPlay();

            var reference = PowerLogTargetParser.FindLatestPlay(game.PowerLog, card.get_Id());
            if(reference == null)
                return new ResolvedPlay();

            return new ResolvedPlay
            {
                SourceEntityId = reference.SourceEntityId,
                Target = ResolveTarget(game, reference.TargetEntityId)
            };
        }

        public EventTarget ResolveEntity(int? entityId)
        {
            return ResolveTarget(Core.Game, entityId);
        }

        private static EventTarget ResolveTarget(GameV2 game, int? entityId)
        {
            if(game == null || !entityId.HasValue || game.Entities == null)
                return null;

            Entity entity;
            if(!game.Entities.TryGetValue(entityId.Value, out entity) || entity == null)
                return null;

            return new EventTarget
            {
                EntityId = entity.Id,
                CardId = entity.CardId,
                Name = entity.LocalizedName ?? entity.Name,
                Type = ReadEntityType(entity)
            };
        }

        private static string ReadEntityType(Entity entity)
        {
            if(entity == null || entity.Card == null || !entity.Card.TypeEnum.HasValue)
                return "unknown";

            var type = entity.Card.TypeEnum.Value.ToString().ToLowerInvariant();
            return type == "hero" ? "hero" : type == "minion" ? "minion" : type;
        }
    }
}
