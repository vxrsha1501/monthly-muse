"""Generation, review/compare, feedback and history endpoints (FR-03..FR-11, FR-16)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.errors import Forbidden, NotFound
from app.db.models import FeedbackEvent, GeneratedMessage, GenerationRequest, MessageStatus, User
from app.schemas.generation import (FeedbackIn, GenerateIn, GenerateOut, HistoryItemOut,
                                    MessagePatchIn, StageOut)
from app.services import feedback_service, generation_service, history_service
from app.services.generation_service import run_request

router = APIRouter(tags=["messages"])


# --------------------------------------------------------------- generation

@router.post("/messages/generate", response_model=GenerateOut, status_code=202)
def generate(data: GenerateIn, background: BackgroundTasks,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> GenerateOut:
    """One-off or form-based generation (Section 10.3): 202 + request_id, poll for stage."""
    req = generation_service.start_generation(db, user, data, trigger="manual")
    background.add_task(run_request, req.id)
    return GenerateOut(request_id=req.id, status=req.status.value)


@router.get("/generation-requests/{request_id}", response_model=StageOut)
def request_stage(request_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> StageOut:
    return generation_service.get_request_stage(db, user.id, request_id)


@router.post("/generation-requests/{request_id}/regenerate", response_model=GenerateOut, status_code=202)
def regenerate(request_id: str, background: BackgroundTasks,
               instruction: str | None = Query(default=None, max_length=500),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> GenerateOut:
    """New batch with an optional 'what to change' instruction (Section 3.3)."""
    message = db.scalar(select(GeneratedMessage).where(GeneratedMessage.request_id == request_id).limit(1))
    if message is not None:
        feedback_service.record_event(db, user, message, event_type="regenerate", reward=-0.3, processed=True)
        db.commit()
    req = generation_service.regenerate(db, user, request_id, instruction)
    background.add_task(run_request, req.id)
    return GenerateOut(request_id=req.id, status=req.status.value)


# --------------------------------------------------------------- history

@router.get("/messages")
def list_messages(q: str | None = None, tone: str | None = None, topic: str | None = None,
                  month: str | None = None, status: str | None = None,
                  min_score: float | None = None, max_score: float | None = None,
                  semantic: bool = False, page: int = 1, page_size: int = 20,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    items, total = history_service.search_history(
        db, user.id, q=q, tone=tone, topic=topic, month=month, status=status,
        min_score=min_score, max_score=max_score, semantic=semantic, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/messages/{message_id}")
def message_detail(message_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    data = history_service.message_detail(db, user.id, message_id)
    message: GeneratedMessage = data["message"]
    return {
        "message": {
            "id": message.id, "text": message.final_text or message.text, "tone": message.intended_tone,
            "style_label": message.style_label, "score": message.score, "status": message.status.value,
            "rank": message.rank, "word_count": message.word_count, "nearest_cosine": message.nearest_cosine,
            "features": {"relevance": message.f_relevance, "tone": message.f_tone,
                         "personalization": message.f_personalization, "novelty": message.f_novelty,
                         "length": message.f_length, "history": message.f_history},
            "explain": message.explain, "created_at": message.created_at.isoformat(),
        },
        "batch": [{"id": m.id, "text": m.final_text or m.text, "rank": m.rank, "score": m.score,
                   "status": m.status.value, "style_label": m.style_label} for m in data["batch"]],
        "inputs": data["inputs"],
        "chosen": data["chosen"],
        "feedback": data["feedback"],
    }


@router.post("/messages/{message_id}/reuse")
def reuse(message_id: str, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> dict:
    """'Generate similar' - recover the inputs of a past batch."""
    return history_service.reuse_as_start(db, user.id, message_id)


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(message_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = db.get(GeneratedMessage, message_id)
    if row is None or row.user_id != user.id:
        raise NotFound("message")
    row.status = MessageStatus.ARCHIVED
    db.commit()


# --------------------------------------------------------------- review actions

@router.patch("/messages/{message_id}")
def edit_message(message_id: str, data: MessagePatchIn,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = feedback_service.handle_edit(db, user, message_id, data.final_text)
    return {"id": row.id, "final_text": row.final_text, "word_count": row.word_count,
            "status": row.status.value}


@router.post("/messages/{message_id}/select")
def select_message(message_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    """Select as final: updates u, Beta arms, length stats, tone counts and weights."""
    row = feedback_service.handle_select(db, user, message_id)
    return {"id": row.id, "status": row.status.value, "request_id": row.request_id}


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: str, data: FeedbackIn,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return feedback_service.handle_feedback(db, user, message_id, data.event_type, data.reason)
