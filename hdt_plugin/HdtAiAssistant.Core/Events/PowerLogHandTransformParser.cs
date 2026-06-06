using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace HdtAiAssistant.Core.Events
{
    public static class PowerLogHandTransformParser
    {
        private static readonly Regex EntityBlockPattern = new Regex(
            @"\bEntity=\[([^\]]*)\]",
            RegexOptions.Compiled);

        private static readonly Regex EntityIdPattern = new Regex(
            @"\bid=(\d+)\b",
            RegexOptions.Compiled);

        private static readonly Regex EntityCardIdPattern = new Regex(
            @"\bcardId=([A-Za-z0-9_]+)",
            RegexOptions.Compiled);

        private static readonly Regex UpdatedCardIdPattern = new Regex(
            @"\bCardID=([A-Za-z0-9_]+)",
            RegexOptions.Compiled);

        public static IDictionary<int, string> ReadLatestHandCardIds(IEnumerable<string> lines)
        {
            var result = new Dictionary<int, string>();
            if(lines == null)
                return result;

            foreach(var line in lines)
            {
                if(string.IsNullOrEmpty(line))
                    continue;

                var entityBlock = EntityBlockPattern.Match(line);
                if(!entityBlock.Success)
                    continue;

                var entityText = entityBlock.Groups[1].Value;
                if(entityText.IndexOf("zone=HAND", System.StringComparison.Ordinal) < 0)
                    continue;

                var entityId = ReadEntityId(entityText);
                if(!entityId.HasValue)
                    continue;

                var cardId = ReadUpdatedCardId(line) ?? ReadEntityCardId(entityText);
                if(string.IsNullOrEmpty(cardId))
                    continue;

                result[entityId.Value] = cardId;
            }

            return result;
        }

        public static bool IsCoinCardId(string cardId)
        {
            if(string.IsNullOrEmpty(cardId))
                return false;

            var normalized = cardId.ToUpperInvariant();
            return normalized == "GAME_005"
                || normalized == "CORE_GAME_005"
                || normalized.IndexOf("COIN") >= 0;
        }

        private static int? ReadEntityId(string entityText)
        {
            var match = EntityIdPattern.Match(entityText);
            int value;
            return match.Success && int.TryParse(match.Groups[1].Value, out value) && value > 0
                ? (int?)value
                : null;
        }

        private static string ReadUpdatedCardId(string line)
        {
            var match = UpdatedCardIdPattern.Match(line);
            return match.Success ? match.Groups[1].Value : null;
        }

        private static string ReadEntityCardId(string entityText)
        {
            var match = EntityCardIdPattern.Match(entityText);
            return match.Success ? match.Groups[1].Value : null;
        }
    }
}
