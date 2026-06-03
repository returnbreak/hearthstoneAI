using System.Threading;
using System.Threading.Tasks;

namespace HdtAiAssistant.Core.Publishing
{
    /// <summary>
    /// 后端传输接口——定义向 AI 后端发送数据的抽象通道。
    /// 具体实现可以是 WebSocket、HTTP 等任意传输方式。
    /// </summary>
    public interface IBackendTransport
    {
        /// <summary>
        /// 异步发送字符串负载到后端。
        /// </summary>
        /// <param name="payload">待发送的 JSON 字符串。</param>
        /// <param name="cancellationToken">取消令牌。</param>
        Task SendAsync(string payload, CancellationToken cancellationToken);
    }
}
