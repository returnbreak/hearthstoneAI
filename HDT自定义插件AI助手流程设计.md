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

