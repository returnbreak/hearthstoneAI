# HDT 自定义插件路线：炉石 AI 助手架构设计

## 1. 目标

本方案目标是构建一个基于 Hearthstone Deck Tracker（HDT）自定义插件的炉石 AI 助手。系统不再优先依赖屏幕识别，而是通过 HDT 已解析的炉石日志和游戏实体状态获取结构化对局数据，再交给本地或云端 AI 服务分析，给出当前回合的打牌建议。

核心目标：

- 实时获取对局状态：手牌、牌库、场面、英雄血量、护甲、法力水晶、已打出的牌、区域变化等。
- 将对局状态标准化为 `GameState JSON`。
- 结合卡牌数据库、套牌数据库、规则引擎和大模型，生成打牌建议。
- 只做只读辅助和建议展示，不做自动点击、自动出牌、绕过检测或读取隐藏信息。

非目标：

- 不识别或推断对手未公开手牌的具体卡名。
- 不修改炉石客户端、不读内存、不注入游戏进程。
- 不以自动化操作为第一版目标。
- 不依赖视频录制作为主数据源。

## 2. 为什么选择 HDT 插件路线

原屏幕识别路线需要处理截图、ROI 裁剪、OCR、卡图匹配、动画遮挡、分辨率适配等问题。它可行，但工程复杂度高，维护成本也高。

HDT 插件路线更适合本项目：

- HDT 已经成熟解析炉石日志，并维护游戏状态。
- HDT 支持插件扩展，插件可以访问 `API.GameEvents` 和 `Hearthstone.Game.Entities`。
- 能直接获取结构化状态，比从画面反推状态稳定。
- 与现有项目中的 HearthstoneJSON 卡牌数据、套牌数据和后续可选上下文检索天然兼容。
- 便于保存对局 JSONL，用于后续回放测试、调试和评估。

参考资料：

- HDT 仓库：https://github.com/HearthSim/Hearthstone-Deck-Tracker
- HDT 插件文档：https://github.com/HearthSim/Hearthstone-Deck-Tracker/wiki/Creating-Plugins
- HDT 日志配置：https://github.com/HearthSim/Hearthstone-Deck-Tracker/wiki/Setting-up-the-log.config
- HearthSim Game State Protocol：https://hearthsim.info/docs/gamestate-protocol/
- `hslog` 解析库：https://pypi.org/project/hslog/

## 3. 总体架构

```text
Hearthstone 客户端
  -> Power.log
  -> Hearthstone Deck Tracker
  -> 自定义 HDT 插件
  -> GameState Snapshot / GameEvent Stream
  -> 本地 AI 后端
  -> 普通规则引擎 + LangChain 模型调用
  -> 第二版补充 Schema 校验 + fallback + JSONL 回放测试
  -> 可选受控上下文检索
  -> 建议结果
  -> HDT Overlay / 独立悬浮窗 / 第二屏网页
```

推荐采用“插件轻、后端重”的架构。HDT 插件只负责采集、标准化和推送状态，不在插件里做复杂 AI 推理。AI 决策、数据增强、缓存、日志存储和评估都放到本地后端服务中。

本项目只规划两版：

- 第一版：`HDT 插件 + FastAPI + 普通规则引擎 + LangChain 调模型`。
- 第二版：在第一版基础上加入 `JSON Schema 校验 + fallback + JSONL 回放测试`。

LangChain 只放在 Python AI 后端，用于模型调用和 Prompt 编排；不进入 HDT 插件。第二版不把 RAG 作为必需能力，只保留受控上下文检索作为可选增强。暂不引入 LangGraph，除非后续流程分支明显增多、需要持久化工作流或复杂状态编排。

## 4. 技术选型

| 模块 | 推荐技术 | 选择理由 |
| --- | --- | --- |
| HDT 插件 | C#，.NET Framework 4.7.2 Class Library | 符合 HDT 插件文档和运行环境 |
| 插件接口 | `IPlugin`、`API.GameEvents`、`Hearthstone.Game.Entities` | 获取事件和实体状态 |
| 本地通信 | WebSocket 优先，HTTP REST 备用 | WebSocket 适合实时事件流，REST 适合调试 |
| AI 后端 | Python FastAPI + LangChain | FastAPI 负责服务接口，LangChain 负责模型调用、Prompt 编排和可选结构化输出接入 |
| 状态存储 | SQLite + JSONL | SQLite 用于查询，JSONL 用于调试和回放测试原始记录 |
| 卡牌数据 | HearthstoneJSON，本项目已有 `hearthstone_data/card_data` | 稳定、结构化、多语言、可用 `dbfId/card_id` 关联 |
| 套牌数据 | 本项目已有 `hearthstone_data/deck_data` | 可用于卡组理解和提示词补充，优先结构化直查，不默认做 RAG |
| AI 模型 | OpenAI API / 本地 Qwen 模型二选一 | 云端模型适合快速完成第一版，本地模型适合低延迟和可控成本 |
| UI 展示 | HDT Overlay 或独立本地网页 | 第一版建议先用网页，后续再做 HDT 内嵌/悬浮窗 |
| 调试工具 | 保存 `game_state.jsonl`、`events.jsonl` | 便于离线复盘和回放问题 |

## 5. 核心组件

### 5.1 HDT 插件

职责：

- 监听 HDT 游戏事件。
- 读取当前游戏实体集合。
- 生成标准化 `GameState`。
- 在关键时机推送快照给 AI 后端。
- 保存最小本地日志，便于排查。

建议模块：

```text
HdtAiAssistantPlugin
  PluginEntry.cs
  GameEventCollector.cs
  EntityStateReader.cs
  GameStateBuilder.cs
  StateDiff.cs
  SnapshotPublisher.cs
  PluginConfig.cs
```

关键事件：

- 游戏开始。
- 起手换牌开始/结束。
- 回合开始。
- 抽牌。
- 出牌。
- 攻击。
- 英雄血量/护甲变化。
- 随从进入/离开场面。
- 游戏结束。

插件不应该把所有 `OnUpdate()` 调用都发给后端。`OnUpdate()` 约每 100ms 调用一次，如果每次都触发 AI 请求，会造成重复、延迟和费用浪费。推荐只在状态发生实质变化时推送快照，并在“轮到我方行动”时触发 AI 建议。

### 5.2 本地 AI 后端

职责：

- 接收插件推送的 `GameEvent` 和 `GameState`。
- 维护当前对局状态。
- 补充卡牌中文名、费用、职业、类型、效果文本等信息。
- 调用规则引擎做确定性分析。
- 调用大模型生成解释型建议。
- 将建议推送给 UI。
- 保存对局数据用于调试和 JSONL 回放测试。

建议模块：

```text
ai_backend/
  app.py
  ingest/
    websocket_server.py
    rest_api.py
  state/
    game_state.py
    state_store.py
    replay_writer.py
  data/
    card_db.py
    deck_db.py
  coach/
    rule_engine.py
    prompt_builder.py
    llm_client.py
    langchain_client.py
    schema_validator.py
    fallback.py
    recommendation_ranker.py
  replay/
    jsonl_replay.py
  ui/
    websocket_broadcast.py
```

第一版只要求 `rule_engine.py`、`prompt_builder.py`、`llm_client.py` 或 `langchain_client.py` 可用。第二版再启用 `schema_validator.py`、`fallback.py` 和 `jsonl_replay.py`。受控上下文检索只作为可选增强，不是第二版验收前提。

### 5.3 规则引擎

规则引擎先于大模型执行，负责处理确定性问题。这样可以减少大模型幻觉，提高建议稳定性。

第一版规则：

- 是否有斩杀。
- 当前法力能打出的合法组合。
- 是否存在明显费用浪费。
- 是否应该先过牌。
- 是否需要优先解嘲讽。
- 自己英雄是否处于危险血线。
- 当前手牌是否有解场、回血、直伤、站场资源。

规则引擎输出不一定直接作为最终答案，但应作为大模型输入的一部分。

### 5.4 大模型教练

大模型负责解释、排序和取舍，不负责凭空读取游戏状态。

第一版中，大模型调用通过 LangChain 封装，目标是减少模型供应商切换成本，并统一 Prompt 输入和响应解析。此时不做复杂 Agent，不让模型自主调用工具，只执行固定链路：

```text
GameState
  -> 卡牌数据增强
  -> 规则引擎分析
  -> Prompt 模板
  -> LangChain 调用 LLM
  -> Recommendation
```

第二版中，在上述链路中优先加入 JSON Schema 校验和 fallback，并用 JSONL 回放测试验证稳定性。可选加入受控上下文检索，但不默认引入通用向量库 RAG：

```text
GameState
  -> 卡牌数据增强
  -> 规则引擎分析
  -> Prompt 模板
  -> LangChain 调用 LLM
  -> JSON Schema 校验
  -> 校验失败时 fallback 到规则引擎建议
  -> Recommendation
```

可选上下文检索只从受控数据源补充少量信息：

- `card_db`：按 `card_id/dbfId` 直接查卡牌文本，不需要 RAG。
- `deck_db`：按当前套牌 ID 或卡组列表直接查套牌信息，不需要 RAG。
- `strategy_notes`：少量人工整理的职业对局或套牌策略，可做关键词检索。
- `replay_notes`：历史回放测试中沉淀的错误案例和修正规则，可做关键词检索。

第二版不建议做通用向量库 RAG、联网查攻略、检索大量环境文章，避免引入过期或和当前局面无关的噪声。

输入：

- 当前标准化 `GameState`。
- 最近事件列表。
- 规则引擎结果。
- 当前套牌信息。
- 卡牌效果文本。
- 对局模式、职业、回合数。

输出：

```json
{
  "summary": "本回合优先抢血，有接近斩杀窗口。",
  "recommended_actions": [
    {
      "priority": 1,
      "action": "使用火球术攻击敌方英雄",
      "reason": "敌方剩余血量较低，保留直伤可以逼近斩杀。",
      "risk": "如果对手下回合有回血，可能错过解场价值。"
    }
  ],
  "warnings": [
    "不要先随从交换，当前伤害可能不足以二次组织进攻。"
  ],
  "confidence": "medium"
}
```

要求：

- 输出最多 2-3 条建议。
- 必须说明理由。
- 必须说明不确定性。
- 不允许声称知道对手隐藏手牌。
- 不给出自动操作指令。

## 6. 数据结构设计

### 6.1 GameEvent

```json
{
  "game_id": "2026-06-01-001",
  "timestamp": "2026-06-01T20:15:30.123+08:00",
  "turn": 6,
  "player": "me",
  "type": "card_played",
  "entity_id": 42,
  "card_id": "CS2_029",
  "dbf_id": 315,
  "name": "Fireball",
  "zone_from": "HAND",
  "zone_to": "PLAY",
  "target_entity_id": 64
}
```

### 6.2 GameState

```json
{
  "game_id": "2026-06-01-001",
  "timestamp": "2026-06-01T20:15:31.000+08:00",
  "mode": "standard",
  "turn": 6,
  "active_player": "me",
  "my_hero": {
    "class": "MAGE",
    "hp": 24,
    "armor": 0,
    "attack": 0
  },
  "enemy_hero": {
    "class": "HUNTER",
    "hp": 17,
    "armor": 0,
    "attack": 0
  },
  "mana": {
    "current": 6,
    "max": 6
  },
  "hand": [
    {
      "entity_id": 42,
      "card_id": "CS2_029",
      "dbf_id": 315,
      "name": "Fireball",
      "cost": 4,
      "type": "SPELL"
    }
  ],
  "my_board": [],
  "enemy_board": [],
  "my_deck_count": 18,
  "enemy_hand_count": 4,
  "enemy_deck_count": 20,
  "known_enemy_cards": [
    {
      "card_id": "EX1_539",
      "name": "Kill Command",
      "source": "played"
    }
  ],
  "recent_events": []
}
```

### 6.3 Recommendation

```json
{
  "game_id": "2026-06-01-001",
  "snapshot_timestamp": "2026-06-01T20:15:31.000+08:00",
  "generated_at": "2026-06-01T20:15:32.200+08:00",
  "phase": "my_turn",
  "summary": "当前可以考虑抢血，保留场面交换不是最高收益。",
  "actions": [
    {
      "rank": 1,
      "title": "火球术打脸",
      "steps": ["使用 Fireball 指向敌方英雄"],
      "reason": "敌方血量 17，当前手牌直伤能制造两回合斩杀压力。",
      "risk": "如果敌方下回合有回血或强嘲讽，进攻计划会变差。"
    }
  ],
  "confidence": "medium"
}
```

## 7. 数据流

### 7.1 实时建议流程

第一版实时流程：

```text
1. 炉石产生 Power.log
2. HDT 解析日志并更新内部 Game 状态
3. 自定义插件监听事件或检测状态变化
4. 插件构建 GameState Snapshot
5. 插件通过 WebSocket 推送给 Python 后端
6. 后端保存事件和快照
7. 后端补充卡牌信息
8. 规则引擎生成候选分析
9. LangChain 调用 LLM 生成 2-3 条建议
10. UI 显示建议
```

第二版实时流程：

```text
1. 炉石产生 Power.log
2. HDT 解析日志并更新内部 Game 状态
3. 自定义插件监听事件或检测状态变化
4. 插件构建 GameState Snapshot
5. 插件通过 WebSocket 推送给 Python 后端
6. 后端保存事件和快照
7. 后端补充卡牌和套牌信息
8. 规则引擎生成候选分析
9. 可选补充受控上下文，例如套牌说明或人工策略笔记
10. LangChain 调用 LLM 生成 2-3 条建议
11. JSON Schema 校验建议格式和安全边界
12. 校验失败或模型超时时 fallback 到规则引擎建议
13. UI 显示建议
```

### 7.2 JSONL 回放测试流程

第二版加入 JSONL 回放测试，但不把训练数据建设作为当前范围。

```text
1. 读取 game_state.jsonl 和 events.jsonl
2. 按时间线重建对局状态
3. 对每个我方回合重新运行规则引擎
4. 运行可选上下文补充、Prompt 构造和 LangChain 模型调用
5. 校验 Recommendation JSON 是否符合 Schema
6. 检查越界表述，例如声称知道对手隐藏手牌
7. 输出 replay_test_report.md
```

JSONL 回放测试用于验证建议链路稳定性、复现错误案例和降低实时调试成本。它不要求第一版实现。

## 8. AI 调用策略

不建议每次事件都调用大模型。推荐触发条件：

- 我方回合开始。
- 我方法力、手牌或场面发生重大变化。
- 敌方回合结束后进入我方可行动状态。
- 玩家手动点击“重新分析”。

节流策略：

- 同一状态哈希不重复请求。
- 同一回合最多自动请求 2 次。
- 优先使用规则引擎快速判断，只有需要解释时调用 LLM。
- 大模型超时时保留上一条建议，并标记“建议可能过期”。

推荐状态哈希字段：

```text
turn
active_player
mana
my_hero hp/armor
enemy_hero hp/armor
hand card_id list
my_board entity summary
enemy_board entity summary
```

## 9. UI 方案

第一版推荐先做独立本地网页：

```text
http://127.0.0.1:8765
```

原因：

- 开发快。
- 调试方便。
- 不依赖 HDT Overlay 细节。
- 后续可迁移为 HDT 内嵌窗口或桌面悬浮窗。

UI 内容：

- 当前回合摘要。
- 推荐操作 1-3 条。
- 每条建议的理由和风险。
- 当前识别到的手牌、场面、血量。
- 最近事件日志。
- “重新分析”按钮。

不建议在第一版中做复杂动画、自动定位或游戏内覆盖。先验证 AI 建议质量，再优化显示体验。

## 10. 安全与边界

必须明确以下边界：

- 只使用 HDT 可获得的日志和实体状态。
- 不读取对手隐藏手牌。
- 不做自动出牌。
- 不修改游戏客户端。
- 不绕过反作弊或平台限制。
- 不在正式比赛中默认启用，具体使用场景需要遵守赛事规则。

对手信息处理原则：

- 对手已打出的牌可以记录。
- 对手发现、揭示、偷取、复制后公开的信息可以记录。
- 对手未公开手牌只记录数量、在手停留时间、来源推断，不记录具体卡名。

## 11. 实现流程

### 第一版：HDT 插件 + FastAPI + 普通规则引擎 + LangChain 调模型

目标：完成可实时使用的最小闭环。插件能拿到 HDT 状态，后端能接收、增强、分析，并通过 LangChain 调用模型生成建议。

任务：

1. 创建 C# `.NET Framework 4.7.2` Class Library。
2. 引用 `Hearthstone Deck Tracker.exe` 并实现 `Plugins.IPlugin`。
3. 在游戏开始、回合开始、出牌、游戏结束等关键事件记录 `GameEvent`。
4. 从 `Hearthstone.Game.Entities` 构建基础 `GameState`。
5. 插件通过 WebSocket 推送 `GameEvent` 和 `GameState` 到 `ws://127.0.0.1:8765/ws/hdt`。
6. Python FastAPI 后端接收事件和状态，并保存 `game_state.jsonl`、`events.jsonl`。
7. 读取 `hearthstone_data/card_data/latest_full_cards.zhCN.json`，补充中文名、费用、类型和效果文本。
8. 实现普通 Python 规则引擎，输出 `rule_analysis`。
9. 实现 Prompt 模板，输入 `GameState + rule_analysis + card_text`。
10. 使用 LangChain 调用 OpenAI API 或本地 Qwen 兼容接口。
11. 将模型建议推送到本地网页 UI。

验收标准：

- 打一局游戏后能看到完整事件和状态时间线。
- 至少包含回合数、双方英雄血量、手牌、场面、已打出的牌。
- 后端断开后插件不崩溃，重新连接后继续推送最新状态。
- 每次我方回合开始能生成 1-3 条建议。
- 建议包含操作、理由、风险和置信度。
- 不依赖屏幕截图，不自动出牌，不读取隐藏信息。

第一版不做：

- 通用 RAG。
- JSON Schema 强校验。
- fallback 策略。
- JSONL 回放测试。
- 训练数据筛选。
- LangGraph。

### 第二版：Schema 校验 + fallback + JSONL 回放测试

目标：在第一版可用闭环上提高建议质量、输出稳定性和可测试性。

任务：

1. 定义 `Recommendation` JSON Schema。
2. 对 LLM 输出做 JSON Schema 校验。
3. 增加安全边界校验，拒绝“知道对手隐藏手牌”等越界表述。
4. 模型超时、格式错误或越界时 fallback 到规则引擎建议。
5. 实现 `jsonl_replay.py`，读取 `game_state.jsonl` 和 `events.jsonl` 离线回放。
6. 对回放中的每个我方回合重新运行完整建议链路。
7. 输出 `replay_test_report.md`，记录成功率、Schema 错误、越界输出和 fallback 次数。
8. 可选加入受控上下文检索，例如套牌说明、人工策略笔记或历史错误案例；该能力不作为第二版验收前提。

验收标准：

- LLM 输出必须符合 `Recommendation` JSON Schema。
- 超时、格式错误、越界输出时能返回规则引擎 fallback 建议。
- 使用保存的 JSONL 能离线重放建议链路，不依赖实时炉石。
- 回放报告能定位失败样本，便于修复 Prompt、规则或数据增强逻辑。

## 12. Prompt 输入模板

建议后端构造紧凑文本，不要把全量原始 JSON 直接塞给模型。

```text
你是炉石传说对局分析助手。你只能根据已公开信息和当前状态给建议，不能声称知道对手隐藏手牌。

当前状态：
- 模式：标准
- 回合：6
- 当前行动方：我方
- 我方英雄：法师，24 血，0 护甲
- 敌方英雄：猎人，17 血，0 护甲
- 法力：6/6
- 我方手牌：火球术(4费，造成6点伤害)，奥术智慧(3费，抽2张牌)
- 我方场面：无
- 敌方场面：2/3 随从 x1
- 已知敌方信息：上回合打出杀戮命令

规则引擎分析：
- 当前没有直接斩杀。
- 火球术打脸后敌方剩余 11 血。
- 奥术智慧可以补资源，但会消耗 3 费。

请输出 JSON：
- summary
- recommended_actions，最多 3 条
- warnings
- confidence
```

## 13. 测试策略

### 插件测试

- 用真实对局测试事件是否完整。
- 对比 HDT UI 和导出的 JSON，确认手牌、场面、血量一致。
- 测试游戏开始、投降、断线重连、观战、酒馆/标准模式切换。

### 后端测试

- 对卡牌索引、状态合并、规则引擎写单元测试。
- 第一版测试 FastAPI WebSocket 接收、JSONL 保存、卡牌增强、规则引擎和 LangChain 模型调用。
- 第二版使用保存的 JSONL 回放，不依赖实时炉石。
- 第二版对 LLM 输出做 JSON Schema 校验测试。
- 第二版测试 fallback：模型超时、非 JSON、字段缺失、越界表述时必须返回规则引擎建议。

### AI 建议测试

- 构造固定场景：斩杀、必须解场、费用不足、过牌优先、隐藏信息不足。
- 检查建议是否越界。
- 检查输出是否稳定。
- 第二版保存错误案例到回放报告，用于修复规则、Prompt 或可选上下文。

## 14. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| HDT 插件 API 变化 | 插件失效 | 锁定 HDT 版本，封装适配层 |
| 炉石日志字段变化 | 状态缺失 | 保留 Power.log/JSONL 原始记录，便于修复 |
| AI 延迟过高 | 建议来不及看 | 先规则引擎快速响应，LLM 异步补充解释 |
| AI 幻觉 | 错误建议 | 限制输入、Schema 校验、系统提示禁止隐藏信息 |
| 费用过高 | 无法长期使用 | 状态哈希去重、回合级触发、本地模型备选 |
| UI 干扰游戏 | 使用体验差 | 第一版用第二屏网页，后续再做轻量悬浮窗 |
| 对手隐藏信息边界不清 | 可能违规或误导 | 明确只记录已公开信息和数量 |

## 15. 两版范围

### 第一版范围

- HDT 插件导出当前对局状态。
- Python 后端接收并保存 JSONL。
- 卡牌数据增强。
- 简单规则引擎。
- 我方回合开始时通过 LangChain 调用 AI。
- 本地网页展示 1-3 条建议。

第一版不做：

- 通用 RAG。
- JSON Schema 强校验。
- fallback。
- JSONL 回放测试。
- LangGraph。
- 自动出牌。
- 复杂 UI。
- 移动端。
- 屏幕识别。
- 训练模型。
- 对手卡组精准预测。

### 第二版范围

第二版只在第一版基础上增加：

- `Recommendation` JSON Schema 校验。
- 模型超时、格式错误、越界输出时 fallback 到规则引擎建议。
- JSONL 回放测试。
- 输出 `replay_test_report.md` 辅助定位建议链路问题。
- 可选受控上下文检索，例如套牌说明、人工策略笔记和历史错误案例。

第二版不把 RAG 作为必做项。卡牌文本和套牌信息优先使用结构化数据库直查；只有人工策略笔记、历史复盘经验这类非结构化内容，才考虑小范围关键词检索或向量检索。

第二版仍不做：

- LangGraph。
- 通用向量库 RAG。
- 联网查攻略。
- 大量环境文章检索。
- 自动出牌。
- 屏幕识别主链路。
- 模型训练或微调。
- 对手隐藏手牌推断。

## 16. 推荐目录结构

```text
hearthstone_ai_assistant/
  hdt_plugin/
    HdtAiAssistantPlugin.csproj
    PluginEntry.cs
    GameEventCollector.cs
    EntityStateReader.cs
    GameStateBuilder.cs
    SnapshotPublisher.cs
  ai_backend/
    app.py
    ingest/
      websocket_server.py
      rest_api.py
    state/
      game_state.py
      state_store.py
      replay_writer.py
    data/
      card_db.py
      deck_db.py
    coach/
      rule_engine.py
      prompt_builder.py
      llm_client.py
      langchain_client.py
      schema_validator.py
      fallback.py
      recommendation_ranker.py
      optional_context.py
    replay/
      jsonl_replay.py
    ui/
      static/
      websocket_broadcast.py
  data/
    game_logs/
    replays/
  docs/
    api_schema.md
    prompt_templates.md
```

现有数据目录继续复用：

```text
hearthstone_data/
  card_data/
  deck_data/
```

## 17. 最终建议

最优路线是：

```text
HDT 自定义插件
  -> 结构化 GameState
  -> Python AI 后端
  -> 第一版：普通规则引擎 + LangChain 调模型
  -> 第二版：JSON Schema 校验 + fallback + JSONL 回放测试
  -> 可选：受控上下文检索
  -> 本地网页展示建议
  -> 对局 JSONL 沉淀调试和回放数据
```

这条路线比纯屏幕识别更稳定，也更适合当前目标。第一版先做可用闭环，第二版优先补质量和稳定性，而不是优先做 RAG。LangChain 放在 Python 后端负责模型调用、Prompt 编排和可选结构化输出接入；LangGraph 暂不进入这两版范围，避免第一版复杂化。屏幕识别可以保留为后续兜底模块，但不应作为主采集层。
## 18. 补充：随从交互与战斗关键词采集

炉石建议质量不能只依赖手牌、血量和基础场面。随从交换是保持场面伤害、压制力和斩杀窗口的核心部分，因此插件层需要尽量完整采集公开战斗状态，后端规则层再做候选攻击和交换评估。

插件层只负责采集，不负责完整策略搜索。第一阶段扩展 `MinionSnapshot` 和 `HeroSnapshot` 字段：

- 随从基础战斗字段：`attack`、`health`、`damage`、`zone_position`、`can_attack`、`attacks_this_turn`、`attacks_remaining`。
- 常见战斗关键词：`taunt`、`divine_shield`、`stealth`、`immune`、`frozen`、`rush`、`charge`、`windfury`、`mega_windfury`、`lifesteal`、`poisonous`、`venomous`、`reborn`、`deathrattle`、`dormant`、`silenced`、`cant_attack`。
- 英雄战斗字段：`attack`、`can_attack`、`attacks_this_turn`、`attacks_remaining`、`immune`、`frozen`。

后端后续应增加 `combat_analyzer`，基于这些公开字段生成候选：

- 合法攻击列表。
- 嘲讽约束。
- 打脸候选和斩杀候选。
- 随从交换候选。
- 交换后的场攻、剩余生命、圣盾破除、吸血回血、风怒二次攻击等粗粒度结果。

第一阶段不尝试实现完整炉石模拟器，也不覆盖所有卡牌特效。优先覆盖普通攻击、嘲讽、圣盾、吸血、风怒、冻结、突袭、冲锋、剧毒/烈毒、复生、亡语等常见公开关键词，让后端规则引擎具备基本随从交换判断能力。
