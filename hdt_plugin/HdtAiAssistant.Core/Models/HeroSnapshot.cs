namespace HdtAiAssistant.Core.Models
{
    public sealed class HeroSnapshot
    {
        public string Class { get; set; }
        public int Hp { get; set; }
        public int Armor { get; set; }
        public int Attack { get; set; }
        public bool CanAttack { get; set; }
        public int AttacksThisTurn { get; set; }
        public int AttacksRemaining { get; set; }
        public bool Immune { get; set; }
        public bool Frozen { get; set; }
    }
}
