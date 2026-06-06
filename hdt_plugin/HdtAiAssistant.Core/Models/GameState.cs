using System.Collections.Generic;

namespace HdtAiAssistant.Core.Models
{
    /// <summary>
    /// 游戏状态快照——汇总某一时刻对局的完整公开信息，发送给 AI 后端。
    /// 包含英雄状态、手牌、战场、已知敌方卡牌以及近期事件列表。
    /// </summary>
    public sealed class GameState
    {
        /// <summary>初始化所有集合属性，避免外部使用时出现 null 引用。</summary>
        public GameState()
        {
            MyHero = new HeroSnapshot();
            EnemyHero = new HeroSnapshot();
            MyMana = new ManaSnapshot();
            EnemyMana = new ManaSnapshot();
            Hand = new List<CardSnapshot>();
            MyBoard = new List<MinionSnapshot>();
            EnemyBoard = new List<MinionSnapshot>();
            KnownEnemyCards = new List<CardSnapshot>();
            RecentEvents = new List<GameEvent>();
        }

        /// <summary>对局唯一标识。</summary>
        public string GameId { get; set; }
        /// <summary>快照生成的 ISO 8601 时间戳。</summary>
        public string Timestamp { get; set; }
        /// <summary>游戏模式（standard / wild / arena 等）。</summary>
        public string Mode { get; set; }
        /// <summary>当前回合数。</summary>
        public int Turn { get; set; }
        /// <summary>当前活跃玩家（"me" 或 "opponent"）。</summary>
        public string ActivePlayer { get; set; }
        /// <summary>我方英雄状态。</summary>
        public HeroSnapshot MyHero { get; set; }
        /// <summary>敌方英雄状态。</summary>
        public HeroSnapshot EnemyHero { get; set; }
        /// <summary>当前法力水晶状态。</summary>
        public ManaSnapshot MyMana { get; set; }
        public ManaSnapshot EnemyMana { get; set; }
        /// <summary>我方手牌列表。</summary>
        public List<CardSnapshot> Hand { get; private set; }
        /// <summary>我方战场随从列表。</summary>
        public List<MinionSnapshot> MyBoard { get; private set; }
        /// <summary>敌方战场随从列表。</summary>
        public List<MinionSnapshot> EnemyBoard { get; private set; }
        /// <summary>我方牌库剩余卡牌数。</summary>
        public int MyDeckCount { get; set; }
        /// <summary>敌方手牌数。</summary>
        public int EnemyHandCount { get; set; }
        /// <summary>敌方牌库剩余卡牌数。</summary>
        public int EnemyDeckCount { get; set; }
        /// <summary>已知的敌方卡牌列表（从已打出的卡牌推断）。</summary>
        public List<CardSnapshot> KnownEnemyCards { get; private set; }
        /// <summary>最近的游戏事件列表（环形缓冲区，最多保留最近 N 条）。</summary>
        public List<GameEvent> RecentEvents { get; private set; }
    }
}
