using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace HdtAiAssistant.Core.Publishing
{
    /// <summary>
    /// WebSocket 后端传输实现——通过 WebSocket 协议将数据发送到 AI 后端。
    /// 支持自动重连：发送时若连接断开则自动重新建立连接。
    /// 实现 IDisposable 以释放 WebSocket 资源。
    /// </summary>
    public sealed class WebSocketBackendTransport : IBackendTransport, IDisposable
    {
        private readonly Uri _endpoint;
        private ClientWebSocket _socket;

        /// <summary>
        /// 创建 WebSocket 传输实例。
        /// </summary>
        /// <param name="endpoint">WebSocket 服务端地址（如 ws://127.0.0.1:8765/ws/hdt）。</param>
        /// <exception cref="ArgumentException">endpoint 为空或 null 时抛出。</exception>
        public WebSocketBackendTransport(string endpoint)
        {
            if(string.IsNullOrWhiteSpace(endpoint))
                throw new ArgumentException("Endpoint is required.", "endpoint");
            _endpoint = new Uri(endpoint);
        }

        /// <summary>
        /// 异步发送字符串负载。若 WebSocket 未连接则先自动连接。
        /// </summary>
        /// <param name="payload">待发送的字符串负载。</param>
        /// <param name="cancellationToken">取消令牌。</param>
        public async Task SendAsync(string payload, CancellationToken cancellationToken)
        {
            await EnsureConnectedAsync(cancellationToken).ConfigureAwait(false);

            var bytes = Encoding.UTF8.GetBytes(payload ?? string.Empty);
            await _socket.SendAsync(
                new ArraySegment<byte>(bytes),
                WebSocketMessageType.Text,
                true,
                cancellationToken).ConfigureAwait(false);
        }

        /// <summary>释放 WebSocket 连接资源。</summary>
        public void Dispose()
        {
            if(_socket != null)
                _socket.Dispose();
        }

        /// <summary>
        /// 确保 WebSocket 处于连接状态。若已断开则丢弃旧连接并重新建立。
        /// </summary>
        private async Task EnsureConnectedAsync(CancellationToken cancellationToken)
        {
            if(_socket != null && _socket.State == WebSocketState.Open)
                return;

            // 丢弃可能处于异常状态的旧连接
            if(_socket != null)
                _socket.Dispose();

            _socket = new ClientWebSocket();
            await _socket.ConnectAsync(_endpoint, cancellationToken).ConfigureAwait(false);
        }
    }
}
