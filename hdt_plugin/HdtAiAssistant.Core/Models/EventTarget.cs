namespace HdtAiAssistant.Core.Models
{
    public sealed class EventTarget
    {
        public int? EntityId { get; set; }
        public string CardId { get; set; }
        public string Name { get; set; }
        public string Type { get; set; }
    }
}
