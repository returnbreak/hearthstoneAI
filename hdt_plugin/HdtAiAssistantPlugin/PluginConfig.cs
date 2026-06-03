using System;
using System.Globalization;
using System.IO;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// 插件配置——管理 HDT AI Assistant 插件的所有可配置参数。
    /// 配置以键值对形式存储在 config.txt 文件中，支持启动时加载和默认值回退。
    /// </summary>
    public sealed class PluginConfig
    {
        /// <summary>默认后端 WebSocket 地址。</summary>
        public const string DefaultBackendWebSocketUrl = "ws://127.0.0.1:8765/ws/hdt";

        /// <summary>使用默认值初始化配置。</summary>
        public PluginConfig()
        {
            BackendWebSocketUrl = DefaultBackendWebSocketUrl;
            PollIntervalMilliseconds = 500;
            MaxRecentEvents = 20;
            MaxAutomaticRecommendationsPerTurn = 2;
        }

        /// <summary>后端 WebSocket 地址。</summary>
        public string BackendWebSocketUrl { get; private set; }
        /// <summary>轮询间隔（毫秒），控制状态快照的采集频率。</summary>
        public int PollIntervalMilliseconds { get; private set; }
        /// <summary>最多保留的近期游戏事件数量。</summary>
        public int MaxRecentEvents { get; private set; }
        /// <summary>每回合最多自动触发 AI 推荐的次数。</summary>
        public int MaxAutomaticRecommendationsPerTurn { get; private set; }

        /// <summary>插件配置目录路径（%AppData%/HdtAiAssistantPlugin）。</summary>
        public static string ConfigDirectory
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "HdtAiAssistantPlugin");
            }
        }

        /// <summary>
        /// 从 config.txt 加载配置。若文件不存在则返回默认配置。
        /// </summary>
        /// <returns>加载了文件配置或默认值的 PluginConfig 实例。</returns>
        public static PluginConfig Load()
        {
            var config = new PluginConfig();
            var path = Path.Combine(ConfigDirectory, "config.txt");
            if(!File.Exists(path))
                return config;

            // 逐行解析 key=value 格式，跳过空行和注释行
            foreach(var rawLine in File.ReadAllLines(path))
            {
                var line = rawLine == null ? string.Empty : rawLine.Trim();
                if(line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                    continue;

                var splitAt = line.IndexOf('=');
                if(splitAt <= 0)
                    continue;

                var key = line.Substring(0, splitAt).Trim();
                var value = line.Substring(splitAt + 1).Trim();
                config.Apply(key, value);
            }

            return config;
        }

        /// <summary>
        /// 确保配置文件存在。若不存在则在配置目录中创建一个包含默认值的 config.txt。
        /// </summary>
        public void EnsureConfigFile()
        {
            Directory.CreateDirectory(ConfigDirectory);
            var path = Path.Combine(ConfigDirectory, "config.txt");
            if(File.Exists(path))
                return;

            File.WriteAllLines(path, new[]
            {
                "# HDT AI Assistant plugin config",
                "backend_websocket_url=" + BackendWebSocketUrl,
                "poll_interval_ms=" + PollIntervalMilliseconds.ToString(CultureInfo.InvariantCulture),
                "max_recent_events=" + MaxRecentEvents.ToString(CultureInfo.InvariantCulture),
                "max_automatic_recommendations_per_turn=" + MaxAutomaticRecommendationsPerTurn.ToString(CultureInfo.InvariantCulture)
            });
        }

        /// <summary>将单个键值对应用到当前配置对象。</summary>
        private void Apply(string key, string value)
        {
            if(string.Equals(key, "backend_websocket_url", StringComparison.OrdinalIgnoreCase))
            {
                if(!string.IsNullOrWhiteSpace(value))
                    BackendWebSocketUrl = value;
                return;
            }

            if(string.Equals(key, "poll_interval_ms", StringComparison.OrdinalIgnoreCase))
            {
                PollIntervalMilliseconds = Clamp(ParseInt(value, PollIntervalMilliseconds), 100, 10000);
                return;
            }

            if(string.Equals(key, "max_recent_events", StringComparison.OrdinalIgnoreCase))
            {
                MaxRecentEvents = Clamp(ParseInt(value, MaxRecentEvents), 1, 100);
                return;
            }

            if(string.Equals(key, "max_automatic_recommendations_per_turn", StringComparison.OrdinalIgnoreCase))
                MaxAutomaticRecommendationsPerTurn = Clamp(ParseInt(value, MaxAutomaticRecommendationsPerTurn), 1, 10);
        }

        /// <summary>安全地将字符串解析为整数，失败时返回回退值。</summary>
        private static int ParseInt(string value, int fallback)
        {
            int parsed;
            return int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed)
                ? parsed
                : fallback;
        }

        /// <summary>将值限制在 [min, max] 范围内。</summary>
        private static int Clamp(int value, int min, int max)
        {
            if(value < min)
                return min;
            if(value > max)
                return max;
            return value;
        }
    }
}
