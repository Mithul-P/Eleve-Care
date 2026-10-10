from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import Settings
from ..deps import db_dep, settings_dep
from ..security import CurrentUser
from ..services import chatbot
from ..services.notifications import build_notifications

router = APIRouter(prefix="/chatbot", tags=["chatbot"])


class Turn(BaseModel):
    role: str = Field(..., max_length=20)
    content: str = Field(..., max_length=8000)


class ChatQuery(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    elevator_id: str | None = None
    component_id: str | None = None
    history: list[Turn] = Field(default_factory=list, max_length=20)


@router.post("/query")
async def query(body: ChatQuery, _: CurrentUser, db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    return await chatbot.answer(db, s, body.message, body.elevator_id, body.component_id,
                                [t.model_dump() for t in body.history])


@router.get("/notifications")
async def proactive(_: CurrentUser, db=Depends(db_dep), s: Settings = Depends(settings_dep)):
    """Proactive messages the assistant raises when the chat opens."""
    items = [n for n in await build_notifications(db) if n["kind"] in ("near_failure", "checkup_due", "attention")]
    return {"llm_enabled": bool(s.anthropic_api_key), "messages": items[:6]}
