import asyncio
import json

import websockets


SAMPLE_STATE = {
    "type": "game_state",
    "trigger": "manual",
    "state": {
        "game_id": "sample-match",
        "turn": 9,
        "active_player": "me",
        "mana": {"current": 9, "max": 9},
        "my_mana": {"current": 9, "max": 9},
        "enemy_mana": {"current": 8, "max": 8},
        "my_hero": {"class": "MAGE", "hp": 24, "armor": 0, "attack": 0},
        "enemy_hero": {"class": "HUNTER", "hp": 12, "armor": 0, "attack": 0},
        "hand": [{"name": "Fireball", "cost": 4, "type": "SPELL", "card_id": "CS2_029"}],
        "my_board": [],
        "enemy_board": [],
    },
}


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8765/ws/hdt") as websocket:
        await websocket.send(json.dumps(SAMPLE_STATE))
        print(await websocket.recv())


if __name__ == "__main__":
    asyncio.run(main())
