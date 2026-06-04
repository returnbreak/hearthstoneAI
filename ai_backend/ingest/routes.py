from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_backend.cards import CardCatalog
from ai_backend.ingest.filter import IngestFilter
from ai_backend.state.replay_writer import ReplayWriter
from ai_backend.state.state_store import StateStore
from ai_backend.ui.broadcast import BroadcastHub


def create_ingest_router(
    state_store: StateStore,
    replay_writer: ReplayWriter,
    ui_hub: BroadcastHub,
    card_catalog: CardCatalog | None = None,
) -> APIRouter:
    router = APIRouter()
    ingest_filter = IngestFilter()
    card_catalog = card_catalog or CardCatalog.from_latest_data()

    @router.get("/api/state")
    async def get_state() -> Dict[str, Any]:
        return state_store.snapshot()

    @router.websocket("/ws/hdt")
    async def hdt_ingest(websocket: WebSocket) -> None:
        await websocket.accept()

        while True:
            try:
                envelope = await websocket.receive_json()

                if not isinstance(envelope, dict):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Envelope must be a JSON object.",
                    })
                    continue

                if not ingest_filter.accept(envelope):
                    await websocket.send_json({
                        "type": "ack",
                        "filtered": True,
                        "message_count": state_store.snapshot()["message_count"],
                    })
                    continue

                envelope = card_catalog.enrich_envelope(envelope)
                state_store.apply(envelope)
                replay_writer.write(envelope)

                snapshot = state_store.snapshot()
                await ui_hub.broadcast({
                    "type": "backend_update",
                    "snapshot": snapshot,
                    "envelope": envelope,
                })

                await websocket.send_json({
                    "type": "ack",
                    "message_count": snapshot["message_count"],
                })

            except WebSocketDisconnect:
                break

            except ValueError as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": str(exc),
                })

    return router
