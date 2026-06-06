using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace HdtAiAssistant.Core.Events
{
    public sealed class PowerLogPlayReference
    {
        public int? SourceEntityId { get; set; }
        public int? TargetEntityId { get; set; }
    }

    public static class PowerLogTargetParser
    {
        private static readonly Regex SourceIdPattern = new Regex(
            @"\bEntity=\[[^\]]*\bid=(\d+)\b",
            RegexOptions.Compiled);

        private static readonly Regex SourceBlockPattern = new Regex(
            @"\bEntity=\[([^\]]*)\]",
            RegexOptions.Compiled);

        private static readonly Regex TargetIdPattern = new Regex(
            @"\bTarget=\[[^\]]*\bid=(\d+)\b",
            RegexOptions.Compiled);

        public static int? FindLatestPlayTargetEntityId(
            IEnumerable<string> lines,
            string cardId)
        {
            var play = FindLatestPlay(lines, cardId);
            return play == null ? null : play.TargetEntityId;
        }

        public static PowerLogPlayReference FindLatestPlay(
            IEnumerable<string> lines,
            string cardId)
        {
            if(lines == null || string.IsNullOrEmpty(cardId))
                return null;

            var buffered = lines as IList<string> ?? new List<string>(lines);
            for(var index = buffered.Count - 1; index >= 0; index--)
            {
                var line = buffered[index];
                if(string.IsNullOrEmpty(line)
                    || line.IndexOf("BLOCK_START", System.StringComparison.Ordinal) < 0
                    || line.IndexOf("BlockType=PLAY", System.StringComparison.Ordinal) < 0
                    || !SourceCardMatches(line, cardId))
                    continue;

                return new PowerLogPlayReference
                {
                    SourceEntityId = ReadId(SourceIdPattern, line),
                    TargetEntityId = ReadId(TargetIdPattern, line)
                };
            }
            return null;
        }

        private static int? ReadId(Regex pattern, string line)
        {
            var match = pattern.Match(line);
            int value;
            return match.Success && int.TryParse(match.Groups[1].Value, out value) && value > 0
                ? (int?)value
                : null;
        }

        private static bool SourceCardMatches(string line, string cardId)
        {
            var source = SourceBlockPattern.Match(line);
            if(!source.Success)
                return false;

            return Regex.IsMatch(
                source.Groups[1].Value,
                @"(?:^|\s)cardId=" + Regex.Escape(cardId) + @"(?:\s|$)");
        }
    }
}
