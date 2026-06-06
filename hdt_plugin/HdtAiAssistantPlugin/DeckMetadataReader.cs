using System;
using Hearthstone_Deck_Tracker;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistantPlugin
{
    internal sealed class DeckMetadataReader
    {
        public GameMetadata Read(string gameId)
        {
            var metadata = new GameMetadata
            {
                GameId = gameId,
                CapturedAt = DateTimeOffset.Now.ToString("o"),
                DeckAvailable = false
            };

            try
            {
                var deckList = DeckList.Instance;
                var deck = deckList == null
                    ? null
                    : deckList.ActiveDeckVersion ?? deckList.ActiveDeck;
                if(deck == null)
                    return metadata;

                metadata.DeckAvailable = true;
                metadata.DeckId = deck.DeckId.ToString();
                metadata.DeckName = deck.Name;
                metadata.PlayerClass = deck.Class;
                metadata.Format = ReadFormat(deck);

                if(deck.Cards == null)
                    return metadata;

                foreach(var card in deck.Cards)
                {
                    if(card == null)
                        continue;
                    metadata.Cards.Add(new DeckCardMetadata
                    {
                        CardId = card.get_Id(),
                        DbFId = card.DbfId,
                        Name = card.LocalizedName ?? card.Name,
                        Cost = card.Cost,
                        Type = card.Type,
                        Count = card.Count
                    });
                }
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }

            return metadata;
        }

        private static string ReadFormat(Hearthstone_Deck_Tracker.Hearthstone.Deck deck)
        {
            if(deck.IsArenaDeck)
                return "arena";
            if(deck.IsDuelsDeck)
                return "duels";
            if(deck.IsBrawlDeck)
                return "brawl";
            if(deck.IsTwistDeck)
                return "twist";
            if(deck.IsClassicDeck)
                return "classic";
            if(deck.IsWildDeck)
                return "wild";
            if(deck.StandardViable)
                return "standard";
            return "unknown";
        }
    }
}
