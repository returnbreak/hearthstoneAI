# HDT AI Assistant Plugin

This folder contains the first implementation slice for the Hearthstone Deck Tracker plugin.

## Scope

- Implements `Plugins.IPlugin` in `HdtAiAssistantPlugin.PluginEntry`.
- Reads HDT public game state through `API.Core.Game`, `Player`, `Opponent`, and `Entity` objects.
- Publishes `game_event` and `game_state` JSON envelopes to `ws://127.0.0.1:8765/ws/hdt`.
- Uses state hashing and per-turn throttling so `OnUpdate()` does not flood the backend.
- Does not automate gameplay, read memory, infer hidden opponent hand cards, or modify Hearthstone.

## Configuration

On first load the plugin creates:

```text
%APPDATA%\HdtAiAssistantPlugin\config.txt
%APPDATA%\HdtAiAssistantPlugin\plugin.log
```

Supported config keys:

```text
backend_websocket_url=ws://127.0.0.1:8765/ws/hdt
poll_interval_ms=500
max_recent_events=20
max_automatic_recommendations_per_turn=2
```

## Build Notes

`HdtAiAssistantPlugin` targets `.NET Framework 4.7.2` and expects HDT assemblies at:

```text
%LOCALAPPDATA%\HearthstoneDeckTracker\app-1.52.15
```

If HDT is installed elsewhere or the HDT version directory changes, pass `HdtInstallDir` to MSBuild or edit `HdtAiAssistantPlugin.csproj`.

The core logic is kept in `HdtAiAssistant.Core` and can be compiled independently for tests.
