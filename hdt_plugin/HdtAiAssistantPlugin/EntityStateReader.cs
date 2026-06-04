using System;
using System.Collections.Generic;
using System.Linq;
using HearthDb.Enums;
using Hearthstone_Deck_Tracker.Hearthstone;
using Hearthstone_Deck_Tracker.Hearthstone.Entities;
using HdtAiAssistant.Core.Models;

namespace HdtAiAssistantPlugin
{
    /// <summary>
    /// 实体状态读取器——从 HDT 的 GameV2 对象中提取游戏状态信息，
    /// 将其转换为平台无关的 GameState 快照模型。
    ///
    /// 负责读取英雄、手牌、战场随从、法力水晶、已知敌方卡牌等所有公开信息。
    /// </summary>
    internal sealed class EntityStateReader
    {
        /// <summary>
        /// 从 HDT 游戏对象读取完整的游戏状态快照。
        /// </summary>
        /// <param name="game">HDT 游戏实例（可为 null）。</param>
        /// <param name="gameId">当前对局唯一标识。</param>
        /// <returns>填充了当前游戏状态的 GameState 对象。</returns>
        public GameState Read(GameV2 game, string gameId)
        {
            var myMana = ReadMana(ReadPlayerEntity(game, "PlayerEntity", game == null ? null : game.Player));
            var enemyMana = ReadMana(ReadPlayerEntity(game, "OpponentEntity", game == null ? null : game.Opponent));
            var state = new GameState
            {
                GameId = gameId,
                Timestamp = DateTimeOffset.Now.ToString("o"),
                Mode = ReadMode(game),
                Turn = game == null ? 0 : game.GetTurnNumber(),
                ActivePlayer = ReadActivePlayer(game),
                MyHero = ReadHero(game == null ? null : game.Player),
                EnemyHero = ReadHero(game == null ? null : game.Opponent),
                Mana = myMana,
                MyMana = myMana,
                EnemyMana = enemyMana,
                MyDeckCount = game == null ? 0 : game.Player.DeckCount,
                EnemyHandCount = game == null ? 0 : game.Opponent.HandCount,
                EnemyDeckCount = game == null ? 0 : game.Opponent.DeckCount
            };

            // 游戏未初始化时直接返回仅含基本信息的状态
            if(game == null)
                return state;

            // 读取手牌（按区域位置排序）
            foreach(var card in Sorted(game.Player.Hand).Select(ReadCard))
                state.Hand.Add(card);

            // 读取我方战场随从（按区域位置排序）
            foreach(var minion in Sorted(game.Player.Minions).Select(ReadMinion))
                state.MyBoard.Add(minion);

            // 读取敌方战场随从（按区域位置排序）
            foreach(var minion in Sorted(game.Opponent.Minions).Select(ReadMinion))
                state.EnemyBoard.Add(minion);

            // 读取已知敌方卡牌（从已打出的卡牌中推断）
            foreach(var card in ReadKnownOpponentCards(game))
                state.KnownEnemyCards.Add(card);

            return state;
        }

        /// <summary>读取当前游戏模式（standard / wild / arena 等）。</summary>
        private static string ReadMode(GameV2 game)
        {
            if(game == null)
                return "unknown";
            if(game.CurrentFormat.HasValue)
                return game.CurrentFormat.Value.ToString().ToLowerInvariant();
            return game.CurrentGameMode.ToString().ToLowerInvariant();
        }

        /// <summary>判断当前活跃玩家是"me"还是"opponent"。</summary>
        private static string ReadActivePlayer(GameV2 game)
        {
            if(game == null)
                return "unknown";

            // 通过 IsCurrentPlayer 标签找到当前回合的玩家实体
            var currentPlayerEntity = game.Entities.Values.FirstOrDefault(x => x.IsCurrentPlayer);
            if(currentPlayerEntity == null)
                return "unknown";

            if(currentPlayerEntity.IsControlledBy(game.Player.Id))
                return "me";
            if(currentPlayerEntity.IsControlledBy(game.Opponent.Id))
                return "opponent";
            return "unknown";
        }

        /// <summary>读取英雄状态（职业、生命值、护甲、攻击力）。</summary>
        private static HeroSnapshot ReadHero(Player player)
        {
            var hero = player == null ? null : player.Hero;
            return new HeroSnapshot
            {
                Class = ReadHeroClass(player, hero),
                Hp = hero == null ? 0 : hero.Health,
                Armor = hero == null ? 0 : hero.GetTag(GameTag.ARMOR),
                Attack = hero == null ? 0 : hero.Attack,
                CanAttack = CanAttack(hero),
                AttacksThisTurn = ReadAttacksThisTurn(hero),
                AttacksRemaining = ReadAttacksRemaining(hero),
                Immune = HasTag(hero, GameTag.IMMUNE),
                Frozen = HasTag(hero, GameTag.FROZEN)
            };
        }

        /// <summary>
        /// 读取英雄职业。优先级：CurrentClass > OriginalClass > 英雄卡牌的 CardClass。
        /// </summary>
        private static string ReadHeroClass(Player player, Entity hero)
        {
            if(player != null && !string.IsNullOrWhiteSpace(player.CurrentClass))
                return player.CurrentClass;
            if(player != null && !string.IsNullOrWhiteSpace(player.OriginalClass))
                return player.OriginalClass;
            if(hero != null)
                return hero.Card.CardClass.ToString();
            return "UNKNOWN";
        }

        /// <summary>读取当前法力水晶状态（当前可用 / 上限）。</summary>
        private static Entity ReadPlayerEntity(GameV2 game, string propertyName, Player player)
        {
            if(game == null)
                return null;

            var property = game.GetType().GetProperty(propertyName);
            if(property != null)
            {
                var entity = property.GetValue(game, null) as Entity;
                if(entity != null)
                    return entity;
            }

            if(player == null || game.Entities == null)
                return null;

            return game.Entities.Values.FirstOrDefault(x => x != null && x.Id == player.Id);
        }

        private static ManaSnapshot ReadMana(Entity playerEntity)
        {
            var max = ReadTagByName(playerEntity, "RESOURCES");
            var used = ReadTagByName(playerEntity, "RESOURCES_USED");
            var temporary = ReadTagByName(playerEntity, "TEMP_RESOURCES");
            var current = max - used + temporary;
            if(current < 0)
                current = 0;

            return new ManaSnapshot
            {
                Current = current,
                Max = max
            };
        }

        private static int ReadTagByName(Entity entity, string tagName)
        {
            if(entity == null)
                return 0;

            GameTag tag;
            return Enum.TryParse(tagName, out tag) ? entity.GetTag(tag) : 0;
        }

        /// <summary>将 HDT 实体转换为 CardSnapshot。</summary>
        private static CardSnapshot ReadCard(Entity entity)
        {
            if(entity == null)
                return new CardSnapshot();

            return new CardSnapshot
            {
                EntityId = entity.Id,
                CardId = entity.CardId,
                DbFId = entity.Card == null ? 0 : entity.Card.DbfId,
                Name = entity.LocalizedName ?? entity.Name,
                Cost = entity.Cost,
                Type = entity.Card == null || entity.Card.TypeEnum == null ? "UNKNOWN" : entity.Card.TypeEnum.Value.ToString(),
                Text = ReadCardText(entity),
                Zone = entity.IsInHand ? "HAND" : entity.IsInDeck ? "DECK" : entity.IsInPlay ? "PLAY" : null
            };
        }

        private static string ReadCardText(Entity entity)
        {
            if(entity == null || entity.Card == null)
                return null;

            return ReadStringProperty(entity.Card, "Text")
                ?? ReadStringProperty(entity.Card, "LocalizedText")
                ?? ReadStringProperty(entity.Card, "CardTextInHand");
        }

        private static string ReadStringProperty(object instance, string propertyName)
        {
            var property = instance.GetType().GetProperty(propertyName);
            if(property == null)
                return null;

            return property.GetValue(instance, null) as string;
        }

        /// <summary>将 HDT 实体转换为 MinionSnapshot，包含战斗相关关键字。</summary>
        private static MinionSnapshot ReadMinion(Entity entity)
        {
            if(entity == null)
                return new MinionSnapshot();

            return new MinionSnapshot
            {
                EntityId = entity.Id,
                CardId = entity.CardId,
                DbFId = entity.Card == null ? 0 : entity.Card.DbfId,
                Name = entity.LocalizedName ?? entity.Name,
                Text = ReadCardText(entity),
                Attack = entity.Attack,
                Health = entity.Health,
                Damage = entity.GetTag(GameTag.DAMAGE),
                ZonePosition = entity.ZonePosition,
                CanAttack = CanAttack(entity),
                AttacksThisTurn = ReadAttacksThisTurn(entity),
                AttacksRemaining = ReadAttacksRemaining(entity),
                Taunt = entity.HasTag(GameTag.TAUNT),
                DivineShield = entity.HasTag(GameTag.DIVINE_SHIELD),
                Stealth = entity.HasTag(GameTag.STEALTH),
                Immune = entity.HasTag(GameTag.IMMUNE),
                Frozen = entity.HasTag(GameTag.FROZEN),
                Rush = entity.HasTag(GameTag.RUSH),
                Charge = entity.HasTag(GameTag.CHARGE) || entity.HasTag(GameTag.CHARGE_READY) || entity.HasTag(GameTag.NON_KEYWORD_CHARGE),
                Windfury = entity.HasTag(GameTag.WINDFURY),
                MegaWindfury = entity.HasTag(GameTag.MEGA_WINDFURY),
                Lifesteal = entity.HasTag(GameTag.LIFESTEAL),
                Poisonous = entity.HasTag(GameTag.POISONOUS) || entity.HasTag(GameTag.NON_KEYWORD_POISONOUS),
                Venomous = entity.HasTag(GameTag.VENOMOUS),
                Reborn = entity.HasTag(GameTag.REBORN),
                Deathrattle = entity.HasTag(GameTag.DEATHRATTLE) || entity.HasTag(GameTag.DEATH_RATTLE),
                Dormant = entity.HasTag(GameTag.DORMANT),
                Silenced = entity.HasTag(GameTag.SILENCED),
                CantAttack = entity.HasTag(GameTag.CANT_ATTACK),
                Exhausted = entity.HasTag(GameTag.EXHAUSTED)
            };
        }

        private static bool CanAttack(Entity entity)
        {
            if(entity == null)
                return false;
            return entity.Attack > 0
                && ReadAttacksRemaining(entity) > 0
                && !entity.HasTag(GameTag.CANT_ATTACK)
                && !entity.HasTag(GameTag.FROZEN)
                && !entity.HasTag(GameTag.DORMANT)
                && !entity.HasTag(GameTag.EXHAUSTED);
        }

        private static int ReadAttacksThisTurn(Entity entity)
        {
            return entity == null ? 0 : entity.GetTag(GameTag.NUM_ATTACKS_THIS_TURN);
        }

        private static int ReadAttacksRemaining(Entity entity)
        {
            if(entity == null)
                return 0;

            var remaining = ReadMaxAttacksPerTurn(entity) - ReadAttacksThisTurn(entity);
            return remaining < 0 ? 0 : remaining;
        }

        private static int ReadMaxAttacksPerTurn(Entity entity)
        {
            if(entity == null)
                return 0;

            var max = entity.HasTag(GameTag.MEGA_WINDFURY)
                ? 4
                : entity.HasTag(GameTag.WINDFURY) ? 2 : 1;

            max += entity.GetTag(GameTag.EXTRA_ATTACKS_THIS_TURN);
            return max < 0 ? 0 : max;
        }

        private static bool HasTag(Entity entity, GameTag tag)
        {
            return entity != null && entity.HasTag(tag);
        }

        /// <summary>
        /// 读取已知敌方卡牌——从对手本局已打出的非隐藏卡牌中提取，
        /// 按 CardId 去重后返回。
        /// </summary>
        private static IEnumerable<CardSnapshot> ReadKnownOpponentCards(GameV2 game)
        {
            return game.Opponent.CardsPlayedThisMatch
                .Where(x => x != null && !string.IsNullOrEmpty(x.CardId) && !x.Info.Hidden)
                .GroupBy(x => x.CardId)
                .Select(group =>
                {
                    var card = ReadCard(group.First());
                    card.Source = "played";
                    return card;
                })
                .OrderBy(x => x.CardId);
        }

        /// <summary>按区域位置排序实体列表，同位置按实体 ID 排序。</summary>
        private static IEnumerable<Entity> Sorted(IEnumerable<Entity> entities)
        {
            if(entities == null)
                return Enumerable.Empty<Entity>();
            return entities.OrderBy(x => x.ZonePosition).ThenBy(x => x.Id);
        }
    }
}
