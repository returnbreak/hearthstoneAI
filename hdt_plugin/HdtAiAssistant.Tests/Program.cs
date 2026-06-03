using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using HdtAiAssistant.Core.Models;
using HdtAiAssistant.Core.Publishing;
using HdtAiAssistant.Core.Throttling;

namespace HdtAiAssistant.Tests
{
    /// <summary>
    /// HDT AI Assistant 单元测试入口——纯控制台程序，不依赖 HDT 运行时。
    /// 测试核心逻辑：状态哈希计算、节流器行为、JSON 序列化和发布器错误处理。
    /// </summary>
    internal static class Program
    {
        private static int _failures;

        /// <summary>运行所有测试用例，返回 0 表示全部通过，1 表示存在失败。</summary>
        private static int Main()
        {
            Run("state hash ignores volatile fields", StateHashIgnoresVolatileFields);
            Run("state hash changes when relevant state changes", StateHashChangesForRelevantState);
            Run("state hash changes when combat keyword changes", StateHashChangesForCombatKeyword);
            Run("throttler publishes only changed states", ThrottlerPublishesOnlyChangedStates);
            Run("throttler limits recommendation triggers per turn", ThrottlerLimitsRecommendationTriggersPerTurn);
            RunAsync("publisher emits game_state envelopes", PublisherEmitsGameStateEnvelope);
            RunAsync("publisher emits combat fields", PublisherEmitsCombatFields);
            Run("event envelopes include game end reason", EventEnvelopeIncludesGameEndReason);
            RunAsync("publisher records transport failures without throwing", PublisherRecordsTransportFailures);

            if(_failures > 0)
            {
                Console.Error.WriteLine(_failures + " test(s) failed.");
                return 1;
            }

            Console.WriteLine("All tests passed.");
            return 0;
        }

        /// <summary>
        /// 验证：状态哈希忽略易变字段（GameId、Timestamp、RecentEvents）。
        /// 两个仅在易变字段上不同的 GameState 应产生相同的哈希。
        /// </summary>
        private static void StateHashIgnoresVolatileFields()
        {
            var first = SampleState();
            var second = SampleState();
            first.GameId = "game-a";
            second.GameId = "game-b";
            first.Timestamp = "2026-06-02T10:00:00+08:00";
            second.Timestamp = "2026-06-02T10:01:00+08:00";
            second.RecentEvents.Add(new GameEvent { Type = "card_played", CardId = "CS2_029" });

            AssertEqual(StateHashCalculator.Calculate(first), StateHashCalculator.Calculate(second));
        }

        /// <summary>
        /// 验证：状态哈希在关键字段变化时产生不同结果。
        /// 改变法力水晶或手牌应导致哈希变化。
        /// </summary>
        private static void StateHashChangesForRelevantState()
        {
            var first = SampleState();
            var second = SampleState();
            second.Mana.Current = 1;
            var third = SampleState();
            third.Hand.Add(new CardSnapshot { EntityId = 9, CardId = "EX1_277", DbFId = 5, Name = "Arcane Missiles", Cost = 1, Type = "SPELL" });

            AssertNotEqual(StateHashCalculator.Calculate(first), StateHashCalculator.Calculate(second));
            AssertNotEqual(StateHashCalculator.Calculate(first), StateHashCalculator.Calculate(third));
        }

        private static void StateHashChangesForCombatKeyword()
        {
            var first = SampleState();
            var second = SampleState();
            second.MyBoard[0].Windfury = false;

            AssertNotEqual(StateHashCalculator.Calculate(first), StateHashCalculator.Calculate(second));
        }

        /// <summary>
        /// 验证：节流器仅上报状态发生变化的快照。
        /// 第一次上报应允许；相同状态再次上报应被拒绝；修改后的状态应被允许。
        /// </summary>
        private static void ThrottlerPublishesOnlyChangedStates()
        {
            var throttler = new SnapshotThrottler();
            var state = SampleState();

            AssertTrue(throttler.ShouldPublishState(state));
            AssertFalse(throttler.ShouldPublishState(SampleState()));

            var changed = SampleState();
            changed.EnemyHero.Hp = 12;
            AssertTrue(throttler.ShouldPublishState(changed));
        }

        /// <summary>
        /// 验证：节流器每回合限制自动推荐触发次数。
        /// - MyTurnStarted 允许首次触发
        /// - 相同状态不允许再次触发
        /// - SignificantStateChange 允许在状态变化时触发
        /// - 达到每回合上限后拒绝新触发
        /// - 进入新回合后重置计数器
        /// </summary>
        private static void ThrottlerLimitsRecommendationTriggersPerTurn()
        {
            var throttler = new SnapshotThrottler(maxAutomaticRecommendationsPerTurn: 2);
            var state = SampleState();

            AssertTrue(throttler.ShouldTriggerRecommendation(state, RecommendationTrigger.MyTurnStarted));
            AssertFalse(throttler.ShouldTriggerRecommendation(SampleState(), RecommendationTrigger.MyTurnStarted));

            var changed = SampleState();
            changed.Hand.Add(new CardSnapshot { EntityId = 10, CardId = "CS2_023", Name = "Arcane Intellect", Cost = 3, Type = "SPELL" });
            AssertTrue(throttler.ShouldTriggerRecommendation(changed, RecommendationTrigger.SignificantStateChange));

            var changedAgain = SampleState();
            changedAgain.EnemyHero.Hp = 4;
            AssertFalse(throttler.ShouldTriggerRecommendation(changedAgain, RecommendationTrigger.SignificantStateChange));

            changedAgain.Turn = 7;
            AssertTrue(throttler.ShouldTriggerRecommendation(changedAgain, RecommendationTrigger.MyTurnStarted));
        }

        /// <summary>
        /// 验证：发布器正确生成 game_state JSON 信封，
        /// 包含 type、trigger 字段以及完整的游戏状态数据。
        /// </summary>
        private static async Task PublisherEmitsGameStateEnvelope()
        {
            var transport = new RecordingTransport();
            var publisher = new SnapshotPublisher(transport);

            await publisher.PublishGameStateAsync(SampleState(), RecommendationTrigger.MyTurnStarted, CancellationToken.None);

            AssertEqual(1, transport.Payloads.Count);
            AssertContains(transport.Payloads[0], "\"type\":\"game_state\"");
            AssertContains(transport.Payloads[0], "\"trigger\":\"my_turn_started\"");
            AssertContains(transport.Payloads[0], "\"card_id\":\"CS2_029\"");
        }

        private static async Task PublisherEmitsCombatFields()
        {
            var transport = new RecordingTransport();
            var publisher = new SnapshotPublisher(transport);

            await publisher.PublishGameStateAsync(SampleState(), RecommendationTrigger.MyTurnStarted, CancellationToken.None);

            AssertContains(transport.Payloads[0], "\"can_attack\":true");
            AssertContains(transport.Payloads[0], "\"attacks_remaining\":1");
            AssertContains(transport.Payloads[0], "\"windfury\":true");
            AssertContains(transport.Payloads[0], "\"lifesteal\":true");
        }

        private static void EventEnvelopeIncludesGameEndReason()
        {
            var payload = JsonPayloadWriter.WriteGameEventEnvelope(new GameEvent
            {
                GameId = "game-1",
                Timestamp = "2026-06-03T11:30:00+08:00",
                Turn = 7,
                Player = "me",
                Type = "game_conceded",
                Reason = "conceded",
                Result = "loss"
            });

            AssertContains(payload, "\"type\":\"game_conceded\"");
            AssertContains(payload, "\"reason\":\"conceded\"");
            AssertContains(payload, "\"result\":\"loss\"");
        }

        /// <summary>
        /// 验证：发布器在传输失败时不抛出异常，
        /// 而是更新 IsBackendAvailable 为 false 并记录错误消息。
        /// </summary>
        private static async Task PublisherRecordsTransportFailures()
        {
            var transport = new RecordingTransport { ThrowOnSend = true };
            var publisher = new SnapshotPublisher(transport);

            await publisher.PublishGameStateAsync(SampleState(), RecommendationTrigger.Manual, CancellationToken.None);

            AssertFalse(publisher.IsBackendAvailable);
            AssertContains(publisher.LastError, "send failed");
        }

        /// <summary>构建一个用于测试的标准游戏状态样本。</summary>
        private static GameState SampleState()
        {
            return new GameState
            {
                GameId = "game-1",
                Timestamp = "2026-06-02T10:00:00+08:00",
                Mode = "standard",
                Turn = 6,
                ActivePlayer = "me",
                MyHero = new HeroSnapshot { Class = "MAGE", Hp = 24, Armor = 0, Attack = 3, CanAttack = true, AttacksThisTurn = 0, AttacksRemaining = 1 },
                EnemyHero = new HeroSnapshot { Class = "HUNTER", Hp = 17, Armor = 0, Attack = 0, CanAttack = false, AttacksThisTurn = 0, AttacksRemaining = 0 },
                Mana = new ManaSnapshot { Current = 6, Max = 6 },
                Hand =
                {
                    new CardSnapshot { EntityId = 42, CardId = "CS2_029", DbFId = 315, Name = "Fireball", Cost = 4, Type = "SPELL" }
                },
                MyBoard =
                {
                    new MinionSnapshot
                    {
                        EntityId = 100,
                        CardId = "EX1_565",
                        DbFId = 559,
                        Name = "Flametongue Totem",
                        Attack = 0,
                        Health = 3,
                        Damage = 0,
                        ZonePosition = 1,
                        CanAttack = true,
                        AttacksThisTurn = 0,
                        AttacksRemaining = 1,
                        Windfury = true,
                        Lifesteal = true
                    }
                },
                EnemyBoard =
                {
                    new MinionSnapshot
                    {
                        EntityId = 101,
                        CardId = "CS2_179",
                        DbFId = 90,
                        Name = "Sen'jin Shieldmasta",
                        Attack = 3,
                        Health = 5,
                        Damage = 0,
                        ZonePosition = 1,
                        Taunt = true
                    }
                },
                MyDeckCount = 18,
                EnemyHandCount = 4,
                EnemyDeckCount = 20
            };
        }

        /// <summary>运行一个同步测试用例。</summary>
        private static void Run(string name, Action test)
        {
            try
            {
                test();
                Console.WriteLine("PASS " + name);
            }
            catch(Exception ex)
            {
                _failures++;
                Console.Error.WriteLine("FAIL " + name + ": " + ex.Message);
            }
        }

        /// <summary>运行一个异步测试用例。</summary>
        private static void RunAsync(string name, Func<Task> test)
        {
            try
            {
                test().Wait();
                Console.WriteLine("PASS " + name);
            }
            catch(Exception ex)
            {
                _failures++;
                Console.Error.WriteLine("FAIL " + name + ": " + Unwrap(ex).Message);
            }
        }

        private static void AssertTrue(bool value)
        {
            if(!value)
                throw new InvalidOperationException("Expected true.");
        }

        private static void AssertFalse(bool value)
        {
            if(value)
                throw new InvalidOperationException("Expected false.");
        }

        private static void AssertEqual<T>(T expected, T actual)
        {
            if(!EqualityComparer<T>.Default.Equals(expected, actual))
                throw new InvalidOperationException("Expected '" + expected + "', got '" + actual + "'.");
        }

        private static void AssertNotEqual<T>(T first, T second)
        {
            if(EqualityComparer<T>.Default.Equals(first, second))
                throw new InvalidOperationException("Expected values to differ, both were '" + first + "'.");
        }

        private static void AssertContains(string text, string expected)
        {
            if(text == null || !text.Contains(expected))
                throw new InvalidOperationException("Expected '" + text + "' to contain '" + expected + "'.");
        }

        /// <summary>解包 AggregateException，返回真正的内部异常以便输出清晰的错误消息。</summary>
        private static Exception Unwrap(Exception ex)
        {
            var aggregate = ex as AggregateException;
            return aggregate != null && aggregate.InnerExceptions.Count == 1
                ? aggregate.InnerExceptions[0]
                : ex;
        }
    }

    /// <summary>
    /// 记录传输实现——用于测试的 IBackendTransport 桩，
    /// 记录所有发送的负载并支持模拟发送失败。
    /// </summary>
    internal sealed class RecordingTransport : IBackendTransport
    {
        private readonly List<string> _payloads = new List<string>();

        /// <summary>所有已发送负载的列表。</summary>
        public List<string> Payloads
        {
            get { return _payloads; }
        }

        /// <summary>设为 true 时模拟发送失败。</summary>
        public bool ThrowOnSend { get; set; }

        /// <summary>
        /// 若 ThrowOnSend 为 true 则抛出异常；
        /// 否则将负载添加到 Payloads 列表中。
        /// </summary>
        public Task SendAsync(string payload, CancellationToken cancellationToken)
        {
            if(ThrowOnSend)
                throw new InvalidOperationException("send failed");
            Payloads.Add(payload);
            return Task.CompletedTask;
        }
    }
}
