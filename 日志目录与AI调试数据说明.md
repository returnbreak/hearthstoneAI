# 日志目录与 AI 调试数据说明

每局对局使用一个独立目录：

```text
data/game_logs/<game_id>/
```

## 常规数据

每类常规数据保存两份内容相同、用途不同的文件：

| 数据 | 程序读取和回放 | 人工查看 |
| --- | --- | --- |
| 场面状态 | `game_state.jsonl` | `game_state.json` |
| 对局事件 | `events.jsonl` | `events.json` |
| 推荐结果 | `recommendations.jsonl` | `recommendations.json` |
| 开局信息和己方套牌 | `game_metadata.jsonl` | `game_metadata.json` |

`.jsonl` 每行是一个完整 JSON 对象，适合逐条追加、回放和程序处理。

同名 `.json` 是格式化 JSON 数组，带缩进和层级，仅用于人工查看。后续业务逻辑不会读取这些文件。

当前状态日志不再记录 `can_attack` 和 `attacks_remaining`，改为：

```json
"attacks_this_turn": 0,
"max_attacks_per_turn": 2
```

普通攻击上限为 1，风怒为 2，超级风怒为 4。后端再结合冻结、休眠、疲惫和不能攻击等公开状态生成合法攻击。

事件目标采用一个嵌套对象：

```json
"target": {
  "entity_id": 107,
  "card_id": "CORE_BT_480",
  "name": "火色魔印奔行者",
  "type": "minion"
}
```

无目标事件不输出 `target` 字段。已删除始终为空的 `zone_from`、
`zone_to`，以及重复的 `target_player`、`target_is_hero` 等字段。

事件采用按需字段：

- `game_id`、`timestamp`、`turn`、`player`、`type` 是基础字段。
- `entity_id`、`dbf_id`、`damage_amount` 只有大于 0 时记录。
- `card_id`、`name`、`reason`、`result` 只有非空时记录。
- `target` 只有 HDT 提供真实目标实体时记录。

状态日志只记录 `my_mana` 和 `enemy_mana`，不再记录与 `my_mana`
重复的 `mana`。手牌和已知敌方卡牌不再记录恒定的 `zone` 和
没有消费者的 `source`。

推荐日志中，成功决策使用 `summary` 表达理由，不再重复保存同值的
`reason`。`validation_status` 为 `passed` 时不再记录固定的校验原因；
校验失败时仍保留具体原因。值为空的可选字段不会写入日志。

## AI 请求调试数据

AI 请求只保存 JSON，不保存 JSONL：

```text
data/game_logs/<game_id>/debug/ai_requests/<时间戳与触发标识>.json
```

每个文件对应一次真实模型调用，包含：

- 触发原因、回合和状态版本
- system 提示词和解析后的结构化提示词数据
- 模型名称、流式配置等非重复请求配置
- 解析后的模型返回和校验后的决策
- 候选动作数量、提示词长度和耗时

为避免同一内容重复占用空间，调试日志不再保存：

- 与结构化 `payload` 相同的 `user_prompt`
- 与 system 提示词和 `payload` 重复的 `model_request.messages`
- 与 `raw_model_output` 相同的 `raw_model_content`
- 模型请求调试对象中的重复原始响应文本

这些文件只用于定位推荐错误和响应延迟，不参与推荐、回放或前端展示，也不会记录 API Key。

## 开局套牌数据

HDT 插件在对局开始时读取当前选中的己方套牌，并发送 `game_metadata`。后端会使用本地卡牌库补充卡牌名称、费用、类型、文本和图片等公开信息，然后同时写入：

```text
game_metadata.jsonl
game_metadata.json
```

没有选择套牌时仍会记录开局信息，但 `deck_available` 为 `false`。
