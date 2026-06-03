namespace HdtAiAssistant.Core.Models
{
    /// <summary>
    /// 法力水晶快照——记录当前回合的法力状态。
    /// </summary>
    public sealed class ManaSnapshot
    {
        /// <summary>当前可用法力水晶数。</summary>
        public int Current { get; set; }
        /// <summary>法力水晶上限。</summary>
        public int Max { get; set; }
    }
}
