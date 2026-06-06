namespace HdtAiAssistant.Core.Models
{
    public sealed class DeckCardMetadata
    {
        public string CardId { get; set; }
        public int DbFId { get; set; }
        public string Name { get; set; }
        public int Cost { get; set; }
        public string Type { get; set; }
        public int Count { get; set; }
    }
}
