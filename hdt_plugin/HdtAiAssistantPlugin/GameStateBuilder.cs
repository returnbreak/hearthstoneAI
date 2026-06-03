using System.Collections.Generic;
using Hearthstone_Deck_Tracker.API;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// 游戏状态构建器——维护近期游戏事件的环形缓冲区，
    /// 并在每次构建快照时将缓冲的事件附加到 GameState 中。
    /// </summary>
    internal sealed class GameStateBuilder
    {
        private readonly EntityStateReader _reader;
        private readonly Queue<GameEvent> _recentEvents;
        private readonly int _maxRecentEvents;

        /// <summary>
        /// 创建状态构建器。
        /// </summary>
        /// <param name="reader">实体状态读取器。</param>
        /// <param name="maxRecentEvents">最多保留的近期事件数（至少为 1）。</param>
        public GameStateBuilder(EntityStateReader reader, int maxRecentEvents)
        {
            _reader = reader;
            _maxRecentEvents = maxRecentEvents < 1 ? 1 : maxRecentEvents;
            _recentEvents = new Queue<GameEvent>();
        }

        /// <summary>
        /// 记录一条游戏事件。当事件数超过上限时自动丢弃最旧的事件（环形缓冲）。
        /// </summary>
        public void Record(GameEvent gameEvent)
        {
            if(gameEvent == null)
                return;

            _recentEvents.Enqueue(gameEvent);
            while(_recentEvents.Count > _maxRecentEvents)
                _recentEvents.Dequeue();
        }

        /// <summary>
        /// 构建当前游戏状态快照，包含从 HDT 读取的状态和缓冲的近期事件。
        /// </summary>
        /// <param name="gameId">当前对局唯一标识。</param>
        /// <returns>完整的 GameState 快照。</returns>
        public GameState Build(string gameId)
        {
            var state = _reader.Read(Core.Game, gameId);
            foreach(var gameEvent in _recentEvents)
                state.RecentEvents.Add(gameEvent);
            return state;
        }
    }
}
