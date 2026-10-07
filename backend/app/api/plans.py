"""Monthly Plans CRUD + pause/resume (FR-12)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db.models import CycleStatus, PostCycle, User
from app.schemas.plans import PlanIn, PlanOut, PlanPatch
from app.services import plan_service

router = APIRouter(prefix="/monthly-plans", tags=["plans"])


def _to_out(db: Session, plan) -> PlanOut:
    cycles = list(db.scalars(select(PostCycle).where(PostCycle.plan_id == plan.id)
                             .order_by(PostCycle.target_month)).all())
    # "Next generation" must match what the scheduler will actually run next
    # (PLANNED / GENERATION_FAILED with generate_at due) - not a cycle that has
    # already generated and is merely awaiting review.
    upcoming = next((c for c in cycles if c.status in (CycleStatus.PLANNED,
                                                       CycleStatus.GENERATION_FAILED)), None)
    last = next((c for c in reversed(cycles) if c.status != CycleStatus.PLANNED), None)
    out = PlanOut.model_validate(plan)
    out.next_generate_at = upcoming.generate_at if upcoming else None
    out.last_status = last.status.value if last else None
    return out


@router.post("", response_model=PlanOut, status_code=201)
def create_plan(data: PlanIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> PlanOut:
    plan = plan_service.create_plan(db, user, data)
    return _to_out(db, plan)


@router.get("", response_model=list[PlanOut])
def list_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_to_out(db, p) for p in plan_service.list_plans(db, user.id)]


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> PlanOut:
    return _to_out(db, plan_service.get_plan(db, user.id, plan_id))


@router.patch("/{plan_id}", response_model=PlanOut)
def patch_plan(plan_id: str, data: PlanPatch, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)) -> PlanOut:
    return _to_out(db, plan_service.patch_plan(db, user, plan_id, data))


@router.post("/{plan_id}/pause", response_model=PlanOut)
def pause(plan_id: str, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)) -> PlanOut:
    return _to_out(db, plan_service.pause_plan(db, user.id, plan_id, active=False))


@router.post("/{plan_id}/resume", response_model=PlanOut)
def resume(plan_id: str, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)) -> PlanOut:
    return _to_out(db, plan_service.pause_plan(db, user.id, plan_id, active=True))


@router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: str, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    plan_service.delete_plan(db, user.id, plan_id)
