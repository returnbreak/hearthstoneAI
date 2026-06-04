"""
基于规则的炉石传说 AI 教练模块。

本模块提供了一套纯规则的决策辅助系统，仅依赖炉石传说的公开游戏状态
（即双方玩家都能在 UI 上看到的信息），不涉及任何隐藏信息（如对手手牌、
牌库顺序等）。

主要组件：
    - CombatAnalyzer:     计算合法的攻击目标（考虑嘲讽、免疫、潜行等机制）
    - LethalChecker:      判断当前回合是否具备斩杀条件
    - BoardTradeEvaluator: 评估并推荐最优的场面交换方案
    - RecommendationEngine: 综合以上分析，产出最终的推荐行动
    - routes.py:           将推荐引擎暴露为 FastAPI HTTP 接口
"""
