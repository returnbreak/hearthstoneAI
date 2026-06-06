using System.Collections.Generic;

namespace HdtAiAssistant.Core.Models
{
    public sealed class GameMetadata
    {
        public GameMetadata()
        {
            Cards = new List<DeckCardMetadata>();
        }

        public string GameId { get; set; }
        public string CapturedAt { get; set; }
        public bool DeckAvailable { get; set; }
        public string DeckId { get; set; }
        public string DeckName { get; set; }
        public string PlayerClass { get; set; }
        public string Format { get; set; }
        public List<DeckCardMetadata> Cards { get; private set; }
    }
}
