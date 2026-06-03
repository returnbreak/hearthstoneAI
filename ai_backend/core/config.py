"""
后端全局配置——定义项目目录、日志路径和静态资源路径。

路径约定：
  - BASE_DIR    = ai_backend/（本应用目录）
  - PROJECT_ROOT = BASE_DIR 的父目录（项目根目录）
  - LOG_DIR     = PROJECT_ROOT / data / game_logs（JSONL 日志输出目录）
  - STATIC_DIR  = BASE_DIR / ui / static（前端静态文件目录）
"""

from pathlib import Path


# 本模块所在文件为 ai_backend/core/config.py，
# .resolve().parents[1] 向上两级得到 ai_backend/
BASE_DIR = Path(__file__).resolve().parents[1]

# 项目根目录（hearthstoneAI/），用于引用 hdt_plugin/、data/ 等同级目录
PROJECT_ROOT = BASE_DIR.parent

# 游戏日志输出目录：data/game_logs/
# 存放 game_state.jsonl、events.jsonl 等对局记录
LOG_DIR = PROJECT_ROOT / "data" / "game_logs"

# 前端静态文件目录：ai_backend/ui/static/
# 包含 index.html、CSS、JS 等前端资源
STATIC_DIR = BASE_DIR / "ui" / "static"
