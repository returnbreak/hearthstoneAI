namespace HdtAiAssistant.Core.Throttling
{
    /// <summary>
    /// 推荐触发原因——说明是什么导致了本次 AI 推荐请求。
    /// </summary>
    public enum RecommendationTrigger
    {
        /// <summary>无触发（仅上报状态，不请求推荐）。</summary>
        None,
        /// <summary>我的回合开始。</summary>
        MyTurnStarted,
        /// <summary>游戏状态发生了显著变化（如出牌、抽牌）。</summary>
        SignificantStateChange,
        /// <summary>玩家手动触发。</summary>
        Manual,
        /// <summary>对局开始。</summary>
        GameStarted,
        /// <summary>对局结束。</summary>
        GameEnded
    }
}
