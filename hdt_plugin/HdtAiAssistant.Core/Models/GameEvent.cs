namespace HdtAiAssistant.Core.Models
{
    /// <summary>
    /// 游戏事件——记录游戏过程中发生的单次事件（抽牌、出牌、回合开始等）。
    /// </summary>
    public sealed class GameEvent
    {
        /// <summary>对局唯一标识。</summary>
        public string GameId { get; set; }
        /// <summary>事件发生的 ISO 8601 时间戳。</summary>
        public string Timestamp { get; set; }
        /// <summary>事件发生时的回合数。</summary>
        public int Turn { get; set; }
        /// <summary>事件所属玩家（"me" / "opponent" / "system"）。</summary>
        public string Player { get; set; }
        /// <summary>事件类型（如 card_played、card_drawn、turn_started）。</summary>
        public string Type { get; set; }
        /// <summary>关联的实体 ID。</summary>
        public int EntityId { get; set; }
        /// <summary>关联的卡牌 ID。</summary>
        public string CardId { get; set; }
        /// <summary>关联的数据库卡牌 ID。</summary>
        public int DbFId { get; set; }
        /// <summary>卡牌名称。</summary>
        public string Name { get; set; }
        /// <summary>卡牌来源区域（移动前所在区域）。</summary>
        public string ZoneFrom { get; set; }
        /// <summary>卡牌目标区域（移动后所在区域）。</summary>
        public string ZoneTo { get; set; }
        /// <summary>事件目标实体 ID（如法术目标）。</summary>
        public int TargetEntityId { get; set; }
        public string Reason { get; set; }
        public string Result { get; set; }
    }
}
