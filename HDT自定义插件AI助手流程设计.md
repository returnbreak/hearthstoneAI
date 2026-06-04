# HDT 自定义插件 AI 助手流程设计

## 1. 文档目的

本文档基于《HDT 自定义插件路线：炉石 AI 助手架构设计》整理具体流程设计，目标是把系统从“架构模块”细化为可执行的端到端流程。

当前只规划两版：

- 第一版：`HDT 插件 + FastAPI + 普通规则引擎 + LangChain 调模型`。
- 第二版：`JSON Schema 校验 + fallback + JSONL 回放测试`。

第二版不把 RAG 作为必做项。卡牌文本和套牌信息优先使用结构化数据库直查；只有人工策略笔记、历史错误案例等非结构化内容，才作为可选受控上下文检索。

## 2. 设计边界

系统只做只读建议，不做自动出牌。

允许：

- 使用 HDT 已解析的日志和实体状态。
- 读取当前可见的手牌、场面、英雄血量、费用、已打出卡牌等公开状态。
- 保存 `GameState`、`GameEvent`、AI 建议和错误样本，用于调试和回放测试。
- 通过本地网页展示建议。

不允许：

- 自动点击、自动拖拽、自动出牌。
- 读取炉石客户端内存。
- 修改炉石客户端。
- 推断或记录对手未公开手牌的具体卡名。
- 让模型声称知道隐藏信息。
- 联网检索实时攻略作为实时建议依据。

## 3. 总体流程

```text
Hearthstone 客户端
  -> Power.log
  -> Hearthstone Deck Tracker
  -> 自定义 HDT 插件
  -> GameEvent / GameState
  -> FastAPI 后端
  -> 卡牌数据增强
  -> 普通规则引擎
  -> Prompt 构造
  -> LangChain 调用 LLM
  -> Recommendation
  -> 本地网页 UI
```

第二版在后端建议链路中增加：

```text
LLM 输出
  -> JSON Schema 校验
  -> 安全边界校验
  -> 失败时 fallback 到规则引擎建议
  -> JSONL 回放测试
```

可选增强：

```text
受控上下文检索
  -> 套牌说明
  -> 人工策略笔记
  -> 历史错误案例
```

## 4. 参与模块

| 模块 | 职责 | 第一版 | 第二版 |
| --- | --- | --- | --- |
| HDT 插件 | 监听事件、读取实体、构建状态、推送后端 | 必做 | 沿用 |
| FastAPI 后端 | 接收 WebSocket、维护状态、提供 UI/API | 必做 | 沿用 |
| 卡牌数据增强 | 通过 HearthstoneJSON 补充卡牌文本 | 必做 | 沿用 |
| 规则引擎 | 斩杀、费用、场面危险度等确定性分析 | 必做 | 沿用 |
| LangChain 客户端 | 封装模型调用和 Prompt 输入 | 必做 | 沿用 |
| Schema 校验器 | 校验 Recommendation JSON | 不做 | 必做 |
| fallback | 模型失败时返回规则建议 | 不做 | 必做 |
| JSONL 回放器 | 离线重放对局状态，测试建议链路 | 不做 | 必做 |
| 可选上下文检索 | 补充套牌说明、策略笔记、历史错误案例 | 不做 | 可选 |

## 5. 第一版流程

### 5.1 启动流程

```text
1. 用户启动 Hearthstone Deck Tracker
2. HDT 加载自定义插件
3. 用户启动 Python FastAPI 后端
4. 后端监听：
   - WebSocket: ws://127.0.0.1:8765/ws/hdt
   - 本地网页: http://127.0.0.1:8765
5. 插件尝试连接后端 WebSocket
6. 连接成功后进入待机状态
```

异常处理：

- 后端未启动：插件不崩溃，定时重连。
- WebSocket 中断：插件缓存最近状态，恢复连接后推送最新快照。
- 插件加载失败：写入本地插件日志，提示用户检查 HDT 插件目录和依赖。

### 5.2 对局采集流程

```text
1. 炉石客户端产生 Power.log
2. HDT 解析 Power.log 并维护内部 Game 状态
3. 插件监听 HDT 游戏事件
4. 插件在关键事件发生时读取实体集合
5. 插件构建 GameEvent
6. 插件构建 GameState Snapshot
7. 插件通过 WebSocket 推送到 FastAPI 后端
8. 后端写入 events.jsonl 和 game_state.jsonl
```

关键触发事件：

- 游戏开始。
- 起手换牌开始和结束。
- 回合开始。
- 我方可行动。
- 抽牌。
- 出牌。
- 攻击。
- 随从进入或离开场面。
- 英雄血量或护甲变化。
- 游戏结束。

节流原则：

- 不把每次 `OnUpdate()` 都发给后端。
- 只在状态发生实质变化时推送。
- 同一状态哈希不重复触发 AI 请求。
- 同一回合最多自动触发 2 次 AI 请求。

### 5.3 状态增强流程

```text
1. 后端接收 GameState
2. 后端读取 hand、board、graveyard、known_enemy_cards 等字段
3. 使用 card_id 或 dbf_id 查询 HearthstoneJSON
4. 补充中文名、费用、类型、职业、效果文本
5. 未匹配卡牌标记 unknown_card
6. 输出 EnrichedGameState
```

卡牌数据增强使用结构化直查，不使用 RAG。

输入示例：

```json
{
  "card_id": "CS2_029",
  "dbf_id": 315,
  "name": "Fireball"
}
```

输出示例：

```json
{
  "card_id": "CS2_029",
  "dbf_id": 315,
  "name": "火球术",
  "cost": 4,
  "type": "SPELL",
  "text": "造成6点伤害。"
}
```

### 5.4 规则引擎流程

```text
1. 输入 EnrichedGameState
2. 计算当前可用费用
3. 枚举当前可打出的手牌
4. 汇总可用直伤、回血、解场、站场资源
5. 判断是否存在简单斩杀
6. 判断是否需要优先解嘲讽
7. 判断是否有明显费用浪费
8. 输出 rule_analysis
```

第一版规则引擎只做确定性和粗粒度判断，不尝试完整搜索所有复杂牌序。

输出示例：

```json
{
  "has_lethal": false,
  "playable_cards": ["火球术", "奥术智慧"],
  "direct_damage_available": 6,
  "enemy_hp_after_best_burn": 11,
  "must_clear_taunt": false,
  "mana_warning": "使用火球术后剩余2费",
  "risk_notes": ["敌方场面压力较低，可以考虑抢血"]
}
```

### 5.5 AI 建议流程

```text
1. 后端判断是否需要调用 AI
2. 构造紧凑 Prompt
3. Prompt 包含：
   - 当前 GameState 摘要
   - 卡牌增强文本
   - rule_analysis
   - 安全边界要求
4. LangChain 调用 LLM
5. 解析模型输出
6. 生成 Recommendation
7. 推送给 UI
```

第一版可以只做轻量解析，不做严格 JSON Schema 校验。如果模型输出格式异常，可以显示“建议生成失败，请重新分析”，但不要求完整 fallback。

Prompt 约束：

- 最多输出 1-3 条建议。
- 每条建议必须包含理由和风险。
- 必须说明不确定性。
- 不得声称知道对手隐藏手牌。
- 不得输出自动操作指令。

### 5.6 UI 展示流程

```text
1. 用户打开 http://127.0.0.1:8765
2. UI 建立 WebSocket 连接
3. 后端广播最新 GameState 摘要和 Recommendation
4. UI 显示：
   - 当前回合
   - 我方手牌
   - 双方场面
   - 双方英雄血量
   - 推荐操作
   - 理由
   - 风险
   - 置信度
5. 用户可点击“重新分析”
6. 后端对当前状态重新运行建议链路
```

UI 不遮挡游戏，不自动操作游戏。

## 6. 第二版流程

第二版目标不是扩展复杂策略，而是提高第一版的稳定性、可测性和安全边界。

### 6.1 Recommendation Schema 校验流程

```text
1. LangChain 返回模型输出
2. 后端尝试解析为 JSON
3. 使用 Recommendation JSON Schema 校验字段
4. 校验成功则进入安全边界检查
5. 校验失败则进入 fallback
```

Recommendation 必需字段：

```json
{
  "summary": "string",
  "actions": [
    {
      "rank": "number",
      "title": "string",
      "steps": ["string"],
      "reason": "string",
      "risk": "string"
    }
  ],
  "warnings": ["string"],
  "confidence": "low | medium | high"
}
```

校验规则：

- `actions` 最多 3 条。
- `rank` 必须从 1 开始。
- `title`、`reason`、`risk` 不得为空。
- `confidence` 只能是 `low`、`medium`、`high`。
- 输出不得包含自动点击、自动出牌、读取内存等越界内容。

### 6.2 安全边界检查流程

```text
1. 输入已通过 Schema 校验的 Recommendation
2. 检查是否声称知道对手隐藏手牌
3. 检查是否建议自动操作客户端
4. 检查是否使用未公开信息作为理由
5. 检查是否超过建议数量上限
6. 通过则推送 UI
7. 失败则进入 fallback
```

越界示例：

- “对手手里应该有杀戮命令，所以……”
- “自动点击火球术并拖到敌方英雄。”
- “读取对手手牌后可以发现……”

允许表达：

- “对手上回合已经打出杀戮命令。”
- “对手手牌数量为 4，但具体内容未知。”
- “如果对手有回血或嘲讽，下回合计划会变差。”

### 6.3 fallback 流程

```text
触发条件：
  - LLM 超时
  - LLM 返回非 JSON
  - Schema 校验失败
  - 安全边界检查失败
  - LangChain 调用异常

处理流程：
1. 记录失败原因
2. 读取 rule_analysis
3. 生成规则引擎建议
4. 标记 confidence = low
5. 标记 source = rule_fallback
6. 推送 UI
```

fallback 输出示例：

```json
{
  "summary": "模型建议不可用，当前展示规则引擎建议。",
  "actions": [
    {
      "rank": 1,
      "title": "考虑火球术攻击敌方英雄",
      "steps": ["使用火球术指向敌方英雄"],
      "reason": "规则引擎检测到可制造血量压力。",
      "risk": "该建议未经过大模型解释排序。"
    }
  ],
  "warnings": ["当前为 fallback 建议，置信度较低。"],
  "confidence": "low",
  "source": "rule_fallback"
}
```

### 6.4 JSONL 回放测试流程

```text
1. 读取 events.jsonl 和 game_state.jsonl
2. 按 game_id 分组
3. 按 timestamp 排序
4. 重建每个时间点的 GameState
5. 找出每个我方可行动回合
6. 对每个快照运行完整建议链路
7. 记录 LLM 成功、Schema 错误、安全错误、fallback 次数
8. 输出 replay_test_report.md
```

回放测试不依赖实时炉石，也不要求启动 HDT。

回放报告应包含：

- 总测试快照数量。
- 成功生成建议数量。
- fallback 次数。
- Schema 错误样本。
- 越界输出样本。
- 平均建议耗时。
- 最慢建议样本。
- 需要修复的 Prompt 或规则问题。

### 6.5 可选受控上下文检索流程

该能力不是第二版验收前提。

只有当第一版和第二版基础可靠后，才考虑加入。

允许的数据源：

- `deck_db`：当前套牌说明、套牌职业、套牌曲线。
- `strategy_notes`：人工整理的少量策略笔记。
- `replay_notes`：JSONL 回放测试中沉淀的错误案例和修正说明。

不建议的数据源：

- 通用向量库大规模攻略。
- 联网搜索。
- 环境文章批量检索。
- 未验证的玩家评论。

可选检索流程：

```text
1. 根据职业、套牌 ID、当前回合和关键卡牌生成检索 query
2. 从受控数据源检索最多 3 条短上下文
3. 对上下文做长度限制和来源标记
4. 只作为 Prompt 的辅助背景
5. 不允许覆盖 GameState 和 rule_analysis
6. 如果检索为空，直接跳过
```

## 7. 状态哈希和触发条件

### 7.1 状态哈希字段

用于去重和节流：

```text
turn
active_player
mana.current
mana.max
my_hero.hp
my_hero.armor
enemy_hero.hp
enemy_hero.armor
hand card_id list
my_board entity summary
enemy_board entity summary
```

同一哈希不重复请求 LLM。

### 7.2 自动触发条件

自动触发 AI 建议：

- 我方回合开始。
- 敌方回合结束后进入我方可行动状态。
- 我方手牌、费用或场面发生重大变化。

不自动触发：

- 对手回合中普通动画变化。
- UI 刷新。
- 重复 `OnUpdate()`。
- 状态哈希无变化。

手动触发：

- 用户点击“重新分析”。

## 8. 日志和文件输出

第一版输出：

```text
data/game_logs/events.jsonl
data/game_logs/game_state.jsonl
data/game_logs/backend.log
```

第二版新增：

```text
data/game_logs/recommendations.jsonl
data/game_logs/fallbacks.jsonl
data/replays/replay_test_report.md
```

建议记录字段：

```json
{
  "game_id": "string",
  "snapshot_timestamp": "string",
  "generated_at": "string",
  "state_hash": "string",
  "source": "llm | rule_fallback",
  "latency_ms": 1200,
  "recommendation": {}
}
```

## 9. 异常处理矩阵

| 异常 | 第一版处理 | 第二版处理 |
| --- | --- | --- |
| 后端未启动 | 插件重连，不崩溃 | 同第一版 |
| WebSocket 断开 | 插件缓存最新状态 | 同第一版 |
| 卡牌数据缺失 | 标记 `unknown_card` | 同第一版，并记录错误样本 |
| 规则引擎异常 | 返回基础状态摘要 | 进入 fallback 错误报告 |
| LLM 超时 | 显示建议生成失败 | fallback 到规则建议 |
| LLM 非 JSON | 显示建议生成失败 | Schema 失败并 fallback |
| 越界输出 | 第一版依赖 Prompt 约束 | 安全检查失败并 fallback |
| UI 断开 | 后端继续维护状态 | 同第一版 |

## 10. 第一版验收清单

- HDT 插件能加载。
- 插件能监听游戏开始、回合开始、出牌、游戏结束事件。
- 插件能构建基础 `GameState`。
- 插件能连接 FastAPI WebSocket。
- 后端能保存 `events.jsonl` 和 `game_state.jsonl`。
- 后端能补充卡牌中文名、费用、类型和效果文本。
- 规则引擎能输出基础 `rule_analysis`。
- LangChain 能调用模型生成建议。
- 本地网页能显示当前状态和 1-3 条建议。
- 系统不依赖截图。
- 系统不自动出牌。

## 11. 第二版验收清单

- 定义并启用 `Recommendation` JSON Schema。
- LLM 输出格式错误时能被拦截。
- 越界输出能被拦截。
- LLM 超时时能 fallback 到规则引擎建议。
- fallback 建议能正常显示到 UI。
- JSONL 回放测试能重放至少一局对局。
- `replay_test_report.md` 能列出成功、失败和 fallback 样本。
- 第二版不要求通用 RAG。
- 可选上下文检索不影响基础建议链路。

## 12. 推荐实现顺序

```text
1. FastAPI WebSocket 骨架
2. HDT 插件连接和状态推送
3. JSONL 保存
4. 卡牌数据增强
5. 普通规则引擎
6. LangChain 模型调用
7. 本地网页 UI
8. Recommendation Schema
9. fallback
10. JSONL 回放测试
11. 可选受控上下文检索
```

第一版完成 1-7。

第二版完成 8-10。

第 11 项只在确实需要时再做。
## 13. 补充：随从交互流程

当前第一版流程需要补充随从交互信息。插件层在构建 `GameState` 时，不只输出双方场面随从的攻血，还要输出影响交换判断的公开关键词和攻击状态。

插件采集流程补充：

```text
1. 从 HDT 实体读取双方场面随从
2. 为每个随从补充攻击状态和关键词
3. 为双方英雄补充攻击状态
4. 输出扩展后的 GameState
5. 后端 combat_analyzer 根据 GameState 枚举合法攻击和交换候选
6. rule_engine 基于候选判断是否解场、打脸、保留场攻或寻找斩杀
7. LLM 只负责解释和排序，不直接凭空推演所有交换
```

新增随从字段：

```json
{
  "can_attack": true,
  "attacks_this_turn": 0,
  "attacks_remaining": 1,
  "zone_position": 2,
  "taunt": true,
  "divine_shield": false,
  "stealth": false,
  "immune": false,
  "frozen": false,
  "rush": false,
  "charge": false,
  "windfury": false,
  "mega_windfury": false,
  "lifesteal": false,
  "poisonous": false,
  "venomous": false,
  "reborn": false,
  "deathrattle": false,
  "dormant": false,
  "silenced": false,
  "cant_attack": false
}
```

新增英雄字段：

```json
{
  "attack": 3,
  "can_attack": true,
  "attacks_this_turn": 0,
  "attacks_remaining": 1,
  "immune": false,
  "frozen": false
}
```

边界保持不变：插件只采集 HDT 已解析的公开实体状态，不读取隐藏手牌，不自动攻击，不做自动出牌。

## 14. 补充：合法动作和动作序列生成流程

后续建议链路不应要求 AI 自己从原始局面里“猜”哪些动作合法。后端需要先根据公开状态生成合法动作和候选动作序列，再让 AI 在候选空间内做策略选择。

核心原则：

```text
代码负责硬规则和合法性。
AI 负责策略排序、复杂卡牌文本理解和解释。
代码最后再次校验 AI 输出。
```

### 14.1 合法动作生成流程

```text
1. 输入 EnrichedGameState
2. 读取 my_mana.current 作为 available_mana
3. 读取我方手牌、我方场面、敌方场面、双方英雄状态
4. 生成 play_card 候选
5. 生成 minion_attack 候选
6. 生成 hero_attack 候选
7. 生成 hero_power 候选
8. 预留 activate_ability 候选
9. 固定加入 end_turn
10. 输出 legal_actions
```

`legal_actions` 建议结构：

```json
{
  "play_card": [],
  "minion_attack": [],
  "hero_attack": [],
  "hero_power": [],
  "activate_ability": [],
  "end_turn": [
    {
      "type": "end_turn"
    }
  ]
}
```

### 14.2 play_card 合法性流程

```text
1. 遍历 hand
2. 检查 card.cost <= available_mana
3. 如果是随从牌，检查我方场面是否少于 7 个随从
4. 如果是需要目标的牌，根据卡牌文本和目标规则生成 possible_targets
5. 如果不需要目标，possible_targets 为空
6. 输出 play_card action
```

示例：

```json
{
  "type": "play_card",
  "source": 42,
  "card_id": "CS2_029",
  "name": "Fireball",
  "cost": 4,
  "target_required": true,
  "possible_targets": ["enemy_hero", 101, 102],
  "effect_summary": {
    "kind": "damage",
    "damage": 6
  }
}
```

第一阶段不需要完整模拟所有战吼、亡语、发现和随机效果，但需要把卡牌 `text`、费用、类型、候选目标和粗粒度效果给到 AI。

### 14.3 minion_attack 合法性流程

```text
1. 遍历 my_board
2. 检查 can_attack = true
3. 检查 attacks_remaining > 0
4. 检查 attack > 0
5. 排除 frozen、dormant、immune、cant_attack、exhausted
6. 读取 enemy_board 中可被攻击的随从
7. 如果敌方存在 taunt，只允许攻击 taunt 随从
8. 如果敌方不存在 taunt，允许攻击可见随从和非免疫敌方英雄
9. 输出 minion_attack action
```

示例：

```json
{
  "type": "minion_attack",
  "source": 100,
  "target": 201,
  "damage": 3,
  "target_type": "minion"
}
```

### 14.4 hero_attack 合法性流程

```text
1. 检查 my_hero.attack > 0
2. 检查 my_hero.can_attack = true
3. 检查 my_hero.attacks_remaining > 0
4. 排除 frozen、immune
5. 按嘲讽规则生成目标
6. 输出 hero_attack action
7. 在 action 中标记 self_damage_risk，供 AI 判断是否值得撞怪
```

示例：

```json
{
  "type": "hero_attack",
  "source": "my_hero",
  "target": 201,
  "damage": 3,
  "self_damage_risk": 5
}
```

### 14.5 hero_power 合法性流程

```text
1. 检查 remaining_mana >= 2
2. 检查本回合英雄技能是否已使用
3. 根据职业生成目标候选
4. 默认 priority = low
5. 如果能补足斩杀、补刀、回血保命或抽牌找关键牌，可以在 heuristics 中提高价值
```

示例：

```json
{
  "type": "hero_power",
  "source": "my_hero_power",
  "cost": 2,
  "target": "enemy_hero",
  "damage": 1,
  "priority": "low"
}
```

### 14.6 activate_ability 预留流程

`activate_ability` 用于泰坦技能、地点技能和其他可激活实体能力。第一阶段可以只预留结构，不要求完整实现。

后续实现时流程为：

```text
1. 扫描 my_board 和可见可激活实体
2. 判断 ability 是否可用
3. 判断本回合是否已经使用
4. 生成 possible_targets
5. 输出 activate_ability action
```

示例：

```json
{
  "type": "activate_ability",
  "source": 300,
  "ability_id": "titan_1",
  "possible_targets": [201, "enemy_hero"]
}
```

### 14.7 动作序列生成流程

动作序列必须考虑法力水晶、手牌消耗、攻击次数、英雄技能使用状态和目标合法性，不能简单把动作随意排列。

建议流程：

```text
1. 输入 legal_actions 和 EnrichedGameState
2. 初始化 sequence_state：
   - remaining_mana = my_mana.current
   - used_hand_entities = []
   - used_attack_counts = {}
   - hero_power_used = false
   - board_slots = 当前我方场面数量
3. 先枚举费用范围内的 play_card 组合
4. 对每个出牌组合更新 remaining_mana、used_hand_entities 和 board_slots
5. 在更新后的序列状态上追加 minion_attack / hero_attack 候选
6. 如果 remaining_mana >= 2 且 hero_power 未使用，再追加 hero_power 候选
7. 始终保留 end_turn
8. 对每条序列计算粗粒度 heuristics
9. 限制输出数量，保留价值最高的序列给 Prompt
```

序列状态示例：

```json
{
  "remaining_mana": 3,
  "used_hand_entities": [42],
  "used_attack_counts": {
    "100": 1
  },
  "hero_power_used": false,
  "board_slots": 4
}
```

候选序列示例：

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
  "heuristics": {
    "enemy_hero_damage": 9,
    "clears_taunt": false,
    "spends_all_mana": true,
    "protects_my_hero": false
  }
}
```

### 14.8 序列合法性约束

生成和校验动作序列时必须满足：

- `total_cost <= my_mana.current`。
- 同一手牌实体不能在同一序列中重复打出。
- 打出随从牌后，我方场面不能超过 7 个随从。
- 同一攻击者使用次数不能超过 `attacks_remaining`。
- 英雄技能同一序列最多使用一次。
- 敌方有嘲讽且序列尚未处理嘲讽时，不能攻击敌方英雄。
- 攻击目标不能是潜行、免疫、休眠或不存在的实体。
- AI 返回的动作目标必须在对应动作的 `possible_targets` 中。
- `end_turn` 只能作为序列最后一步。

第一阶段可以不完整模拟复杂触发效果，但不允许违反这些硬规则。

### 14.9 序列筛选原则

不应把所有组合都塞给 AI。后端应限制数量并优先保留：

```text
1. 可能斩杀的序列
2. 能先处理嘲讽再打脸的序列
3. 能解高威胁随从的序列
4. 能保护己方低血量英雄的序列
5. 能保持或提升场攻的序列
6. 能合理用完费用的序列
7. 能过牌寻找关键牌的序列
8. 资源保留价值较高的 end_turn 或低动作序列
```

建议限制：

```text
max_card_combinations = 128
max_sequences_for_prompt = 20
max_actions_per_sequence = 6
```

### 14.10 Prompt 构造流程补充

Prompt 不应只包含原始 `GameState`。应包含：

```text
1. 当前局面摘要
2. 手牌和场面卡牌文本
3. constraints
4. legal_actions
5. legal_sequences
6. heuristics
7. 策略原则
8. 安全边界
```

策略原则固定写入：

```text
只能从 legal_actions 或 legal_sequences 中选择动作。
如果存在明确斩杀，优先斩杀。
如果敌方有嘲讽，不能推荐直接攻击英雄，除非序列已经先处理嘲讽。
出牌和英雄技能必须满足费用限制。
通常先考虑手牌能否制造斩杀、解场、回血或形成强节奏。
然后考虑随从和英雄攻击。
英雄技能一般低优先级，除非补足斩杀、补刀、保命、抽牌或费用正好剩余。
如果己方血量危险，优先防守。
如果当前局面明显偏抢血/斩杀，可以降低普通随从交换优先级。
不能声称知道对手隐藏手牌。
不能建议自动操作客户端。
```

### 14.11 AI 输出校验流程

```text
1. 解析 AI 输出 JSON
2. 检查 chosen_sequence_id 是否存在
3. 如果 AI 输出自定义 actions，则逐项匹配 legal_actions
4. 重新计算费用、目标、攻击次数、英雄技能使用状态
5. 检查是否违反隐藏信息和自动操作边界
6. 通过则 validation_status = passed
7. 失败则 validation_status = failed，并进入 fallback 或重新请求 AI
```

校验失败记录示例：

```json
{
  "validation_status": "failed",
  "reason": "target enemy_hero is illegal while enemy taunt exists",
  "source": "llm",
  "fallback": "rule_engine"
}
```

### 14.12 推荐输出结构补充

后续 Recommendation 建议使用结构化动作，而不只是自然语言步骤：

```json
{
  "summary": "当前可以通过手牌直伤加场攻建立斩杀。",
  "plan": "lethal",
  "chosen_sequence_id": "seq-001",
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
    }
  ],
  "reasoning_summary": "火球术加随从攻击合计足够击杀敌方英雄。",
  "risks": ["如果卡牌文本识别错误，需要人工复核。"],
  "confidence": "medium",
  "validation_status": "passed"
}
```

UI 可以把结构化动作翻译成人类可读文本，但日志和回放测试应保留结构化动作，便于校验。

### 14.13 当前运行流程修订：先枚举，再交给 AI 决策

当前版本不再执行后端推荐逻辑。流程改为：

```text
1. HDT 插件采集公开对局状态
2. 后端 StateStore 保存最新状态
3. 前端或 AI 调用 /api/recommendation
4. RecommendationEngine 调用 ActionPlanner.generate(state)
5. 后端返回 plan = "action_space"
6. AI 从 details.action_space.legal_actions 或 legal_sequences 中选择路线
7. 后端校验 AI 返回动作是否合法
8. UI 展示 AI 选择和校验结果
```

也就是说，`/api/recommendation` 当前只是兼容旧命名的动作空间接口，不再代表“后端已经给出推荐”。

当前后端返回示例：

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
      "legal_actions": {
        "play_card": [],
        "minion_attack": [],
        "hero_attack": [],
        "hero_power": [
          {
            "type": "hero_power",
            "source": "my_hero_power",
            "cost": 2,
            "hero_class": "MAGE",
            "target_required": true,
            "target": "enemy_hero",
            "possible_targets": ["enemy_hero", 101, "my_hero"],
            "damage": 1,
            "effect": {
              "kind": "damage",
              "damage": 1
            }
          }
        ],
        "activate_ability": [],
        "end_turn": [{"type": "end_turn"}]
      },
      "legal_sequences": []
    }
  }
}
```

英雄技能处理也同步改为“所有基础职业英雄技能都枚举”。当法力不足时不生成英雄技能动作；当技能需要场面空位且我方场面已满时，该技能不属于当前合法动作。法师和牧师这类需要目标的技能会带 `possible_targets`；战士、术士、盗贼、德鲁伊、恶魔猎手等不需要目标的技能会通过 `effect.kind` 描述效果。

AI 决策阶段必须遵守：

- 只能选择后端给出的动作或序列。
- 不能新造实体 ID、目标或费用。
- 不能绕过嘲讽、潜行、休眠、免疫等硬规则。
- 可以根据提示词原则判断“斩杀、解场、保血、抢血、节奏、资源”等优先级。
- 后端只校验合法性，不替 AI 判断策略优劣。

### 14.14 推荐日志写入流程修订

动作空间是 AI 决策输入，不是最终推荐结果。因此当前流程中：

```text
/api/recommendation 返回 plan = "action_space"
        ↓
不写 recommendations.jsonl
        ↓
AI 选择具体打法
        ↓
后端校验动作合法性
        ↓
校验通过后才写 recommendations.jsonl
```

这意味着网页端可以继续频繁请求动作空间，但不会再把每次请求都写成推荐日志。推荐日志只用于保存 AI 最终给出的打法，例如 `chosen_sequence_id`、具体 `actions`、理由、风险和校验结果。

### 14.15 HDT 接入过滤流程

为避免 HDT 重发历史记录导致日志时间线混乱，`/ws/hdt` 当前在写日志前执行过滤：

```text
HDT WebSocket 消息
        ↓
基础 JSON 校验
        ↓
IngestFilter.accept(envelope)
        ↓
通过：StateStore.apply → ReplayWriter.write → UI broadcast
丢弃：返回 ack filtered=true，不写日志，不刷新 UI
```

过滤器维护当前对局的最高回合数：

- 如果同一 `game_id` 中已经看到 `turn = 5`，之后又收到 `turn = 0/1/2/3/4` 的状态或事件，会被视为历史回放并丢弃。
- 如果收到 `game_started`，过滤器重置，允许新对局从 `turn = 0/1` 开始。
- 事件指纹不包含 `timestamp`，因为历史回放事件可能会带新的接收时间，但事件本身相同。

这个规则解决的是“旧时间线重新写入当前对局目录”的问题。它不会完整判断所有炉石事件是否合法，只负责保护日志顺序和去重。
