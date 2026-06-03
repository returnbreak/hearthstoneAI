using System;
using System.IO;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// 插件日志——将插件运行日志写入配置目录下的 plugin.log 文件。
    /// 日志写入失败时静默忽略，确保不会因日志异常影响 HDT 正常运行。
    /// </summary>
    internal static class PluginLog
    {
        /// <summary>记录一条 INFO 级别日志。</summary>
        public static void Info(string message)
        {
            Write("INFO", message);
        }

        /// <summary>记录一条 ERROR 级别日志（含异常堆栈）。</summary>
        public static void Error(Exception exception)
        {
            if(exception == null)
                return;
            Write("ERROR", exception.ToString());
        }

        /// <summary>
        /// 将日志写入文件。格式：ISO 8601 时间戳 [级别] 消息。
        /// 写入失败时静默忽略——日志绝不能中断 HDT 插件执行。
        /// </summary>
        private static void Write(string level, string message)
        {
            try
            {
                Directory.CreateDirectory(PluginConfig.ConfigDirectory);
                var path = Path.Combine(PluginConfig.ConfigDirectory, "plugin.log");
                File.AppendAllText(path, DateTimeOffset.Now.ToString("o") + " [" + level + "] " + message + Environment.NewLine);
            }
            catch
            {
                // 日志记录绝不能导致 HDT 插件执行中断。
            }
        }
    }
}
