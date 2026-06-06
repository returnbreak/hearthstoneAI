using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using HdtAiAssistant.Core.Events;
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
            Run("event envelopes include attack target metadata", EventEnvelopeIncludesAttackTargetMetadata);
            Run("event envelopes omit missing target fields", EventEnvelopeOmitsMissingTargetFields);
            Run("power log target parser reads play target entity id", PowerLogTargetParserReadsPlayTargetEntityId);
            Run("power log hand transform parser reads latest coin card id", PowerLogHandTransformParserReadsLatestCoinCardId);
            Run("game metadata envelope includes deck cards", GameMetadataEnvelopeIncludesDeckCards);
            RunAsync("publisher emits game metadata envelope", PublisherEmitsGameMetadataEnvelope);
            Run("attack factory creates minion attack from real attack data", AttackFactoryCreatesMinionAttackFromRealAttackData);
            Run("attack factory creates hero attack from real attack data", AttackFactoryCreatesHeroAttackFromRealAttackData);
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
            second.MyMana.Current = 1;
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
            AssertContains(transport.Payloads[0], "\"my_mana\":{\"current\":6,\"max\":6}");
            AssertContains(transport.Payloads[0], "\"enemy_mana\":{\"current\":5,\"max\":5}");
            AssertNotContains(transport.Payloads[0], "\"mana\":");
            AssertNotContains(transport.Payloads[0], "\"zone\":");
            AssertNotContains(transport.Payloads[0], "\"source\":");
        }

        private static async Task PublisherEmitsCombatFields()
        {
            var transport = new RecordingTransport();
            var publisher = new SnapshotPublisher(transport);

            await publisher.PublishGameStateAsync(SampleState(), RecommendationTrigger.MyTurnStarted, CancellationToken.None);

            AssertContains(transport.Payloads[0], "\"attacks_this_turn\":0");
            AssertContains(transport.Payloads[0], "\"max_attacks_per_turn\":2");
            AssertNotContains(transport.Payloads[0], "\"can_attack\"");
            AssertNotContains(transport.Payloads[0], "\"attacks_remaining\"");
            AssertContains(transport.Payloads[0], "\"windfury\":true");
            AssertContains(transport.Payloads[0], "\"lifesteal\":true");
            AssertContains(transport.Payloads[0], "\"text\":\"Deal 6 damage.\"");
            AssertContains(transport.Payloads[0], "\"text\":\"Adjacent minions have +2 Attack.\"");
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

        private static void EventEnvelopeIncludesAttackTargetMetadata()
        {
            var payload = JsonPayloadWriter.WriteGameEventEnvelope(new GameEvent
            {
                GameId = "game-1",
                Timestamp = "2026-06-03T11:30:00+08:00",
                Turn = 7,
                Player = "me",
                Type = "minion_attack",
                EntityId = 100,
                CardId = "EX1_565",
                Name = "Flametongue Totem",
                Target = new EventTarget
                {
                    EntityId = 101,
                    CardId = "CS2_179",
                    Name = "Sen'jin Shieldmasta",
                    Type = "minion"
                },
                DamageAmount = 3
            });

            AssertContains(payload, "\"type\":\"minion_attack\"");
            AssertContains(payload, "\"target\":{\"entity_id\":101,\"card_id\":\"CS2_179\",\"name\":\"Sen'jin Shieldmasta\",\"type\":\"minion\"}");
            AssertNotContains(payload, "\"target_player\"");
            AssertNotContains(payload, "\"target_is_hero\"");
            AssertContains(payload, "\"damage_amount\":3");
        }

        private static void EventEnvelopeOmitsMissingTargetFields()
        {
            var payload = JsonPayloadWriter.WriteGameEventEnvelope(new GameEvent
            {
                GameId = "game-1",
                Timestamp = "2026-06-05T18:00:00+08:00",
                Turn = 3,
                Player = "me",
                Type = "turn_started"
            });

            AssertNotContains(payload, "\"target\"");
            AssertNotContains(payload, "\"entity_id\"");
            AssertNotContains(payload, "\"card_id\"");
            AssertNotContains(payload, "\"dbf_id\"");
            AssertNotContains(payload, "\"name\"");
            AssertNotContains(payload, "\"damage_amount\"");
            AssertNotContains(payload, "\"reason\"");
            AssertNotContains(payload, "\"result\"");
        }

        private static void PowerLogTargetParserReadsPlayTargetEntityId()
        {
            var lines = new[]
            {
                "D 15:45:04.900 BLOCK_START BlockType=PLAY Entity=[entityName=初始之火 id=103 zone=HAND zonePos=4 cardId=CORE_SW_108 player=1] EffectCardId= Target=[entityName=火色魔印奔行者 id=107 zone=PLAY zonePos=1 cardId=CORE_BT_480 player=2] SubOption=-1",
                "D 15:45:04.901 TAG_CHANGE Entity=[entityName=火色魔印奔行者 id=107 zone=PLAY zonePos=1 cardId=CORE_BT_480 player=2] tag=DAMAGE value=2",
                "D 15:45:05.000 BLOCK_START BlockType=PLAY Entity=[entityName=其他法术 id=108 zone=HAND zonePos=3 cardId=OTHER_SPELL player=1] EffectCardId= Target=[entityName=初始之火 id=109 zone=PLAY zonePos=2 cardId=CORE_SW_108 player=2] SubOption=-1"
            };

            AssertEqual(107, PowerLogTargetParser.FindLatestPlayTargetEntityId(lines, "CORE_SW_108"));
            AssertEqual(null, PowerLogTargetParser.FindLatestPlayTargetEntityId(lines, "CS2_029"));
        }

        private static void PowerLogHandTransformParserReadsLatestCoinCardId()
        {
            var lines = new[]
            {
                "D 17:43:20.000 FULL_ENTITY - Updating Entity=[entityName=半兽人迦罗娜 id=57 zone=HAND zonePos=5 cardId=TIME_875 player=1] CardID=TIME_875",
                "D 17:43:27.900 SHOW_ENTITY - Updating Entity=[entityName=半兽人迦罗娜 id=57 zone=HAND zonePos=4 cardId=TIME_875 player=1] CardID=GAME_005",
                "D 17:43:28.000 TAG_CHANGE Entity=[entityName=古神的眼线 id=63 zone=PLAY zonePos=3 cardId=CATA_200 player=1] tag=ZONE value=PLAY"
            };

            var cards = PowerLogHandTransformParser.ReadLatestHandCardIds(lines);

            AssertEqual("GAME_005", cards[57]);
        }

        private static void GameMetadataEnvelopeIncludesDeckCards()
        {
            var metadata = new GameMetadata
            {
                GameId = "game-1",
                CapturedAt = "2026-06-05T12:00:00+08:00",
                DeckAvailable = true,
                DeckId = "deck-1",
                DeckName = "Test Deck",
                PlayerClass = "MAGE",
                Format = "standard"
            };
            metadata.Cards.Add(new DeckCardMetadata
            {
                CardId = "CS2_029",
                DbFId = 315,
                Name = "Fireball",
                Cost = 4,
                Type = "SPELL",
                Count = 2
            });

            var payload = JsonPayloadWriter.WriteGameMetadataEnvelope(metadata);

            AssertContains(payload, "\"type\":\"game_metadata\"");
            AssertContains(payload, "\"deck_available\":true");
            AssertContains(payload, "\"deck_id\":\"deck-1\"");
            AssertContains(payload, "\"name\":\"Test Deck\"");
            AssertContains(payload, "\"card_id\":\"CS2_029\"");
            AssertContains(payload, "\"count\":2");
        }

        private static async Task PublisherEmitsGameMetadataEnvelope()
        {
            var transport = new RecordingTransport();
            var publisher = new SnapshotPublisher(transport);
            var metadata = new GameMetadata
            {
                GameId = "game-1",
                CapturedAt = "2026-06-05T12:00:00+08:00",
                DeckAvailable = false
            };

            await publisher.PublishGameMetadataAsync(metadata, CancellationToken.None);

            AssertEqual(1, transport.Payloads.Count);
            AssertContains(transport.Payloads[0], "\"type\":\"game_metadata\"");
            AssertContains(transport.Payloads[0], "\"deck_available\":false");
        }

        private static void AttackFactoryCreatesMinionAttackFromRealAttackData()
        {
            var evt = AttackEventFactory.Create(
                "game-1",
                3,
                "me",
                new AttackParticipant
                {
                    EntityId = 100,
                    CardId = "EX1_565",
                    DbFId = 559,
                    Name = "Flametongue Totem",
                    Type = "MINION",
                    Attack = 3
                },
                new AttackParticipant
                {
                    EntityId = 101,
                    CardId = "CS2_179",
                    DbFId = 90,
                    Name = "Sen'jin Shieldmasta",
                    Type = "MINION"
                });

            AssertEqual("game-1", evt.GameId);
            AssertEqual(3, evt.Turn);
            AssertEqual("me", evt.Player);
            AssertEqual("minion_attack", evt.Type);
            AssertEqual(100, evt.EntityId);
            AssertEqual("EX1_565", evt.CardId);
            AssertEqual(559, evt.DbFId);
            AssertEqual("Flametongue Totem", evt.Name);
            AssertEqual(101, evt.Target.EntityId);
            AssertEqual("CS2_179", evt.Target.CardId);
            AssertEqual("Sen'jin Shieldmasta", evt.Target.Name);
            AssertEqual("minion", evt.Target.Type);
            AssertEqual(3, evt.DamageAmount);
        }

        private static void AttackFactoryCreatesHeroAttackFromRealAttackData()
        {
            var evt = AttackEventFactory.Create(
                "game-1",
                4,
                "opponent",
                new AttackParticipant
                {
                    CardId = "HERO_05",
                    Name = "Rexxar",
                    Type = "HERO",
                    Attack = 2
                },
                new AttackParticipant
                {
                    CardId = "HERO_08",
                    Name = "Jaina Proudmoore",
                    Type = "HERO"
                });

            AssertEqual("opponent", evt.Player);
            AssertEqual("hero_attack", evt.Type);
            AssertEqual("HERO_05", evt.CardId);
            AssertEqual("Rexxar", evt.Name);
            AssertEqual("hero", evt.Target.Type);
            AssertEqual(2, evt.DamageAmount);
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
                MyHero = new HeroSnapshot { Class = "MAGE", Hp = 24, Armor = 0, Attack = 3, AttacksThisTurn = 0, MaxAttacksPerTurn = 1 },
                EnemyHero = new HeroSnapshot { Class = "HUNTER", Hp = 17, Armor = 0, Attack = 0, AttacksThisTurn = 0, MaxAttacksPerTurn = 1 },
                MyMana = new ManaSnapshot { Current = 6, Max = 6 },
                EnemyMana = new ManaSnapshot { Current = 5, Max = 5 },
                Hand =
                {
                    new CardSnapshot
                    {
                        EntityId = 42,
                        CardId = "CS2_029",
                        DbFId = 315,
                        Name = "Fireball",
                        Cost = 4,
                        Type = "SPELL",
                        Text = "Deal 6 damage."
                    }
                },
                MyBoard =
                {
                    new MinionSnapshot
                    {
                        EntityId = 100,
                        CardId = "EX1_565",
                        DbFId = 559,
                        Name = "Flametongue Totem",
                        Text = "Adjacent minions have +2 Attack.",
                        Attack = 0,
                        Health = 3,
                        Damage = 0,
                        ZonePosition = 1,
                        AttacksThisTurn = 0,
                        MaxAttacksPerTurn = 2,
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

        private static void AssertNotContains(string text, string expected)
        {
            if(text != null && text.Contains(expected))
                throw new InvalidOperationException("Expected '" + text + "' not to contain '" + expected + "'.");
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
