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
| 卡牌数据 | HearthstoneJSON，本项目当前使用 `hearthstone_data/cards` | 稳定、结构化、多语言、可用 `dbfId/card_id` 关联 |
| 套牌数据 | 本项目当前使用 `hearthstone_data/decks/strategy_context.zhCN.json` | 可用于卡组理解和提示词补充，优先结构化直查，不默认做 RAG |
| AI 模型 | DeepSeek v4 flash，通过 LangChain 调用 | 当前默认使用更快的 DeepSeek flash 模型，仍可通过 `DEEPSEEK_MODEL` 切换供应商或具体模型 |
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
  "target": {
    "entity_id": 64,
    "card_id": "CS2_182",
    "name": "Chillwind Yeti",
    "type": "minion"
  }
}
```

事件目标统一使用嵌套 `target`。无目标事件不输出 `target` 字段。
`target_player` 和 `target_is_hero` 不记录，英雄目标通过
`target.type = "hero"` 判断。出牌目标从 HDT 保存的 Power.log
`BLOCK_START BlockType=PLAY` 记录中取得实体 ID，再由
`Game.Entities` 解析卡牌 ID、名称和实体类型。

事件只输出有意义的可选字段。`entity_id`、`dbf_id` 和
`damage_amount` 只有值大于 0 时输出；`card_id`、`name`、`reason`
和 `result` 只有非空时输出。这样 `turn_started` 不会携带整组
`null` 或 `0` 字段，攻击和有目标出牌仍保留完整实体关联。

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
  "my_mana": {
    "current": 6,
    "max": 6
  },
  "enemy_mana": {
    "current": 0,
    "max": 5
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
      "name": "Kill Command"
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
7. 读取 `hearthstone_data/cards/card_index.zhCN.json`，补充中文名、费用、类型和效果文本。
8. 实现普通 Python 规则引擎，输出 `rule_analysis`。
9. 实现 Prompt 模板，输入 `GameState + rule_analysis + card_text`。
10. 使用 LangChain 流式调用 DeepSeek v4 flash。
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
  cards/
    card_index.zhCN.json
    images/
  decks/
    strategy_context.zhCN.json
```

目录命名约定：

- `cards/`：卡牌数据、卡牌索引和卡牌图片。
- `decks/`：套牌环境、对手原型、己方套牌策略和提示词策略上下文。

旧的 `latest/`、`meta/`、`card_data/`、`deck_data/` 不再作为运行时主目录使用。

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

- 随从基础战斗字段：`attack`、`health`、`damage`、`zone_position`、`attacks_this_turn`、`max_attacks_per_turn`。
- 常见战斗关键词：`taunt`、`divine_shield`、`stealth`、`immune`、`frozen`、`rush`、`charge`、`windfury`、`mega_windfury`、`lifesteal`、`poisonous`、`venomous`、`reborn`、`deathrattle`、`dormant`、`silenced`、`cant_attack`。
- 英雄战斗字段：`attack`、`attacks_this_turn`、`max_attacks_per_turn`、`immune`、`frozen`。

后端后续应增加 `combat_analyzer`，基于这些公开字段生成候选：

- 合法攻击列表。
- 嘲讽约束。
- 打脸候选和斩杀候选。
- 随从交换候选。
- 交换后的场攻、剩余生命、圣盾破除、吸血回血、风怒二次攻击等粗粒度结果。

第一阶段不尝试实现完整炉石模拟器，也不覆盖所有卡牌特效。优先覆盖普通攻击、嘲讽、圣盾、吸血、风怒、冻结、突袭、冲锋、剧毒/烈毒、复生、亡语等常见公开关键词，让后端规则引擎具备基本随从交换判断能力。

## 19. 补充：合法动作、动作序列与 AI 策略分工

后续路线不建议用纯代码完整模拟炉石全部结算过程。完整模拟会涉及战吼、亡语、奥秘、随机目标、泰坦技能、地点、发现、抽牌、复生、沉默、光环站位、结算顺序等大量细节，工程复杂度接近一个小型炉石规则引擎。

更合理的架构是：

```text
HDT 公开数据
  -> 后端数据补齐
  -> 硬规则生成合法动作和候选动作序列
  -> Prompt Builder 把候选动作、局面、卡牌文本和策略原则交给 AI
  -> AI 负责策略排序、复杂效果理解和解释
  -> 后端再次校验 AI 输出是否合法
  -> UI 展示建议
```

也就是说，代码不负责“理解所有卡牌最优策略”，但必须负责“不让 AI 推荐非法动作”。

### 19.1 代码必须负责的硬规则

这些规则不能交给大模型猜，必须由后端确定性处理：

- 当前可用法力水晶：`my_mana.current`。
- 手牌是否在手、费用是否足够。
- 随从牌是否有场位，场上最多 7 个随从。
- 武器、英雄牌、地点等是否满足基础打出条件。
- 攻击者是否能攻击：攻击力、冻结、休眠、`cant_attack`、`exhausted`，以及 `attacks_this_turn < max_attacks_per_turn`。
- 目标是否合法：潜行、免疫、休眠、是否存在嘲讽。
- 敌方有嘲讽时，不能直接攻击敌方英雄，也不能攻击非嘲讽随从。
- 英雄技能是否本回合可用，是否有 2 费，是否需要目标。
- 动作序列中的费用累计不能超过当前可用法力。
- 动作序列中同一张手牌不能被重复打出。
- 动作序列中同一个攻击者的累计攻击次数不能超过 `max_attacks_per_turn`。
- AI 返回的动作必须能在合法动作候选中找到，或能被后端校验为合法。

### 19.2 合法动作分类

后端应统一生成 `LegalAction`，暂分为 6 类：

```text
play_card
minion_attack
hero_attack
hero_power
activate_ability
end_turn
```

#### play_card

表示从手牌打出一张牌。

```json
{
  "type": "play_card",
  "source": 42,
  "card_id": "CS2_029",
  "name": "Fireball",
  "cost": 4,
  "target_required": true,
  "possible_targets": ["enemy_hero", 101, 102]
}
```

基础合法性：

- 卡牌必须在我方手牌。
- `cost <= remaining_mana`。
- 随从牌需要我方场上有空位。
- 需要目标的卡必须提供合法目标。
- 目标候选由后端生成，AI 不应凭空创建目标。

#### minion_attack

表示我方随从攻击敌方随从或英雄。

```json
{
  "type": "minion_attack",
  "source": 100,
  "target": "enemy_hero",
  "damage": 3
}
```

基础合法性：

- 攻击者在我方场面。
- `attacks_this_turn < max_attacks_per_turn`。
- 攻击力大于 0。
- 未被冻结、未休眠、未被标记不能攻击或已疲惫。
- 目标必须可被攻击。
- 敌方有嘲讽时，目标必须是嘲讽随从。

#### hero_attack

表示我方英雄攻击。

```json
{
  "type": "hero_attack",
  "source": "my_hero",
  "target": 201,
  "damage": 3
}
```

基础合法性与随从攻击类似，但还要提示 AI 考虑己方血量风险，因为英雄攻击随从会承受反伤。

#### hero_power

表示使用英雄技能。

```json
{
  "type": "hero_power",
  "source": "my_hero_power",
  "cost": 2,
  "target": "enemy_hero",
  "priority": "low"
}
```

基础合法性：

- 当前剩余法力至少 2。
- 本回合英雄技能未使用。
- 如果职业技能需要目标，目标必须在 `possible_targets` 内。

英雄技能默认低优先级，但在补足斩杀、费用刚好剩余、法师补刀、猎人抢血、术士找牌、牧师保命等情况下可以提高策略价值。

#### activate_ability

表示泰坦、地点或可激活实体能力。

```json
{
  "type": "activate_ability",
  "source": 300,
  "ability_id": "titan_1",
  "target": 201
}
```

第一阶段可以先预留结构，不要求完整支持。后续接入时需要记录能力是否可用、是否已使用、剩余次数和目标候选。

#### end_turn

表示结束回合。

```json
{
  "type": "end_turn"
}
```

`end_turn` 始终作为合法动作候选。部分局面下最优策略可能是保留资源、不破奥秘、不送随从或等待更好时机。

### 19.3 合法动作序列

单个动作不等于最终建议。后端还需要生成 `LegalSequence`，表示当前回合可以执行的一组动作。

```json
{
  "type": "sequence",
  "sequence_id": "seq-001",
  "total_cost": 6,
  "remaining_mana": 0,
  "actions": [
    {
      "type": "play_card",
      "source": 42,
      "target": "enemy_hero"
    },
    {
      "type": "minion_attack",
      "source": 100,
      "target": "enemy_hero"
    },
    {
      "type": "hero_power",
      "target": "enemy_hero"
    }
  ],
  "tags": ["burn_plan", "face_pressure"],
  "estimated_result": {
    "enemy_hero_damage": 9,
    "spends_all_mana": true,
    "requires_ai_effect_reasoning": false
  }
}
```

序列生成原则：

- 优先枚举费用范围内的 `play_card` 组合。
- 再加入场面交互：`minion_attack`、`hero_attack`。
- 最后考虑 `hero_power`，除非它能补足斩杀或解决关键目标。
- 每加入一个动作，都要更新 `remaining_mana`、已使用手牌、已攻击实体、英雄技能使用状态。
- 序列中的每一步都必须满足当前序列状态下的硬规则。
- 第一阶段可以只做粗粒度序列，不完整模拟复杂触发效果。

当前不要求后端穷举所有复杂牌序。序列数量需要限制，例如：

```text
max_card_combinations = 128
max_sequences_for_prompt = 20
max_actions_per_sequence = 6
```

后端应优先保留高价值候选：

- 可能斩杀。
- 能过嘲讽。
- 能清理高威胁随从。
- 能保留或提升场攻。
- 能用完费用且不明显亏节奏。
- 能回血或降低己方死亡风险。

### 19.4 给 AI 的输入不是完整模拟结果，而是候选空间

Prompt 不应要求 AI 从原始 JSON 自己推导所有合法动作。后端应该把候选空间整理好：

```json
{
  "constraints": {
    "available_mana": 7,
    "enemy_taunts": [201],
    "enemy_hero_health_total": 12,
    "my_hero_health_total": 8
  },
  "legal_actions": {
    "play_card": [],
    "minion_attack": [],
    "hero_attack": [],
    "hero_power": [],
    "activate_ability": [],
    "end_turn": []
  },
  "legal_sequences": [],
  "heuristics": {
    "board_face_damage": 4,
    "spell_damage_modifier": 1,
    "possible_burn_damage": 7,
    "has_taunt_blocker": true
  }
}
```

AI 的职责是基于这些候选动作和策略原则选择最优路线，而不是凭空创建未校验动作。

### 19.5 Prompt 策略原则

Prompt Builder 应固定注入以下原则：

```text
你只能从后端提供的 legal_actions 或 legal_sequences 中选择动作。
如果存在明确斩杀，优先斩杀。
如果敌方有嘲讽，不能推荐直接攻击英雄，除非序列已经先处理嘲讽。
出牌必须满足费用限制，动作序列总费用不能超过当前可用法力。
通常优先考虑手牌能否制造斩杀、解关键场面或形成强节奏。
然后考虑随从和英雄攻击等场面交互。
英雄技能一般低优先级，除非补足斩杀、补刀、回血保命、抽牌找关键牌或费用正好剩余。
如果己方血量危险，优先考虑防守、回血、解高攻随从和降低下回合死亡风险。
如果当前套牌或局面明显偏抢血/斩杀，可以降低普通随从交换优先级。
不允许声称知道对手隐藏手牌。
不允许建议自动操作客户端。
```

### 19.6 AI 输出后的二次校验

大模型返回后，后端必须再次校验：

- `play_card.source` 是否仍在手牌。
- 费用累计是否超过当前剩余法力。
- 攻击者是否满足 `attacks_this_turn < max_attacks_per_turn`。
- 目标是否在合法目标集合中。
- 是否违反嘲讽。
- 是否重复使用英雄技能。
- 是否包含不存在的实体 ID。
- 是否包含隐藏信息推断或自动操作表述。

如果校验失败：

```text
1. 丢弃非法动作。
2. 标记 recommendation.validation_status = "failed"。
3. 记录失败原因到 recommendations.jsonl 或 fallbacks.jsonl。
4. fallback 到规则引擎建议或重新要求 AI 只在候选动作中选择。
```

### 19.7 Recommendation 结构扩展

后续 `Recommendation` 建议扩展为结构化动作输出：

```json
{
  "game_id": "2026-06-01-001",
  "snapshot_timestamp": "2026-06-01T20:15:31.000+08:00",
  "plan": "lethal | clear_taunt | stabilize | pressure | value | end_turn",
  "chosen_sequence_id": "seq-001",
  "actions": [
    {
      "type": "play_card",
      "source": 42,
      "target": "enemy_hero"
    }
  ],
  "reasoning_summary": "当前手牌直伤加场攻足够斩杀。",
  "risks": ["如果法术目标限制识别错误，需要人工复核。"],
  "confidence": "medium",
  "validation_status": "passed"
}
```

UI 可以展示自然语言解释，但后端和测试应优先保存结构化动作，方便校验和回放。

### 19.8 当前实现修订：后端只生成合法动作空间

当前版本不再让后端规则引擎选择 `lethal`、`clear_taunt`、`pressure` 等最终路线。后端的职责边界调整为：

- `ActionPlanner` 负责从公开状态中生成合法候选动作。
- `RecommendationEngine` 只作为接口门面，返回 `plan = "action_space"`。
- `actions` 固定为空数组，表示后端没有替 AI 选择动作。
- `details.action_space` 包含出牌、攻击、英雄技能、结束回合和候选序列。
- AI 根据 `details.action_space`、局面摘要和策略原则自行决策。
- 后端只在 AI 返回后做合法性校验，不做“哪个动作最好”的判断。

兼容现有前端和接口命名，接口仍可叫 `/api/recommendation`，但语义已经变成“生成给 AI 决策用的合法动作空间”。

当前返回结构示例：

```json
{
  "plan": "action_space",
  "summary": "Legal action space generated. AI must choose the final line.",
  "actions": [],
  "confidence": 0.0,
  "details": {
    "decision_owner": "ai",
    "backend_scope": "legal_action_generation_only",
    "action_space": {
      "available_mana": 7,
      "playable_cards": [],
      "card_combinations": [],
      "legal_attacks": [],
      "hero_power": {},
      "legal_actions": {
        "play_card": [],
        "minion_attack": [],
        "hero_attack": [],
        "hero_power": [],
        "activate_ability": [],
        "end_turn": []
      },
      "legal_sequences": []
    }
  }
}
```

英雄技能也按合法动作统一进入动作空间，而不是只处理少数能造成伤害的职业。当前识别的基础英雄技能包括：

| 职业 | effect.kind | 说明 |
| --- | --- | --- |
| HUNTER | `damage_enemy_hero` | 对敌方英雄造成 2 点伤害 |
| MAGE | `damage` | 造成 1 点伤害，包含可选目标 |
| PRIEST | `restore_health` | 恢复 2 点生命，包含可选目标 |
| WARRIOR | `gain_armor` | 获得 2 点护甲 |
| WARLOCK | `draw_card_self_damage` | 抽 1 张牌并受到 2 点伤害 |
| PALADIN | `summon_minion` | 召唤 1 个 1/1 白银之手新兵 |
| SHAMAN | `summon_totem` | 召唤 1 个基础图腾 |
| ROGUE | `equip_weapon` | 装备 1/2 匕首 |
| DRUID | `attack_and_armor` | 本回合获得 1 点攻击力和 1 点护甲 |
| DEMONHUNTER | `attack_gain` | 本回合获得 1 点攻击力 |
| DEATHKNIGHT | `summon_ghoul` | 召唤 1 个 1/1 冲锋食尸鬼 |

注意：这些英雄技能目前是基础结构化枚举，不等于完整模拟所有衍生英雄技能、任务奖励英雄技能或卡牌改写后的英雄技能。后续如果 HDT 快照能提供更精确的英雄技能卡牌 ID，应优先以卡牌 ID 和文本识别结果覆盖职业默认值。

### 19.9 日志策略修订：动作空间不作为推荐落盘

当前 `plan = "action_space"` 的结果只是给 AI 决策使用的中间数据，不再写入 `recommendations.jsonl`。

日志边界调整为：

- `game_state.jsonl`：继续记录 HDT 采集到的公开状态。
- `events.jsonl`：继续记录对局事件，例如出牌、抽牌、投降、结束。
- `recommendations.jsonl`：只记录 AI 已经选择出的最终打法，不记录后端枚举的动作空间。

这样可以避免网页端轮询 `/api/recommendation` 时不断把大体积 `action_space` 写入磁盘。等 AI 推荐链路接入后，只有类似下面这种“已选择路线”的结果才写入推荐日志：

```json
{
  "plan": "ai_selected_line",
  "chosen_sequence_id": "seq-003",
  "actions": [
    {"type": "play_card", "source": 42, "target": "enemy_hero"},
    {"type": "minion_attack", "source": 100, "target": "enemy_hero"}
  ],
  "validation_status": "passed"
}
```

### 19.10 接入过滤器：事件去重与历史回放保护

后端接入层增加 `IngestFilter`，在消息进入 `StateStore`、`ReplayWriter` 和 UI 广播之前先过滤异常输入。

### 19.11 当前实现修订：AI 决策接口接入

当前版本在动作空间接口之外新增独立 AI 决策接口：

```text
POST /api/ai/decision
```

职责边界：

- `/api/recommendation` 继续只返回 `plan = "action_space"`，用于查看后端枚举出的合法动作空间。
- `/api/ai/decision` 读取当前 `StateStore.latest_state`，内部调用 `RecommendationEngine` 生成 `action_space`。
- `DecisionPromptBuilder` 将当前局面、手牌/场面卡牌文本、`legal_actions`、`legal_sequences` 和策略原则整理成 prompt。
- `DecisionPromptBuilder` 要求模型用中文回答，并且只能基于传入的 `game_state`、`action_space` 和卡牌文本推理。
- `LangChainDeepSeekDecisionClient` 通过 LangChain 调用 DeepSeek v4 flash，并优先使用流式输出拼接模型结果；没有配置 `DEEPSEEK_API_KEY` 时返回 `plan = "unavailable"`。
- `AiDecisionService` 使用 `RecommendationValidator` 校验 AI 返回的 `chosen_sequence_id` 是否存在于 `legal_sequences`。
- 只有校验通过的 `plan = "ai_decision"` 会写入 `recommendations.jsonl`；`action_space`、`unavailable`、`ai_decision_rejected` 不作为最终推荐落盘。
- 动作空间包含 `trade_card`，用于表示可交易牌的 1 费换牌动作；武器牌会携带攻击、耐久和文本，并避免在同一序列里用盗贼英雄技能覆盖刚装备的武器。

AI 输出结构：

```json
{
  "chosen_sequence_id": "seq-003",
  "reason": "先用手牌处理嘲讽，再用场攻压低敌方英雄血量。",
  "risk": "如果对手下回合有群体解场，场面压力会下降。",
  "confidence": 0.72
}
```

后端返回结构：

```json
{
  "plan": "ai_decision",
  "summary": "先用手牌处理嘲讽，再用场攻压低敌方英雄血量。",
  "chosen_sequence_id": "seq-003",
  "actions": [],
  "risk": "如果对手下回合有群体解场，场面压力会下降。",
  "confidence": 0.72,
  "validation": {
    "validation_status": "passed"
  }
}
```

推荐日志中，成功决策的 `summary` 已经表达模型理由，因此不再重复记录
同值的 `reason`。校验通过时只记录 `validation_status = "passed"`；
校验失败时仍保留具体 `validation.reason`。

前端不再依赖点击 `AI Decision` 按钮后才调用 AI。当前实现改为后端自动触发：

- 我方回合开始且法力水晶已经刷新后，自动调用一次 AI。
- 对手回合中，如果检测到对方法力为 0，且敌方英雄和场面随从都没有可见攻击动作，则提前构造“即将轮到我方”的预测状态，并预热调用 AI。
- 预热调用只用于提前拿到可用决策和推送 UI，不写入 `recommendations.jsonl`，避免把预测结果当成真实回合最终推荐。
- 正式我方回合触发且校验通过的 `plan = "ai_decision"` 才写入 `recommendations.jsonl`。
- 所有真实模型调用只在 `debug/ai_requests/` 下写入一份格式化 JSON，
  并通过 `metadata.trigger` 区分 `own_turn`、`hand_increased` 与
  `opponent_spent_out`，不再生成 `ai_decision_attempts.jsonl`。

AI 调试 JSON 保留结构化 `payload`、系统提示词、模型配置、解析后的模型
输出、校验后决策和性能诊断。不重复保存 `user_prompt`、
`model_request.messages`、`raw_model_content` 或原始响应文本副本。

低延迟配置：

- 默认模型：`deepseek-v4-flash`。
- 默认超时：`AI_DECISION_TIMEOUT_SECONDS=15`。
- 默认开启流式：`AI_DECISION_STREAMING=true`。
- 默认只把较小候选序列集送入 prompt，减少模型输入体积。

当前过滤规则：

- 同一对局内，如果 `game_state.turn` 小于已经看到的最高回合，认为是历史回放，丢弃。
- 同一对局内，如果 `game_event.turn` 小于已经看到的最高回合，认为是历史事件回放，丢弃。
- `game_started` 会重置过滤器状态，允许新对局从低回合重新开始。
- 事件去重使用事件指纹，排除 `timestamp`，避免 HDT 重发同一事件但带新时间戳时重复写入。

被过滤的消息不会写入：

- `game_state.jsonl`
- `events.jsonl`
- UI 实时状态

WebSocket 仍会返回 ack，并带上：

```json
{
  "type": "ack",
  "filtered": true
}
```

这样插件端不会因为过滤而误判连接失败。
## 19.12 当前实现修订：套牌意识与对局预案上下文

当前版本新增 `matchup_context`，用于把“对手职业 + 已见卡牌 + 当前回合节奏 + 场面压力”整理成给 AI 使用的对局背景。它不替代合法动作生成，也不替代 AI 最终决策，只作为 prompt 中的概率化策略提示。

实现边界：

- 后端只根据公开信息构建套牌意识，包括 `enemy_hero.class`、`known_enemy_cards`、`enemy_board` 和 `recent_events`。
- 不推断对手隐藏手牌，不声明已经确定对手套牌。
- `MatchupContextBuilder` 内部可以保留多个候选原型用于打分，但 `DecisionPromptBuilder` 发送给模型时只保留一个 `matchup_context.identified_enemy_deck`，避免多候选反复影响模型判断。
- 每个原型包含 `confidence`、`evidence`、`win_condition`、`core_cards` 和 `game_plan_against_it`，让 AI 结合当前场面、费用、合法动作、对手赢法和核心牌时机判断该抢血、解场还是保留资源。
- Prompt 明确要求：套牌意识只能作为概率提示，当前场面、费用、卡牌文本和 `legal_sequences` 优先级更高。

当前采用外部文件唯一数据源模式：

```text
hearthstone_data/decks/strategy_context.zhCN.json
  -> MatchupContextBuilder
  -> DecisionPromptBuilder
  -> LangChain 模型决策
```

外部环境文件结构示例：

```json
{
  "HUNTER": [
    {
      "name": "快攻猎",
      "style": "aggro",
      "base_confidence": 0.34,
      "signals": ["奥术射击", "杀戮命令", "野兽"],
      "win_condition": "前期持续压血，后期用直伤和英雄技能完成斩杀。",
      "core_cards": [
        {
          "name": "低费攻击源",
          "role": "前期持续伤害",
          "play_timing": "前两回合优先按费打出，建立可重复攻击点。",
          "keep_condition": "起手优先保留。",
          "counter_priority": "优先解能持续攻击或吃增益的随从。"
        }
      ],
      "strategy_detail_level": "representative_core",
      "game_plan": "默认尊重抢血压力；如果我方速度慢于对手，优先处理关键场面和攻击源，再寻找反杀窗口。"
    }
  ]
}
```

这意味着后续如果要更新环境套牌，只需要维护 `hearthstone_data/decks/strategy_context.zhCN.json` 中的 `deck_archetypes`，不必改 prompt 主逻辑，也不需要修改代码里的套牌表。`deck_archetypes` 按职业分组，每个职业保留 1-3 套代表性套牌，己方和对手都读取同一份套牌数据。

当前代码不再保存任何内置职业原型表。若该文件不存在、JSON 无效或没有可用职业列表，`MatchupContextBuilder` 会返回空的候选结果，并在 `meta_source.status` 中标记 `missing`、`invalid` 或 `empty`。这种情况下 AI 仍然可以根据当前场面、费用、卡牌文本和 `legal_sequences` 决策，但不会获得环境套牌候选提示。

`DecisionPromptBuilder` 还会向模型发送 `action_space.lethal_sequence_ids`。只要该列表非空，提示词要求模型必须从其中选择，不能为了资源、解场或站场放弃合法斩杀。

## 19.13 当前实现修订：己方套牌核心赢法与核心牌时机

己方套牌策略不再依赖模型仅凭当前手牌猜测。HDT 开局发送
`game_metadata.deck`，后端通过 `StateStore.decision_state()` 将完整套牌元数据合并到
自动和手动 AI 决策状态中。

`DeckStrategyContextBuilder` 从
`hearthstone_data/decks/strategy_context.zhCN.json` 中的 `deck_archetypes` 加载外部策略。它和 `MatchupContextBuilder` 使用同一份套牌数据；区别只是己方按实际套牌名和签名卡匹配，对手按职业和已见公开卡牌打分。匹配顺序为：

1. 职业和模式兼容。
2. 套牌名称精确匹配。
3. 核心签名卡命中和完整卡组重合度匹配。
4. 匹配失败时仍发送完整套牌和卡牌文本，由模型进行临时分析，同时标记
   `analysis_required=true`，不伪造已确认的核心牌。

写入 prompt 的 `my_deck_context` 包含实际套牌、匹配置信度、核心赢法、
`core_cards[].role`、`play_timing`、`keep_condition` 和
`burst_exception`。套牌策略仍不能覆盖费用、目标、嘲讽、免疫和合法动作约束。

Prompt 新增通用优先级：非爆发斩杀或组合套牌，前期同费用下通常优先让随从占场，
保留能够持续攻击的己方随从，而不是无收益地消耗法术。只有高收益交换、保护英雄、
阻断关键机制或建立明确斩杀时才降低场面优先级；爆发套牌则按核心赢法保留伤害和组合组件。

网页端采用顶部主推荐布局：推荐区占四列网格中的三列，右侧集中显示回合、法力和双方英雄；
推荐区额外展示己方套牌、核心赢法和核心牌出牌时机。
