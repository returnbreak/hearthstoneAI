using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Controls;
using Hearthstone_Deck_Tracker.API;
using Hearthstone_Deck_Tracker.Enums;
using Hearthstone_Deck_Tracker.Hearthstone;
using Hearthstone_Deck_Tracker.Plugins;
using HdtAiAssistant.Core.Models;
using HdtAiAssistant.Core.Publishing;
using HdtAiAssistant.Core.Throttling;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// HDT AI Assistant 插件入口——实现 IPlugin 接口，作为 HDT 与 AI 后端之间的桥梁。
    ///
    /// 核心职责：
    /// 1. 注册 HDT 游戏事件回调（回合开始、出牌、抽牌等）。
    /// 2. 将游戏事件和状态快照通过 WebSocket 发送到本地 AI 后端。
    /// 3. 通过节流器控制上报频率，避免冗余请求。
    /// </summary>
    public sealed class PluginEntry : IPlugin
    {
        private PluginConfig _config;
        private GameEventCollector _eventCollector;
        private DeckMetadataReader _deckMetadataReader;
        private PowerLogEventResolver _powerLogEventResolver;
        private GameStateBuilder _stateBuilder;
        private SnapshotPublisher _publisher;
        private SnapshotThrottler _throttler;
        private CancellationTokenSource _cancellation;
        private string _gameId;
        private DateTimeOffset _lastPoll = DateTimeOffset.MinValue;
        private bool _loaded;
        private bool _inGame;

        // ---- IPlugin 接口属性 ----

        /// <summary>插件名称，显示在 HDT 插件列表中。</summary>
        public string Name { get { return "HDT AI Assistant"; } }
        /// <summary>插件描述。</summary>
        public string Description { get { return "Publishes public Hearthstone game events and state snapshots to the local AI assistant backend."; } }
        /// <summary>插件按钮文本。</summary>
        public string ButtonText { get { return "Open AI Assistant"; } }
        /// <summary>插件作者。</summary>
        public string Author { get { return "hearthstoneAI"; } }
        /// <summary>插件版本。</summary>
        public Version Version { get { return new Version(0, 1, 0); } }
        /// <summary>插件右键菜单项。</summary>
        public MenuItem MenuItem { get { return CreateMenuItem(); } }

        // ---- 生命周期 ----

        /// <summary>
        /// 插件加载时调用。初始化所有组件、注册 HDT 事件回调、建立后端连接。
        /// 重复调用安全（通过 _loaded 标志防止重复初始化）。
        /// </summary>
        public void OnLoad()
        {
            if(_loaded)
                return;

            _config = PluginConfig.Load();
            _config.EnsureConfigFile();
            _eventCollector = new GameEventCollector();
            _deckMetadataReader = new DeckMetadataReader();
            _powerLogEventResolver = new PowerLogEventResolver();
            _stateBuilder = new GameStateBuilder(new EntityStateReader(), _config.MaxRecentEvents);
            _publisher = new SnapshotPublisher(new WebSocketBackendTransport(_config.BackendWebSocketUrl));
            _throttler = new SnapshotThrottler(_config.MaxAutomaticRecommendationsPerTurn);
            _cancellation = new CancellationTokenSource();
            _gameId = CreateGameId();

            // 注册 HDT 游戏事件回调
            GameEvents.OnGameStart.Add(OnGameStart);
            GameEvents.OnGameEnd.Add(OnGameEnd);
            GameEvents.OnTurnStart.Add(OnTurnStart);
            GameEvents.OnPlayerDraw.Add(card => OnCardEvent("me", "card_drawn", card));
            GameEvents.OnPlayerGet.Add(card => OnCardEvent("me", "card_created_in_hand", card));
            GameEvents.OnPlayerPlay.Add(card => OnCardEvent("me", "card_played", card));
            GameEvents.OnPlayerHandDiscard.Add(card => OnCardEvent("me", "card_discarded_from_hand", card));
            GameEvents.OnOpponentPlay.Add(card => OnCardEvent("opponent", "card_played", card));
            GameEvents.OnOpponentHandDiscard.Add(card => OnCardEvent("opponent", "card_discarded_from_hand", card));
            GameEvents.OnPlayerMinionAttack.Add(info => OnAttackEvent("me", info));
            GameEvents.OnOpponentMinionAttack.Add(info => OnAttackEvent("opponent", info));

            _loaded = true;
            PluginLog.Info("Loaded. Backend=" + _config.BackendWebSocketUrl);
        }

        /// <summary>
        /// 插件卸载时调用。取消所有待处理的后端请求并清理资源。
        /// </summary>
        public void OnUnload()
        {
            if(!_loaded)
                return;

            _loaded = false;
            if(_cancellation != null)
                _cancellation.Cancel();
            PluginLog.Info("Unloaded.");
        }

        /// <summary>插件按钮点击时打开本地 AI 助手界面。</summary>
        public void OnButtonPress()
        {
            OpenLocalUi();
        }

        /// <summary>
        /// HDT 每帧调用。按配置的轮询间隔定期检查状态变化并上报。
        /// </summary>
        public void OnUpdate()
        {
            if(!_loaded || !_inGame || _config == null || _publisher == null)
                return;

            var now = DateTimeOffset.Now;
            if((now - _lastPoll).TotalMilliseconds < _config.PollIntervalMilliseconds)
                return;
            _lastPoll = now;

            PublishSnapshotIfChanged(RecommendationTrigger.None);
        }

        // ---- 事件处理 ----

        /// <summary>对局开始时生成新 GameId 并上报事件。</summary>
        private void OnGameStart()
        {
            StartGameIfNeeded("hdt_game_start");
        }

        /// <summary>对局结束时上报事件。</summary>
        private void OnGameEnd()
        {
            var endState = _stateBuilder.Build(_gameId);
            var result = ReadGameResult();
            var reason = InferGameEndReason(endState, result);
            _inGame = false;

            if(reason == "conceded")
            {
                var conceded = _eventCollector.CreateLifecycleEvent(_gameId, ReadTurn(), "game_conceded", reason, result);
                conceded.Player = "me";
                RecordAndPublishEventOnly(conceded);
            }
            else if(reason == "opponent_conceded")
            {
                var conceded = _eventCollector.CreateLifecycleEvent(_gameId, ReadTurn(), "opponent_conceded", reason, result);
                conceded.Player = "opponent";
                RecordAndPublishEventOnly(conceded);
            }

            var evt = _eventCollector.CreateLifecycleEvent(_gameId, ReadTurn(), "game_ended", reason, result);
            RecordAndPublishEventOnly(evt);
        }

        /// <summary>
        /// 回合开始时上报事件。若轮到玩家回合则触发 AI 推荐；
        /// 若轮到对手回合则仅上报状态。
        /// </summary>
        private void OnTurnStart(ActivePlayer activePlayer)
        {
            StartGameIfNeeded("implicit_turn_start");

            var player = activePlayer == ActivePlayer.Player ? "me" : activePlayer == ActivePlayer.Opponent ? "opponent" : "unknown";
            var trigger = activePlayer == ActivePlayer.Player
                ? RecommendationTrigger.MyTurnStarted
                : RecommendationTrigger.None;
            var evt = _eventCollector.CreateTurnStartedEvent(_gameId, ReadTurn(), player);
            RecordAndPublish(evt, trigger);
        }

        /// <summary>通用卡牌事件处理（抽牌、出牌、弃牌等）。</summary>
        private void OnCardEvent(string player, string type, Card card)
        {
            StartGameIfNeeded("implicit_card_event");

            var resolvedPlay = type == "card_played"
                ? _powerLogEventResolver.ResolveLatestPlay(card)
                : new ResolvedPlay();
            var evt = _eventCollector.CreateCardEvent(
                _gameId,
                ReadTurn(),
                player,
                type,
                card,
                resolvedPlay.SourceEntityId,
                resolvedPlay.Target);
            RecordAndPublish(evt, RecommendationTrigger.SignificantStateChange);
        }

        private void OnAttackEvent(string player, AttackInfo attackInfo)
        {
            StartGameIfNeeded("implicit_attack_event");

            var attackerEntityId = Core.Game == null || Core.Game.ProposedAttacker <= 0
                ? (int?)null
                : Core.Game.ProposedAttacker;
            var defenderEntityId = Core.Game == null || Core.Game.ProposedDefender <= 0
                ? (int?)null
                : Core.Game.ProposedDefender;
            var evt = _eventCollector.CreateAttackEvent(
                _gameId,
                ReadTurn(),
                player,
                attackInfo,
                attackerEntityId,
                _powerLogEventResolver.ResolveEntity(defenderEntityId));
            RecordAndPublish(evt, RecommendationTrigger.SignificantStateChange);
        }

        // ---- 发布逻辑 ----

        /// <summary>
        /// 记录事件到缓冲区，通过 Fire-and-Forget 发送事件，并检查是否需要上报状态快照。
        /// 异常被捕获并记录到日志，确保不会因发送失败而中断 HDT 运行。
        /// </summary>
        private void RecordAndPublish(GameEvent gameEvent, RecommendationTrigger trigger)
        {
            try
            {
                _stateBuilder.Record(gameEvent);
                FireAndForget(_publisher.PublishGameEventAsync(gameEvent, _cancellation.Token));
                PublishSnapshotIfChanged(trigger);
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }
        }

        private void StartGameIfNeeded(string reason)
        {
            if(_inGame)
                return;

            _gameId = CreateGameId();
            _inGame = true;
            var evt = _eventCollector.CreateLifecycleEvent(_gameId, ReadTurn(), "game_started", reason, null);
            RecordAndPublish(evt, RecommendationTrigger.GameStarted);
            PublishGameMetadata();
        }

        private void PublishGameMetadata()
        {
            try
            {
                var metadata = _deckMetadataReader.Read(_gameId);
                FireAndForget(_publisher.PublishGameMetadataAsync(metadata, _cancellation.Token));
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }
        }

        private void RecordAndPublishEventOnly(GameEvent gameEvent)
        {
            try
            {
                _stateBuilder.Record(gameEvent);
                FireAndForget(_publisher.PublishGameEventAsync(gameEvent, _cancellation.Token));
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }
        }

        /// <summary>
        /// 构建当前状态快照，通过节流器判断是否需要上报和推荐，
        /// 若需要则通过 Fire-and-Forget 发送。
        /// </summary>
        private void PublishSnapshotIfChanged(RecommendationTrigger trigger)
        {
            try
            {
                var state = _stateBuilder.Build(_gameId);
                if(!_throttler.ShouldPublishState(state))
                    return;

                // 节流器可能降级触发原因（如超过每回合推荐次数上限）
                var effectiveTrigger = _throttler.ShouldTriggerRecommendation(state, trigger)
                    ? trigger
                    : RecommendationTrigger.None;
                FireAndForget(_publisher.PublishGameStateAsync(state, effectiveTrigger, _cancellation.Token));
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }
        }

        /// <summary>安全地读取当前回合数（HDT 未就绪时返回 0）。</summary>
        private int ReadTurn()
        {
            try
            {
                return Core.Game == null ? 0 : Core.Game.GetTurnNumber();
            }
            catch
            {
                return 0;
            }
        }

        private static string ReadGameResult()
        {
            try
            {
                if(Core.Game == null || Core.Game.CurrentGameStats == null)
                    return null;

                var stats = Core.Game.CurrentGameStats;
                var property = stats.GetType().GetProperty("Result")
                    ?? stats.GetType().GetProperty("GameResult")
                    ?? stats.GetType().GetProperty("Outcome");
                var value = property == null ? null : property.GetValue(stats, null);
                return value == null ? null : value.ToString().ToLowerInvariant();
            }
            catch
            {
                return null;
            }
        }

        private static string InferGameEndReason(GameState state, string result)
        {
            var normalizedResult = result ?? string.Empty;
            var myHeroAlive = state != null && state.MyHero != null && state.MyHero.Hp > 0;
            var enemyHeroAlive = state != null && state.EnemyHero != null && state.EnemyHero.Hp > 0;

            if(myHeroAlive && normalizedResult.IndexOf("loss", StringComparison.OrdinalIgnoreCase) >= 0)
                return "conceded";
            if(myHeroAlive && normalizedResult.IndexOf("defeat", StringComparison.OrdinalIgnoreCase) >= 0)
                return "conceded";
            if(enemyHeroAlive && normalizedResult.IndexOf("win", StringComparison.OrdinalIgnoreCase) >= 0)
                return "opponent_conceded";
            if(enemyHeroAlive && normalizedResult.IndexOf("victory", StringComparison.OrdinalIgnoreCase) >= 0)
                return "opponent_conceded";

            if(state != null && state.MyHero != null && state.MyHero.Hp <= 0)
                return "my_hero_dead";
            if(state != null && state.EnemyHero != null && state.EnemyHero.Hp <= 0)
                return "enemy_hero_dead";

            if(myHeroAlive && enemyHeroAlive)
                return "ended_with_heroes_alive";
            return "unknown";
        }

        /// <summary>生成对局唯一标识（基于当前时间的 yyyyMMdd-HHmmss-fff 格式）。</summary>
        private static string CreateGameId()
        {
            return DateTimeOffset.Now.ToString("yyyyMMdd-HHmmss-fff");
        }

        /// <summary>
        /// Fire-and-Forget 模式执行异步任务——不等待完成，
        /// 仅当任务失败时记录异常日志。
        /// </summary>
        private static void FireAndForget(Task task)
        {
            if(task == null)
                return;
            task.ContinueWith(t => PluginLog.Error(t.Exception), TaskContinuationOptions.OnlyOnFaulted);
        }

        /// <summary>创建右键菜单项。</summary>
        private static MenuItem CreateMenuItem()
        {
            var item = new MenuItem { Header = "HDT AI Assistant" };
            item.Click += (sender, args) => OpenLocalUi();
            return item;
        }

        /// <summary>在默认浏览器中打开本地 AI 助手页面。</summary>
        private static void OpenLocalUi()
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "http://127.0.0.1:8765",
                    UseShellExecute = true
                });
            }
            catch(Exception ex)
            {
                PluginLog.Error(ex);
            }
        }
    }
}
