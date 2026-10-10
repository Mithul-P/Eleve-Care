"""WebSocket stream for real-time dashboards: /ws/stream?token=<JWT>."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..security import decode_token
from ..services.broadcaster import broadcaster

router = APIRouter(tags=["stream"])


@router.websocket("/ws/stream")
async def stream(ws: WebSocket, token: str = ""):
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    await broadcaster.connect(ws)
    runner = getattr(ws.app.state, "runner", None)
    await ws.send_text(json.dumps({"type": "hello", "data": runner.status() if runner else None}, default=str))
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(ws)
