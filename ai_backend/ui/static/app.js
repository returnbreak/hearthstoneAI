/**
 * app.js — HDT AI 助手前端渲染逻辑
 *
 * 职责：
 *   1. 通过 REST API 获取初始快照（页面首次加载）
 *   2. 通过 WebSocket 接收后端实时推送，增量更新页面
 *   3. 将快照数据渲染为 DOM：英雄、回合、手牌、随从、事件日志
 *
 * 数据流：
 *   GET /api/state  ──► render(snapshot)   （首次加载）
 *   WS  /ws/ui      ──► render(snapshot)   （实时更新，约每秒多次）
 *
 * 设计要点：
 *   - 所有 DOM 元素引用在模块顶层一次性获取，避免重复查询
 *   - render() 是无状态的：每次调用完全重绘，不依赖上一次的 DOM 状态
 *   - escapeHtml() 防御 XSS，所有用户可控的字符串都经过转义
 *   - WebSocket 断开后自动重连，1 秒间隔退避
 */

// ── DOM 元素引用（模块顶层，一次性获取） ──────────────────

/** 顶部连接状态指示文字（"Connected" / "Disconnected, retrying"） */
const connection = document.getElementById("connection");

/** 已处理消息计数（右上角数字） */
const messageCount = document.getElementById("message-count");

/** 当前回合数显示 */
const turn = document.getElementById("turn");

/** 法力水晶信息（"Mana 3/10"） */
const mana = document.getElementById("mana");

/** 己方英雄信息 */
const myHero = document.getElementById("my-hero");

/** 对方英雄信息 */
const enemyHero = document.getElementById("enemy-hero");

/** 手牌容器 */
const hand = document.getElementById("hand");

/** 己方随从（战场）容器 */
const myBoard = document.getElementById("my-board");

/** 对方随从（战场）容器 */
const enemyBoard = document.getElementById("enemy-board");

/** 近期事件列表（<ol>） */
const events = document.getElementById("events");


// ── 顶层渲染入口 ─────────────────────────────────────────

/**
 * 将后端快照渲染为整个页面的 DOM。
 *
 * 每次调用都是"全量重绘"——先清空再填充，
 * 不依赖上一次渲染的中间状态，保证与后端数据严格一致。
 *
 * @param {Object} snapshot - 后端快照对象，结构为：
 *   {
 *     latest_state:  Object|null  — 最新游戏状态（可能为 null）
 *     recent_events: Array        — 近期事件列表
 *     message_count: number       — 已处理消息总数
 *   }
 */
function render(snapshot) {
  // latest_state 可能为 null（尚未收到任何 game_state 消息）
  // 用 || {} 兜底，避免后续访问 .turn / .mana 等属性时抛 TypeError
  const state = snapshot.latest_state || {};

  // 消息计数：右上角显示后端已处理的消息总数
  messageCount.textContent = snapshot.message_count || 0;

  // 回合数：null/undefined 时显示 "--"，0 是合法值所以用 ?? 而非 ||
  turn.textContent = state.turn ?? "--";

  // 法力水晶：优先显示新协议 my_mana/enemy_mana，旧日志仍可回退到 mana
  mana.textContent = formatManaLine(state);

  // 英雄信息：委托给 formatHero() 做格式化，不存在时为 "--"
  myHero.textContent = formatHero(state.my_hero);
  enemyHero.textContent = formatHero(state.enemy_hero);

  // 手牌、己方随从、对方随从：委托给 renderCards() 统一渲染
  // 第二个参数用 || [] 兜底——字段可能不存在或为 null
  renderCards(hand, state.hand || [], formatCard);         // 手牌用 formatCard
  renderCards(myBoard, state.my_board || [], formatMinion); // 随从用 formatMinion
  renderCards(enemyBoard, state.enemy_board || [], formatMinion);

  // 事件日志：委托给 renderEvents() 渲染
  renderEvents(snapshot.recent_events || []);
}

// ── 卡片 / 英雄 / 随从 格式化函数 ─────────────────────────

/**
 * 将英雄对象格式化为单行文本。
 *
 * 输入示例：
 *   { class: "MAGE", hp: 30, armor: 5, attack: 0, can_attack: false, frozen: false, immune: false }
 * 输出示例：
 *   "MAGE 30+5 atk 0"            — 无特殊状态
 *   "WARRIOR 25+10 atk 3 · can attack" — 可以攻击
 *
 * @param {Object|null|undefined} hero - 英雄数据对象
 * @returns {string} 格式化后的英雄文本，或 "--"（数据缺失时）
 */
function formatManaLine(state) {
  const mine = state.my_mana || state.mana;
  const enemy = state.enemy_mana;
  const mineText = mine ? `Me ${mine.current ?? 0}/${mine.max ?? 0}` : "Me --";
  const enemyText = enemy ? `Opponent ${enemy.current ?? 0}/${enemy.max ?? 0}` : "Opponent --";
  return `Mana ${mineText} | ${enemyText}`;
}

function formatHero(hero) {
  // 英雄数据不存在（尚未收到或字段缺失）
  if (!hero) return "--";

  // 收集激活的状态标记（仅收集值为 true 的关键词）
  const flags = [];
  if (hero.can_attack) flags.push("can attack");  // 英雄可以攻击
  if (hero.frozen) flags.push("frozen");            // 被冻结
  if (hero.immune) flags.push("immune");            // 免疫

  // 拼接示例： "MAGE 30+5 atk 0 · can attack, frozen"
  // class 不存在时用 "UNKNOWN"；hp/armor/attack 为 null/undefined 时用 0
  return `${hero.class || "UNKNOWN"} ${hero.hp ?? 0}+${hero.armor ?? 0} atk ${hero.attack ?? 0}${flags.length ? " · " + flags.join(", ") : ""}`;
}

/**
 * 将手牌对象格式化为 { title, meta, tags } 结构，供 renderCards() 使用。
 *
 * 输入示例：
 *   { name: "火球术", card_id: "CS2_029", cost: 4, type: "SPELL" }
 * 返回示例：
 *   { title: "火球术", meta: "4 mana · SPELL", tags: "CS2_029" }
 *
 * @param {Object} card - 手牌卡牌数据
 * @returns {{title: string, meta: string, tags: string}}
 */
function formatCard(card) {
  return {
    // 优先显示卡牌名称，其次 card_id，都没有则 "Unknown card"
    title: card.name || card.card_id || "Unknown card",
    // 费用 + 卡牌类型，如 "4 mana · SPELL"
    // cost 可能为 0（0 费卡），所以用 ?? 而非 ||
    meta: `${card.cost ?? 0} mana · ${card.type || "UNKNOWN"}`,
    // 标签显示 card_id，用于调试识别卡牌
    tags: card.card_id || ""
  };
}

/**
 * 将随从对象格式化为 { title, meta, tags } 结构，供 renderCards() 使用。
 *
 * 输入示例：
 *   { name: "石拳食人魔", attack: 6, health: 7, attacks_remaining: 1, taunt: true }
 * 返回示例：
 *   { title: "石拳食人魔", meta: "6/7 · attacks 1", tags: "taunt" }
 *
 * @param {Object} minion - 随从实体数据
 * @returns {{title: string, meta: string, tags: string}}
 */
function formatMinion(minion) {
  // 炉石传说中随从可能的关键词——只收集值为 true 的
  const tags = [
    "taunt",           // 嘲讽
    "divine_shield",   // 圣盾
    "stealth",         // 潜行
    "rush",            // 突袭
    "charge",          // 冲锋
    "windfury",        // 风怒
    "mega_windfury",   // 超级风怒（一回合攻击四次）
    "lifesteal",       // 吸血
    "poisonous",       // 剧毒
    "venomous",        // 毒素（同剧毒，不同实现）
    "reborn",          // 复生
    "deathrattle",     // 亡语（作为关键词标注在随从身上）
    "frozen",          // 冻结
    "immune"           // 免疫
  ].filter((key) => minion[key]);  // 只保留值为 true 的关键词

  return {
    // 优先显示名称，其次 card_id，都没有则 "Unknown minion"
    title: minion.name || minion.card_id || "Unknown minion",
    // 攻击/生命 + 剩余攻击次数，如 "6/7 · attacks 1"
    // attack/health 可能为 0（0 攻随从），所以用 ?? 而非 ||
    meta: `${minion.attack ?? 0}/${minion.health ?? 0} · attacks ${minion.attacks_remaining ?? 0}`,
    // 关键词用逗号连接，如 "taunt, divine_shield"
    tags: tags.join(", ")
  };
}

/**
 * 通用的卡片列表渲染函数——根据数据列表和格式化器生成 DOM 节点。
 *
 * 手牌和随从都复用此函数，区别仅在于传入的 formatter 不同：
 *   - 手牌 → formatCard（显示费用 + 卡牌类型）
 *   - 随从 → formatMinion（显示攻击/生命 + 关键词）
 *
 * 渲染策略：
 *   - 先清空容器（innerHTML = ""），再逐个创建 .card 节点
 *   - 空列表时显示 "No data" 占位文案，CSS 类设为 empty
 *   - 非空时 CSS 类设为 cards，清除占位样式
 *
 * @param {HTMLElement} container - 卡片容器 DOM 元素
 * @param {Array}       items     - 数据对象数组
 * @param {Function}    formatter - 格式化函数，接收 (item) → {title, meta, tags}
 */
function renderCards(container, items, formatter) {
  // 清空容器（移除上一次渲染的所有子节点）
  container.innerHTML = "";

  // 空列表：设置 empty 样式类 + 占位文案
  if (!items.length) {
    container.className = "cards empty";
    container.textContent = "No data";
    return;
  }

  // 非空：恢复正常样式类
  container.className = "cards";

  for (const item of items) {
    // 用 formatter 将原始数据转为展示结构
    const data = formatter(item);

    // 创建一张卡牌的 DOM 节点
    const node = document.createElement("div");
    node.className = "card";

    // 结构：左侧（标题 + 副文本） | 右侧（标签）
    // escapeHtml() 防御 XSS——所有用户可控字符串都转义
    node.innerHTML = `<div><strong>${escapeHtml(data.title)}</strong><br><small>${escapeHtml(data.meta)}</small></div><div class="tags">${escapeHtml(data.tags)}</div>`;

    container.appendChild(node);
  }
}

/**
 * 渲染近期事件列表（页面右下方 <ol> 区域）。
 *
 * 显示策略：
 *   - 只取最近 12 条（slice(-12)），避免 DOM 节点过多
 *   - 反转顺序（reverse()）：最新的在最上面，符合阅读直觉
 *   - 用 textContent 而非 innerHTML——事件文本不需要 HTML 格式，
 *     textContent 天然防 XSS 且性能更好
 *
 * @param {Array} items - 事件对象数组，每个元素包含 turn/player/type/name/card_id 等字段
 */
function renderEvents(items) {
  // 清空列表
  events.innerHTML = "";

  // 取最近 12 条并反转——最新的在前
  for (const event of items.slice(-12).reverse()) {
    const node = document.createElement("li");

    // 拼接格式："<回合> <玩家> <事件类型> <详情>"
    // trim() 去除首尾空格——各字段都可能缺失，不保留多余空白
    // 用 textContent 赋值，自动转义，无需 escapeHtml()
    node.textContent = `${event.turn ?? ""} ${event.player || ""} ${event.type || ""} ${event.name || event.card_id || event.reason || ""} ${event.result || ""}`.trim();

    events.appendChild(node);
  }
}

// ── 安全工具 ─────────────────────────────────────────────

/**
 * HTML 转义——防御 XSS 攻击。
 *
 * 将字符串中的特殊字符替换为 HTML 实体，
 * 防止用户可控数据（卡牌名称、card_id 等）被注入 <script> 标签。
 *
 * 使用场景：
 *   - renderCards() 中所有 innerHTML 赋值前都经过此函数
 *   - renderEvents() 使用 textContent 赋值，不需要此函数
 *
 * @param {*} value - 要转义的值（会通过 String() 强制转为字符串）
 * @returns {string} 转义后的安全字符串
 */
function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")   // & 必须先转义，否则会破坏后续实体
    .replaceAll("<", "&lt;")    // 防 <script> 标签注入
    .replaceAll(">", "&gt;")    // 防 > 闭合标签注入
    .replaceAll('"', "&quot;"); // 防属性值注入
}

// ── 网络通信：REST + WebSocket ──────────────────────────

/**
 * 首次加载：通过 REST API 获取当前游戏状态快照。
 *
 * GET /api/state 返回的 JSON 结构与 WebSocket 推送的 snapshot 相同，
 * 直接复用 render() 渲染。
 *
 * 失败时静默忽略——如果后端尚未启动或网络异常，
 * 不会弹错误提示，等 WebSocket 连接成功后再渲染。
 */
async function loadInitial() {
  const response = await fetch("/api/state");
  // response.json() 解析 JSON 响应体为 JS 对象
  render(await response.json());
}

/**
 * 建立 WebSocket 连接并绑定事件处理。
 *
 * 连接生命周期：
 *   open     ──► 显示 "Connected"，等待后端推送
 *   message  ──► 收到后端推送 → JSON 解析 → render() 重绘页面
 *   close    ──► 显示 "Disconnected, retrying"，1 秒后自动重连
 *
 * 设计要点：
 *   - 自动重连：close 事件中 setTimeout(connect, 1000) 递归调用自身
 *   - 没有最大重试限制——只要后端恢复，前端最终一定会连上
 *   - 使用 location.host 构建 WebSocket URL，部署时无需修改
 *   - 协议自动匹配：HTTPS 页面用 wss://，HTTP 页面用 ws://
 */
function connect() {
  // 根据页面协议选择 WebSocket 协议（https → wss, http → ws）
  const protocol = location.protocol === "https:" ? "wss" : "ws";

  // 连接后端 /ws/ui 端点
  const socket = new WebSocket(`${protocol}://${location.host}/ws/ui`);

  // 连接成功：更新状态文字
  socket.addEventListener("open", () => {
    connection.textContent = "Connected";
  });

  // 收到后端推送：解析 JSON → 提取 snapshot → 渲染
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    // payload 结构：{ type: "backend_update", snapshot: {...}, envelope: {...} }
    // 只渲染 snapshot 部分，envelope 暂未在前端使用
    if (payload.snapshot) render(payload.snapshot);
  });

  // 连接断开：更新状态文字 + 1 秒后重试
  socket.addEventListener("close", () => {
    connection.textContent = "Disconnected, retrying";
    // 递归调用自身，形成无限重连循环
    setTimeout(connect, 1000);
  });
}

// ── 启动入口 ─────────────────────────────────────────────
// 页面加载后立即执行：
//   1. REST 获取初始快照（失败静默忽略）
//   2. WebSocket 建立长连接（持续接收推送）

loadInitial().catch(() => {});
connect();
