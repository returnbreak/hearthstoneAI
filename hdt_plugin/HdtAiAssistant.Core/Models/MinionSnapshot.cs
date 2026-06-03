namespace HdtAiAssistant.Core.Models
{
    public sealed class MinionSnapshot
    {
        public int EntityId { get; set; }
        public string CardId { get; set; }
        public int DbFId { get; set; }
        public string Name { get; set; }
        public int Attack { get; set; }
        public int Health { get; set; }
        public int Damage { get; set; }
        public int ZonePosition { get; set; }
        public bool CanAttack { get; set; }
        public int AttacksThisTurn { get; set; }
        public int AttacksRemaining { get; set; }
        public bool Taunt { get; set; }
        public bool DivineShield { get; set; }
        public bool Stealth { get; set; }
        public bool Immune { get; set; }
        public bool Frozen { get; set; }
        public bool Rush { get; set; }
        public bool Charge { get; set; }
        public bool Windfury { get; set; }
        public bool MegaWindfury { get; set; }
        public bool Lifesteal { get; set; }
        public bool Poisonous { get; set; }
        public bool Venomous { get; set; }
        public bool Reborn { get; set; }
        public bool Deathrattle { get; set; }
        public bool Dormant { get; set; }
        public bool Silenced { get; set; }
        public bool CantAttack { get; set; }
        public bool Exhausted { get; set; }
    }
}
