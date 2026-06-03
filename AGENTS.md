# Repository Guidelines

## Project Structure & Module Organization

This repository currently contains planning documentation for a Hearthstone Deck Tracker (HDT) AI assistant:

- `HDT自定义插件AI助手架构设计.md`: architecture, module boundaries, data schemas, and safety limits.
- `HDT自定义插件AI助手流程设计.md`: end-to-end runtime, fallback, replay, and validation flows.

When implementation begins, follow the documented layout:

- `hdt_plugin/`: C# HDT plugin (`PluginEntry.cs`, collectors, state builders, publishers).
- `ai_backend/`: Python FastAPI service, rule engine, LangChain client, schema validation, replay tools.
- `data/game_logs/` and `data/replays/`: generated JSONL logs and replay reports. Do not commit large runtime logs unless needed as small fixtures.
- `docs/`: API schemas, prompt templates, and design updates.

## Build, Test, and Development Commands

There is no buildable source tree yet. After implementation, use consistent commands and document changes here:

- `python -m venv .venv`: create the backend virtual environment.
- `pip install -r ai_backend/requirements.txt`: install backend dependencies.
- `uvicorn ai_backend.app:app --host 127.0.0.1 --port 8765`: run the local API/UI service.
- `pytest`: run backend unit and replay tests.
- Build the HDT plugin from Visual Studio or MSBuild targeting `.NET Framework 4.7.2`.

## Coding Style & Naming Conventions

Use descriptive, module-specific names matching the architecture docs: `GameStateBuilder`, `SnapshotPublisher`, `rule_engine.py`, `schema_validator.py`. Prefer explicit JSON schema models for `GameEvent`, `GameState`, and `Recommendation`. Python should use 4-space indentation, type hints where useful, and snake_case filenames. C# should use PascalCase types and methods, with one primary class per file.

## Testing Guidelines

Backend tests should cover card data enrichment, state merging, rule engine decisions, prompt construction, schema validation, and fallback behavior. Name Python tests `test_*.py`. Replay tests should consume JSONL fixtures and produce `replay_test_report.md`. HDT plugin testing should verify event capture, reconnect behavior, and state snapshots against real or recorded HDT data.

## Commit & Pull Request Guidelines

No repository-specific commit convention is established yet. Use short imperative commits, for example `Add FastAPI state ingest endpoint`. PRs should include a concise summary, affected modules, test results, and screenshots for UI changes. Link related issues or design sections when changing architecture.

## Security & Configuration Tips

Keep the assistant read-only. Do not add automatic play, memory reading, client modification, hidden-card inference, or anti-cheat bypass behavior. Store API keys in environment variables, never in committed files. Keep model outputs constrained to public game state and validated recommendation JSON.
