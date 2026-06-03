namespace HdtAiAssistant.Core.Models
{
    /// <summary>
    /// 卡牌快照——记录单张卡牌在某一时刻的关键信息。
    /// </summary>
    public sealed class CardSnapshot
    {
        /// <summary>游戏内实体 ID。</summary>
        public int EntityId { get; set; }
        /// <summary>卡牌 ID（如 CS2_029）。</summary>
        public string CardId { get; set; }
        /// <summary>Hearthstone 数据库中的卡牌 ID。</summary>
        public int DbFId { get; set; }
        /// <summary>卡牌名称。</summary>
        public string Name { get; set; }
        /// <summary>当前法力消耗。</summary>
        public int Cost { get; set; }
        /// <summary>卡牌类型（如 SPELL、MINION、WEAPON）。</summary>
        public string Type { get; set; }
        /// <summary>卡牌所在区域（HAND / DECK / PLAY）。</summary>
        public string Zone { get; set; }
        /// <summary>卡牌信息来源（如 "played" 表示从已打出卡牌中推断）。</summary>
        public string Source { get; set; }
    }
}
