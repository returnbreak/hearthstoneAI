using System;
using System.Threading;
using System.Threading.Tasks;
using HdtAiAssistant.Core.Models;
using HdtAiAssistant.Core.Throttling;

namespace HdtAiAssistant.Core.Publishing
{
    /// <summary>
    /// 快照发布器——将游戏状态和游戏事件序列化为 JSON 后通过传输层发送到 AI 后端。
    /// 负责跟踪后端可用性并记录最近一次错误信息。
    /// </summary>
    public sealed class SnapshotPublisher
    {
        private readonly IBackendTransport _transport;

        /// <summary>
        /// 创建发布器实例。
        /// </summary>
        /// <param name="transport">后端传输实现。</param>
        /// <exception cref="ArgumentNullException">transport 为 null 时抛出。</exception>
        public SnapshotPublisher(IBackendTransport transport)
        {
            if(transport == null)
                throw new ArgumentNullException("transport");
            _transport = transport;
        }

        /// <summary>后端是否当前可用（最近一次发送是否成功）。</summary>
        public bool IsBackendAvailable { get; private set; }

        /// <summary>最近一次发送失败的错误消息，成功时为 null。</summary>
        public string LastError { get; private set; }

        /// <summary>发布游戏状态快照到后端。</summary>
        /// <param name="state">游戏状态快照。</param>
        /// <param name="trigger">推荐触发原因。</param>
        /// <param name="cancellationToken">取消令牌。</param>
        public Task PublishGameStateAsync(GameState state, RecommendationTrigger trigger, CancellationToken cancellationToken)
        {
            var payload = JsonPayloadWriter.WriteGameStateEnvelope(state, trigger);
            return SendAsync(payload, cancellationToken);
        }

        /// <summary>发布单个游戏事件到后端。</summary>
        /// <param name="gameEvent">游戏事件。</param>
        /// <param name="cancellationToken">取消令牌。</param>
        public Task PublishGameEventAsync(GameEvent gameEvent, CancellationToken cancellationToken)
        {
            var payload = JsonPayloadWriter.WriteGameEventEnvelope(gameEvent);
            return SendAsync(payload, cancellationToken);
        }

        /// <summary>通过传输层发送数据并更新可用性状态。</summary>
        private async Task SendAsync(string payload, CancellationToken cancellationToken)
        {
            try
            {
                await _transport.SendAsync(payload, cancellationToken).ConfigureAwait(false);
                IsBackendAvailable = true;
                LastError = null;
            }
            catch(Exception ex)
            {
                IsBackendAvailable = false;
                LastError = ex.Message;
            }
        }
    }
}
