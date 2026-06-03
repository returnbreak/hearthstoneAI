using HdtAiAssistant.Core.Models;

namespace HdtAiAssistant.Core.Throttling
{
    /// <summary>
    /// 快照节流器——控制状态上报和 AI 推荐的频率，避免向后端发送过多冗余请求。
    ///
    /// 两条独立的节流规则：
    /// 1. 状态上报：仅当状态哈希与上次不同时才允许上报。
    /// 2. 推荐触发：每回合最多允许 N 次自动推荐，且仅在我的回合内触发。
    /// </summary>
    public sealed class SnapshotThrottler
    {
        private readonly int _maxAutomaticRecommendationsPerTurn;
        private string _lastPublishedHash;
        private string _lastRecommendationHash;
        private int _currentTurn = -1;
        private int _automaticRecommendationsThisTurn;

        /// <summary>
        /// 创建节流器实例。
        /// </summary>
        /// <param name="maxAutomaticRecommendationsPerTurn">每回合最多自动推荐次数（至少为 1）。</param>
        public SnapshotThrottler(int maxAutomaticRecommendationsPerTurn)
        {
            _maxAutomaticRecommendationsPerTurn = maxAutomaticRecommendationsPerTurn < 1
                ? 1
                : maxAutomaticRecommendationsPerTurn;
        }

        /// <summary>
        /// 使用默认参数创建节流器（每回合最多 2 次自动推荐）。
        /// </summary>
        public SnapshotThrottler()
            : this(2)
        {
        }

        /// <summary>
        /// 判断是否应该向後端上报当前状态。
        /// 仅当状态与上次上报时不同才返回 true。
        /// </summary>
        /// <param name="state">当前游戏状态。</param>
        /// <returns>应该上报时返回 true。</returns>
        public bool ShouldPublishState(GameState state)
        {
            var hash = StateHashCalculator.Calculate(state);
            if(hash == _lastPublishedHash)
                return false;

            _lastPublishedHash = hash;
            return true;
        }

        /// <summary>
        /// 判断是否应该触发 AI 推荐。
        /// 手动触发始终允许；自动触发受每回合次数限制，且仅在我的回合内生效。
        /// </summary>
        /// <param name="state">当前游戏状态。</param>
        /// <param name="trigger">触发原因。</param>
        /// <returns>应该触发推荐时返回 true。</returns>
        public bool ShouldTriggerRecommendation(GameState state, RecommendationTrigger trigger)
        {
            if(state == null)
                return false;

            // 手动触发始终允许
            if(trigger == RecommendationTrigger.Manual)
                return true;

            // 仅在玩家回合内才可能自动触发
            if(state.ActivePlayer != "me")
                return false;

            // 进入新回合时重置计数器
            if(state.Turn != _currentTurn)
            {
                _currentTurn = state.Turn;
                _automaticRecommendationsThisTurn = 0;
                _lastRecommendationHash = null;
            }

            // 状态与上次推荐时相同则跳过
            var hash = StateHashCalculator.Calculate(state);
            if(hash == _lastRecommendationHash)
                return false;

            // 达到每回合自动推荐上限
            if(_automaticRecommendationsThisTurn >= _maxAutomaticRecommendationsPerTurn)
                return false;

            // 仅响应回合开始和显著状态变化两种自动触发
            if(trigger != RecommendationTrigger.MyTurnStarted &&
                trigger != RecommendationTrigger.SignificantStateChange)
                return false;

            _lastRecommendationHash = hash;
            _automaticRecommendationsThisTurn++;
            return true;
        }
    }
}
